package com.shortdrama.interaction.viewmodel

import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import com.shortdrama.interaction.data.model.Drama
import com.shortdrama.interaction.data.model.Episode
import com.shortdrama.interaction.data.repository.DramaRepository
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

class PlayerViewModel(savedStateHandle: SavedStateHandle) : ViewModel() {

    private val dramaId: Int = savedStateHandle.get<Int>("dramaId") ?: 1
    private val episodeId: Int = savedStateHandle.get<Int>("episodeId") ?: 0

    val drama: Drama? = DramaRepository.getDramaById(dramaId)

    private val _currentEpisode = MutableStateFlow(
        drama?.episodes?.find { it.id == episodeId } ?: drama?.episodes?.firstOrNull()
    )
    val currentEpisode: StateFlow<Episode?> = _currentEpisode.asStateFlow()

    fun selectEpisode(episode: Episode) {
        _currentEpisode.value = episode
    }

    fun nextEpisode() {
        val current = _currentEpisode.value ?: return
        val episodes = drama?.episodes ?: return
        val index = episodes.indexOfFirst { it.id == current.id }
        if (index < episodes.size - 1) {
            _currentEpisode.value = episodes[index + 1]
        }
    }

    fun previousEpisode() {
        val current = _currentEpisode.value ?: return
        val episodes = drama?.episodes ?: return
        val index = episodes.indexOfFirst { it.id == current.id }
        if (index > 0) {
            _currentEpisode.value = episodes[index - 1]
        }
    }
}
