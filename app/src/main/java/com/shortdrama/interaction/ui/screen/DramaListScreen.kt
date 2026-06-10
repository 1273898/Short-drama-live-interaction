package com.shortdrama.interaction.ui.screen

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.tween
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutVertically
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.shortdrama.interaction.ui.component.DramaCard
import com.shortdrama.interaction.ui.component.EpisodeList
import com.shortdrama.interaction.viewmodel.DramaListUiState
import com.shortdrama.interaction.viewmodel.DramaListViewModel
import com.shortdrama.interaction.data.model.AppEvent
import androidx.compose.material3.SnackbarDuration
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.SnackbarResult
import androidx.compose.runtime.LaunchedEffect

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun DramaListScreen(
    viewModel: DramaListViewModel = androidx.lifecycle.viewmodel.compose.viewModel(),
    onEpisodeClick: (dramaId: Int, episodeId: Int) -> Unit
) {
    var expandedDramaId by remember { mutableStateOf<Int?>(null) }
    val uiState by viewModel.uiState.collectAsState()
    val dramaList by viewModel.dramas.collectAsState()
    val snackbarHostState = remember { SnackbarHostState() }

    // Collect error events for Snackbar
    LaunchedEffect(Unit) {
        viewModel.events.collect { event ->
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
                is AppEvent.ShowToast -> { /* 不使用 Toast */ }
            }
        }
    }

    val expandedDrama = dramaList.find { it.id == expandedDramaId }
    Scaffold(
        snackbarHost = { SnackbarHost(hostState = snackbarHostState) },
        topBar = {
            TopAppBar(
                title = {
                    Text(
                        text = "短剧互动",
                        fontWeight = FontWeight.Bold
                    )
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.background,
                    titleContentColor = MaterialTheme.colorScheme.onBackground
                )
            )
        }
    ) { paddingValues ->
        Box(
            modifier = Modifier
                .fillMaxSize()
                .padding(paddingValues)
        ) {
            when (val state = uiState) {
                is DramaListUiState.Loading -> {
                    CircularProgressIndicator(
                        modifier = Modifier.align(Alignment.Center),
                        color = MaterialTheme.colorScheme.primary
                    )
                }

                is DramaListUiState.Error -> {
                    Column(
                        modifier = Modifier.align(Alignment.Center),
                        horizontalAlignment = Alignment.CenterHorizontally
                    ) {
                        Text(
                            text = state.message,
                            color = Color.White.copy(alpha = 0.7f)
                        )
                        Spacer(modifier = Modifier.height(16.dp))
                        Button(onClick = { viewModel.loadDramas() }) {
                            Text("重试")
                        }
                    }
                }

                is DramaListUiState.Success -> {
                    LazyVerticalGrid(
                        columns = GridCells.Fixed(2),
                        modifier = Modifier
                            .fillMaxSize()
                            .background(MaterialTheme.colorScheme.background),
                        contentPadding = PaddingValues(16.dp),
                        horizontalArrangement = Arrangement.spacedBy(16.dp),
                        verticalArrangement = Arrangement.spacedBy(24.dp)
                    ) {
                        items(state.dramas, key = { it.id }) { drama ->
                            val toggleExpand = {
                                expandedDramaId = if (expandedDramaId == drama.id) null else drama.id
                            }
                            DramaCard(
                                drama = drama,
                                onClick = {
                                    val firstEpisode = drama.episodes.firstOrNull()
                                    if (firstEpisode != null) {
                                        onEpisodeClick(drama.id, firstEpisode.id)
                                    }
                                },
                                onLongClick = toggleExpand,
                                onExpandClick = toggleExpand
                            )
                        }
                    }
                }
            }

            // Expanded episode panel at bottom
            AnimatedVisibility(
                visible = expandedDrama != null,
                enter = slideInVertically(animationSpec = tween(120), initialOffsetY = { it }),
                exit = slideOutVertically(animationSpec = tween(120), targetOffsetY = { it }),
                modifier = Modifier.align(Alignment.BottomCenter)
            ) {
                expandedDrama?.let { drama ->
                    Column(
                        modifier = Modifier
                            .fillMaxWidth()
                            .clip(RoundedCornerShape(topStart = 16.dp, topEnd = 16.dp))
                            .background(Color(0xFF1A1A2E))
                            .padding(16.dp)
                    ) {
                        Text(
                            text = drama.title,
                            style = MaterialTheme.typography.titleMedium,
                            fontWeight = FontWeight.Bold,
                            color = Color.White,
                            modifier = Modifier.padding(bottom = 12.dp)
                        )
                        EpisodeList(
                            episodes = drama.episodes,
                            currentEpisodeId = drama.episodes.firstOrNull()?.id ?: 0,
                            onEpisodeClick = { episode ->
                                onEpisodeClick(drama.id, episode.id)
                                expandedDramaId = null
                            }
                        )
                    }
                }
            }
        }
    }
}
