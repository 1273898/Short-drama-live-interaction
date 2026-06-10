package com.shortdrama.interaction.viewmodel

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.shortdrama.interaction.data.model.AppEvent
import com.shortdrama.interaction.data.model.Drama
import com.shortdrama.interaction.data.repository.DramaRepository
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.launch

sealed class DramaListUiState {
    object Loading : DramaListUiState()
    data class Success(val dramas: List<Drama>) : DramaListUiState()
    data class Error(val message: String) : DramaListUiState()
}

class DramaListViewModel : ViewModel() {
    private val repository = DramaRepository

    private val _uiState = MutableStateFlow<DramaListUiState>(DramaListUiState.Loading)
    val uiState: StateFlow<DramaListUiState> = _uiState.asStateFlow()

    /** 缓存的剧集列表，避免每次重组时重复提取 */
    val dramas: StateFlow<List<Drama>> = _uiState
        .map { state -> (state as? DramaListUiState.Success)?.dramas ?: emptyList() }
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())

    private val _events = MutableSharedFlow<AppEvent>(extraBufferCapacity = 1)
    val events: SharedFlow<AppEvent> = _events.asSharedFlow()

    init { loadDramas() }

    fun loadDramas() {
        viewModelScope.launch {
            _uiState.value = DramaListUiState.Loading
            try {
                val result = repository.getAllDramas()
                _uiState.value = DramaListUiState.Success(result)
            } catch (e: Exception) {
                _uiState.value = DramaListUiState.Error(e.message ?: "加载失败")
                _events.tryEmit(AppEvent.ShowSnackbar(
                    message = "剧集加载失败，请检查网络",
                    actionLabel = "重试",
                    onAction = { loadDramas() }
                ))
            }
        }
    }
}
