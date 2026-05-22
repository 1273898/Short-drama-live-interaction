package com.shortdrama.interaction.data.repository

import com.shortdrama.interaction.data.model.Drama
import com.shortdrama.interaction.data.model.Episode

object DramaRepository {

    // Video server URL - change this to your server IP for LAN access
    private const val BASE_URL = "http://10.0.2.2:8080"

    private val dramas = listOf(
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
        )
    )

    fun getAllDramas(): List<Drama> = dramas

    fun getDramaById(id: Int): Drama? = dramas.find { it.id == id }

    fun getEpisodeById(episodeId: Int): Episode? =
        dramas.flatMap { it.episodes }.find { it.id == episodeId }
}
