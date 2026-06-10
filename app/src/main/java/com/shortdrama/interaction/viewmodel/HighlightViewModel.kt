package com.shortdrama.interaction.viewmodel

import android.app.Application
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import com.shortdrama.interaction.data.model.SpriteExpression
import com.shortdrama.interaction.data.model.SpriteState
import com.shortdrama.interaction.data.network.HighlightComment
import com.shortdrama.interaction.data.network.HighlightRepository
import com.shortdrama.interaction.data.network.RoastCacheEntity
import com.shortdrama.interaction.data.network.RoastResult
import com.shortdrama.interaction.data.network.ServerHighlightPoint
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import android.util.Log
import java.util.concurrent.ConcurrentHashMap
import com.shortdrama.interaction.data.model.AppEvent
import com.shortdrama.interaction.data.network.AiRoastService
import com.shortdrama.interaction.data.repository.AudioMode
import com.shortdrama.interaction.data.repository.PlaybackProgressStore

data class MascotStats(
    val showCount: Int = 0,
    val agreeCount: Int = 0,
    val dislikeCount: Int = 0,
    val emotionWeights: Map<String, Int> = emptyMap()
)

data class HighlightState(
    val points: List<ServerHighlightPoint> = emptyList(),
    val highlightSegments: List<ServerHighlightPoint> = emptyList(),
    val isLoading: Boolean = false,
    val error: String? = null,
    val triggeredHighlight: ServerHighlightPoint? = null,
    val showDebugDialog: Boolean = false,
    val debugHighlight: ServerHighlightPoint? = null,
    val spriteState: SpriteState = SpriteState(),
    val currentHighlightKey: String = "",
    val perHighlightStats: Map<String, MascotStats> = emptyMap(),
    val isMascotAlwaysVisible: Boolean = false,
    val audioMode: AudioMode = AudioMode.NOTIFICATION
) {
    val currentStats: MascotStats
        get() = perHighlightStats[currentHighlightKey] ?: MascotStats()
    val spriteShowCount: Int get() = currentStats.showCount
    val agreeCount: Int get() = currentStats.agreeCount
    val dislikeCount: Int get() = currentStats.dislikeCount
}

class HighlightViewModel(
    private val repository: HighlightRepository,
    private val progressStore: PlaybackProgressStore
) : ViewModel() {

    private val _state = MutableStateFlow(HighlightState())
    val state: StateFlow<HighlightState> = _state.asStateFlow()

    private val _highlightTriggerEvent = MutableSharedFlow<ServerHighlightPoint>(replay = 0, extraBufferCapacity = 1)
    val highlightTriggerEvent: SharedFlow<ServerHighlightPoint> = _highlightTriggerEvent.asSharedFlow()

    private val _aiRoastEvent = MutableSharedFlow<String>(replay = 0, extraBufferCapacity = 1)
    val aiRoastEvent: SharedFlow<String> = _aiRoastEvent.asSharedFlow()

    private val _stopTtsEvent = MutableSharedFlow<Unit>(replay = 0, extraBufferCapacity = 1)
    val stopTtsEvent: SharedFlow<Unit> = _stopTtsEvent.asSharedFlow()

    private val _events = MutableSharedFlow<AppEvent>(extraBufferCapacity = 1)
    val events: SharedFlow<AppEvent> = _events.asSharedFlow()

    private var currentDramaTitle: String = ""
    private var currentDramaDescription: String = ""
    private var currentEpisodeTitle: String = ""
    private var currentEpisodeId: Int = -1

    // ===== Emotion State Machine =====

    private var lastEmotionTag: String? = null
    private val shownRoastIds = ConcurrentHashMap<String, MutableSet<String>>()

    /** 线程安全的 getOrPut：ConcurrentHashMap.computeIfAbsent 保证原子性 */
    private fun MutableMap<String, MutableSet<String>>.getOrPutConcurrent(
        key: String
    ): MutableSet<String> {
        return (this as ConcurrentHashMap).computeIfAbsent(key) { mutableSetOf() }
    }

    private val affinityGroups = listOf(
        setOf("感动", "心疼", "期待"),
        setOf("震惊", "无语", "吃瓜"),
        setOf("爆笑", "吃瓜")
    )

    private val conflictPairs = mapOf(
        "感动" to setOf("嘲笑", "愤怒"),
        "愤怒" to setOf("爆笑", "期待")
    )

    private fun isSameAffinityGroup(tag1: String, tag2: String): Boolean =
        affinityGroups.any { tag1 in it && tag2 in it }

    private fun isConflict(tag1: String, tag2: String): Boolean =
        conflictPairs[tag1]?.contains(tag2) == true || conflictPairs[tag2]?.contains(tag1) == true

    /** 吐槽候选排序：亲缘组 > 未展示 > 权重 > 冲突降级 */
    private fun roastCacheComparator(
        emotionWeights: Map<String, Int>,
        shown: Set<String>
    ): Comparator<RoastCacheEntity> = compareByDescending<RoastCacheEntity> { candidate ->
        if (lastEmotionTag != null && isSameAffinityGroup(lastEmotionTag!!, candidate.emotionTag)) 100 else 0
    }.thenByDescending { candidate ->
        if (candidate.id.toString() !in shown) 50 else 0
    }.thenByDescending { candidate ->
        emotionWeights[candidate.emotionTag] ?: 0
    }.thenBy { candidate ->
        if (lastEmotionTag != null && isConflict(lastEmotionTag!!, candidate.emotionTag)) 1 else 0
    }

    /** 服务端预生成评论排序：亲缘组 > 未展示 > 冲突降级 */
    private fun commentComparator(
        shown: Set<String>
    ): Comparator<Pair<Int, HighlightComment>> = compareByDescending<Pair<Int, HighlightComment>> { (_, c) ->
        if (lastEmotionTag != null && isSameAffinityGroup(lastEmotionTag!!, c.emotionTag)) 100 else 0
    }.thenByDescending { (idx, _) ->
        if ("c_$idx" !in shown) 50 else 0
    }.thenBy { (_, c) ->
        if (lastEmotionTag != null && isConflict(lastEmotionTag!!, c.emotionTag)) 1 else 0
    }

    // ===== Fallback Pool =====

    private fun naturalFallback(serverText: String, dramaTitle: String = ""): RoastResult {
        if (serverText.isNotBlank()) return RoastResult(emotionTag = "吐槽", text = serverText)

        val drama = dramaTitle.ifBlank { currentDramaTitle }
        val categorized: Map<String, List<String>> = mapOf(
            "震惊" to listOf(
                "{drama}里这反转我直接愣住",
                "{drama}这一段信息量好大",
                "完全没想到{drama}会这样发展",
                "哇{drama}这里太出乎意料了"
            ),
            "无语" to listOf(
                "{drama}编剧你是懂折磨人的",
                "这角色在{drama}里也太离谱了",
                "不是吧{drama}又来这一套",
                "{drama}这段看得我血压飙升"
            ),
            "吃瓜" to listOf(
                "{drama}名场面来了！",
                "这段{drama}我反复看了好几遍",
                "坐等{drama}这角色被打脸",
                "{drama}这段我要截图发朋友圈"
            ),
            "爆笑" to listOf(
                "哈哈{drama}这里笑死我了",
                "{drama}这个表情绝了哈哈",
                "不行了{drama}这段太搞笑了",
                "我笑到邻居来敲{drama}的门"
            ),
            "感动" to listOf(
                "{drama}这段好温暖",
                "看{drama}这里眼眶红了",
                "好喜欢{drama}这个角色",
                "{drama}这段看得我心都化了"
            ),
            "期待" to listOf(
                "后面{drama}不会更虐吧",
                "好想知道{drama}接下来怎样",
                "急急急{drama}快更新",
                "我已经开始期待{drama}下一集了"
            )
        )

        val allTags = categorized.keys.toList()
        val preferredTag = if (lastEmotionTag != null) {
            // Try to pick a tag from the same affinity group
            val sameGroupTags = allTags.filter { isSameAffinityGroup(lastEmotionTag!!, it) }
            if (sameGroupTags.isNotEmpty()) sameGroupTags.random() else allTags.random()
        } else {
            allTags.random()
        }

        val texts = categorized[preferredTag] ?: categorized.values.first()
        val text = texts.random().replace("{drama}", drama)
        return RoastResult(emotionTag = preferredTag, text = text)
    }

    // ===== State =====

    private val triggeredSet = mutableSetOf<String>()
    private var lastHighlightTimestamp: Long = 0L

    /** 切集时清除已触发的高光记录 */
    fun clearTriggeredHighlights() {
        triggeredSet.clear()
        lastHighlightTimestamp = 0L
    }
    private var spriteDismissJob: Job? = null
    private var expressionDismissJob: Job? = null
    private var loadJob: Job? = null
    private val highlightCache = linkedMapOf<String, List<ServerHighlightPoint>>()

    init {
        viewModelScope.launch {
            val saved = progressStore.getMascotAlwaysVisible()
            val mode = progressStore.getAudioMode()
            _state.update { it.copy(isMascotAlwaysVisible = saved, audioMode = mode) }
        }
    }

    private fun highlightKey(highlight: ServerHighlightPoint): String {
        return "${highlight.timestamp}_${highlight.type}"
    }

    private fun cacheKey(highlight: ServerHighlightPoint): String {
        return "${currentEpisodeTitle}_${highlight.timestamp}"
    }

    // ===== Roast Selection =====

    private fun selectRoastFromCache(
        candidates: List<RoastCacheEntity>,
        emotionWeights: Map<String, Int>
    ): RoastCacheEntity? {
        if (candidates.isEmpty()) return null

        val highlightKey = _state.value.currentHighlightKey
        val shown = shownRoastIds.getOrPutConcurrent(highlightKey)

        // Reset shown set if all candidates have been shown
        if (shown.containsAll(candidates.map { it.id.toString() })) {
            shown.clear()
            Log.d("HighlightVM", "情绪状态机: 全部已展示，重置 shownRoastIds")
        }

        // Build sorted candidate list
        val sorted = candidates.sortedWith(roastCacheComparator(emotionWeights, shown))

        val selected = sorted.first()
        shown.add(selected.id.toString())
        lastEmotionTag = selected.emotionTag

        val groupInfo = if (lastEmotionTag != null && isSameAffinityGroup(lastEmotionTag!!, selected.emotionTag))
            " (同组)" else ""
        val conflictInfo = if (lastEmotionTag != null && isConflict(lastEmotionTag!!, selected.emotionTag))
            " (冲突降级)" else ""
        Log.d("HighlightVM", "情绪状态机: last=[$lastEmotionTag], selected=[${selected.emotionTag}]$groupInfo$conflictInfo")

        return selected
    }

    private fun shouldDowngradeToFallback(highlightKey: String): Boolean {
        val candidates = _state.value.perHighlightStats[highlightKey] ?: return false
        val stats = candidates
        return stats.dislikeCount >= stats.showCount
    }

    private fun selectBestComment(comments: List<HighlightComment>, highlightKey: String): HighlightComment {
        if (comments.size == 1) return comments.first()

        val shown = shownRoastIds.getOrPutConcurrent(highlightKey)

        // 重置：全部已展示过则清空
        if (shown.size >= comments.size) {
            shown.clear()
        }

        // 按亲缘组 + 未展示优先 + 冲突降级排序
        // 使用 "c_" 前缀区分评论索引与 Room 自动生成 ID，避免去重碰撞
        val indexed = comments.mapIndexed { idx, c -> idx to c }
        val sorted = indexed.sortedWith(commentComparator(shown))

        val (selectedIdx, selected) = sorted.first()
        shown.add("c_$selectedIdx")
        return selected
    }

    // ===== Room Cache Operations =====

    private suspend fun saveRoastsToRoom(
        highlight: ServerHighlightPoint,
        roasts: List<RoastResult>
    ) {
        if (roasts.isEmpty()) return
        try {
            repository.saveRoastsToRoom(
                highlightKey = cacheKey(highlight),
                episodeTitle = currentEpisodeTitle,
                dramaTitle = currentDramaTitle,
                highlightType = highlight.type,
                roasts = roasts
            )
            Log.d("HighlightVM", "Room 写入: key=${cacheKey(highlight)}, ${roasts.size} 条")
        } catch (e: Exception) {
            Log.e("HighlightVM", "Room 写入失败", e)
        }
    }

    private suspend fun loadRoastsFromRoom(highlight: ServerHighlightPoint): List<RoastCacheEntity> {
        return try {
            val ck = cacheKey(highlight)
            val entities = repository.loadRoastsFromRoom(ck)
            Log.d("HighlightVM", "Room 查询: key=$ck, ${entities.size} 条")
            entities
        } catch (e: Exception) {
            Log.e("HighlightVM", "Room 查询失败", e)
            emptyList()
        }
    }

    // ===== AI Roast Fetch =====

    private fun fetchAiRoast(highlight: ServerHighlightPoint, source: String = "") {
        val suffix = if (source.isNotEmpty()) "($source)" else ""
        val ck = cacheKey(highlight)
        viewModelScope.launch {
            val videoId = "ep_${currentEpisodeTitle}"
            Log.d("HighlightVM", "AI 请求$suffix: key=$ck, drama=$currentDramaTitle, episode=$currentEpisodeTitle, type=${highlight.type}")

            val cached = repository.fetchAiRoasts(
                highlightKey = ck,
                videoId = videoId,
                highlight = highlight,
                dramaTitle = currentDramaTitle,
                episodeTitle = currentEpisodeTitle,
                dramaDescription = currentDramaDescription
            )

            if (cached.isNotEmpty()) {
                Log.d("HighlightVM", "AI 成功$suffix: ${cached.size} 条")
                val emotionWeights = _state.value.currentStats.emotionWeights
                val selected = selectRoastFromCache(cached, emotionWeights)
                if (selected != null) {
                    updateSpriteText(selected.roastText, selected.emotionTag, selected.id, highlight)
                }
            } else {
                val reason = if (AiRoastService.getRoastCountForType(highlight.type) <= 0) "L4不请求"
                else if (com.shortdrama.interaction.BuildConfig.AI_API_KEY.isBlank()) "API key未配置"
                else "AI超时/返回空"
                Log.w("HighlightVM", "AI 失败$suffix: $reason，降级")
                val fallback = naturalFallback(highlight.roastText)
                updateSpriteText(fallback.text, fallback.emotionTag, 0, highlight, fromAi = false)
            }
        }
    }

    private fun updateSpriteText(
        text: String,
        emotionTag: String,
        roastId: Int,
        highlight: ServerHighlightPoint,
        fromAi: Boolean = true
    ) {
        val currentState = _state.value
        // Only update if sprite is still visible for the same highlight
        if (currentState.spriteState.isVisible &&
            currentState.spriteState.highlightTimestamp == highlight.timestamp
        ) {
            _state.update {
                it.copy(spriteState = it.spriteState.copy(
                    roastText = text,
                    emotionTag = emotionTag,
                    currentRoastId = roastId
                ))
            }
            Log.d("HighlightVM", "气泡更新: text=[$text], tag=[$emotionTag], fromAi=$fromAi, roastId=$roastId")
            // Only emit TTS for initial display (not AI replacement of already-visible text)
            // AI returns after initial fallback is already displayed and TTS emitted
            if (!fromAi) {
                _aiRoastEvent.tryEmit(text)
            }
        } else {
            Log.d("HighlightVM", "气泡已消失，跳过文本更新: text=[$text]")
        }
    }

    // ===== Public API =====

    fun loadHighlights(videoId: String) {
        // 切集时立即清除旧状态，防止旧高光数据残留导致误触发
        triggeredSet.clear()
        lastHighlightTimestamp = 0L

        highlightCache[videoId]?.let { cached ->
            Log.d("HighlightVM", "内存缓存命中: $videoId, ${cached.size} 条, timestamps=${cached.map { it.timestamp }}")
            _state.update {
                it.copy(
                    points = cached,
                    highlightSegments = cached,
                    isLoading = false,
                    perHighlightStats = emptyMap(),
                    currentHighlightKey = "",
                    spriteState = SpriteState()
                )
            }
            return
        }

        loadJob?.cancel()
        loadJob = viewModelScope.launch {
            Log.d("HighlightVM", "loadHighlights 开始: videoId=$videoId")
            // 先清空旧高光段，防止加载期间旧数据残留
            _state.update { it.copy(isLoading = true, error = null, highlightSegments = emptyList()) }
            try {
                val highlights = repository.getHighlights(videoId)
                Log.d("HighlightVM", "Repository 返回: ${highlights.size} 条, timestamps=${highlights.map { it.timestamp }}, types=${highlights.map { it.type }}")
                val deduplicated = highlights.distinctBy { it.timestamp }
                    .filter { it.type == "L1" || it.type == "L2" }
                    .take(2)
                Log.d("HighlightVM", "去重过滤后: ${deduplicated.size} 条, timestamps=${deduplicated.map { it.timestamp }}")
                highlightCache[videoId] = deduplicated
                // 驱逐策略：最多缓存 20 集数据
                while (highlightCache.size > 20) {
                    val oldestKey = highlightCache.keys.first()
                    highlightCache.remove(oldestKey)
                    Log.d("HighlightVM", "内存缓存驱逐: $oldestKey")
                }
                _state.update {
                    it.copy(
                        points = deduplicated,
                        highlightSegments = deduplicated,
                        isLoading = false,
                        perHighlightStats = emptyMap(),
                        currentHighlightKey = "",
                        spriteState = SpriteState()
                    )
                }
                Log.d("HighlightVM", "状态已更新: highlightSegments=${_state.value.highlightSegments.size} 条")
            } catch (e: Exception) {
                Log.e("HighlightVM", "加载异常: videoId=$videoId", e)
                _state.update { it.copy(error = "高光点加载失败", isLoading = false) }
                _events.tryEmit(AppEvent.ShowSnackbar(
                    message = "高光数据加载失败，请检查网络",
                    actionLabel = "重试",
                    onAction = { loadHighlights(videoId) }
                ))
            }
        }
    }

    fun setDramaInfo(title: String, description: String) {
        currentDramaTitle = title
        currentDramaDescription = description
    }

    fun setCurrentEpisode(title: String, episodeId: Int) {
        currentEpisodeTitle = title
        currentEpisodeId = episodeId
        viewModelScope.launch {
            val weights = progressStore.getEmotionWeights(episodeId)
            if (weights.isNotEmpty()) {
                Log.d("HighlightVM", "加载表情权重: episodeId=$episodeId, ${weights.size} 个标签")
            }
        }
    }

    fun onHighlightReached(highlight: ServerHighlightPoint) {
        Log.d("HighlightVM", "onHighlightReached: ${highlight.timestamp}ms, type=${highlight.type}, serverComments=${highlight.comments.size}")

        val key = highlightKey(highlight)
        if (key in triggeredSet) {
            Log.d("HighlightVM", "跳过已触发: ${highlight.timestamp}ms")
            return
        }
        triggeredSet.add(key)
        _highlightTriggerEvent.tryEmit(highlight)

        val isRapidSuccession = (highlight.timestamp - lastHighlightTimestamp) < 3000L
        lastHighlightTimestamp = highlight.timestamp
        if (isRapidSuccession) {
            spriteDismissJob?.cancel()
        }

        // 延迟 1.5s 再显示精灵，让用户先看到高光剧情
        viewModelScope.launch {
            delay(1500L)

            // Check if this highlight should be downgraded to fallback
            if (shouldDowngradeToFallback(key)) {
                Log.d("HighlightVM", "高光点 $key 所有吐槽被踩 → 降级使用通用模板")
                val fallback = naturalFallback(highlight.roastText)
                _state.update {
                    val existing = it.perHighlightStats[key] ?: MascotStats()
                    it.copy(
                        triggeredHighlight = highlight,
                        showDebugDialog = false,
                        debugHighlight = null,
                        currentHighlightKey = key,
                        perHighlightStats = it.perHighlightStats + (key to existing.copy(showCount = existing.showCount + 1)),
                        spriteState = SpriteState(
                            isVisible = true,
                            roastText = fallback.text,
                            highlightTimestamp = highlight.timestamp,
                            expression = SpriteExpression.DEFAULT,
                            emotionTag = fallback.emotionTag
                        )
                    )
                }
                _aiRoastEvent.tryEmit(fallback.text)
                scheduleSpriteDismiss()
                return@launch
            }

            // Priority 1: Use server-provided pre-generated comments
            if (highlight.comments.isNotEmpty()) {
                val selected = selectBestComment(highlight.comments, key)
                val displayText = selected.text
                val displayTag = selected.emotionTag

                _state.update {
                    val existing = it.perHighlightStats[key] ?: MascotStats()
                    it.copy(
                        triggeredHighlight = highlight,
                        showDebugDialog = false,
                        debugHighlight = null,
                        currentHighlightKey = key,
                        perHighlightStats = it.perHighlightStats + (key to existing.copy(showCount = existing.showCount + 1)),
                        spriteState = SpriteState(
                            isVisible = true,
                            roastText = displayText,
                            highlightTimestamp = highlight.timestamp,
                            expression = SpriteExpression.DEFAULT,
                            currentRoastId = 0,
                            emotionTag = displayTag
                        )
                    )
                }
                lastEmotionTag = displayTag
                Log.d("HighlightVM", "精灵显示(服务端预生成): key=$key, text=[$displayText], tag=[$displayTag], 共${highlight.comments.size}条")
                _aiRoastEvent.tryEmit(displayText)
                scheduleSpriteDismiss()
                return@launch
            }

            // Priority 2: Check Room cache
            val cached = loadRoastsFromRoom(highlight)
            if (cached.isNotEmpty()) {
                Log.d("HighlightVM", "Room 缓存命中: key=${cacheKey(highlight)}, ${cached.size} 条")
                val emotionWeights = _state.value.currentStats.emotionWeights
                val selected = selectRoastFromCache(cached, emotionWeights)
                val displayText = selected?.roastText ?: naturalFallback(highlight.roastText).text
                val displayTag = selected?.emotionTag ?: "吐槽"
                val displayId = selected?.id ?: 0

                _state.update {
                    val existing = it.perHighlightStats[key] ?: MascotStats()
                    it.copy(
                        triggeredHighlight = highlight,
                        showDebugDialog = false,
                        debugHighlight = null,
                        currentHighlightKey = key,
                        perHighlightStats = it.perHighlightStats + (key to existing.copy(showCount = existing.showCount + 1)),
                        spriteState = SpriteState(
                            isVisible = true,
                            roastText = displayText,
                            highlightTimestamp = highlight.timestamp,
                            expression = SpriteExpression.DEFAULT,
                            currentRoastId = displayId,
                            emotionTag = displayTag
                        )
                    )
                }
                Log.d("HighlightVM", "精灵显示(Room缓存): key=$key, text=[$displayText], tag=[$displayTag]")
                _aiRoastEvent.tryEmit(displayText)
                scheduleSpriteDismiss()
                return@launch
            }

            // No cache - display fallback, then fetch AI
            Log.d("HighlightVM", "Room 缓存未命中: key=${cacheKey(highlight)}，使用 fallback + 请求 AI")
            val fallback = naturalFallback(highlight.roastText)
            _state.update {
                val existing = it.perHighlightStats[key] ?: MascotStats()
                it.copy(
                    triggeredHighlight = highlight,
                    showDebugDialog = false,
                    debugHighlight = null,
                    currentHighlightKey = key,
                    perHighlightStats = it.perHighlightStats + (key to existing.copy(showCount = existing.showCount + 1)),
                    spriteState = SpriteState(
                        isVisible = true,
                        roastText = fallback.text,
                        highlightTimestamp = highlight.timestamp,
                        expression = SpriteExpression.DEFAULT,
                        emotionTag = fallback.emotionTag
                    )
                )
            }
            Log.d("HighlightVM", "精灵显示(fallback): key=$key, text=[${fallback.text}], tag=[${fallback.emotionTag}]")
            _aiRoastEvent.tryEmit(fallback.text)

            // Async AI fetch (L4 type won't fetch)
            if (AiRoastService.getRoastCountForType(highlight.type) > 0) {
                fetchAiRoast(highlight, "auto")
            }

            scheduleSpriteDismiss()
        }
    }

    fun debugTriggerSprite() {
        val fakeHighlight = ServerHighlightPoint(
            timestampSeconds = 999f,
            type = "L2",
            confidence = 0.8f,
            roastText = "这是一条测试吐槽"
        )
        onHighlightReached(fakeHighlight)
    }

    fun onManualJump(highlight: ServerHighlightPoint) {
        Log.d("HighlightVM", "手动跳转: ${highlight.timestamp}ms, type=${highlight.type}, serverComments=${highlight.comments.size}")
        val key = highlightKey(highlight)
        triggeredSet.remove(key)
        triggeredSet.add(key)
        _highlightTriggerEvent.tryEmit(highlight)

        spriteDismissJob?.cancel()

        // Priority 1: Server-provided comments — 直接使用第一条
        if (highlight.comments.isNotEmpty()) {
            val firstComment = highlight.comments.first()
            val displayText = firstComment.text
            val displayTag = firstComment.emotionTag

            _state.update {
                val existing = it.perHighlightStats[key] ?: MascotStats()
                it.copy(
                    currentHighlightKey = key,
                    perHighlightStats = it.perHighlightStats + (key to existing.copy(showCount = existing.showCount + 1)),
                    spriteState = SpriteState(
                        isVisible = true,
                        roastText = displayText,
                        highlightTimestamp = highlight.timestamp,
                        expression = SpriteExpression.DEFAULT,
                        currentRoastId = 0,
                        emotionTag = displayTag
                    )
                )
            }
            Log.d("HighlightVM", "精灵显示(手动-服务端): key=$key, text=[$displayText], tag=[$displayTag]")
            _aiRoastEvent.tryEmit(displayText)
            scheduleSpriteDismiss()
            return
        }

        // Priority 2: Room cache + AI fallback
        viewModelScope.launch {
            val cached = loadRoastsFromRoom(highlight)
            val emotionWeights = _state.value.currentStats.emotionWeights

            val (displayText, displayTag, displayId) = if (cached.isNotEmpty()) {
                val selected = selectRoastFromCache(cached, emotionWeights)
                Triple(selected?.roastText ?: naturalFallback(highlight.roastText).text,
                       selected?.emotionTag ?: "吐槽",
                       selected?.id ?: 0)
            } else {
                val fb = naturalFallback(highlight.roastText)
                Triple(fb.text, fb.emotionTag, 0)
            }

            _state.update {
                val existing = it.perHighlightStats[key] ?: MascotStats()
                it.copy(
                    currentHighlightKey = key,
                    perHighlightStats = it.perHighlightStats + (key to existing.copy(showCount = existing.showCount + 1)),
                    spriteState = SpriteState(
                        isVisible = true,
                        roastText = displayText,
                        highlightTimestamp = highlight.timestamp,
                        expression = SpriteExpression.DEFAULT,
                        currentRoastId = displayId,
                        emotionTag = displayTag
                    )
                )
            }
            Log.d("HighlightVM", "精灵显示(手动): key=$key, text=[$displayText], tag=[$displayTag]")
            _aiRoastEvent.tryEmit(displayText)

            if (cached.isEmpty() && AiRoastService.getRoastCountForType(highlight.type) > 0) {
                fetchAiRoast(highlight, "manual")
            }

            scheduleSpriteDismiss()
        }
    }

    private fun scheduleSpriteDismiss() {
        spriteDismissJob?.cancel()
        spriteDismissJob = viewModelScope.launch {
            delay(5000)
            _state.update {
                it.copy(spriteState = it.spriteState.copy(
                    isVisible = false,
                    expression = SpriteExpression.DEFAULT
                ))
            }
            _stopTtsEvent.tryEmit(Unit)
            Log.d("HighlightVM", "高光 5s 到期：精灵隐藏 + TTS 停止")
        }
    }

    fun dismissSprite() {
        spriteDismissJob?.cancel()
        _state.update { it.copy(spriteState = it.spriteState.copy(isVisible = false)) }
    }

    fun toggleMascotAlwaysVisible() {
        val newValue = !_state.value.isMascotAlwaysVisible
        _state.update { it.copy(isMascotAlwaysVisible = newValue) }
        viewModelScope.launch {
            progressStore.saveMascotAlwaysVisible(newValue)
        }
    }

    fun setAudioMode(mode: AudioMode) {
        _state.update { it.copy(audioMode = mode) }
        viewModelScope.launch {
            progressStore.saveAudioMode(mode)
        }
    }

    fun cycleAudioMode() {
        val next = when (_state.value.audioMode) {
            AudioMode.ON -> AudioMode.NOTIFICATION
            AudioMode.NOTIFICATION -> AudioMode.OFF
            AudioMode.OFF -> AudioMode.ON
        }
        setAudioMode(next)
    }

    fun onSpriteDoubleTap() {
        val key = _state.value.currentHighlightKey
        val currentTag = _state.value.spriteState.emotionTag

        // 常驻模式 + 无高光激活时，展示灵感表情
        if (key.isEmpty()) {
            if (_state.value.isMascotAlwaysVisible) {
                showInspirationExpression()
            }
            return
        }

        expressionDismissJob?.cancel()
        _state.update {
            val existing = it.perHighlightStats[key] ?: MascotStats()
            val newWeights = if (currentTag.isNotEmpty()) {
                existing.emotionWeights.toMutableMap().apply {
                    put(currentTag, (get(currentTag) ?: 0) + 1)
                }
            } else {
                existing.emotionWeights
            }
            it.copy(
                perHighlightStats = it.perHighlightStats + (key to existing.copy(
                    agreeCount = existing.agreeCount + 1,
                    emotionWeights = newWeights
                )),
                spriteState = it.spriteState.copy(expression = SpriteExpression.HAPPY, isAnimating = true)
            )
        }
        Log.d("HighlightVM", "点赞: key=$key, agreeCount=${_state.value.currentStats.agreeCount}, tag=[$currentTag], weights=${_state.value.currentStats.emotionWeights}")

        // Persist weights
        viewModelScope.launch {
            val weights = _state.value.currentStats.emotionWeights
            if (weights.isNotEmpty()) {
                progressStore.saveEmotionWeights(currentEpisodeId, weights)
            }
        }

        scheduleExpressionReset("点赞")
    }

    fun onSpriteLongPress() {
        val key = _state.value.currentHighlightKey
        val currentTag = _state.value.spriteState.emotionTag

        // 常驻模式 + 无高光激活时，展示灵感表情
        if (key.isEmpty()) {
            if (_state.value.isMascotAlwaysVisible) {
                showInspirationExpression()
            }
            return
        }

        expressionDismissJob?.cancel()
        _state.update {
            val existing = it.perHighlightStats[key] ?: MascotStats()
            val newWeights = if (currentTag.isNotEmpty()) {
                existing.emotionWeights.toMutableMap().apply {
                    put(currentTag, (get(currentTag) ?: 0) - 1)
                }
            } else {
                existing.emotionWeights
            }
            it.copy(
                perHighlightStats = it.perHighlightStats + (key to existing.copy(
                    dislikeCount = existing.dislikeCount + 1,
                    emotionWeights = newWeights
                )),
                spriteState = it.spriteState.copy(expression = SpriteExpression.SAD, isAnimating = true)
            )
        }
        Log.d("HighlightVM", "踩: key=$key, dislikeCount=${_state.value.currentStats.dislikeCount}, tag=[$currentTag], weights=${_state.value.currentStats.emotionWeights}")

        // Persist weights
        viewModelScope.launch {
            val weights = _state.value.currentStats.emotionWeights
            if (weights.isNotEmpty()) {
                progressStore.saveEmotionWeights(currentEpisodeId, weights)
            }
        }

        scheduleExpressionReset("踩")
    }

    private fun showInspirationExpression() {
        expressionDismissJob?.cancel()
        _state.update {
            it.copy(spriteState = it.spriteState.copy(expression = SpriteExpression.INSPIRATION, isAnimating = true))
        }
        Log.d("HighlightVM", "常驻模式: 展示灵感表情")
        scheduleExpressionReset("灵感")
    }

    private fun scheduleExpressionReset(actionName: String) {
        expressionDismissJob = viewModelScope.launch {
            delay(5000)
            _state.update {
                it.copy(spriteState = it.spriteState.copy(expression = SpriteExpression.DEFAULT, isAnimating = false))
            }
            Log.d("HighlightVM", "${actionName}表情自动恢复")
        }
    }

    fun dismissSnackbar() {
        _state.update { it.copy(triggeredHighlight = null) }
    }

    fun dismissDebugDialog() {
        _state.update { it.copy(showDebugDialog = false, debugHighlight = null) }
    }

    override fun onCleared() {
        super.onCleared()
        spriteDismissJob?.cancel()
        expressionDismissJob?.cancel()
        highlightCache.clear()
        repository.clearAiCache()
        _state.update { HighlightState() }
    }

    class Factory(private val application: Application) : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T {
            if (modelClass.isAssignableFrom(HighlightViewModel::class.java)) {
                return HighlightViewModel(
                    HighlightRepository(application),
                    PlaybackProgressStore(application)
                ) as T
            }
            throw IllegalArgumentException("Unknown ViewModel class")
        }
    }
}