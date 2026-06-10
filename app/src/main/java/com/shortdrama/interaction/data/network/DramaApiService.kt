package com.shortdrama.interaction.data.network

import retrofit2.http.GET
import retrofit2.http.Path

interface DramaApiService {
    @GET("api/dramas")
    suspend fun getDramas(): DramaListResponse

    @GET("api/dramas/{dramaId}/episodes")
    suspend fun getEpisodes(@Path("dramaId") dramaId: Int): EpisodeListResponse
}
