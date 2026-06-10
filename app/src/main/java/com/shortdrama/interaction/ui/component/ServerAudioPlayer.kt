package com.shortdrama.interaction.ui.component

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFocusRequest
import android.media.AudioManager
import android.media.MediaPlayer
import android.util.Log
import java.util.concurrent.atomic.AtomicBoolean

/**
 * 服务端 TTS 音频播放器：独立于 ExoPlayer 的 MediaPlayer 实例。
 * 音频焦点策略与 SpriteTtsManager 完全一致：
 * USAGE_ASSISTANT + AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK
 */
class ServerAudioPlayer(
    private val context: Context,
    private val onPlaybackComplete: () -> Unit
) {
    private var mediaPlayer: MediaPlayer? = null
    private val audioManager = context.getSystemService(Context.AUDIO_SERVICE) as AudioManager
    private var focusRequest: AudioFocusRequest? = null
    @Volatile private var hasFocus = false
    private val isPlaying = AtomicBoolean(false)

    fun play(audioUrl: String) {
        if (audioUrl.isBlank()) {
            Log.w("ServerAudioPlayer", "play: audioUrl 为空，跳过")
            onPlaybackComplete()
            return
        }

        // 停止上一次播放
        stop()

        // 请求音频焦点
        if (!requestFocus()) {
            Log.w("ServerAudioPlayer", "音频焦点请求失败，降级")
            onPlaybackComplete()
            return
        }

        Log.d("ServerAudioPlayer", "play: $audioUrl")
        try {
            mediaPlayer = MediaPlayer().apply {
                setAudioAttributes(
                    AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_ASSISTANT)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                        .build()
                )
                setOnPreparedListener {
                    try {
                        Log.d("ServerAudioPlayer", "onPrepared, 开始播放")
                        this@ServerAudioPlayer.isPlaying.set(true)
                        start()
                    } catch (e: IllegalStateException) {
                        Log.w("ServerAudioPlayer", "onPrepared: MediaPlayer 已释放")
                    }
                }
                setOnCompletionListener {
                    Log.d("ServerAudioPlayer", "onCompletion")
                    if (this@ServerAudioPlayer.isPlaying.compareAndSet(true, false)) {
                        abandonFocus()
                        onPlaybackComplete()
                    }
                }
                setOnErrorListener { _, what, extra ->
                    Log.w("ServerAudioPlayer", "onError: what=$what, extra=$extra")
                    if (this@ServerAudioPlayer.isPlaying.compareAndSet(true, false)) {
                        abandonFocus()
                        onPlaybackComplete()
                    }
                    true
                }
                setDataSource(audioUrl)
                prepareAsync()
            }
        } catch (e: Exception) {
            Log.e("ServerAudioPlayer", "播放异常: ${e.message}", e)
            if (isPlaying.compareAndSet(true, false)) {
                abandonFocus()
            }
            onPlaybackComplete()
        }
    }

    fun stop() {
        val wasPlaying = isPlaying.getAndSet(false)
        try {
            mediaPlayer?.let {
                if (it.isPlaying) it.stop()
                it.release()
            }
        } catch (_: Exception) {}
        mediaPlayer = null
        // 强制释放焦点
        try {
            focusRequest?.let { audioManager.abandonAudioFocusRequest(it) }
            hasFocus = false
        } catch (_: Exception) {}
        // 安全兜底：stop 后不触发 onCompletion 时手动通知
        if (wasPlaying) {
            Log.d("ServerAudioPlayer", "stop: 手动触发 onPlaybackComplete 兜底")
            onPlaybackComplete()
        }
    }

    fun release() {
        stop()
    }

    private fun requestFocus(): Boolean {
        abandonFocus()
        return try {
            val req = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK)
                .setAudioAttributes(
                    AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_ASSISTANT)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                        .build()
                )
                .setOnAudioFocusChangeListener { focusChange ->
                    Log.d("ServerAudioPlayer", "焦点变化: $focusChange")
                }
                .build()
            focusRequest = req
            val result = audioManager.requestAudioFocus(req)
            hasFocus = result == AudioManager.AUDIOFOCUS_REQUEST_GRANTED
            Log.d("ServerAudioPlayer", "请求焦点: ${if (hasFocus) "成功" else "失败"}")
            hasFocus
        } catch (e: Exception) {
            Log.w("ServerAudioPlayer", "请求焦点异常", e)
            false
        }
    }

    private fun abandonFocus() {
        if (!hasFocus) return
        try {
            focusRequest?.let { audioManager.abandonAudioFocusRequest(it) }
            hasFocus = false
            Log.d("ServerAudioPlayer", "释放焦点")
        } catch (e: Exception) {
            Log.w("ServerAudioPlayer", "释放焦点异常", e)
        }
    }
}
