package com.shortdrama.interaction.data.network

import com.google.gson.annotations.SerializedName

data class DramaListResponse(
    @SerializedName("dramas") val dramas: List<ApiDrama>
)

data class ApiDrama(
    @SerializedName("drama_id") val dramaId: Int,
    @SerializedName("title") val title: String,
    @SerializedName("description") val description: String,
    @SerializedName("cover") val cover: String
)

data class EpisodeListResponse(
    @SerializedName("episodes") val episodes: List<ApiEpisode>
)

data class ApiEpisode(
    @SerializedName("episode_id") val episodeId: Int,
    @SerializedName("title") val title: String,
    @SerializedName("episode_number") val episodeNumber: Int,
    @SerializedName("video_url") val videoUrl: String
)
