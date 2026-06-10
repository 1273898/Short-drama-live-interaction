package com.shortdrama.interaction.data.network

import android.content.Context
import android.util.Log
import androidx.room.*
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase
import com.google.gson.annotations.SerializedName
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import retrofit2.http.GET
import retrofit2.http.Path
import com.google.gson.Gson
import com.google.gson.reflect.TypeToken
import java.util.concurrent.TimeUnit

private val gson = Gson()

fun List<HighlightComment>.toJson(): String = gson.toJson(this)

fun String.toHighlightCommentList(): List<HighlightComment> {
    if (this.isBlank() || this == "[]") return emptyList()
    return try {
        val type = object : TypeToken<List<HighlightComment>>() {}.type
        gson.fromJson(this, type) ?: emptyList()
    } catch (e: Exception) {
        emptyList()
    }
}

data class HighlightComment(
    @SerializedName("emotionTag") val emotionTag: String = "",
    @SerializedName("text") val text: String = ""
)

data class HighlightResponse(
    @SerializedName("video_id") val videoId: String,
    @SerializedName("highlights") val highlights: List<ServerHighlightPoint>
)

data class ServerHighlightPoint(
    @SerializedName("timestamp") val timestampSeconds: Float = 0f,
    @SerializedName("type") val type: String = "",
    @SerializedName("confidence") val confidence: Float = 0f,
    @SerializedName("start_time") val startTimeSeconds: Float = timestampSeconds,
    @SerializedName("end_time") val endTimeSeconds: Float = timestampSeconds,
    @SerializedName("roast_text") val roastText: String = "",
    @SerializedName("score") val score: Int = 2,
    @SerializedName("comments") val comments: List<HighlightComment> = emptyList(),
    @SerializedName("subtitle_context") val subtitleContext: String = ""
) {
    val timestamp: Long get() = (timestampSeconds * 1000).toLong()
    val startTime: Long get() = (startTimeSeconds * 1000).toLong()
    val endTime: Long get() = (endTimeSeconds * 1000).toLong()
}

@Entity(tableName = "highlights")
data class HighlightEntity(
    @PrimaryKey(autoGenerate = true) val id: Int = 0,
    val videoId: String,
    val timestamp: Long,
    val type: String,
    val confidence: Float,
    val startTime: Long,
    val endTime: Long,
    val level: Int = 1,
    val roastText: String = "",
    val score: Int = 2,
    val commentsJson: String = "[]",
    val cachedAt: Long = System.currentTimeMillis()
)

@Dao
interface HighlightDao {
    @Query("SELECT * FROM highlights WHERE videoId = :videoId")
    suspend fun getByVideoId(videoId: String): List<HighlightEntity>

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insertAll(highlights: List<HighlightEntity>)

    @Query("DELETE FROM highlights WHERE cachedAt < :expireTime")
    suspend fun deleteExpired(expireTime: Long)

    @Query("DELETE FROM highlights")
    suspend fun deleteAll()
}

@Entity(tableName = "roast_cache", indices = [Index("highlightKey")])
data class RoastCacheEntity(
    @PrimaryKey(autoGenerate = true) val id: Int = 0,
    val highlightKey: String,
    val episodeTitle: String,
    val dramaTitle: String,
    val highlightType: String,
    val emotionTag: String,
    val roastText: String,
    val createdAt: Long = System.currentTimeMillis()
)

@Dao
interface RoastCacheDao {
    @Query("SELECT * FROM roast_cache WHERE highlightKey = :highlightKey ORDER BY createdAt DESC")
    suspend fun getByHighlightKey(highlightKey: String): List<RoastCacheEntity>

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insertAll(roasts: List<RoastCacheEntity>)

    @Query("DELETE FROM roast_cache WHERE createdAt < :expireTime")
    suspend fun deleteExpired(expireTime: Long)
}

@Database(entities = [HighlightEntity::class, RoastCacheEntity::class], version = 10, exportSchema = false)
abstract class HighlightDatabase : RoomDatabase() {
    abstract fun highlightDao(): HighlightDao
    abstract fun roastCacheDao(): RoastCacheDao

    companion object {
        @Volatile
        private var INSTANCE: HighlightDatabase? = null

        val MIGRATION_1_2 = object : Migration(1, 2) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("ALTER TABLE highlights ADD COLUMN level INTEGER NOT NULL DEFAULT 1")
            }
        }

        val MIGRATION_2_3 = object : Migration(2, 3) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("ALTER TABLE highlights ADD COLUMN roastText TEXT NOT NULL DEFAULT ''")
            }
        }

        val MIGRATION_3_4 = object : Migration(3, 4) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("""
                    CREATE TABLE IF NOT EXISTS roast_cache (
                        id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                        highlightKey TEXT NOT NULL,
                        episodeTitle TEXT NOT NULL,
                        dramaTitle TEXT NOT NULL,
                        highlightType TEXT NOT NULL,
                        emotionTag TEXT NOT NULL,
                        roastText TEXT NOT NULL,
                        createdAt INTEGER NOT NULL DEFAULT 0
                    )
                """)
                db.execSQL("CREATE INDEX IF NOT EXISTS index_roast_cache_highlightKey ON roast_cache (highlightKey)")
            }
        }

        val MIGRATION_4_5 = object : Migration(4, 5) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("DELETE FROM roast_cache")
            }
        }

        val MIGRATION_5_6 = object : Migration(5, 6) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("ALTER TABLE highlights ADD COLUMN score INTEGER NOT NULL DEFAULT 2")
            }
        }

        val MIGRATION_6_7 = object : Migration(6, 7) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("ALTER TABLE highlights ADD COLUMN commentsJson TEXT NOT NULL DEFAULT '[]'")
            }
        }

        val MIGRATION_7_8 = object : Migration(7, 8) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("DELETE FROM highlights")
                db.execSQL("DELETE FROM roast_cache")
            }
        }

        val MIGRATION_8_9 = object : Migration(8, 9) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("DELETE FROM highlights")
                db.execSQL("DELETE FROM roast_cache")
            }
        }

        val MIGRATION_9_10 = object : Migration(9, 10) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("DELETE FROM highlights")
                db.execSQL("DELETE FROM roast_cache")
            }
        }

        fun getInstance(context: Context): HighlightDatabase {
            return INSTANCE ?: synchronized(this) {
                val instance = Room.databaseBuilder(
                    context.applicationContext,
                    HighlightDatabase::class.java,
                    "highlight_cache.db"
                )
                    .addMigrations(MIGRATION_1_2, MIGRATION_2_3, MIGRATION_3_4, MIGRATION_4_5, MIGRATION_5_6, MIGRATION_6_7, MIGRATION_7_8, MIGRATION_8_9, MIGRATION_9_10)
                    .build()
                INSTANCE = instance
                instance
            }
        }
    }
}

interface HighlightApiService {
    @GET("api/highlights/{video_id}")
    suspend fun getHighlights(@Path("video_id") videoId: String): HighlightResponse
}

object HighlightApiClient {
    private val BASE_URL = "${com.shortdrama.interaction.BuildConfig.SERVER_BASE_URL}/"

    private val okHttpClient: OkHttpClient by lazy {
        OkHttpClient.Builder()
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(30, TimeUnit.SECONDS)
            .addInterceptor(
                HttpLoggingInterceptor().apply {
                    level = if (com.shortdrama.interaction.BuildConfig.DEBUG)
                        HttpLoggingInterceptor.Level.BASIC
                    else
                        HttpLoggingInterceptor.Level.NONE
                }
            )
            .build()
    }

    private val retrofit: Retrofit by lazy {
        Retrofit.Builder()
            .baseUrl(BASE_URL)
            .client(okHttpClient)
            .addConverterFactory(GsonConverterFactory.create())
            .build()
    }

    val apiService: HighlightApiService by lazy {
        retrofit.create(HighlightApiService::class.java)
    }
}

class HighlightRepository(private val context: Context) {
    private val dao: HighlightDao by lazy { HighlightDatabase.getInstance(context).highlightDao() }
    private val roastCacheDao: RoastCacheDao by lazy { HighlightDatabase.getInstance(context).roastCacheDao() }
    private val cacheValidMs = 24 * 60 * 60 * 1000L

    suspend fun saveRoastsToRoom(
        highlightKey: String,
        episodeTitle: String,
        dramaTitle: String,
        highlightType: String,
        roasts: List<RoastResult>
    ) {
        if (roasts.isEmpty()) return
        val entities = roasts.map { roast ->
            RoastCacheEntity(
                highlightKey = highlightKey,
                episodeTitle = episodeTitle,
                dramaTitle = dramaTitle,
                highlightType = highlightType,
                emotionTag = roast.emotionTag,
                roastText = roast.text
            )
        }
        withContext(Dispatchers.IO) {
            roastCacheDao.insertAll(entities)
        }
    }

    suspend fun loadRoastsFromRoom(highlightKey: String): List<RoastCacheEntity> {
        return withContext(Dispatchers.IO) {
            roastCacheDao.getByHighlightKey(highlightKey)
        }
    }

    private val aiRoastService = AiRoastService()

    suspend fun fetchAiRoasts(
        highlightKey: String,
        videoId: String,
        highlight: ServerHighlightPoint,
        dramaTitle: String,
        episodeTitle: String,
        dramaDescription: String
    ): List<RoastCacheEntity> {
        val count = AiRoastService.getRoastCountForType(highlight.type)
        if (count <= 0) return emptyList()

        val roasts = aiRoastService.generateRoasts(
            videoId = videoId,
            highlightTimestamp = highlight.timestamp,
            dramaTitle = dramaTitle,
            highlightType = highlight.type,
            episodeTitle = episodeTitle,
            dramaDescription = dramaDescription,
            roastText = highlight.roastText
        )
        if (roasts.isNotEmpty()) {
            saveRoastsToRoom(highlightKey, episodeTitle, dramaTitle, highlight.type, roasts)
        }
        return loadRoastsFromRoom(highlightKey)
    }

    fun clearAiCache() {
        aiRoastService.clearCache()
    }

    suspend fun clearHighlightCache() {
        withContext(Dispatchers.IO) {
            dao.deleteAll()
            Log.d("HighlightRepo", "已清除所有高亮缓存")
        }
    }

    suspend fun getHighlights(videoId: String): List<ServerHighlightPoint> = withContext(Dispatchers.IO) {
        try {
            val cached = dao.getByVideoId(videoId)
            val now = System.currentTimeMillis()
            val validCache = cached.filter { (now - it.cachedAt) < cacheValidMs }

            // 缓存命中：24h 内的有效缓存直接返回（不依赖评论是否存在）
            if (validCache.isNotEmpty()) {
                Log.d("HighlightRepo", "缓存命中: videoId=$videoId, 共 ${validCache.size} 条, timestamps=${validCache.map { it.timestamp }}")
                return@withContext validCache.map { entity ->
                    ServerHighlightPoint(
                        timestampSeconds = entity.timestamp / 1000f,
                        type = entity.type,
                        confidence = entity.confidence,
                        startTimeSeconds = entity.startTime / 1000f,
                        endTimeSeconds = entity.endTime / 1000f,
                        roastText = entity.roastText,
                        score = entity.score,
                        comments = entity.commentsJson.toHighlightCommentList()
                    )
                }
            }

            Log.d("HighlightRepo", "缓存未命中: videoId=$videoId, cached=${cached.size} 条, 请求网络")
            val response = withTimeoutOrNull(15000L) {
                HighlightApiClient.apiService.getHighlights(videoId)
            }
            if (response == null) {
                Log.w("HighlightRepo", "高光 API 超时 (15s)")
                // 超时回退：优先用 24h 内缓存，其次用过期缓存
                val fallback = if (validCache.isNotEmpty()) validCache else cached
                if (fallback.isNotEmpty()) {
                    Log.d("HighlightRepo", "超时回退缓存: ${fallback.size} 条")
                    return@withContext fallback.map { entity ->
                        ServerHighlightPoint(
                            timestampSeconds = entity.timestamp / 1000f,
                            type = entity.type,
                            confidence = entity.confidence,
                            startTimeSeconds = entity.startTime / 1000f,
                            endTimeSeconds = entity.endTime / 1000f,
                            roastText = entity.roastText,
                            score = entity.score,
                            comments = entity.commentsJson.toHighlightCommentList()
                        )
                    }
                }
                Log.w("HighlightRepo", "无缓存可用，使用 fallback 数据")
                return@withContext fallbackData()
            }
            Log.d("HighlightRepo", "网络请求成功: videoId=$videoId, 共 ${response.highlights.size} 条")

            if (response.highlights.isEmpty()) {
                Log.w("HighlightRepo", "服务端返回空列表，使用模拟数据")
                return@withContext fallbackData()
            }

            val entities = response.highlights.map {
                HighlightEntity(
                    videoId = videoId,
                    timestamp = it.timestamp,
                    type = it.type,
                    confidence = it.confidence,
                    startTime = it.startTime,
                    endTime = it.endTime,
                    roastText = it.roastText,
                    score = it.score,
                    commentsJson = it.comments.toJson()
                )
            }
            dao.insertAll(entities)
            dao.deleteExpired(now - cacheValidMs)
            response.highlights
        } catch (e: Exception) {
            Log.e("HighlightRepo", "高光数据加载失败: videoId=$videoId", e)
            try {
                val cached = dao.getByVideoId(videoId)
                if (cached.isNotEmpty()) {
                    Log.d("HighlightRepo", "异常回退缓存: ${cached.size} 条")
                    return@withContext cached.map { entity ->
                        ServerHighlightPoint(
                            timestampSeconds = entity.timestamp / 1000f,
                            type = entity.type,
                            confidence = entity.confidence,
                            startTimeSeconds = entity.startTime / 1000f,
                            endTimeSeconds = entity.endTime / 1000f,
                            roastText = entity.roastText,
                            score = entity.score,
                            comments = entity.commentsJson.toHighlightCommentList()
                        )
                    }
                }
            } catch (e2: Exception) {
                Log.e("HighlightRepo", "缓存读取也失败", e2)
            }
            Log.w("HighlightRepo", "服务端不可用且无缓存，使用模拟数据")
            fallbackData()
        }
    }

    private fun fallbackData(): List<ServerHighlightPoint> = listOf(
        ServerHighlightPoint(
            timestampSeconds = 10f, type = "L2", confidence = 0.5f, roastText = "这段剧情不错", score = 2,
            comments = listOf(HighlightComment("吃瓜", "来了来了名场面来了"))
        ),
        ServerHighlightPoint(
            timestampSeconds = 25f, type = "L2", confidence = 0.7f, roastText = "高能预警来了", score = 3,
            comments = listOf(HighlightComment("震惊", "这反转也太突然了吧"))
        )
    )
}