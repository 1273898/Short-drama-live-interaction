package com.shortdrama.interaction.ui.component

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.animateIntAsState
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

@Composable
fun SpriteFeedbackBar(
    showCount: Int,
    agreeCount: Int,
    isAlwaysVisible: Boolean = false,
    onLongPressToggle: () -> Unit = {},
    modifier: Modifier = Modifier
) {
    val isEmpty = showCount == 0 && agreeCount == 0

    val animatedShowCount by animateIntAsState(
        targetValue = showCount,
        animationSpec = tween(150),
        label = "showCount"
    )
    val animatedAgreeCount by animateIntAsState(
        targetValue = agreeCount,
        animationSpec = tween(150),
        label = "agreeCount"
    )

    Row(
        modifier = modifier
            .background(Color.Black.copy(alpha = 0.5f), RoundedCornerShape(20.dp))
            .height(36.dp)
            .pointerInput(Unit) {
                detectTapGestures(onLongPress = { onLongPressToggle() })
            }
            .padding(horizontal = 16.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        AnimatedVisibility(
            visible = isEmpty && !isAlwaysVisible,
            enter = fadeIn(),
            exit = fadeOut()
        ) {
            Text(
                text = "首批吐槽即将降临",
                color = Color.White.copy(alpha = 0.8f),
                fontSize = 13.sp,
                textAlign = TextAlign.Center,
                modifier = Modifier.fillMaxWidth()
            )
        }

        AnimatedVisibility(
            visible = !isEmpty || isAlwaysVisible,
            enter = fadeIn(),
            exit = fadeOut()
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    text = "共有 $animatedAgreeCount 人认同",
                    color = Color.White,
                    fontSize = 13.sp
                )
                Text(
                    text = " | ",
                    color = Color.White.copy(alpha = 0.4f),
                    fontSize = 13.sp
                )
                Text(
                    text = "已为 $animatedShowCount 人吐槽",
                    color = Color.White,
                    fontSize = 13.sp
                )
            }
        }
    }
}