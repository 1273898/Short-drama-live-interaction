package com.shortdrama.interaction.data.model

data class Drama(
    val id: Int,
    val title: String,
    val description: String,
    val coverUrl: String,
    val episodes: List<Episode>
)
