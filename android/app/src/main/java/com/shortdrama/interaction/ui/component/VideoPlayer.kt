package com.shortdrama.interaction.ui.component

import android.net.Uri
import android.view.GestureDetector
import android.view.MotionEvent
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Slider
import androidx.compose.material3.SliderDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.media3.common.MediaItem
import androidx.media3.common.PlaybackParameters
import androidx.media3.common.Player
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.ui.PlayerView
import kotlinx.coroutines.delay
import java.io.File

@Composable
fun VideoPlayer(
    videoPath: String,
    modifier: Modifier = Modifier,
    isActive: Boolean = true,
    startPosition: Long = 0L,
    onPositionChanged: (Long) -> Unit = {}
) {
    val context = LocalContext.current
    var isPaused by remember { mutableStateOf(false) }
    var currentPosition by remember { mutableLongStateOf(startPosition) }
    var duration by remember { mutableLongStateOf(0L) }
    var seekIndicator by remember { mutableStateOf("") }
    var isSpeedUp by remember { mutableStateOf(false) }

    val exoPlayer = remember(videoPath) {
        ExoPlayer.Builder(context).build().apply {
            val mediaItem = if (videoPath.startsWith("/") || videoPath.contains(":\\")) {
                MediaItem.fromUri(Uri.fromFile(File(videoPath)))
            } else {
                MediaItem.fromUri(videoPath)
            }
            setMediaItem(mediaItem)
            repeatMode = Player.REPEAT_MODE_ALL
            prepare()
            seekTo(startPosition)
            playWhenReady = false
        }
    }

    // Update position periodically
    LaunchedEffect(exoPlayer, isActive) {
        while (isActive) {
            if (exoPlayer.isPlaying) {
                currentPosition = exoPlayer.currentPosition
                duration = exoPlayer.duration.coerceAtLeast(0)
                onPositionChanged(currentPosition)
            }
            delay(500)
        }
    }

    // Control playback based on active state
    LaunchedEffect(isActive) {
        if (isActive) {
            exoPlayer.playWhenReady = true
            exoPlayer.play()
            isPaused = false
        } else {
            exoPlayer.pause()
            onPositionChanged(exoPlayer.currentPosition)
        }
    }

    // Auto-hide seek indicator
    LaunchedEffect(seekIndicator) {
        if (seekIndicator.isNotEmpty()) {
            delay(600)
            seekIndicator = ""
        }
    }

    DisposableEffect(Unit) {
        onDispose {
            onPositionChanged(exoPlayer.currentPosition)
            exoPlayer.release()
        }
    }

    Box(modifier = modifier.background(Color.Black)) {
        AndroidView(
            factory = { ctx ->
                PlayerView(ctx).apply {
                    player = exoPlayer
                    useController = false

                    val gestureDetector = GestureDetector(ctx, object : GestureDetector.SimpleOnGestureListener() {
                        override fun onSingleTapConfirmed(e: MotionEvent): Boolean {
                            if (exoPlayer.isPlaying) {
                                exoPlayer.pause()
                                isPaused = true
                                currentPosition = exoPlayer.currentPosition
                                onPositionChanged(currentPosition)
                            } else {
                                exoPlayer.play()
                                isPaused = false
                            }
                            return true
                        }

                        override fun onDoubleTap(e: MotionEvent): Boolean {
                            val viewWidth = width
                            val tapX = e.x
                            if (tapX > viewWidth * 0.6) {
                                exoPlayer.seekTo(exoPlayer.currentPosition + 15000)
                                seekIndicator = "+15s"
                            } else if (tapX < viewWidth * 0.4) {
                                exoPlayer.seekTo(maxOf(0, exoPlayer.currentPosition - 15000))
                                seekIndicator = "-15s"
                            }
                            return true
                        }

                        override fun onLongPress(e: MotionEvent) {
                            if (!isSpeedUp) {
                                exoPlayer.playbackParameters = PlaybackParameters(1.5f)
                                isSpeedUp = true
                            } else {
                                exoPlayer.playbackParameters = PlaybackParameters(1f)
                                isSpeedUp = false
                            }
                        }
                    })

                    setOnTouchListener { _, event ->
                        gestureDetector.onTouchEvent(event)
                        true
                    }
                }
            },
            modifier = Modifier.fillMaxSize()
        )

        // Seek indicator
        if (seekIndicator.isNotEmpty()) {
            Text(
                text = seekIndicator,
                fontSize = 24.sp,
                fontWeight = FontWeight.Bold,
                color = Color.White,
                modifier = Modifier
                    .align(Alignment.Center)
                    .background(Color.Black.copy(alpha = 0.5f), RoundedCornerShape(8.dp))
                    .padding(horizontal = 20.dp, vertical = 10.dp)
            )
        }

        // Speed indicator
        if (isSpeedUp) {
            Text(
                text = "1.5x",
                fontSize = 14.sp,
                fontWeight = FontWeight.Bold,
                color = Color.White,
                modifier = Modifier
                    .align(Alignment.TopEnd)
                    .padding(16.dp)
                    .background(Color(0xFFFF6B6B), RoundedCornerShape(4.dp))
                    .padding(horizontal = 8.dp, vertical = 4.dp)
            )
        }

        // Progress bar at bottom (visible when paused)
        if (isPaused && duration > 0) {
            Column(
                modifier = Modifier
                    .align(Alignment.BottomCenter)
                    .fillMaxWidth()
                    .background(Color.Black.copy(alpha = 0.7f))
                    .padding(horizontal = 16.dp, vertical = 8.dp)
            ) {
                Slider(
                    value = currentPosition.toFloat(),
                    onValueChange = { newValue ->
                        currentPosition = newValue.toLong()
                        exoPlayer.seekTo(currentPosition)
                    },
                    onValueChangeFinished = {
                        onPositionChanged(currentPosition)
                    },
                    valueRange = 0f..duration.toFloat(),
                    modifier = Modifier.fillMaxWidth(),
                    colors = SliderDefaults.colors(
                        thumbColor = Color.White,
                        activeTrackColor = Color(0xFFFF6B6B),
                        inactiveTrackColor = Color.White.copy(alpha = 0.3f)
                    )
                )
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween
                ) {
                    Text(
                        text = formatTime(currentPosition),
                        color = Color.White,
                        fontSize = 12.sp
                    )
                    Text(
                        text = formatTime(duration),
                        color = Color.White,
                        fontSize = 12.sp
                    )
                }
            }
        }
    }
}

private fun formatTime(ms: Long): String {
    val totalSeconds = ms / 1000
    val minutes = totalSeconds / 60
    val seconds = totalSeconds % 60
    return "%d:%02d".format(minutes, seconds)
}
