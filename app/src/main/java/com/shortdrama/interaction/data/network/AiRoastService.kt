package com.shortdrama.interaction.data.network

import android.util.Log
import com.google.gson.annotations.SerializedName
import com.shortdrama.interaction.BuildConfig
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import okhttp3.OkHttpClient
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import retrofit2.http.Body
import retrofit2.http.Header
import retrofit2.http.POST
import java.util.concurrent.TimeUnit

// ===== Anthropic Messages API 请求/响应模型 =====

data class AnthropicRequest(
    @SerializedName("model") val model: String,
    @SerializedName("max_tokens") val maxTokens: Int,
    @SerializedName("system") val system: String,
    @SerializedName("messages") val messages: List<AnthropicMessage>
)

data class AnthropicMessage(
    @SerializedName("role") val role: String,
    @SerializedName("content") val content: String
)

data class AnthropicResponse(
    @SerializedName("content") val content: List<AnthropicContent>
)

data class AnthropicContent(
    @SerializedName("type") val type: String,
    @SerializedName("text") val text: String
)

// ===== 吐槽结果模型 =====

data class RoastResult(
    val emotionTag: String,
    val text: String
)

// ===== Retrofit 接口 =====

interface AiRoastApi {
    @POST("v1/messages")
    suspend fun messages(
        @Header("x-api-key") apiKey: String,
        @Header("anthropic-version") version: String = "2023-06-01",
        @Header("content-type") contentType: String = "application/json",
        @Body request: AnthropicRequest
    ): AnthropicResponse
}

// ===== 服务类 =====

class AiRoastService {
    companion object {
        private const val BASE_URL = "https://token-plan-cn.xiaomimomo.com/anthropic/"
        private const val MODEL = "mimo-v2.5-pro"
        private const val TIMEOUT_MS = 15000L
        private const val MAX_CACHE_SIZE = 50

        // 共享 OkHttpClient + Retrofit 实例，复用连接池
        private val sharedClient = OkHttpClient.Builder()
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(TIMEOUT_MS, TimeUnit.MILLISECONDS)
            .build()

        private val sharedRetrofit = Retrofit.Builder()
            .baseUrl(BASE_URL)
            .client(sharedClient)
            .addConverterFactory(GsonConverterFactory.create())
            .build()

        private val sharedApi = sharedRetrofit.create(AiRoastApi::class.java)

        fun getRoastCountForType(type: String): Int = when (type) {
            "L1" -> 3
            "L2" -> 2
            "L3" -> 1
            else -> 0
        }
    }

    private val api = sharedApi

    private val cache = java.util.concurrent.ConcurrentHashMap<String, List<RoastResult>>()

    suspend fun generateRoasts(
        videoId: String,
        highlightTimestamp: Long,
        dramaTitle: String,
        highlightType: String,
        episodeTitle: String = "",
        dramaDescription: String = "",
        roastText: String = ""
    ): List<RoastResult> {
        val count = getRoastCountForType(highlightType)
        if (count <= 0) {
            Log.d("AiRoast", "L4 高光点，不请求 AI")
            return emptyList()
        }

        if (BuildConfig.AI_API_KEY.isBlank()) {
            Log.w("AiRoast", "AI_API_KEY 未配置，降级")
            return emptyList()
        }

        val cacheKey = "${videoId}_${highlightTimestamp}"
        cache[cacheKey]?.let {
            Log.d("AiRoast", "内存缓存命中: $cacheKey, ${it.size} 条")
            return it
        }

        return withContext(Dispatchers.IO) {
            try {
                val result = withTimeoutOrNull(TIMEOUT_MS) {
                    val systemPrompt = buildSystemPrompt(dramaTitle, episodeTitle, dramaDescription)
                    val prompt = buildPrompt(dramaTitle, highlightType, roastText, count)
                    Log.d("AiRoast", "发送请求: model=$MODEL, count=$count, highlightType=$highlightType")
                    val request = AnthropicRequest(
                        model = MODEL,
                        maxTokens = 200,
                        system = systemPrompt,
                        messages = listOf(
                            AnthropicMessage(role = "user", content = prompt)
                        )
                    )
                    val response = api.messages(apiKey = BuildConfig.AI_API_KEY, request = request)
                    val text = response.content.firstOrNull()?.text?.trim() ?: ""
                    Log.d("AiRoast", "AI 返回原文: [$text]")
                    parseRoasts(text)
                }

                if (!result.isNullOrEmpty()) {
                    // 驱逐策略：超过上限时移除最早插入的条目
                    if (cache.size >= MAX_CACHE_SIZE) {
                        val oldest = cache.keys.first()
                        cache.remove(oldest)
                        Log.d("AiRoast", "缓存驱逐: $oldest")
                    }
                    cache[cacheKey] = result
                    Log.d("AiRoast", "AI 解析成功: ${result.size} 条, tags=${result.map { it.emotionTag }}")
                    result
                } else {
                    Log.w("AiRoast", "AI 超时或返回空")
                    emptyList()
                }
            } catch (e: Exception) {
                Log.e("AiRoast", "AI 请求失败", e)
                emptyList()
            }
        }
    }

    private fun parseRoasts(text: String): List<RoastResult> {
        val results = mutableListOf<RoastResult>()
        for (line in text.lines()) {
            val trimmed = line.trim()
            if (trimmed.isBlank()) continue
            val tagEnd = trimmed.indexOf(']')
            if (trimmed.startsWith('[') && tagEnd > 1) {
                val tag = trimmed.substring(1, tagEnd).trim()
                val content = trimmed.substring(tagEnd + 1).trim()
                if (tag.isNotBlank() && content.isNotBlank()) {
                    results.add(RoastResult(emotionTag = tag, text = content))
                }
            } else if (trimmed.isNotBlank()) {
                results.add(RoastResult(emotionTag = "吐槽", text = trimmed))
            }
        }
        return results
    }

    private fun buildSystemPrompt(
        dramaTitle: String,
        episodeTitle: String,
        dramaDescription: String
    ): String {
        val descPart = if (dramaDescription.isNotBlank()) "剧情简介：$dramaDescription\n" else ""
        return """你是正在和用户一起实时看短剧《$dramaTitle》（$episodeTitle）的精灵"灵灵"。
${descPart}
你的任务：针对刚刚发生的剧情瞬间，发表像朋友聊天一样的即时反应。

严格要求：
1. 必须引用具体人物名字（如角色名、称呼）或具体动作/台词，禁止泛泛而谈
2. 禁止使用"这剧情"、"这段"、"这部剧"、"太刺激了"等笼统表述
3. 每条不超过20字，口语化，有梗
4. 要有追剧的代入感，像真的在跟朋友一起看

输出格式：每条以[情绪标签]开头，换行分隔。
可用标签：震惊、无语、吃瓜、爆笑、感动、心疼、期待、愤怒、嘲笑。

好的示例：
[震惊]他居然把亲爹的遗嘱撕了？
[感动]小雪为了救弟弟跪了一夜
[吃瓜]这俩人的眼神交流也太暧昧了
[无语]堂堂总裁居然被一碗面骗了
[爆笑]管家这表情笑死我了哈哈哈
[期待]感觉大哥要黑化了！"""
    }

    private fun buildPrompt(
        dramaTitle: String,
        highlightType: String,
        roastText: String,
        count: Int
    ): String {
        val typeDesc = when (highlightType) {
            "L1" -> "极高能剧情（高潮/反转/冲突爆发）"
            "L2" -> "高能剧情（关键对话/情感转折）"
            "L3" -> "精彩剧情（有趣/温馨/意外）"
            else -> "看点"
        }
        val scene = if (roastText.isNotBlank()) {
            "刚刚发生的剧情：$roastText"
        } else {
            "当前正在播放${typeDesc}片段，请根据剧名和集数推测可能的剧情并吐槽"
        }
        val countHint = if (count == 1) "请说1条你的即时反应" else "请从不同情绪角度说${count}条你的即时反应"
        return "《$dramaTitle》${scene}。$countHint（每条不超过20字，以[情绪标签]开头，必须提到具体人物或动作）："
    }

    fun clearCache() {
        cache.clear()
    }
}