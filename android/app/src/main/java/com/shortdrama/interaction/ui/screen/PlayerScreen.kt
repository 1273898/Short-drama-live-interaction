package com.shortdrama.interaction.ui.screen

import androidx.compose.animation.AnimatedVisibility
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
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.shortdrama.interaction.data.model.Episode
import com.shortdrama.interaction.ui.component.VideoPlayer
import com.shortdrama.interaction.viewmodel.PlayerViewModel

@OptIn(ExperimentalMaterial3Api::class, androidx.compose.foundation.ExperimentalFoundationApi::class)
@Composable
fun PlayerScreen(
    viewModel: PlayerViewModel,
    onBack: () -> Unit
) {
    val drama = viewModel.drama
    val episodes = drama?.episodes ?: emptyList()
    val currentEpisode by viewModel.currentEpisode.collectAsState()
    var showEpisodePanel by remember { mutableStateOf(false) }

    if (drama == null || episodes.isEmpty()) {
        Box(modifier = Modifier.fillMaxSize().background(Color.Black))
        return
    }

    // Find initial page index
    val initialPage = episodes.indexOfFirst { it.id == currentEpisode?.id }.coerceAtLeast(0)
    val pagerState = rememberPagerState(initialPage = initialPage) { episodes.size }

    // Sync pager with episode changes
    LaunchedEffect(pagerState) {
        snapshotFlow { pagerState.settledPage }.collect { page ->
            viewModel.selectEpisode(episodes[page])
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
            EpisodePage(
                episode = episode,
                dramaTitle = drama.title,
                episodeIndex = page,
                totalEpisodes = episodes.size,
                isActive = isActive,
                startPosition = viewModel.getPosition(episode.id),
                onPositionChanged = { position -> viewModel.savePosition(episode.id, position) },
                onBack = onBack,
                onOpenEpisodes = { showEpisodePanel = true }
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

        // Episode panel - aligned to right
        Box(modifier = Modifier.fillMaxSize()) {
            AnimatedVisibility(
                visible = showEpisodePanel,
                enter = slideInHorizontally(initialOffsetX = { it }),
                exit = slideOutHorizontally(targetOffsetX = { it }),
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
                        }
                    )
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun EpisodePage(
    episode: Episode,
    dramaTitle: String,
    episodeIndex: Int,
    totalEpisodes: Int,
    isActive: Boolean,
    startPosition: Long,
    onPositionChanged: (Long) -> Unit,
    onBack: () -> Unit,
    onOpenEpisodes: () -> Unit
) {
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
        // Video player - full screen, only active page plays
        VideoPlayer(
            videoPath = episode.videoPath,
            modifier = Modifier.fillMaxSize(),
            isActive = isActive,
            startPosition = startPosition,
            onPositionChanged = onPositionChanged
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
            colors = TopAppBarDefaults.topAppBarColors(
                containerColor = Color.Transparent
            )
        )

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
            items(episodes) { episode ->
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
