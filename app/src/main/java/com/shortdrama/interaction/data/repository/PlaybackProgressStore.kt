package com.shortdrama.interaction.data.repository

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.intPreferencesKey
import androidx.datastore.preferences.core.longPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import com.google.gson.Gson
import com.google.gson.reflect.TypeToken
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map

private val Context.playbackDataStore: DataStore<Preferences> by preferencesDataStore(name = "playback_progress")

enum class AudioMode(val value: Int) {
    OFF(0),           // 静音：无任何额外声音
    NOTIFICATION(1),  // 仅提示音：短促"叮"声，不压低视频
    ON(2);            // 开启：TTS 自动播报，压低视频音量

    companion object {
        fun fromValue(v: Int) = entries.firstOrNull { it.value == v } ?: NOTIFICATION
    }
}

class PlaybackProgressStore(private val context: Context) {

    private fun keyForEpisode(episodeId: Int) = longPreferencesKey("pos_$episodeId")

    suspend fun getProgress(episodeId: Int): Long {
        return context.playbackDataStore.data.map { prefs ->
            prefs[keyForEpisode(episodeId)] ?: 0L
        }.first()
    }

    suspend fun saveProgress(episodeId: Int, positionMs: Long) {
        context.playbackDataStore.edit { prefs ->
            prefs[keyForEpisode(episodeId)] = positionMs
        }
    }

    private val MASCOT_ALWAYS_VISIBLE = booleanPreferencesKey("mascot_always_visible")

    suspend fun saveMascotAlwaysVisible(value: Boolean) {
        context.playbackDataStore.edit { prefs ->
            prefs[MASCOT_ALWAYS_VISIBLE] = value
        }
    }

    suspend fun getMascotAlwaysVisible(): Boolean {
        return context.playbackDataStore.data.map { prefs ->
            prefs[MASCOT_ALWAYS_VISIBLE] ?: false
        }.first()
    }

    private val AUDIO_MODE = intPreferencesKey("audio_mode")

    suspend fun saveAudioMode(mode: AudioMode) {
        context.playbackDataStore.edit { prefs ->
            prefs[AUDIO_MODE] = mode.value
        }
    }

    suspend fun getAudioMode(): AudioMode {
        return context.playbackDataStore.data.map { prefs ->
            AudioMode.fromValue(prefs[AUDIO_MODE] ?: AudioMode.NOTIFICATION.value)
        }.first()
    }

    private fun emotionWeightsKey(episodeId: Int) =
        stringPreferencesKey("ew_$episodeId")

    private val gson = Gson()

    suspend fun saveEmotionWeights(episodeId: Int, weights: Map<String, Int>) {
        val json = gson.toJson(weights)
        context.playbackDataStore.edit { prefs ->
            prefs[emotionWeightsKey(episodeId)] = json
        }
    }

    suspend fun getEmotionWeights(episodeId: Int): Map<String, Int> {
        val json = context.playbackDataStore.data.map { prefs ->
            prefs[emotionWeightsKey(episodeId)] ?: ""
        }.first()
        if (json.isBlank()) return emptyMap()
        return try {
            gson.fromJson(json, object : TypeToken<Map<String, Int>>() {}.type) ?: emptyMap()
        } catch (_: Exception) {
            emptyMap()
        }
    }
}
