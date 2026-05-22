package com.shortdrama.interaction.viewmodel

import androidx.lifecycle.ViewModel
import com.shortdrama.interaction.data.model.Drama
import com.shortdrama.interaction.data.repository.DramaRepository

class DramaListViewModel : ViewModel() {

    val dramas: List<Drama> = DramaRepository.getAllDramas()
}
