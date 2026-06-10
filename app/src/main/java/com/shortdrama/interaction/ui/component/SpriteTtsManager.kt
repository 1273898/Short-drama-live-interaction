package com.shortdrama.interaction.ui.component

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFocusRequest
import android.media.AudioManager
import android.media.ToneGenerator
import android.os.Bundle
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.util.Log
import java.util.Locale

class SpriteTtsManager(private val context: Context) {
    private var tts: TextToSpeech? = null
    @Volatile private var isReady = false
    private var volume: Float = 0.7f

    private val audioManager = context.getSystemService(Context.AUDIO_SERVICE) as AudioManager
    private var focusRequest: AudioFocusRequest? = null
    @Volatile private var hasFocus = false
    private var toneGenerator: ToneGenerator? = null

    fun initialize() {
        tts = TextToSpeech(context) { status ->
            if (status == TextToSpeech.SUCCESS) {
                val result = tts?.setLanguage(Locale.CHINESE)
                if (result == TextToSpeech.LANG_MISSING_DATA || result == TextToSpeech.LANG_NOT_SUPPORTED) {
                    Log.w("SpriteTts", "Chinese language not supported, falling back to default")
                    tts?.setLanguage(Locale.getDefault())
                }
                tts?.setPitch(1.5f)
                tts?.setSpeechRate(0.9f)

                // TTS 播报结束时释放音频焦点
                tts?.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
                    override fun onStart(utteranceId: String) {
                        Log.d("AudioDiag", "TTS 开始: $utteranceId")
                    }
                    override fun onDone(utteranceId: String) {
                        Log.d("AudioDiag", "TTS 完成: $utteranceId，释放焦点")
                        abandonFocus()
                    }
                    @Deprecated("Deprecated in Java")
                    override fun onError(utteranceId: String) {
                        Log.w("AudioDiag", "TTS 错误(deprecated): $utteranceId，释放焦点")
                        abandonFocus()
                    }
                    override fun onError(utteranceId: String, errorCode: Int) {
                        Log.w("AudioDiag", "TTS 错误: $utteranceId, code=$errorCode，释放焦点")
                        abandonFocus()
                    }
                })

                // 尝试选择女声
                try {
                    val voices = tts?.voices
                    val femaleVoice = voices?.find { voice ->
                        voice.locale.language == "zh" &&
                        voice.name.contains("female", ignoreCase = true)
                    } ?: voices?.find { voice ->
                        voice.locale.language == "zh"
                    }
                    if (femaleVoice != null) {
                        tts?.voice = femaleVoice
                        Log.d("SpriteTts", "选择语音: ${femaleVoice.name}")
                    }
                } catch (e: Exception) {
                    Log.w("SpriteTts", "语音选择失败，使用默认", e)
                }

                isReady = true
                Log.d("SpriteTts", "TTS initialized, pitch=1.5, rate=0.9, volume=$volume")
            } else {
                Log.e("SpriteTts", "TTS initialization failed with status: $status")
            }
        }
    }

    private fun requestFocus(): Boolean {
        // 清理可能残留的旧焦点（由上一次 onError/onDone 异步回调设置的）
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
                    Log.d("AudioDiag", "TTS 焦点变化: $focusChange")
                }
                .build()
            focusRequest = req
            val result = audioManager.requestAudioFocus(req)
            hasFocus = result == AudioManager.AUDIOFOCUS_REQUEST_GRANTED
            Log.d("AudioDiag", "TTS 请求焦点: ${if (hasFocus) "成功" else "失败"}")
            hasFocus
        } catch (e: Exception) {
            Log.w("AudioDiag", "TTS 请求焦点异常", e)
            false
        }
    }

    private fun abandonFocus() {
        if (!hasFocus) return
        try {
            focusRequest?.let { audioManager.abandonAudioFocusRequest(it) }
            hasFocus = false
            Log.d("AudioDiag", "TTS 释放焦点 → 视频应收到 AUDIOFOCUS_GAIN")
        } catch (e: Exception) {
            Log.w("AudioDiag", "TTS 释放焦点异常", e)
        }
    }

    fun speak(text: String) {
        if (!isReady || text.isBlank()) return

        // 停止上一次播报（onError 回调会异步 abandonFocus，requestFocus 内部也会先清理）
        try { tts?.stop() } catch (_: Exception) {}

        // 请求临时音频焦点（会让视频降低音量），内部先 abandon 旧焦点再请求新焦点
        requestFocus()

        val utteranceId = "roast_${System.currentTimeMillis()}"
        val params = Bundle().apply {
            putFloat(TextToSpeech.Engine.KEY_PARAM_VOLUME, volume)
            putString(TextToSpeech.Engine.KEY_PARAM_UTTERANCE_ID, utteranceId)
        }
        try {
            val result = tts?.speak(text, TextToSpeech.QUEUE_FLUSH, params, utteranceId)
            if (result == TextToSpeech.ERROR) {
                Log.w("SpriteTts", "TTS speak failed, retrying without params")
                tts?.speak(text, TextToSpeech.QUEUE_FLUSH, null, utteranceId)
            }
        } catch (e: Exception) {
            Log.e("SpriteTts", "TTS speak exception", e)
            abandonFocus()
        }
    }

    fun playNotificationSound() {
        try {
            if (toneGenerator == null) {
                toneGenerator = ToneGenerator(AudioManager.STREAM_NOTIFICATION, 80)
            }
            toneGenerator?.startTone(ToneGenerator.TONE_PROP_ACK, 200)
            Log.d("SpriteTts", "提示音播放")
        } catch (e: Exception) {
            Log.w("SpriteTts", "提示音播放失败", e)
        }
    }

    fun stop() {
        try { tts?.stop() } catch (_: Exception) {}
        abandonFocus()
    }

    fun setVolume(v: Float) {
        volume = v.coerceIn(0f, 1f)
    }

    fun getVolume(): Float = volume

    fun shutdown() {
        abandonFocus()
        tts?.stop()
        tts?.shutdown()
        tts = null
        isReady = false
        try { toneGenerator?.release(); toneGenerator = null } catch (_: Exception) {}
    }
}