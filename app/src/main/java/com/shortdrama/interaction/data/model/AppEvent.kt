package com.shortdrama.interaction.data.model

sealed class AppEvent {
    data class ShowSnackbar(
        val message: String,
        val actionLabel: String? = null,
        val onAction: (() -> Unit)? = null
    ) : AppEvent()

    data class ShowToast(val message: String) : AppEvent()
}
