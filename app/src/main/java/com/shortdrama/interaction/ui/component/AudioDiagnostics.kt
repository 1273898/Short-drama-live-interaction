package com.shortdrama.interaction.ui.component

import android.util.Log
import androidx.media3.exoplayer.ExoPlayer

/**
 * 音频诊断工具：统一 TAG "AudioDiag"，用于 logcat 过滤定位音频丢失环节。
 */
class AudioDiagnostics(private val tag: String = "AudioDiag") {

    @Volatile private var lastFocusLossTime = 0L
    @Volatile private var lastFocusGainTime = 0L
    @Volatile private var lastVolumeRestoreTime = 0L
    @Volatile private var lastPlaybackRestoreTime = 0L
    @Volatile private var focusLossCount = 0
    @Volatile private var volumeRestoreCount = 0
    @Volatile private var playbackRestoreCount = 0
    @Volatile private var healthCheckCount = 0

    fun onFocusLost(reason: String) {
        lastFocusLossTime = System.currentTimeMillis()
        focusLossCount++
        Log.w(tag, "焦点丢失 #$focusLossCount: reason=$reason, t=${lastFocusLossTime}")
    }

    fun onFocusGained() {
        lastFocusGainTime = System.currentTimeMillis()
        val lossDuration = if (lastFocusLossTime > 0) lastFocusGainTime - lastFocusLossTime else 0
        Log.d(tag, "焦点恢复: lossDuration=${lossDuration}ms, totalLosses=$focusLossCount")
        lastFocusLossTime = 0L
    }

    fun onVolumeRestore(from: Float, to: Float) {
        lastVolumeRestoreTime = System.currentTimeMillis()
        volumeRestoreCount++
        Log.d(tag, "音量恢复 #$volumeRestoreCount: $from → $to, t=${lastVolumeRestoreTime}")
    }

    fun onPlaybackRestore(reason: String) {
        lastPlaybackRestoreTime = System.currentTimeMillis()
        playbackRestoreCount++
        Log.d(tag, "播放恢复 #$playbackRestoreCount: reason=$reason, t=${lastPlaybackRestoreTime}")
    }

    /**
     * 周期性音频健康检测。
     * @return true 表示检测到异常并已触发恢复
     */
    fun checkAudioHealth(exoPlayer: ExoPlayer, userPaused: Boolean): Boolean {
        val volume = exoPlayer.volume
        val isPlaying = exoPlayer.isPlaying
        val state = exoPlayer.playbackState
        val playWhenReady = exoPlayer.playWhenReady
        var recovered = false

        // 检测1: 播放中但音量为0
        if (isPlaying && volume == 0f) {
            Log.e(tag, "异常: 播放中但音量=0, state=$state")
            exoPlayer.volume = 1f
            onVolumeRestore(0f, 1f)
            recovered = true
        }

        // 检测2: 非用户暂停，播放状态就绪但未播放
        if (!userPaused && !isPlaying && state == androidx.media3.common.Player.STATE_READY) {
            val timeSinceFocusLoss = if (lastFocusLossTime > 0) System.currentTimeMillis() - lastFocusLossTime else Long.MAX_VALUE
            // 仅在焦点丢失超过3秒仍无恢复时强制播放
            if (timeSinceFocusLoss > 3000L) {
                Log.w(tag, "异常: STATE_READY 但未播放, userPaused=false, focusLostMs=$timeSinceFocusLoss")
                exoPlayer.playWhenReady = true
                exoPlayer.play()
                onPlaybackRestore("health_check_forced")
                recovered = true
            }
        }

        // 检测3: playWhenReady=true 但实际未播放且非缓冲中
        if (playWhenReady && !isPlaying && state != androidx.media3.common.Player.STATE_BUFFERING && state != androidx.media3.common.Player.STATE_IDLE) {
            Log.w(tag, "异常: playWhenReady=true 但未播放, state=$state")
        }

        // 周期性状态摘要（每5次检测 ≈ 每10秒）
        healthCheckCount++
        if (healthCheckCount % 5 == 0) {
            Log.i(tag, "健康检测 #$healthCheckCount: playing=$isPlaying, vol=$volume, state=$state, pwr=$playWhenReady, userPaused=$userPaused, focusLosses=$focusLossCount, volRestores=$volumeRestoreCount, playRestores=$playbackRestoreCount")
        }

        return recovered
    }

    fun logEpisodeChange(episodeId: String) {
        Log.i(tag, "切集: episode=$episodeId, 累计: focusLosses=$focusLossCount, volRestores=$volumeRestoreCount, playRestores=$playbackRestoreCount")
    }

    fun logSeek(targetMs: Long) {
        Log.d(tag, "跳转: target=${targetMs}ms")
    }
}