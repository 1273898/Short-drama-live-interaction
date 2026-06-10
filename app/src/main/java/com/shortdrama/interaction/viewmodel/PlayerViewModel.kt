package com.shortdrama.interaction.viewmodel

import android.app.Application
import android.content.Context
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import com.shortdrama.interaction.data.model.Drama
import com.shortdrama.interaction.data.model.Episode
import com.shortdrama.interaction.data.repository.DramaRepository
import com.shortdrama.interaction.data.repository.AudioMode
import com.shortdrama.interaction.data.repository.PlaybackProgressStore
import com.shortdrama.interaction.data.network.ServerHighlightPoint
import com.shortdrama.interaction.ui.component.SpriteTtsManager
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import java.util.concurrent.ConcurrentHashMap

class PlayerViewModel(
    private val dramaId: Int,
    private val episodeId: Int,
    private val progressStore: PlaybackProgressStore,
    private val branchRepository: com.shortdrama.interaction.data.repository.BranchRepository? = null
) : ViewModel() {

    private val repository = DramaRepository

    // === 分支相关状态 ===
    private val _showBranchOverlay = MutableStateFlow(false)
    val showBranchOverlay: StateFlow<Boolean> = _showBranchOverlay.asStateFlow()

    private val _branches = MutableStateFlow<List<com.shortdrama.interaction.data.model.Branch>>(emptyList())
    val branches: StateFlow<List<com.shortdrama.interaction.data.model.Branch>> = _branches.asStateFlow()

    private val _branchesLoading = MutableStateFlow(false)
    val branchesLoading: StateFlow<Boolean> = _branchesLoading.asStateFlow()

    private val _isGenerating = MutableStateFlow(false)
    val isGenerating: StateFlow<Boolean> = _isGenerating.asStateFlow()

    private val _showSubmitDialog = MutableStateFlow(false)
    val showSubmitDialog: StateFlow<Boolean> = _showSubmitDialog.asStateFlow()

    private val _submitResult = MutableStateFlow<String?>(null)
    val submitResult: StateFlow<String?> = _submitResult.asStateFlow()

    private val _isSubmitting = MutableStateFlow(false)
    val isSubmitting: StateFlow<Boolean> = _isSubmitting.asStateFlow()

    private var completedEpisodes = mutableSetOf<Int>()

    val drama: Drama? = repository.getCachedDramaById(dramaId)
    var spriteTtsManager: SpriteTtsManager? = null
        private set

    private val _highlightTriggerEvent = MutableSharedFlow<ServerHighlightPoint>(replay = 0, extraBufferCapacity = 1)
    val highlightTriggerEvent: SharedFlow<ServerHighlightPoint> = _highlightTriggerEvent.asSharedFlow()

    // Tracks which episodes have had their position loaded from DataStore
    private val readyPositions = ConcurrentHashMap.newKeySet<Int>()

    private val _currentEpisode = MutableStateFlow(
        drama?.episodes?.find { it.id == episodeId } ?: drama?.episodes?.firstOrNull()
    )
    val currentEpisode: StateFlow<Episode?> = _currentEpisode.asStateFlow()

    // 每集的恢复位置（key=episodeId），避免多集并发加载时互相覆盖
    private val restoredPositions = ConcurrentHashMap<Int, Long>()
    private val positionCache = ConcurrentHashMap<Int, Long>()

    init {
        _currentEpisode.value?.let { episode ->
            viewModelScope.launch {
                val pos = progressStore.getProgress(episode.id)
                restoredPositions[episode.id] = pos
                positionCache[episode.id] = pos
                readyPositions.add(episode.id)
            }
        }
    }

    fun selectEpisode(episode: Episode) {
        cancelTts()
        _currentEpisode.value = episode
        // 先用缓存值，再异步更新
        val cachedPos = positionCache[episode.id] ?: 0L
        restoredPositions[episode.id] = cachedPos
        viewModelScope.launch {
            val pos = progressStore.getProgress(episode.id)
            restoredPositions[episode.id] = pos
            positionCache[episode.id] = pos
            readyPositions.add(episode.id)
        }
    }

    /** Check if position has been loaded for the given episode (thread-safe) */
    fun isPositionReady(episodeId: Int): Boolean {
        return readyPositions.contains(episodeId)
    }

    fun initTts(context: Context) {
        if (spriteTtsManager == null) {
            val manager = SpriteTtsManager(context.applicationContext)
            spriteTtsManager = manager
            viewModelScope.launch(Dispatchers.IO) {
                manager.initialize()
            }
        }
    }

    fun cancelTts() {
        spriteTtsManager?.stop()
    }

    fun speakRoast(text: String, audioMode: AudioMode) {
        when (audioMode) {
            AudioMode.ON -> spriteTtsManager?.speak(text)
            AudioMode.NOTIFICATION -> spriteTtsManager?.playNotificationSound()
            AudioMode.OFF -> { /* 静音模式 */ }
        }
    }

    fun onHighlightTriggered(highlight: ServerHighlightPoint) {
        _highlightTriggerEvent.tryEmit(highlight)
    }

    fun savePosition(episodeId: Int, position: Long) {
        viewModelScope.launch {
            progressStore.saveProgress(episodeId, position)
        }
    }

    fun getPosition(episodeId: Int): Long {
        return if (episodeId == _currentEpisode.value?.id) (restoredPositions[episodeId] ?: 0L) else 0L
    }

    // === 分支功能 ===

    fun onEpisodeCompleted() {
        val episode = _currentEpisode.value ?: return
        if (episode.id in completedEpisodes) return
        completedEpisodes.add(episode.id)
        _showBranchOverlay.value = true
        loadBranches(episode.id)
    }

    private fun loadBranches(episodeId: Int) {
        if (branchRepository == null) return
        viewModelScope.launch {
            _branchesLoading.value = true
            try {
                val response = branchRepository.getBranches(episodeId)
                _branches.value = response.branches
                _isGenerating.value = response.isGenerating
                _branchesLoading.value = false
                if (!response.hasBranches && !response.isGenerating) {
                    branchRepository.triggerGeneration(episodeId)
                    _isGenerating.value = true
                    pollForBranches(episodeId)
                }
            } catch (e: Exception) {
                _branchesLoading.value = false
                _submitResult.value = "加载失败: ${e.message}"
            }
        }
    }

    private fun pollForBranches(episodeId: Int) {
        if (branchRepository == null) return
        viewModelScope.launch {
            val response = branchRepository.pollForBranches(episodeId)
            _isGenerating.value = false
            if (response != null) {
                _branches.value = response.branches
            }
        }
    }

    fun voteBranch(branchId: Int) {
        if (branchRepository == null) return
        viewModelScope.launch {
            try {
                val response = branchRepository.voteBranch(branchId)
                _branches.value = _branches.value.map { branch ->
                    if (branch.id == branchId) branch.copy(voteCount = response.voteCount, userHasVoted = true)
                    else branch
                }
            } catch (e: Exception) {
                _submitResult.value = "点赞失败: ${e.message}"
            }
        }
    }

    fun showSubmitDialog() { _showSubmitDialog.value = true }
    fun dismissSubmitDialog() { _showSubmitDialog.value = false; _submitResult.value = null }

    fun submitBranch(title: String, description: String, tone: String, preview: String) {
        val episode = _currentEpisode.value ?: return
        if (branchRepository == null) return
        viewModelScope.launch {
            _isSubmitting.value = true
            try {
                val response = branchRepository.submitBranch(episode.id, title, description, tone, preview)
                _isSubmitting.value = false
                when (response.status) {
                    "validated" -> {
                        _submitResult.value = "提交成功！正在生成漫画..."
                        _showSubmitDialog.value = false
                        loadBranches(episode.id)
                    }
                    "rejected" -> { _submitResult.value = response.rejectionReason ?: "审核未通过" }
                    else -> { _submitResult.value = "提交失败: ${response.status}" }
                }
            } catch (e: Exception) {
                _isSubmitting.value = false
                _submitResult.value = "提交失败: ${e.message}"
            }
        }
    }

    fun dismissBranchOverlay() { _showBranchOverlay.value = false }
    fun clearSubmitResult() { _submitResult.value = null }

    override fun onCleared() {
        super.onCleared()
        spriteTtsManager?.shutdown()
        spriteTtsManager = null
    }

    class Factory(
        private val application: Application,
        private val dramaId: Int,
        private val episodeId: Int,
        private val branchRepository: com.shortdrama.interaction.data.repository.BranchRepository? = null
    ) : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T {
            if (modelClass.isAssignableFrom(PlayerViewModel::class.java)) {
                val store = PlaybackProgressStore(application)
                return PlayerViewModel(dramaId, episodeId, store, branchRepository) as T
            }
            throw IllegalArgumentException("Unknown ViewModel class")
        }
    }
}
