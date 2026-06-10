package com.shortdrama.interaction.ui.screen

import android.net.Uri
import android.util.Log
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInHorizontally
import androidx.compose.animation.slideOutHorizontally
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectHorizontalDragGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.pager.VerticalPager
import androidx.compose.foundation.pager.rememberPagerState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.Face
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SnackbarDuration
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.SnackbarResult
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.DefaultLoadControl
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.analytics.AnalyticsListener
import androidx.media3.exoplayer.source.LoadEventInfo
import androidx.media3.exoplayer.source.MediaLoadData
import androidx.media3.exoplayer.trackselection.DefaultTrackSelector
import com.shortdrama.interaction.data.model.AppEvent
import com.shortdrama.interaction.data.model.Episode
import com.shortdrama.interaction.ui.component.HighlightDebugDialog
import com.shortdrama.interaction.ui.component.HighlightMarkers
import com.shortdrama.interaction.ui.component.HighlightSnackbar
import com.shortdrama.interaction.ui.component.SpriteFeedbackBar
import com.shortdrama.interaction.ui.component.SpriteOverlay
import com.shortdrama.interaction.ui.component.VideoPlayer
import com.shortdrama.interaction.ui.component.BranchOverlay
import com.shortdrama.interaction.ui.component.SubmitBranchDialogContent
import com.shortdrama.interaction.viewmodel.HighlightViewModel
import com.shortdrama.interaction.viewmodel.PlayerViewModel
import java.io.File
import java.io.IOException
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

@OptIn(ExperimentalMaterial3Api::class, androidx.compose.foundation.ExperimentalFoundationApi::class)
@Composable
fun PlayerScreen(
    viewModel: PlayerViewModel,
    onBack: () -> Unit
) {
    val context = LocalContext.current
    val lifecycleOwner = LocalLifecycleOwner.current
    val highlightViewModel: HighlightViewModel = androidx.lifecycle.viewmodel.compose.viewModel(
        factory = HighlightViewModel.Factory(context.applicationContext as android.app.Application)
    )
    val drama = viewModel.drama
    val episodes = drama?.episodes ?: emptyList()
    val currentEpisode by viewModel.currentEpisode.collectAsState()
    val highlightState by highlightViewModel.state.collectAsState()
    var showEpisodePanel by remember { mutableStateOf(false) }
    val snackbarHostState = remember { SnackbarHostState() }

    // 分支相关状态
    val showBranchOverlay by viewModel.showBranchOverlay.collectAsState()
    val branches by viewModel.branches.collectAsState()
    val branchesLoading by viewModel.branchesLoading.collectAsState()
    val isBranchGenerating by viewModel.isGenerating.collectAsState()
    val showSubmitDialog by viewModel.showSubmitDialog.collectAsState()
    val submitResult by viewModel.submitResult.collectAsState()
    val isSubmitting by viewModel.isSubmitting.collectAsState()

    // 显示提交结果 Toast
    LaunchedEffect(submitResult) {
        submitResult?.let {
            android.widget.Toast.makeText(context, it, android.widget.Toast.LENGTH_SHORT).show()
            viewModel.clearSubmitResult()
        }
    }

    // Single shared ExoPlayer instance — prevents per-page player creation that causes black screen
    val exoPlayer = remember {
        val trackSelector = DefaultTrackSelector(context).apply {
            setParameters(
                buildUponParameters()
                    .setForceHighestSupportedBitrate(false)
                    .setMaxVideoSize(Int.MAX_VALUE, Int.MAX_VALUE)
            )
        }
        val loadControl = DefaultLoadControl.Builder()
            .setBufferDurationsMs(1500, 5000, 500, 1000)
            .build()

        ExoPlayer.Builder(context)
            .setTrackSelector(trackSelector)
            .setLoadControl(loadControl)
            .build().apply {
                repeatMode = Player.REPEAT_MODE_OFF
                addListener(object : Player.Listener {
                    override fun onPlayerError(error: PlaybackException) {
                        if (error.errorCode == PlaybackException.ERROR_CODE_DECODER_INIT_FAILED ||
                            error.errorCode == PlaybackException.ERROR_CODE_AUDIO_TRACK_INIT_FAILED) {
                            Log.w("Player", "解码失败重试: pos=${currentPosition}ms")
                            this@apply.apply {
                                val savedPos = currentPosition
                                val currentMediaItem = currentMediaItem
                                stop()
                                if (currentMediaItem != null) {
                                    setMediaItem(currentMediaItem)
                                }
                                prepare()
                                if (savedPos > 0) seekTo(savedPos)
                                playWhenReady = true
                                volume = 1f
                            }
                        }
                    }

                    override fun onPlaybackStateChanged(playbackState: Int) {
                        when (playbackState) {
                            Player.STATE_READY -> {
                                if (volume < 1f) {
                                    volume = 1f
                                }
                            }
                            else -> {}
                        }
                    }
                })

                addAudioAnalyticsListener(this@apply)
            }
    }

    // 分支弹窗出现时暂停视频
    LaunchedEffect(showBranchOverlay, showSubmitDialog) {
        if (showBranchOverlay || showSubmitDialog) {
            exoPlayer.playWhenReady = false
        } else {
            exoPlayer.playWhenReady = true
            exoPlayer.play()
        }
    }

    // Initialize TTS (async, non-blocking)
    DisposableEffect(Unit) {
        viewModel.initTts(context)
        onDispose { }
    }

    // Collect highlight trigger events
    LaunchedEffect(Unit) {
        highlightViewModel.highlightTriggerEvent.collect {
            viewModel.onHighlightTriggered(it)
        }
    }

    // Collect AI roast text for TTS override (mode-aware)
    LaunchedEffect(Unit) {
        highlightViewModel.aiRoastEvent.collect { aiText ->
            val mode = highlightState.audioMode
            viewModel.speakRoast(aiText, mode)
        }
    }

    // Collect TTS stop event (5s after highlight)
    LaunchedEffect(Unit) {
        highlightViewModel.stopTtsEvent.collect {
            viewModel.cancelTts()
        }
    }

    // Collect error events for Snackbar
    LaunchedEffect(Unit) {
        highlightViewModel.events.collect { event ->
            when (event) {
                is AppEvent.ShowSnackbar -> {
                    val result = snackbarHostState.showSnackbar(
                        message = event.message,
                        actionLabel = event.actionLabel,
                        duration = SnackbarDuration.Long
                    )
                    if (result == SnackbarResult.ActionPerformed) {
                        event.onAction?.invoke()
                    }
                }
                is AppEvent.ShowToast -> { /* PlayerScreen 不使用 Toast */ }
            }
        }
    }

    // Load highlights when episode changes — 延迟 200ms 让视频优先准备
    LaunchedEffect(currentEpisode?.id) {
        currentEpisode?.let { episode ->
            delay(200)
            Log.d("PlayerScreen", "加载高光: episode=${episode.title}, id=${episode.id}")
            drama?.let { highlightViewModel.setDramaInfo(it.title, it.description) }
            highlightViewModel.setCurrentEpisode(episode.title, episode.id)
            highlightViewModel.clearCacheAndReload(episode.title)

            // 重试机制：3s 后检查是否加载成功，未成功则重试一次
            delay(3000)
            val segments = highlightViewModel.state.value.highlightSegments
            if (segments.isEmpty()) {
                Log.w("PlayerScreen", "高光加载 3s 后仍为空，重试: episode=${episode.title}")
                highlightViewModel.clearCacheAndReload(episode.title)
            }
        }
    }

    if (drama == null || episodes.isEmpty()) {
        Box(modifier = Modifier.fillMaxSize().background(Color.Black))
        return
    }

    // Find initial page index
    val initialPage = episodes.indexOfFirst { it.id == currentEpisode?.id }.coerceAtLeast(0)
    val pagerState = rememberPagerState(initialPage = initialPage) { episodes.size }
    val coroutineScope = rememberCoroutineScope()

    // 自动下一集：监听 STATE_ENDED，播完先弹分支选择
    DisposableEffect(exoPlayer) {
        val listener = object : Player.Listener {
            override fun onPlaybackStateChanged(playbackState: Int) {
                if (playbackState == Player.STATE_ENDED) {
                    // 先触发分支选择
                    viewModel.onEpisodeCompleted()
                    // 不自动跳下一集，等用户关闭分支弹窗后由用户手动操作
                }
            }
        }
        exoPlayer.addListener(listener)
        onDispose {
            exoPlayer.removeListener(listener)
        }
    }

    // Sync pager → ViewModel (swipe)
    LaunchedEffect(pagerState) {
        var lastSyncedPage = initialPage
        snapshotFlow { pagerState.settledPage }.collect { page ->
            if (page != lastSyncedPage) {
                lastSyncedPage = page
                viewModel.selectEpisode(episodes[page])
            }
        }
    }

    // Shared ExoPlayer: 切集时仅切换 MediaItem，不重建播放器
    LaunchedEffect(pagerState) {
        var lastLoadedPage = initialPage
        snapshotFlow { pagerState.settledPage }.collect { page ->
            // 首次 composition 时跳过（初始加载 effect 已处理）
            if (page == lastLoadedPage) return@collect
            lastLoadedPage = page

            val episode = episodes[page]
            // 保存当前集位置（如果正在播放）
            val prevEpisode = viewModel.currentEpisode.value
            if (prevEpisode != null && prevEpisode.id != episode.id) {
                val savePos = exoPlayer.currentPosition
                viewModel.savePosition(prevEpisode.id, savePos)
            }
            // 清除高光触发记录
            highlightViewModel.clearTriggeredHighlights()
            // 切换媒体源
            val videoPath = episode.videoPath
            val uri = if (videoPath.startsWith("/") || videoPath.contains(":\\")) {
                Uri.fromFile(File(videoPath))
            } else {
                Uri.parse(videoPath.replace(" ", "%20"))
            }
            exoPlayer.setMediaItem(MediaItem.fromUri(uri))
            exoPlayer.prepare()
            // 等待 DataStore 异步加载完成，最多等 3s
            var waitMs = 0
            while (!viewModel.isPositionReady(episode.id) && waitMs < 3000) {
                delay(50)
                waitMs += 50
            }
            val restorePos = viewModel.getPosition(episode.id)
            if (restorePos > 0) {
                exoPlayer.seekTo(restorePos)
            }
            exoPlayer.volume = 1f
            exoPlayer.playWhenReady = true
            exoPlayer.play()
        }
    }

    // Initial media load — wait for position-ready to avoid seekTo(0)
    var initialLoadDone by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) {
        if (initialLoadDone) return@LaunchedEffect
        initialLoadDone = true
        val episode = episodes[initialPage]
        val videoPath = episode.videoPath
        val uri = if (videoPath.startsWith("/") || videoPath.contains(":\\")) {
            Uri.fromFile(File(videoPath))
        } else {
            Uri.parse(videoPath.replace(" ", "%20"))
        }
        exoPlayer.setMediaItem(MediaItem.fromUri(uri))
        exoPlayer.prepare()
        // 等待 DataStore 异步加载完成，最多等 3s
        var waitMs = 0
        while (!viewModel.isPositionReady(episode.id) && waitMs < 3000) {
            delay(50)
            waitMs += 50
        }
        val restorePos = viewModel.getPosition(episode.id)
        if (restorePos > 0) {
            exoPlayer.seekTo(restorePos)
        }
        exoPlayer.volume = 1f
        exoPlayer.playWhenReady = true
        exoPlayer.play()
    }

    // Audio focus management — single instance at PlayerScreen level
    val audioManager = remember {
        context.getSystemService(android.content.Context.AUDIO_SERVICE) as android.media.AudioManager
    }
    val audioFocusRequestRef = remember<java.util.concurrent.atomic.AtomicReference<android.media.AudioFocusRequest>> {
        java.util.concurrent.atomic.AtomicReference(null)
    }
    val audioFocusRequest = remember {
        val mainHandler = android.os.Handler(android.os.Looper.getMainLooper())
        android.media.AudioFocusRequest.Builder(android.media.AudioManager.AUDIOFOCUS_GAIN)
            .setAudioAttributes(
                android.media.AudioAttributes.Builder()
                    .setUsage(android.media.AudioAttributes.USAGE_MEDIA)
                    .setContentType(android.media.AudioAttributes.CONTENT_TYPE_MOVIE)
                    .build()
            )
            .setOnAudioFocusChangeListener { focusChange ->
                mainHandler.post {
                    when (focusChange) {
                        android.media.AudioManager.AUDIOFOCUS_LOSS -> {
                            exoPlayer.volume = 0f
                            exoPlayer.playWhenReady = false
                            Log.w("AudioDiag", "焦点永久丢失，暂停播放，尝试重新获取")
                            audioFocusRequestRef.get()?.let { audioManager.requestAudioFocus(it) }
                        }
                        android.media.AudioManager.AUDIOFOCUS_LOSS_TRANSIENT -> {
                            exoPlayer.volume = 0.15f
                            Log.d("AudioDiag", "焦点临时丢失，音量→0.15")
                        }
                        android.media.AudioManager.AUDIOFOCUS_GAIN -> {
                            exoPlayer.volume = 1f
                            exoPlayer.playWhenReady = true
                            exoPlayer.play()
                            Log.d("AudioDiag", "焦点恢复，音量→1.0，恢复播放")
                        }
                        android.media.AudioManager.AUDIOFOCUS_LOSS_TRANSIENT_CAN_DUCK -> {
                            exoPlayer.volume = 0.15f
                            Log.d("AudioDiag", "音频避让，音量→0.15")
                        }
                    }
                }
            }
            .build()
            .also { audioFocusRequestRef.set(it) }
    }

    // Lifecycle observer: manage ExoPlayer + audio focus
    DisposableEffect(lifecycleOwner) {
        val observer = LifecycleEventObserver { _, event ->
            when (event) {
                Lifecycle.Event.ON_PAUSE -> {
                    exoPlayer.playWhenReady = false
                }
                Lifecycle.Event.ON_RESUME -> {
                    audioManager.requestAudioFocus(audioFocusRequest)
                    exoPlayer.playWhenReady = true
                    exoPlayer.volume = 1f
                    exoPlayer.play()
                }
                else -> {}
            }
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        audioManager.requestAudioFocus(audioFocusRequest)
        onDispose {
            lifecycleOwner.lifecycle.removeObserver(observer)
            audioManager.abandonAudioFocusRequest(audioFocusRequest)
            exoPlayer.release()
        }
    }

    Box(modifier = Modifier.fillMaxSize().background(Color.Black)) {
        // Vertical Pager for smooth swiping
        VerticalPager(
            state = pagerState,
            modifier = Modifier.fillMaxSize(),
            beyondBoundsPageCount = 1  // Pre-load adjacent pages
        ) { page ->
            val episode = episodes[page]
            val isActive = pagerState.settledPage == page
            val points by remember { derivedStateOf { highlightState.points } }
            val segments by remember { derivedStateOf { highlightState.highlightSegments } }

            val onPositionChanged = remember(episode.id) { { position: Long -> viewModel.savePosition(episode.id, position) } }
            val onHighlightReached = remember { { highlight: com.shortdrama.interaction.data.network.ServerHighlightPoint -> highlightViewModel.onHighlightReached(highlight) } }
            val onManualJump = remember { { highlight: com.shortdrama.interaction.data.network.ServerHighlightPoint -> highlightViewModel.onManualJump(highlight) } }
            val onToggleMascot = remember { { highlightViewModel.toggleMascotAlwaysVisible() } }
            val onOpenEpisodes = remember { { showEpisodePanel = true } }
            val onSeekRequest by rememberUpdatedState<(Long) -> Unit> { position: Long ->
                audioManager.requestAudioFocus(audioFocusRequest)
                exoPlayer.seekTo(position)
                exoPlayer.playWhenReady = true
                exoPlayer.volume = 1f
            }

            EpisodePage(
                episode = episode,
                exoPlayer = exoPlayer,
                dramaTitle = drama.title,
                episodeIndex = page,
                totalEpisodes = episodes.size,
                isActive = isActive,
                highlightPoints = points,
                highlightSegments = segments,
                spriteShowCount = highlightState.spriteShowCount,
                agreeCount = highlightState.agreeCount,
                spriteIsVisible = highlightState.spriteState.isVisible,
                isMascotAlwaysVisible = highlightState.isMascotAlwaysVisible,
                onToggleMascotAlwaysVisible = onToggleMascot,
                onPositionChanged = onPositionChanged,
                onHighlightReached = onHighlightReached,
                onManualJump = onManualJump,
                onSeekRequest = onSeekRequest,
                onRecoverAudioFocus = { audioManager.requestAudioFocus(audioFocusRequest) },
                onBack = onBack,
                onOpenEpisodes = onOpenEpisodes
            )
        }

        // Scrim overlay when panel is open
        if (showEpisodePanel) {
            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .background(Color.Black.copy(alpha = 0.5f))
                    .clickable { showEpisodePanel = false }
            )
        }

        // Highlight snackbar overlay
        HighlightSnackbar(
            highlight = highlightState.triggeredHighlight,
            modifier = Modifier.align(Alignment.BottomCenter),
            onDismiss = { highlightViewModel.dismissSnackbar() }
        )

        // Sprite overlay
        SpriteOverlay(
            spriteState = highlightState.spriteState,
            isAlwaysVisible = highlightState.isMascotAlwaysVisible,
            audioMode = highlightState.audioMode,
            onDismiss = { highlightViewModel.dismissSprite() },
            onSpriteDoubleTap = {
                highlightViewModel.onSpriteDoubleTap()
            },
            onSpriteLongPress = {
                highlightViewModel.onSpriteLongPress()
            },
            onCycleAudioMode = { highlightViewModel.cycleAudioMode() }
        )

        // Debug dialog (DEBUG builds only)
        if (highlightState.showDebugDialog) {
            HighlightDebugDialog(
                highlight = highlightState.debugHighlight,
                onDismiss = { highlightViewModel.dismissDebugDialog() }
            )
        }

        // Episode panel - aligned to right
        Box(modifier = Modifier.fillMaxSize()) {
            AnimatedVisibility(
                visible = showEpisodePanel,
                enter = slideInHorizontally(animationSpec = tween(200), initialOffsetX = { it }),
                exit = slideOutHorizontally(animationSpec = tween(150), targetOffsetX = { it }),
                modifier = Modifier.align(Alignment.CenterEnd)
            ) {
                Box(
                    modifier = Modifier
                        .fillMaxHeight()
                        .width(300.dp)
                        .background(
                            Color(0xFF1A1A1A),
                            RoundedCornerShape(topStart = 16.dp, bottomStart = 16.dp)
                        )
                ) {
                    EpisodePanel(
                        episodes = episodes,
                        currentEpisodeId = currentEpisode?.id ?: 0,
                        onEpisodeClick = { episode ->
                            viewModel.selectEpisode(episode)
                            showEpisodePanel = false
                            val targetIndex = episodes.indexOfFirst { it.id == episode.id }
                            if (targetIndex >= 0) {
                                coroutineScope.launch {
                                    pagerState.scrollToPage(targetIndex)
                                }
                            }
                        }
                    )
                }
            }
        }

        // Snackbar for error events
        SnackbarHost(
            hostState = snackbarHostState,
            modifier = Modifier
                .align(Alignment.BottomCenter)
                .padding(bottom = 32.dp)
        )
    }

    // === 分支选择底部弹窗 ===
    if (showBranchOverlay) {
        BranchOverlay(
            branches = branches,
            isLoading = branchesLoading,
            isGenerating = isBranchGenerating,
            baseUrl = "http://10.0.2.2:8081",
            onVote = { branchId -> viewModel.voteBranch(branchId) },
            onSubmitBranch = { viewModel.showSubmitDialog() },
            onDismiss = { viewModel.dismissBranchOverlay() }
        )
    }

    // === 用户提交分支对话框 ===
    if (showSubmitDialog) {
        androidx.compose.ui.window.Dialog(onDismissRequest = { viewModel.dismissSubmitDialog() }) {
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .clip(RoundedCornerShape(20.dp))
                    .background(Color(0xFF1E1E2E))
            ) {
                SubmitBranchDialogContent(
                    isSubmitting = isSubmitting,
                    rejectionReason = submitResult,
                    onSubmit = { title, description, tone, preview ->
                        viewModel.submitBranch(title, description, tone, preview)
                    }
                )
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun EpisodePage(
    episode: Episode,
    exoPlayer: ExoPlayer,
    dramaTitle: String,
    episodeIndex: Int,
    totalEpisodes: Int,
    isActive: Boolean,
    highlightPoints: List<com.shortdrama.interaction.data.network.ServerHighlightPoint>,
    highlightSegments: List<com.shortdrama.interaction.data.network.ServerHighlightPoint>,
    spriteShowCount: Int,
    agreeCount: Int,
    spriteIsVisible: Boolean,
    isMascotAlwaysVisible: Boolean,
    onToggleMascotAlwaysVisible: () -> Unit,
    onPositionChanged: (Long) -> Unit,
    onHighlightReached: (com.shortdrama.interaction.data.network.ServerHighlightPoint) -> Unit,
    onManualJump: (com.shortdrama.interaction.data.network.ServerHighlightPoint) -> Unit,
    onSeekRequest: (Long) -> Unit,
    onRecoverAudioFocus: () -> Unit = {},
    onBack: () -> Unit,
    onOpenEpisodes: () -> Unit
) {
    var currentPositionMs by remember { mutableLongStateOf(0L) }
    var videoDurationMs by remember { mutableLongStateOf(60000L) }
    var isPageReady by remember { mutableStateOf(false) }

    LaunchedEffect(isActive) {
        if (isActive) {
            delay(100)
            isPageReady = true
        } else {
            isPageReady = false
        }
    }

    Box(
        modifier = Modifier
            .fillMaxSize()
            .pointerInput(Unit) {
                detectHorizontalDragGestures { _, dragAmount ->
                    if (dragAmount < -80) {
                        onOpenEpisodes()
                    }
                }
            }
    ) {
        // Video player - full screen, shares ExoPlayer instance
        VideoPlayer(
            exoPlayer = exoPlayer,
            videoPath = episode.videoPath,
            modifier = Modifier.fillMaxSize(),
            isActive = isActive,
            startPosition = 0L,
            highlightPoints = highlightPoints,
            highlightSegments = highlightSegments,
            onPositionChanged = { currentPositionMs = it; onPositionChanged(it) },
            onDurationReady = { videoDurationMs = it },
            onHighlightReached = onHighlightReached,
            onSeekRequest = onSeekRequest,
            onRecoverAudioFocus = onRecoverAudioFocus
        )

        // Top bar overlay
        TopAppBar(
            title = {
                Text(
                    text = dramaTitle,
                    fontWeight = FontWeight.Bold,
                    color = Color.White
                )
            },
            navigationIcon = {
                IconButton(onClick = onBack) {
                    Icon(Icons.Default.ArrowBack, "返回", tint = Color.White)
                }
            },
            actions = {
                IconButton(onClick = onToggleMascotAlwaysVisible) {
                    Icon(
                        imageVector = Icons.Default.Face,
                        contentDescription = if (isMascotAlwaysVisible) "关闭常驻精灵" else "开启常驻精灵",
                        tint = if (isMascotAlwaysVisible) Color(0xFF4CAF50) else Color.White.copy(alpha = 0.5f)
                    )
                }
            },
            colors = TopAppBarDefaults.topAppBarColors(
                containerColor = Color.Transparent
            )
        )

        // Feedback bar: visible only when sprite is in highlight-triggered state
        AnimatedVisibility(
            visible = spriteIsVisible,
            enter = fadeIn(tween(300)),
            exit = fadeOut(tween(300)),
            modifier = Modifier
                .align(Alignment.TopCenter)
                .padding(top = 64.dp)
        ) {
            SpriteFeedbackBar(
                showCount = spriteShowCount,
                agreeCount = agreeCount,
                isAlwaysVisible = isMascotAlwaysVisible,
                onLongPressToggle = onToggleMascotAlwaysVisible
            )
        }

        // "下一高光" 测试按钮
        if (isPageReady) Box(
            modifier = Modifier
                .align(Alignment.TopEnd)
                .padding(top = 72.dp, end = 16.dp)
                .background(
                    Color(0xFFFF6B6B).copy(alpha = 0.8f),
                    RoundedCornerShape(20.dp)
                )
                .clickable {
                    val currentPos = currentPositionMs
                    val sorted = highlightSegments.sortedBy { it.timestamp }
                    val nextHighlight = sorted.firstOrNull { it.timestamp > currentPos + 500 }

                    val target = if (nextHighlight != null) {
                        nextHighlight
                    } else if (sorted.isNotEmpty()) {
                        sorted.first()
                    } else {
                        return@clickable
                    }

                    onSeekRequest(target.timestamp)
                    onManualJump(target)
                    Log.d("Sprite", "跳转到高光点: ${target.timestamp}ms, type=${target.type}")
                }
                .padding(horizontal = 12.dp, vertical = 6.dp)
        ) {
            Text(
                text = "⏭ 高光",
                color = Color.White,
                fontSize = 12.sp,
                fontWeight = FontWeight.Bold
            )
        }

        // Bottom info overlay
        Column(
            modifier = Modifier
                .align(Alignment.BottomStart)
                .padding(16.dp)
        ) {
            Text(
                text = episode.title,
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.Bold,
                color = Color.White
            )
            Text(
                text = "${episodeIndex + 1} / $totalEpisodes",
                style = MaterialTheme.typography.bodySmall,
                color = Color.White.copy(alpha = 0.6f)
            )
        }

        // Right side episode indicator
        Column(
            modifier = Modifier
                .align(Alignment.CenterEnd)
                .padding(end = 12.dp),
            verticalArrangement = Arrangement.Center
        ) {
            Text(
                text = "← 选集",
                style = MaterialTheme.typography.labelSmall,
                color = Color.White.copy(alpha = 0.4f)
            )
        }

        // Server highlight markers above progress area
        if (isPageReady && highlightPoints.isNotEmpty()) {
            Column(
                modifier = Modifier
                    .align(Alignment.BottomCenter)
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp, vertical = 80.dp)
            ) {
                HighlightMarkers(
                    highlights = highlightPoints,
                    durationMs = videoDurationMs,
                    modifier = Modifier.fillMaxWidth()
                )
            }
        }
    }
}

@Composable
private fun EpisodePanel(
    episodes: List<Episode>,
    currentEpisodeId: Int,
    onEpisodeClick: (Episode) -> Unit
) {
    val listState = androidx.compose.foundation.lazy.rememberLazyListState()
    val currentIndex = episodes.indexOfFirst { it.id == currentEpisodeId }.coerceAtLeast(0)

    LaunchedEffect(Unit) {
        listState.scrollToItem(currentIndex)
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp)
    ) {
        Text(
            text = "选集 (${episodes.size})",
            style = MaterialTheme.typography.titleMedium,
            fontWeight = FontWeight.Bold,
            color = Color.White,
            modifier = Modifier.padding(bottom = 16.dp)
        )

        LazyColumn(
            state = listState,
            verticalArrangement = Arrangement.spacedBy(6.dp),
            contentPadding = PaddingValues(bottom = 16.dp)
        ) {
            items(episodes, key = { it.id }) { episode ->
                val isSelected = episode.id == currentEpisodeId
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(8.dp))
                        .background(
                            if (isSelected) MaterialTheme.colorScheme.primary
                            else Color.White.copy(alpha = 0.08f)
                        )
                        .clickable { onEpisodeClick(episode) }
                        .padding(horizontal = 16.dp, vertical = 10.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    if (isSelected) {
                        Icon(
                            imageVector = Icons.Default.PlayArrow,
                            contentDescription = null,
                            tint = Color.White,
                            modifier = Modifier.padding(end = 8.dp)
                        )
                    }
                    Text(
                        text = episode.title,
                        style = MaterialTheme.typography.bodyMedium,
                        color = if (isSelected) Color.White else Color.White.copy(alpha = 0.8f),
                        fontWeight = if (isSelected) FontWeight.Bold else FontWeight.Normal
                    )
                }
            }
        }
    }
}

@Suppress("OPT_IN_USAGE_UNANNOTATED_OVERRIDE")
private fun addAudioAnalyticsListener(exoPlayer: ExoPlayer) {
    exoPlayer.addAnalyticsListener(object : AnalyticsListener {
        override fun onAudioSessionIdChanged(eventTime: AnalyticsListener.EventTime, audioSessionId: Int) {
            Log.d("AudioDiag", "音频会话 ID 变更: sessionId=$audioSessionId")
        }

        override fun onAudioSinkError(eventTime: AnalyticsListener.EventTime, audioSinkError: Exception) {
            Log.e("AudioDiag", "音频输出错误: ${audioSinkError.message}", audioSinkError)
        }

        override fun onLoadError(
            eventTime: AnalyticsListener.EventTime,
            loadEventInfo: LoadEventInfo,
            mediaLoadData: MediaLoadData,
            error: IOException,
            wasCanceled: Boolean
        ) {
            if (mediaLoadData.trackType == C.TRACK_TYPE_AUDIO) {
                Log.e("AudioDiag", "音频加载错误: ${error.message}, canceled=$wasCanceled")
            }
        }

        override fun onDroppedVideoFrames(eventTime: AnalyticsListener.EventTime, droppedFrames: Int, elapsedMs: Long) {
            if (droppedFrames > 10) {
                Log.w("AudioDiag", "丢帧: ${droppedFrames}帧, 耗时=${elapsedMs}ms")
            }
        }
    })
}
