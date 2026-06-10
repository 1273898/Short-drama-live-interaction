package com.shortdrama.interaction.ui.component

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp

val TONE_OPTIONS = listOf("热血", "搞笑", "温情", "反转", "悬疑")

@OptIn(ExperimentalLayoutApi::class, ExperimentalMaterial3Api::class)
@Composable
fun SubmitBranchDialogContent(
    isSubmitting: Boolean,
    rejectionReason: String?,
    onSubmit: (title: String, description: String, tone: String, preview: String) -> Unit,
    modifier: Modifier = Modifier
) {
    var title by remember { mutableStateOf("") }
    var description by remember { mutableStateOf("") }
    var selectedTone by remember { mutableStateOf("") }
    var preview by remember { mutableStateOf("") }

    val textFieldColors = OutlinedTextFieldDefaults.colors(
        focusedTextColor = Color.White,
        unfocusedTextColor = Color.White,
        focusedBorderColor = MaterialTheme.colorScheme.primary,
        unfocusedBorderColor = Color.White.copy(alpha = 0.3f),
        cursorColor = Color.White,
    )

    Column(modifier = modifier.padding(16.dp)) {
        Text(
            text = "创作你的剧情分支",
            style = MaterialTheme.typography.titleLarge,
            fontWeight = FontWeight.Bold,
            color = Color.White
        )
        Spacer(modifier = Modifier.height(16.dp))

        OutlinedTextField(
            value = title,
            onValueChange = { if (it.length <= 10) title = it },
            label = { Text("分支标题", color = Color.White.copy(alpha = 0.6f)) },
            placeholder = { Text("10字以内", color = Color.White.copy(alpha = 0.3f)) },
            modifier = Modifier.fillMaxWidth(),
            singleLine = true,
            colors = textFieldColors
        )
        Spacer(modifier = Modifier.height(12.dp))

        OutlinedTextField(
            value = description,
            onValueChange = { if (it.length <= 50) description = it },
            label = { Text("剧情描述", color = Color.White.copy(alpha = 0.6f)) },
            placeholder = { Text("50字以内", color = Color.White.copy(alpha = 0.3f)) },
            modifier = Modifier.fillMaxWidth(),
            maxLines = 3,
            colors = textFieldColors
        )
        Spacer(modifier = Modifier.height(12.dp))

        Text(
            text = "选择风格",
            style = MaterialTheme.typography.labelMedium,
            color = Color.White.copy(alpha = 0.6f)
        )
        Spacer(modifier = Modifier.height(4.dp))
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            TONE_OPTIONS.forEach { tone ->
                FilterChip(
                    selected = selectedTone == tone,
                    onClick = { selectedTone = if (selectedTone == tone) "" else tone },
                    label = { Text(tone) },
                    colors = FilterChipDefaults.filterChipColors(
                        selectedContainerColor = MaterialTheme.colorScheme.primary,
                        selectedLabelColor = Color.White,
                        containerColor = Color.White.copy(alpha = 0.08f),
                        labelColor = Color.White.copy(alpha = 0.7f),
                    )
                )
            }
        }
        Spacer(modifier = Modifier.height(12.dp))

        OutlinedTextField(
            value = preview,
            onValueChange = { if (it.length <= 30) preview = it },
            label = { Text("走向预览", color = Color.White.copy(alpha = 0.6f)) },
            placeholder = { Text("30字以内", color = Color.White.copy(alpha = 0.3f)) },
            modifier = Modifier.fillMaxWidth(),
            singleLine = true,
            colors = textFieldColors
        )

        if (rejectionReason != null) {
            Spacer(modifier = Modifier.height(8.dp))
            Text(
                text = "审核未通过: $rejectionReason",
                style = MaterialTheme.typography.bodySmall,
                color = Color(0xFFFF4444)
            )
        }
        Spacer(modifier = Modifier.height(16.dp))

        Button(
            onClick = { onSubmit(title, description, selectedTone, preview) },
            modifier = Modifier.fillMaxWidth(),
            enabled = title.isNotBlank() && description.isNotBlank() && selectedTone.isNotBlank() && !isSubmitting,
            shape = RoundedCornerShape(12.dp),
            colors = ButtonDefaults.buttonColors(
                containerColor = MaterialTheme.colorScheme.primary,
                contentColor = Color.White,
            )
        ) {
            if (isSubmitting) {
                CircularProgressIndicator(
                    modifier = Modifier.height(20.dp),
                    color = Color.White,
                    strokeWidth = 2.dp
                )
            } else {
                Text("提交审核", fontWeight = FontWeight.Bold)
            }
        }
    }
}
