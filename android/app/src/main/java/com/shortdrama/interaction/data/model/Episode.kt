package com.shortdrama.interaction.data.model

data class Episode(
    val id: Int,
    val dramaId: Int,
    val title: String,
    val episodeNumber: Int,
    val videoPath: String
)
