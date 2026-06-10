package com.shortdrama.interaction.ui.component

import android.util.Log
import android.view.GestureDetector
import android.view.MotionEvent
import androidx.compose.foundation.Canvas
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
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.media3.common.PlaybackParameters
import androidx.media3.common.Player
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.ui.PlayerView
import com.shortdrama.interaction.data.network.ServerHighlightPoint
import kotlinx.coroutines.delay

@OptIn(androidx.media3.common.util.UnstableApi::class)
@Composable
fun VideoPlayer(
    exoPlayer: ExoPlayer,
    videoPath: String,
    modifier: Modifier = Modifier,
    isActive: Boolean = true,
    startPosition: Long = 0L,
    highlightPoints: List<ServerHighlightPoint> = emptyList(),
    highlightSegments: List<ServerHighlightPoint> = emptyList(),
    onPositionChanged: (Long) -> Unit = {},
    onDurationReady: (Long) -> Unit = {},
    onHighlightReached: (ServerHighlightPoint) -> Unit = {},
    onSeekRequest: (Long) -> Unit = {},
    onRecoverAudioFocus: () -> Unit = {}
) {
    var currentPosition by remember { mutableLongStateOf(startPosition) }
    var duration by remember { mutableLongStateOf(0L) }
    var seekIndicator by remember { mutableStateOf("") }
    var isSpeedUp by remember { mutableStateOf(false) }
    var showMarkers by remember { mutableStateOf(false) }
    var userPaused by remember { mutableStateOf(false) }
    var seekTarget by remember { mutableLongStateOf(-1L) }
    var seekRequestId by remember { mutableLongStateOf(-1L) }
    val triggeredTimestamps = remember { mutableSetOf<String>() }
    val sortedHighlights = remember(highlightPoints) {
        highlightPoints.sortedBy { it.timestamp }
    }
    val audioDiag = remember { AudioDiagnostics() }

    // Reset triggered timestamps when video changes
    LaunchedEffect(videoPath) {
        triggeredTimestamps.clear()
        showMarkers = false
        audioDiag.logEpisodeChange(videoPath)
    }

    // Delay marker display: wait for playback ready + 2s
    LaunchedEffect(exoPlayer) {
        while (exoPlayer.playbackState != Player.STATE_READY || !exoPlayer.playWhenReady) {
            delay(200)
        }
        delay(2000)
        showMarkers = true
    }

    // Timeout retry: retry once if duration not obtained within 10s
    // 关键：isActive 作为 key，非活跃页（isActive=false）不会触发超时重试，
    // 避免预渲染的相邻页因 duration=0 而错误调用 onSeekRequest(0L) 操作共享 ExoPlayer
    var hasRetried by remember(videoPath) { mutableStateOf(false) }
    LaunchedEffect(videoPath, isActive) {
        if (!isActive) return@LaunchedEffect
        delay(10_000)
        if (duration <= 0 && !hasRetried) {
            hasRetried = true
            onSeekRequest(0L)
        }
    }

    // 播放健康恢复 + 位置更新
    // 非活跃页：仅读取共享 ExoPlayer 的 duration/position（供 UI 如进度条使用），不执行任何播放控制
    // 活跃页：额外执行健康恢复和音频诊断
    LaunchedEffect(exoPlayer, isActive) {
        var healthCheckCounter = 0
        while (true) {
            // 始终从共享 ExoPlayer 读取 duration 和 position，供进度条等 UI 使用
            if (exoPlayer.isPlaying) {
                currentPosition = exoPlayer.currentPosition
                val newDuration = exoPlayer.duration.coerceAtLeast(0)
                if (newDuration != duration && newDuration > 0) {
                    duration = newDuration
                    onDurationReady(duration)
                }
                onPositionChanged(currentPosition)
            }

            // 以下操作仅活跃页执行，非活跃页不得操作共享 ExoPlayer
            if (isActive) {
                // STATE_ENDED 不恢复 — 避免播完后被误判为异常暂停导致从头重播
                if (!userPaused && !exoPlayer.isPlaying && exoPlayer.volume > 0f) {
                    val state = exoPlayer.playbackState
                    if (state == Player.STATE_READY || state == Player.STATE_BUFFERING) {
                        onRecoverAudioFocus()
                        exoPlayer.playWhenReady = true
                        exoPlayer.play()
                        audioDiag.onPlaybackRestore("periodic_loop")
                    }
                }
                healthCheckCounter++
                if (healthCheckCounter % 4 == 0) {
                    audioDiag.checkAudioHealth(exoPlayer, userPaused)
                }
                delay(500)
            } else {
                // 非活跃页降低轮询频率，仅读取播放位置供 UI 使用
                delay(5000)
            }
        }
    }

    // 高光检测：highlightSegments 作为 key，数据加载后 effect 重启并开始检测
    // triggeredTimestamps 在 remember 中持久化，不受 effect 重启影响
    LaunchedEffect(exoPlayer, isActive, highlightSegments) {
        val segs = highlightSegments
        if (!isActive || segs.isEmpty()) return@LaunchedEffect

        // 一次性初始检测：协程启动时用当前位置扫描所有高光
        // 防止数据加载延迟导致的"协程重启盲区"遗漏已进入窗口的高光
        val initialPos = exoPlayer.currentPosition
        for (segment in segs) {
            val segKey = "${segment.timestamp}_${segment.type}"
            if (segKey in triggeredTimestamps) continue
            val effectiveStart = if (segment.startTime > 0) segment.startTime else segment.timestamp
            val effectiveEnd = if (segment.endTime > 0) segment.endTime else segment.timestamp
            val isSinglePoint = effectiveStart == effectiveEnd
            val windowStart = if (isSinglePoint) segment.timestamp - 5000L else effectiveStart
            val windowEnd = if (isSinglePoint) segment.timestamp + 5000L else effectiveEnd
            if (initialPos in windowStart..windowEnd) {
                triggeredTimestamps.add(segKey)
                Log.d("VideoPlayer", "★ 高光初始检测触发: segKey=$segKey, pos=${initialPos}ms")
                onHighlightReached(segment)
            }
        }

        var lastCheckPosition = exoPlayer.currentPosition
        while (isActive) {
            if (!exoPlayer.isPlaying) {
                delay(500)
                continue
            }

            val pos = exoPlayer.currentPosition
            val posDelta = pos - lastCheckPosition
            val isSeek = (if (posDelta < 0) -posDelta else posDelta) > 2000L
            val isBackwardSeek = posDelta < -2000L
            val seekFrom = lastCheckPosition
            lastCheckPosition = pos

            // 倒退检测：位置大幅后退时，清除当前位置之后的去重记录
            if (isBackwardSeek) {
                val beforeCount = triggeredTimestamps.size
                triggeredTimestamps.removeAll { key ->
                    val ts = key.substringBefore("_").toLongOrNull() ?: return@removeAll false
                    ts > pos
                }
                if (triggeredTimestamps.size < beforeCount) {
                    Log.d("VideoPlayer", "倒退检测: pos ${seekFrom}ms→${pos}ms, 清除 ${beforeCount - triggeredTimestamps.size} 个去重记录")
                }
            }

            for (segment in segs) {
                val segKey = "${segment.timestamp}_${segment.type}"
                if (segKey in triggeredTimestamps) continue
                val effectiveStart = if (segment.startTime > 0) segment.startTime else segment.timestamp
                val effectiveEnd = if (segment.endTime > 0) segment.endTime else segment.timestamp
                val isSinglePoint = effectiveStart == effectiveEnd

                val windowStart = if (isSinglePoint) segment.timestamp - 5000L else effectiveStart
                val windowEnd = if (isSinglePoint) segment.timestamp + 5000L else effectiveEnd
                val inWindow = pos in windowStart..windowEnd

                val skippedBySeek = isSeek && segment.timestamp in (minOf(seekFrom, pos) - 5000L)..(maxOf(seekFrom, pos) + 5000L)

                val shouldTrigger = if (isSeek) {
                    inWindow || skippedBySeek
                } else if (isSinglePoint) {
                    inWindow
                } else {
                    val rangeLen = effectiveEnd - effectiveStart
                    val delay = if (rangeLen < 1500L) 0L else 1500L
                    pos in effectiveStart..effectiveEnd && pos >= effectiveStart + delay
                }
                if (shouldTrigger) {
                    triggeredTimestamps.add(segKey)
                    Log.d("VideoPlayer", "★ 高光触发: segKey=$segKey, pos=${pos}ms, isSeek=$isSeek")
                    onHighlightReached(segment)
                }
            }
            delay(500)
        }
    }

    // Control playback based on active state (no audio focus — managed by PlayerScreen)
    // 注意：ExoPlayer 由所有页面共享，非活跃页面不能调 pause()，否则会中断活跃页面的播放
    LaunchedEffect(isActive) {
        if (isActive) {
            userPaused = false
            isSpeedUp = false
            exoPlayer.volume = 1f
            exoPlayer.playWhenReady = true
            exoPlayer.play()
        }
        // 非活跃页面不暂停共享 ExoPlayer，由活跃页面控制播放
    }

    // Handle seek requests from slider/gestures
    LaunchedEffect(seekRequestId) {
        if (seekTarget >= 0) {
            userPaused = false
            audioDiag.logSeek(seekTarget)
            onSeekRequest(seekTarget)
            exoPlayer.playWhenReady = true
            exoPlayer.volume = 1f
            // seek 后延迟检查，确保音频恢复
            delay(300)
            if (exoPlayer.volume < 1f) exoPlayer.volume = 1f
            if (!exoPlayer.isPlaying && !userPaused) {
                exoPlayer.play()
            }
        }
    }

    // Auto-hide seek indicator
    LaunchedEffect(seekIndicator) {
        if (seekIndicator.isNotEmpty()) {
            delay(600)
            seekIndicator = ""
        }
    }

    Box(modifier = modifier.background(Color.Black)) {
        AndroidView(
            factory = { ctx ->
                PlayerView(ctx).apply {
                    useController = false

                    val gestureDetector = GestureDetector(ctx, object : GestureDetector.SimpleOnGestureListener() {
                        override fun onSingleTapConfirmed(e: MotionEvent): Boolean {
                            if (exoPlayer.isPlaying) {
                                exoPlayer.pause()
                                userPaused = true
                                currentPosition = exoPlayer.currentPosition
                                onPositionChanged(currentPosition)
                            } else {
                                userPaused = false
                                exoPlayer.volume = 1f
                                exoPlayer.play()
                            }
                            return true
                        }

                        override fun onDoubleTap(e: MotionEvent): Boolean {
                            val viewWidth = width
                            val tapX = e.x
                            if (tapX > viewWidth * 0.6) {
                                seekTarget = exoPlayer.currentPosition + 15000
                                seekIndicator = "+15s"
                                seekRequestId++
                            } else if (tapX < viewWidth * 0.4) {
                                seekTarget = maxOf(0, exoPlayer.currentPosition - 15000)
                                seekIndicator = "-15s"
                                seekRequestId++
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
            update = { playerView ->
                if (isActive) {
                    if (playerView.player !== exoPlayer) {
                        playerView.player = exoPlayer
                    }
                } else {
                    if (playerView.player != null) {
                        playerView.player = null
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

        // Progress bar at bottom
        if (duration > 0) {
            Column(
                modifier = Modifier
                    .align(Alignment.BottomCenter)
                    .fillMaxWidth()
                    .background(Color.Black.copy(alpha = 0.7f))
                    .padding(horizontal = 16.dp, vertical = 8.dp)
            ) {
                Box {
                    Slider(
                        value = currentPosition.toFloat(),
                        onValueChange = { newValue ->
                            currentPosition = newValue.toLong()
                            seekTarget = currentPosition
                        },
                        onValueChangeFinished = {
                            seekRequestId++
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

                    if (showMarkers && sortedHighlights.isNotEmpty()) {
                        Canvas(modifier = Modifier.matchParentSize()) {
                            val thumbRadius = 10.dp.toPx()
                            val usableWidth = size.width - thumbRadius * 2
                            val offsetX = thumbRadius
                            val centerY = size.height / 2
                            val markerSize = 8.dp.toPx()

                            for (point in sortedHighlights) {
                                val ratio = (point.timestamp.toFloat() / duration.toFloat()).coerceIn(0f, 1f)
                                val x = offsetX + ratio * usableWidth

                                val path = Path().apply {
                                    moveTo(x, centerY - markerSize)
                                    lineTo(x + markerSize, centerY)
                                    lineTo(x, centerY + markerSize)
                                    lineTo(x - markerSize, centerY)
                                    close()
                                }
                                drawPath(
                                    path = path,
                                    color = highlightTypeColor(point.type)
                                )
                                drawPath(
                                    path = path,
                                    color = Color.White,
                                    style = Stroke(width = 1.5.dp.toPx())
                                )
                            }
                        }
                    }
                }
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
