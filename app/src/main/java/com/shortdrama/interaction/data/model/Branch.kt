package com.shortdrama.interaction.data.model

import com.google.gson.annotations.SerializedName

data class Branch(
    val id: Int,
    val title: String,
    val description: String,
    val tone: String,
    val preview: String,
    @SerializedName("vote_count") val voteCount: Int,
    val source: String,
    @SerializedName("user_has_voted") val userHasVoted: Boolean,
    val comics: List<BranchComic>
)

data class BranchComic(
    @SerializedName("panel_number") val panelNumber: Int,
    @SerializedName("image_url") val imageUrl: String,
    val description: String
)

data class BranchResponse(
    @SerializedName("episode_id") val episodeId: Int,
    val branches: List<Branch>,
    @SerializedName("has_branches") val hasBranches: Boolean,
    @SerializedName("is_generating") val isGenerating: Boolean
)

data class SubmitBranchRequest(
    @SerializedName("user_id") val userId: String,
    val title: String,
    val description: String,
    val tone: String,
    val preview: String
)

data class SubmitBranchResponse(
    @SerializedName("branch_id") val branchId: Int,
    val status: String,
    @SerializedName("rejection_reason") val rejectionReason: String?
)

data class VoteRequest(
    @SerializedName("user_id") val userId: String
)

data class VoteResponse(
    @SerializedName("branch_id") val branchId: Int,
    @SerializedName("vote_count") val voteCount: Int,
    @SerializedName("already_voted") val alreadyVoted: Boolean
)
