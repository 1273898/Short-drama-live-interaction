package com.shortdrama.interaction.data.model

import androidx.compose.ui.graphics.Color

enum class EmotionType(val label: String, val color: Color, val icon: String, val glowColor: Color) {
    THRILLING("爽到了", Color(0xFFFFD700), "🔥", Color(0xFFFF8C00)),
    ANGRY("气死了", Color(0xFFFF2222), "💢", Color(0xFF990000)),
    SHIPPING("磕到了", Color(0xFFFF1493), "💕", Color(0xFFFF69B4)),
    SHOCKED("惊呆了", Color(0xFF00CCFF), "⚡", Color(0xFF0033CC))
}

data class SpriteState(
    val isVisible: Boolean = false,
    val roastText: String = "",
    val highlightTimestamp: Long = 0L,
    val expression: SpriteExpression = SpriteExpression.DEFAULT,
    val isAnimating: Boolean = false,
    val currentRoastId: Int = 0,
    val emotionTag: String = ""
)

enum class SpriteExpression {
    DEFAULT, HAPPY, SAD, INSPIRATION
}
