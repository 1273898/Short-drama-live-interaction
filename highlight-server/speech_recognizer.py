"""语音识别模块：使用 Vosk 进行离线语音识别"""

import os
import sys
import json
import shutil
import subprocess
import tempfile
import logging
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class TranscriptSegment:
    """语音识别结果片段"""
    start_time: float  # 秒
    end_time: float    # 秒
    text: str          # 识别出的文本
    confidence: float  # 置信度 0-1


class SpeechRecognizer:
    """Vosk 离线语音识别器"""

    def __init__(self, model_path: Optional[str] = None):
        """
        初始化语音识别器。

        Args:
            model_path: Vosk 模型目录路径，默认读取环境变量 VOSK_MODEL_PATH
        """
        self.available = False
        self.model = None
        self.model_path = model_path or os.environ.get("VOSK_MODEL_PATH", "")

        # 检查 ffmpeg（PATH 中找不到时到 conda/Scripts 下查找）
        self.ffmpeg_path = shutil.which("ffmpeg")
        if not self.ffmpeg_path:
            scripts_dir = os.path.join(os.path.dirname(sys.executable), "Scripts")
            candidate = os.path.join(scripts_dir, "ffmpeg.exe")
            if os.path.isfile(candidate):
                self.ffmpeg_path = candidate
            else:
                logger.warning("ffmpeg not found in PATH, speech recognition disabled")
                return

        # 检查模型路径
        if not self.model_path or not os.path.isdir(self.model_path):
            logger.warning(f"Vosk model not found at: {self.model_path}")
            return

        # 加载 Vosk 模型
        try:
            from vosk import Model
            self.model = Model(self.model_path)
            self.available = True
            logger.info(f"Vosk model loaded from: {self.model_path}")
        except Exception as e:
            logger.warning(f"Failed to load Vosk model: {e}")

    def recognize(self, video_path: str) -> List[TranscriptSegment]:
        """
        从视频文件中识别语音。

        Args:
            video_path: 视频文件路径

        Returns:
            TranscriptSegment 列表，按时间顺序排列。失败时返回空列表。
        """
        if not self.available:
            return []

        audio_path = None
        try:
            # 提取音频
            audio_path = self._extract_audio(video_path)
            if not audio_path:
                return []

            # 语音识别
            segments = self._transcribe(audio_path)
            return segments

        except Exception as e:
            logger.error(f"Speech recognition failed: {e}")
            return []

        finally:
            # 清理临时文件
            if audio_path and os.path.exists(audio_path):
                try:
                    os.unlink(audio_path)
                except OSError:
                    pass

    def _extract_audio(self, video_path: str) -> Optional[str]:
        """
        使用 ffmpeg 从视频中提取音频。

        Returns:
            临时 WAV 文件路径，失败时返回 None
        """
        try:
            # 创建临时文件
            fd, temp_path = tempfile.mkstemp(suffix=".wav")
            os.close(fd)

            # ffmpeg 命令：提取 16kHz 单声道 PCM WAV
            cmd = [
                self.ffmpeg_path or "ffmpeg",
                "-i", video_path,
                "-ar", "16000",    # 采样率 16kHz
                "-ac", "1",        # 单声道
                "-f", "wav",       # WAV 格式
                "-y",              # 覆盖已存在文件
                temp_path
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=300  # 5 分钟超时
            )

            if result.returncode != 0:
                stderr_text = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""
                logger.error(f"ffmpeg failed: {stderr_text[:200]}")
                return None

            return temp_path

        except subprocess.TimeoutExpired:
            logger.error("ffmpeg timeout")
            return None
        except Exception as e:
            logger.error(f"Audio extraction failed: {e}")
            return None

    def _transcribe(self, audio_path: str) -> List[TranscriptSegment]:
        """
        使用 Vosk 识别音频文件。

        Returns:
            TranscriptSegment 列表
        """
        from vosk import KaldiRecognizer
        import wave

        segments = []

        try:
            wf = wave.open(audio_path, "rb")

            # 验证音频格式
            if wf.getnchannels() != 1 or wf.getsampwidth() != 2:
                logger.error("Audio must be 16-bit mono PCM")
                return []

            sample_rate = wf.getframerate()
            rec = KaldiRecognizer(self.model, sample_rate)
            rec.SetWords(True)

            # 逐块识别
            chunk_size = 4000  # 约 125ms at 16kHz
            results = []

            while True:
                data = wf.readframes(chunk_size // 2)  # 2 bytes per frame
                if len(data) == 0:
                    break

                if rec.AcceptWaveform(data):
                    result = json.loads(rec.Result())
                    if result.get("text"):
                        results.append(result)

            # 获取最后的结果
            final_result = json.loads(rec.FinalResult())
            if final_result.get("text"):
                results.append(final_result)

            wf.close()

            # 转换为 TranscriptSegment
            segments = self._convert_results(results, sample_rate)

        except Exception as e:
            logger.error(f"Transcription failed: {e}")

        return segments

    def _convert_results(self, results: List[dict], sample_rate: int) -> List[TranscriptSegment]:
        """
        将 Vosk 结果转换为 TranscriptSegment 列表。

        Vosk 返回格式:
        {
            "text": "识别的文本",
            "result": [
                {"word": "词", "start": 0.0, "end": 0.5, "conf": 0.98}
            ]
        }
        """
        segments = []

        for result in results:
            text = result.get("text", "").strip()
            if not text:
                continue

            # 尝试从 result 数组获取精确时间
            word_results = result.get("result", [])
            if word_results:
                start_time = word_results[0].get("start", 0.0)
                end_time = word_results[-1].get("end", 0.0)
                confidence = sum(w.get("conf", 0.5) for w in word_results) / len(word_results)
            else:
                # 没有词级别结果，使用估算
                start_time = 0.0
                end_time = len(text) * 0.1  # 估算每个字符 100ms
                confidence = 0.5

            segments.append(TranscriptSegment(
                start_time=start_time,
                end_time=end_time,
                text=text,
                confidence=confidence
            ))

        # 合并相邻短片段（间隔 < 0.5s）
        merged = self._merge_segments(segments)
        return merged

    def _merge_segments(self, segments: List[TranscriptSegment], gap_threshold: float = 0.5) -> List[TranscriptSegment]:
        """合并间隔较短的相邻片段"""
        if not segments:
            return []

        merged = [segments[0]]

        for seg in segments[1:]:
            prev = merged[-1]
            gap = seg.start_time - prev.end_time

            if gap < gap_threshold:
                # 合并
                merged[-1] = TranscriptSegment(
                    start_time=prev.start_time,
                    end_time=seg.end_time,
                    text=prev.text + " " + seg.text,
                    confidence=(prev.confidence + seg.confidence) / 2
                )
            else:
                merged.append(seg)

        return merged
