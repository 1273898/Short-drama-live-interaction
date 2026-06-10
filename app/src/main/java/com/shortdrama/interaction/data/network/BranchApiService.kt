package com.shortdrama.interaction.data.network

import com.shortdrama.interaction.data.model.*
import retrofit2.http.*

interface BranchApiService {

    @GET("api/episodes/{episodeId}/branches")
    suspend fun getBranches(
        @Path("episodeId") episodeId: Int,
        @Query("user_id") userId: String
    ): BranchResponse

    @POST("api/episodes/{episodeId}/branches")
    suspend fun submitBranch(
        @Path("episodeId") episodeId: Int,
        @Body request: SubmitBranchRequest
    ): SubmitBranchResponse

    @POST("api/branches/{branchId}/vote")
    suspend fun voteBranch(
        @Path("branchId") branchId: Int,
        @Body request: VoteRequest
    ): VoteResponse

    @POST("api/admin/generate/{episodeId}")
    suspend fun triggerGeneration(
        @Path("episodeId") episodeId: Int
    ): Map<String, Any>
}
