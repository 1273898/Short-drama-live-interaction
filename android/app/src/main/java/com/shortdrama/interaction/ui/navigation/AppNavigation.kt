package com.shortdrama.interaction.ui.navigation

import androidx.compose.runtime.Composable
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.NavType
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import androidx.navigation.navArgument
import com.shortdrama.interaction.ui.screen.DramaListScreen
import com.shortdrama.interaction.ui.screen.PlayerScreen
import com.shortdrama.interaction.viewmodel.PlayerViewModel

@Composable
fun AppNavigation() {
    val navController = rememberNavController()

    NavHost(
        navController = navController,
        startDestination = "drama_list"
    ) {
        composable("drama_list") {
            DramaListScreen(
                onDramaClick = { drama ->
                    val firstEpisode = drama.episodes.firstOrNull()
                    if (firstEpisode != null) {
                        navController.navigate("player/${drama.id}/${firstEpisode.id}")
                    }
                }
            )
        }

        composable(
            route = "player/{dramaId}/{episodeId}",
            arguments = listOf(
                navArgument("dramaId") { type = NavType.IntType },
                navArgument("episodeId") { type = NavType.IntType }
            )
        ) {
            val playerViewModel: PlayerViewModel = viewModel()
            PlayerScreen(
                viewModel = playerViewModel,
                onBack = { navController.popBackStack() }
            )
        }
    }
}
