package com.shortdrama.interaction.ui.component

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.Crossfade
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.keyframes
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.Image
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.TransformOrigin
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.shortdrama.interaction.R
import com.shortdrama.interaction.data.model.SpriteExpression
import com.shortdrama.interaction.data.model.SpriteState
import com.shortdrama.interaction.data.repository.AudioMode

@Composable
fun SpriteOverlay(
    spriteState: SpriteState,
    isAlwaysVisible: Boolean = false,
    audioMode: AudioMode = AudioMode.NOTIFICATION,
    modifier: Modifier = Modifier,
    @Suppress("UNUSED_PARAMETER") onDismiss: () -> Unit = {},
    onSpriteDoubleTap: () -> Unit = {},
    onSpriteLongPress: () -> Unit = {},
    onCycleAudioMode: () -> Unit = {}
) {
    // Double-tap scale animation
    var scaleTarget by remember { mutableFloatStateOf(1f) }
    val scale by animateFloatAsState(
        targetValue = scaleTarget,
        animationSpec = spring(dampingRatio = 0.65f, stiffness = Spring.StiffnessMedium),
        finishedListener = {
            if (scaleTarget != 1f) scaleTarget = 1f
        },
        label = "spriteScale"
    )

    // Long-press shake animation
    var shakeTarget by remember { mutableFloatStateOf(0f) }
    val shakeAngle by animateFloatAsState(
        targetValue = shakeTarget,
        animationSpec = if (shakeTarget != 0f) {
            keyframes {
                durationMillis = 600
                0f at 0
                15f at 75
                -15f at 150
                15f at 225
                -15f at 300
                15f at 375
                -15f at 450
                8f at 525
                0f at 600
            }
        } else {
            tween(0)
        },
        finishedListener = {
            if (shakeTarget != 0f) shakeTarget = 0f
        },
        label = "spriteShake"
    )

    // Breath animation — hoisted from SpriteCharacter to merge graphicsLayer
    val infiniteTransition = rememberInfiniteTransition(label = "breath")
    val breathScale by infiniteTransition.animateFloat(
        initialValue = 1f,
        targetValue = 1.05f,
        animationSpec = infiniteRepeatable(
            animation = tween(2500, easing = LinearEasing),
            repeatMode = RepeatMode.Reverse
        ),
        label = "breathScale"
    )

    val isSpriteImageVisible = spriteState.isVisible || isAlwaysVisible
    val spriteSize = 112.dp
    val bubbleText = when {
        spriteState.isVisible && spriteState.roastText.isNotBlank() -> spriteState.roastText
        else -> ""
    }

    Box(modifier = modifier.fillMaxSize()) {
        AnimatedVisibility(
            visible = isSpriteImageVisible,
            enter = fadeIn(animationSpec = tween(300)),
            exit = fadeOut(animationSpec = tween(300)),
            modifier = Modifier
                .align(Alignment.BottomStart)
                .padding(start = 0.dp, bottom = 240.dp)
        ) {
            androidx.compose.runtime.key(spriteState.highlightTimestamp) {
            // Box 布局：精灵位置固定，气泡覆盖在精灵上方
            Box(contentAlignment = Alignment.BottomStart) {
                // 新高光到来时重置动画状态，防止累积偏移
                LaunchedEffect(spriteState.highlightTimestamp) {
                    scaleTarget = 1f
                    shakeTarget = 0f
                }

                // 精灵角色 + 音频按钮（音频按钮紧贴精灵正下方）
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Box(
                        modifier = Modifier
                            .size(spriteSize)
                            .graphicsLayer {
                                transformOrigin = TransformOrigin(0f, 1f)
                                scaleX = scale * breathScale
                                scaleY = scale * breathScale
                                rotationZ = shakeAngle
                            }
                            .pointerInput(Unit) {
                                detectTapGestures(
                                    onDoubleTap = {
                                        scaleTarget = 1.3f
                                        onSpriteDoubleTap()
                                    },
                                    onLongPress = {
                                        shakeTarget = 1f
                                        onSpriteLongPress()
                                    }
                                )
                            }
                    ) {
                        SpriteCharacter(expression = spriteState.expression)
                    }

                    // 音频模式切换按钮 — 紧贴精灵正下方
                    val modeIcon = when (audioMode) {
                        AudioMode.ON -> "🔊"
                        AudioMode.NOTIFICATION -> "🔔"
                        AudioMode.OFF -> "🔇"
                    }
                    Text(
                        text = modeIcon,
                        fontSize = 14.sp,
                        modifier = Modifier
                            .offset(y = (-2).dp)
                            .clickable { onCycleAudioMode() }
                    )
                }

                // 气泡 — 使用气泡图片，文字叠加在图片上
                if (bubbleText.isNotBlank()) {
                    Crossfade(
                        targetState = bubbleText,
                        animationSpec = tween(200),
                        label = "roastCrossfade",
                        modifier = Modifier
                            .align(Alignment.BottomStart)
                            .offset(x = 0.dp, y = -(spriteSize + 8.dp))
                    ) { text ->
                        BubbleImage(roastText = text, maxWidth = 180.dp)
                    }
                }
            }
            }
        }
    }
}

@Composable
private fun BubbleImage(roastText: String, maxWidth: androidx.compose.ui.unit.Dp = 220.dp) {
    Box(
        contentAlignment = Alignment.Center,
        modifier = Modifier.width(maxWidth)
    ) {
        Image(
            painter = painterResource(id = R.drawable.sprite_bubble),
            contentDescription = "气泡",
            modifier = Modifier.matchParentSize(),
            contentScale = ContentScale.FillBounds,
            alpha = 0.65f
        )
        Text(
            text = roastText,
            color = Color(0xFF5D4037).copy(alpha = 0.7f),
            fontSize = 14.sp,
            lineHeight = 20.sp,
            maxLines = 3,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier.padding(horizontal = 24.dp, vertical = 16.dp)
        )
    }
}

@Composable
private fun SpriteCharacter(
    expression: SpriteExpression,
    modifier: Modifier = Modifier
) {
    val imageRes = when (expression) {
        SpriteExpression.DEFAULT -> R.drawable.sprite_default
        SpriteExpression.HAPPY -> R.drawable.sprite_happy
        SpriteExpression.SAD -> R.drawable.sprite_sad
        SpriteExpression.INSPIRATION -> R.drawable.sprite_inspiration
    }

    Image(
        painter = painterResource(id = imageRes),
        contentDescription = when (expression) {
            SpriteExpression.DEFAULT -> "小精灵"
            SpriteExpression.HAPPY -> "小精灵开心"
            SpriteExpression.SAD -> "小精灵难过"
            SpriteExpression.INSPIRATION -> "小精灵灵感"
        },
        modifier = modifier,
        contentScale = ContentScale.Fit,
        alpha = 1f
    )
}
