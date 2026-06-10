package com.shortdrama.interaction.ui.navigation

import android.app.Application
import android.util.Log
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.platform.LocalContext
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
                onEpisodeClick = { dramaId, episodeId ->
                    navController.navigate("player/$dramaId/$episodeId")
                }
            )
        }

        composable(
            route = "player/{dramaId}/{episodeId}",
            arguments = listOf(
                navArgument("dramaId") { type = NavType.IntType },
                navArgument("episodeId") { type = NavType.IntType }
            )
        ) { backStackEntry ->
            val entryStart = remember { System.currentTimeMillis() }
            val context = LocalContext.current
            val dramaId = backStackEntry.arguments?.getInt("dramaId") ?: 1
            val episodeId = backStackEntry.arguments?.getInt("episodeId") ?: 0
            val branchRepository = remember { com.shortdrama.interaction.data.repository.BranchRepository(context.applicationContext) }
            val playerViewModel: PlayerViewModel = viewModel(
                factory = PlayerViewModel.Factory(
                    context.applicationContext as Application,
                    dramaId,
                    episodeId,
                    branchRepository
                )
            )
            Log.d("Perf", "[Perf] Nav composable entry: ${System.currentTimeMillis() - entryStart}ms")
            PlayerScreen(
                viewModel = playerViewModel,
                onBack = { navController.popBackStack() }
            )
        }
    }
}
