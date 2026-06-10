package com.shortdrama.interaction.data.repository

import android.content.Context
import android.provider.Settings
import com.shortdrama.interaction.data.model.*
import com.shortdrama.interaction.data.network.BranchApiClient
import kotlinx.coroutines.delay

class BranchRepository(private val context: Context) {

    private val api = BranchApiClient.branchApi

    fun getUserId(): String {
        return Settings.Secure.getString(context.contentResolver, Settings.Secure.ANDROID_ID)
            ?: "anonymous"
    }

    suspend fun getBranches(episodeId: Int): BranchResponse {
        return api.getBranches(episodeId, getUserId())
    }

    suspend fun submitBranch(
        episodeId: Int,
        title: String,
        description: String,
        tone: String,
        preview: String
    ): SubmitBranchResponse {
        return api.submitBranch(
            episodeId,
            SubmitBranchRequest(
                userId = getUserId(),
                title = title,
                description = description,
                tone = tone,
                preview = preview
            )
        )
    }

    suspend fun voteBranch(branchId: Int): VoteResponse {
        return api.voteBranch(branchId, VoteRequest(userId = getUserId()))
    }

    suspend fun triggerGeneration(episodeId: Int) {
        try {
            api.triggerGeneration(episodeId)
        } catch (_: Exception) {}
    }

    suspend fun pollForBranches(
        episodeId: Int,
        maxAttempts: Int = 40,
        intervalMs: Long = 3000
    ): BranchResponse? {
        repeat(maxAttempts) {
            delay(intervalMs)
            val response = getBranches(episodeId)
            if (response.hasBranches) {
                return response
            }
        }
        return null
    }
}
