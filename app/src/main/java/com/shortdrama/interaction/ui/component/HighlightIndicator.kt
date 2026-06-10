package com.shortdrama.interaction.ui.component

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.shortdrama.interaction.BuildConfig
import com.shortdrama.interaction.data.network.ServerHighlightPoint
import kotlinx.coroutines.delay

fun highlightTypeColor(type: String): Color = when (type) {
    "L1" -> Color(0xFFFF1744) // 亮红
    "L2" -> Color(0xFFFF9100) // 亮橙
    "L3" -> Color(0xFFFFEB3B) // 亮黄
    "L4" -> Color(0xFFFFF176) // 浅亮黄
    else -> Color.White
}

fun highlightTypeLabel(type: String): String = when (type) {
    "L1" -> "极高能"
    "L2" -> "高能"
    "L3" -> "精彩"
    "L4" -> "看点"
    else -> type
}

@Composable
fun HighlightMarkers(
    highlights: List<ServerHighlightPoint>,
    durationMs: Long,
    modifier: Modifier = Modifier
) {
    if (highlights.isEmpty() || durationMs <= 0) return

    Canvas(modifier = modifier.height(20.dp)) {
        val width = size.width
        val centerY = size.height / 2
        val dotRadius = 5.dp.toPx()

        for (point in highlights) {
            val x = (point.timestamp.toFloat() / durationMs.toFloat()) * width
            drawCircle(
                color = highlightTypeColor(point.type),
                radius = dotRadius,
                center = Offset(x, centerY)
            )
        }
    }
}

@Composable
fun HighlightSnackbar(
    highlight: ServerHighlightPoint?,
    modifier: Modifier = Modifier,
    onDismiss: () -> Unit = {}
) {
    var visible by remember { mutableStateOf(false) }

    LaunchedEffect(highlight) {
        if (highlight != null) {
            visible = true
            delay(5000)
            visible = false
            onDismiss()
        }
    }

    AnimatedVisibility(
        visible = visible && highlight != null,
        enter = fadeIn(),
        exit = fadeOut(),
        modifier = modifier
    ) {
        if (highlight != null) {
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 32.dp),
                contentAlignment = Alignment.Center
            ) {
                Surface(
                    shape = RoundedCornerShape(8.dp),
                    color = Color.Black.copy(alpha = 0.85f),
                    contentColor = Color.White
                ) {
                    Row(
                        modifier = Modifier.padding(horizontal = 16.dp, vertical = 10.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        Box(
                            modifier = Modifier
                                .size(8.dp)
                                .background(highlightTypeColor(highlight.type), CircleShape)
                        )
                        Text(
                            text = "剧情高光点 (${highlightTypeLabel(highlight.type)})",
                            fontSize = 14.sp,
                            fontWeight = FontWeight.Medium
                        )
                    }
                }
            }
        }
    }
}

@Composable
fun HighlightDebugDialog(
    highlight: ServerHighlightPoint?,
    onDismiss: () -> Unit
) {
    if (!BuildConfig.DEBUG || highlight == null) return

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("高光点调试信息") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("时间戳: ${highlight.timestamp}ms (${highlight.timestamp / 1000}s)")
                Text("类型: ${highlight.type} (${highlightTypeLabel(highlight.type)})")
                Text("置信度: ${(highlight.confidence * 100).toInt()}%")
                Text("触发来源: 服务端识别")
            }
        },
        confirmButton = {
            TextButton(onClick = onDismiss) {
                Text("关闭")
            }
        }
    )
}
