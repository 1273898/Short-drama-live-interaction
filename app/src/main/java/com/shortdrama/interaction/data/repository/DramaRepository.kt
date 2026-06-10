package com.shortdrama.interaction.data.repository

import android.util.Log
import com.shortdrama.interaction.data.model.Drama
import com.shortdrama.interaction.data.model.Episode
import com.shortdrama.interaction.data.network.DramaApiService
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import okhttp3.OkHttpClient
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import java.util.concurrent.TimeUnit

object DramaRepository {

    private val BASE_URL = com.shortdrama.interaction.BuildConfig.SERVER_BASE_URL
    private const val TIMEOUT_MS = 10000L

    private var cachedDramas: List<Drama>? = null

    private val _hardcodedDramas: List<Drama> by lazy { listOf(
        Drama(
            id = 1,
            title = "北派寻宝笔记",
            description = "神秘的北派寻宝之旅，探寻失落的宝藏",
            coverUrl = "$BASE_URL/北派寻宝笔记/cover/9472672297f3cdd1324733ff87ba1273~tplv-s85hriknmn-jpeg.jpg",
            episodes = (63..72).map { num ->
                Episode(
                    id = (num - 63) + 1,
                    dramaId = 1,
                    title = "第${num}集",
                    episodeNumber = num,
                    videoPath = "$BASE_URL/北派寻宝笔记/第${num}集.mp4"
                )
            }
        ),
        Drama(
            id = 2,
            title = "天下第一纨绔",
            description = "纨绔子弟逆袭成长的热血故事",
            coverUrl = "$BASE_URL/天下第一纨绔/cover/689333212bd7d566acd343d404d4b454~tplv-s85hriknmn-jpeg.jpg",
            episodes = (1..10).map { num ->
                Episode(
                    id = 100 + num,
                    dramaId = 2,
                    title = "第${num}集",
                    episodeNumber = num,
                    videoPath = "$BASE_URL/天下第一纨绔/第${num}集.mp4"
                )
            }
        ))
    }

    fun getHardcodedDramas(): List<Drama> = _hardcodedDramas

    private val api: DramaApiService by lazy {
        val client = OkHttpClient.Builder()
            .connectTimeout(TIMEOUT_MS, TimeUnit.MILLISECONDS)
            .readTimeout(TIMEOUT_MS, TimeUnit.MILLISECONDS)
            .build()
        Retrofit.Builder()
            .baseUrl("$BASE_URL/")
            .client(client)
            .addConverterFactory(GsonConverterFactory.create())
            .build()
            .create(DramaApiService::class.java)
    }

    suspend fun getAllDramas(): List<Drama> = withContext(Dispatchers.IO) {
        try {
            val result = withTimeoutOrNull(TIMEOUT_MS) {
                val response = api.getDramas()
                coroutineScope {
                    response.dramas.map { apiDrama ->
                        async {
                            val episodesResp = api.getEpisodes(apiDrama.dramaId)
                            Drama(
                                id = apiDrama.dramaId,
                                title = apiDrama.title,
                                description = apiDrama.description,
                                coverUrl = "$BASE_URL/${apiDrama.cover.trimStart('/')}",
                                episodes = episodesResp.episodes.map { ep ->
                                    Episode(
                                        id = ep.episodeId,
                                        dramaId = apiDrama.dramaId,
                                        title = ep.title,
                                        episodeNumber = ep.episodeNumber,
                                        videoPath = "$BASE_URL/${ep.videoUrl.trimStart('/')}"
                                    )
                                }
                            )
                        }
                    }.awaitAll()
                }
            }
            result?.let {
                Log.d("DramaRepo", "服务端加载成功: ${it.size} 部剧")
                cachedDramas = it
                it
            } ?: run {
                Log.w("DramaRepo", "服务端返回空或超时，使用硬编码数据")
                getHardcodedDramas()
            }
        } catch (e: Exception) {
            Log.e("DramaRepo", "服务端请求失败", e)
            getHardcodedDramas()
        }
    }

    fun getCachedDramaById(id: Int): Drama? =
        cachedDramas?.find { it.id == id } ?: getHardcodedDramas().find { it.id == id }
}