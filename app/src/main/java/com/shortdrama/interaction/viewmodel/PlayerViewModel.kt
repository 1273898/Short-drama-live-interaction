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
    private val progressStore: PlaybackProgressStore
) : ViewModel() {

    private val repository = DramaRepository

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

    override fun onCleared() {
        super.onCleared()
        spriteTtsManager?.shutdown()
        spriteTtsManager = null
    }

    class Factory(
        private val application: Application,
        private val dramaId: Int,
        private val episodeId: Int
    ) : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T {
            if (modelClass.isAssignableFrom(PlayerViewModel::class.java)) {
                val store = PlaybackProgressStore(application)
                return PlayerViewModel(dramaId, episodeId, store) as T
            }
            throw IllegalArgumentException("Unknown ViewModel class")
        }
    }
}
