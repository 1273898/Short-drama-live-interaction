"""模型预加载与单例管理（阶段2）

统一管理所有 PyTorch/Transformers 模型的加载、缓存和生命周期。
避免重复加载导致内存溢出和初始化耗时。

阶段2 新增依赖：
pip install transformers funasr modelscope
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu

阶段1约束（必须遵守）：
- 禁止 torchaudio.load()，音频读取统一用 soundfile.read()
- 禁止 torch.hub.load() 联网加载，必须本地 .jit 或本地缓存优先
- subprocess 必须加 encoding="utf-8", errors="replace"
- JSON 序列化 numpy 类型必须使用 NumpyEncoder
"""
from __future__ import annotations

import os
import sys
import time
import logging
from pathlib import Path
from typing import Optional, Any, Callable

# 修复 Windows GBK 编码问题（torch.onnx.export 输出 Unicode 字符）
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

# modelscope 模型缓存到项目目录（避免 ~/.cache/modelscope）
os.environ.setdefault("MODELSCOPE_CACHE", str(Path(__file__).parent / ".torch_cache" / "modelscope"))

logger = logging.getLogger(__name__)

# 过滤 funasr/modelscope 的 trust_remote_code 警告
class _TrustRemoteCodeFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return "trust_remote_code" not in record.getMessage()

logging.getLogger().addFilter(_TrustRemoteCodeFilter())

# 本地模型文件路径
_LOCAL_VAD_PATH = Path(__file__).parent / ".torch_cache" / "silero_vad" / "silero_vad.jit"

# ONNX VAD 模型路径（优先级高于 JIT）
_ONNX_VAD_CANDIDATES = [
    Path(__file__).parent / ".torch_cache" / "silero_vad" / "silero_vad.onnx",
    Path(__file__).parent.parent / ".venv" / "lib" / "site-packages" / "silero_vad" / "data" / "silero_vad.onnx",
]
# silero-vad pip 包自带的 ONNX 模型
try:
    import silero_vad as _sv
    _ONNX_VAD_CANDIDATES.append(Path(_sv.__file__).parent / "data" / "silero_vad.onnx")
except ImportError:
    pass

# emotion2vec 情绪模型 ID（funasr 会自动缓存到 .torch_cache/modelscope）
_EMOTION_MODEL_ID = "iic/emotion2vec_base_finetuned"

# ASR 模型 ID（funasr 会自动缓存到 .torch_cache/modelscope）
_ASR_MODEL_ID = "paraformer-zh"

# 情绪标签（emotion2vec 8-class → 4-class 映射）
EMOTION_LABELS = ["喜", "怒", "悲", "平淡"]

# INT8 量化缓存路径（ASR 仍用 PyTorch 动态量化）
_ASR_QUANT_CACHE = Path(__file__).parent / ".torch_cache" / "asr_quant_int8.pt"


def _get_memory_mb() -> float:
    """获取当前进程内存占用（MB）。"""
    try:
        import psutil
        return psutil.Process().memory_info().rss / 1024 / 1024
    except ImportError:
        return 0.0


class ModelManager:
    """模型单例管理器。

    管理三个模型：
    - Silero-VAD：人声检测（本地 .jit / ONNX）
    - emotion2vec 情绪模型：8→4分类映射（喜/怒/悲/平淡）
    - Paraformer ASR：中文语音识别

    使用方式：
        manager = ModelManager()
        manager.load_all()  # 服务启动时调用

        # 或按需加载
        model = manager.load_silero_vad()
        emotion_model = manager.load_emotion_model()
    """

    _instance: Optional["ModelManager"] = None

    def __new__(cls) -> "ModelManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        self._models: dict[str, Any] = {}
        self._vad_utils: dict[str, Callable] = {}
        self._available: dict[str, bool] = {
            "silero_vad": False,
            "emotion": False,
            "asr": False,
        }
        logger.info("ModelManager 初始化完成")

    # ── Silero-VAD ──────────────────────────────────────────────

    def load_silero_vad(self) -> Any:
        """加载 Silero-VAD 模型（CPU 模式）。

        优先使用本地 .jit 文件，禁止 torch.hub.load 联网加载。

        Returns:
            Silero-VAD 模型实例。

        Raises:
            RuntimeError: 模型加载失败。
        """
        if "silero_vad" in self._models:
            return self._models["silero_vad"]

        try:
            import torch
        except ImportError:
            raise RuntimeError(
                "PyTorch 未安装。请安装 CPU 版本:\n"
                "pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu"
            )

        mem_before = _get_memory_mb()
        t0 = time.monotonic()

        # 仅本地 .jit 文件（禁止 torch.hub.load 联网加载）
        if _LOCAL_VAD_PATH.exists():
            try:
                logger.info(f"从本地文件加载 Silero-VAD: {_LOCAL_VAD_PATH}")
                model = torch.jit.load(str(_LOCAL_VAD_PATH), map_location="cpu")
                model.eval()
                self._models["silero_vad"] = model
                self._register_builtin_utils()
                self._available["silero_vad"] = True
                elapsed = time.monotonic() - t0
                mem_after = _get_memory_mb()
                logger.info(
                    f"Silero-VAD 加载成功 (本地 .jit) | "
                    f"耗时 {elapsed:.1f}s | 内存 +{mem_after - mem_before:.1f}MB → {mem_after:.1f}MB"
                )
                return model
            except Exception as e:
                logger.warning(f"本地 .jit 加载失败: {e}")

        raise RuntimeError(
            f"Silero-VAD 模型加载失败。本地 .jit 不可用: {_LOCAL_VAD_PATH}"
        )

    def load_silero_vad_onnx(self) -> Any:
        """加载 Silero-VAD ONNX 模型。

        Returns:
            onnxruntime.InferenceSession 实例，或 None（加载失败）。
        """
        if "silero_vad_onnx" in self._models:
            return self._models["silero_vad_onnx"]

        try:
            import onnxruntime as ort
        except ImportError:
            logger.info("onnxruntime 未安装，VAD 将使用 PyTorch JIT")
            return None

        for path in _ONNX_VAD_CANDIDATES:
            if path.exists():
                try:
                    t0 = time.monotonic()
                    opts = ort.SessionOptions()
                    opts.intra_op_num_threads = 1
                    opts.inter_op_num_threads = 1
                    sess = ort.InferenceSession(str(path), sess_options=opts, providers=["CPUExecutionProvider"])
                    self._models["silero_vad_onnx"] = sess
                    elapsed = time.monotonic() - t0
                    logger.info(f"Silero-VAD ONNX 加载成功: {path} | 耗时 {elapsed:.2f}s")
                    return sess
                except Exception as e:
                    logger.warning(f"ONNX 模型加载失败 ({path}): {e}")

        logger.info("未找到 ONNX VAD 模型，使用 PyTorch JIT")
        return None

    def get_vad_onnx_session(self) -> Any:
        """获取已加载的 ONNX VAD session。"""
        return self._models.get("silero_vad_onnx")

    def get_vad_model(self) -> Any:
        """获取已加载的 Silero-VAD 模型实例。"""
        return self._models.get("silero_vad")

    def get_vad_util(self, name: str) -> Optional[Callable]:
        """获取 VAD 辅助函数。"""
        return self._vad_utils.get(name)

    def _register_builtin_utils(self) -> None:
        """注册内置辅助函数。优先 ONNX，fallback 到 JIT。"""
        onnx_sess = self.load_silero_vad_onnx()
        if onnx_sess is not None:
            self._vad_utils["get_speech_timestamps"] = self._onnx_get_speech_timestamps
            logger.info("VAD 推理后端: ONNX Runtime")
        else:
            self._vad_utils["get_speech_timestamps"] = self._builtin_get_speech_timestamps
            logger.info("VAD 推理后端: PyTorch JIT")

    @staticmethod
    def _builtin_get_speech_timestamps(
        audio: Any,
        model: Any,
        sampling_rate: int = 16000,
        threshold: float = 0.5,
        min_speech_duration_ms: int = 250,
        min_silence_duration_ms: int = 100,
        speech_pad_ms: int = 30,
        return_seconds: bool = False,
        **kwargs,
    ) -> list:
        """内置的 get_speech_timestamps 实现。"""
        import torch

        window_size_samples = 512 if sampling_rate == 16000 else 256
        min_speech_samples = int(min_speech_duration_ms / 1000 * sampling_rate)
        min_silence_samples = int(min_silence_duration_ms / 1000 * sampling_rate)
        pad_samples = int(speech_pad_ms / 1000 * sampling_rate)

        if audio.dim() > 1:
            audio = audio.squeeze()

        speech_probs = []
        model.reset_states()
        for i in range(0, len(audio), window_size_samples):
            chunk = audio[i:i + window_size_samples]
            if len(chunk) < window_size_samples:
                chunk = torch.nn.functional.pad(chunk, (0, window_size_samples - len(chunk)))
            prob = model(chunk, sampling_rate).item()
            speech_probs.append(prob)

        speech_mask = [p >= threshold for p in speech_probs]
        segments = []
        in_speech = False
        start = 0

        for i, is_speech in enumerate(speech_mask):
            sample_pos = i * window_size_samples
            if is_speech and not in_speech:
                start = sample_pos
                in_speech = True
            elif not is_speech and in_speech:
                if sample_pos - start >= min_speech_samples:
                    segments.append((start, sample_pos))
                in_speech = False

        if in_speech and len(audio) - start >= min_speech_samples:
            segments.append((start, len(audio)))

        if not segments:
            return []

        merged = [segments[0]]
        for seg_start, seg_end in segments[1:]:
            prev_start, prev_end = merged[-1]
            if seg_start - prev_end < min_silence_samples:
                merged[-1] = (prev_start, seg_end)
            else:
                merged.append((seg_start, seg_end))

        result = []
        for seg_start, seg_end in merged:
            padded_start = max(0, seg_start - pad_samples)
            padded_end = min(len(audio), seg_end + pad_samples)
            entry = {"start": padded_start, "end": padded_end}
            if return_seconds:
                entry = {"start": padded_start / sampling_rate, "end": padded_end / sampling_rate}
            result.append(entry)

        return result

    @staticmethod
    def _onnx_get_speech_timestamps(
        audio: Any,
        model: Any,
        sampling_rate: int = 16000,
        threshold: float = 0.5,
        min_speech_duration_ms: int = 250,
        min_silence_duration_ms: int = 100,
        speech_pad_ms: int = 30,
        return_seconds: bool = False,
        **kwargs,
    ) -> list:
        """ONNX Runtime 版本的 get_speech_timestamps。

        与 JIT 版本相同的后处理逻辑，仅推理循环使用 ONNX Runtime。
        model 参数在此版本中被忽略，使用全局 ONNX session。
        """
        import numpy as np
        manager = get_manager()
        ort_sess = manager.get_vad_onnx_session()
        if ort_sess is None:
            # fallback to JIT
            return ModelManager._builtin_get_speech_timestamps(
                audio, model, sampling_rate, threshold,
                min_speech_duration_ms, min_silence_duration_ms,
                speech_pad_ms, return_seconds, **kwargs
            )

        import torch
        window_size_samples = 512 if sampling_rate == 16000 else 256
        min_speech_samples = int(min_speech_duration_ms / 1000 * sampling_rate)
        min_silence_samples = int(min_silence_duration_ms / 1000 * sampling_rate)
        pad_samples = int(speech_pad_ms / 1000 * sampling_rate)

        if audio.dim() > 1:
            audio = audio.squeeze()

        audio_np = audio.numpy() if isinstance(audio, torch.Tensor) else np.asarray(audio, dtype=np.float32)

        # ONNX 推理循环（内部模型需要 context 前缀，与 JIT wrapper 行为一致）
        context_size = 64 if sampling_rate == 16000 else 32
        state = np.zeros((2, 1, 128), dtype=np.float32)
        context = np.zeros((1, context_size), dtype=np.float32)
        speech_probs = []
        for i in range(0, len(audio_np), window_size_samples):
            chunk = audio_np[i:i + window_size_samples]
            if len(chunk) < window_size_samples:
                chunk = np.pad(chunk, (0, window_size_samples - len(chunk)))
            x_with_ctx = np.concatenate([context, chunk.reshape(1, -1)], axis=1).astype(np.float32)
            out, state = ort_sess.run(None, {"input": x_with_ctx, "state": state})
            speech_probs.append(float(out[0, 0]))
            context = x_with_ctx[:, -context_size:]

        # 后处理（与 JIT 版完全一致）
        speech_mask = [p >= threshold for p in speech_probs]
        segments = []
        in_speech = False
        start = 0

        for i, is_speech in enumerate(speech_mask):
            sample_pos = i * window_size_samples
            if is_speech and not in_speech:
                start = sample_pos
                in_speech = True
            elif not is_speech and in_speech:
                if sample_pos - start >= min_speech_samples:
                    segments.append((start, sample_pos))
                in_speech = False

        if in_speech and len(audio_np) - start >= min_speech_samples:
            segments.append((start, len(audio_np)))

        if not segments:
            return []

        merged = [segments[0]]
        for seg_start, seg_end in segments[1:]:
            prev_start, prev_end = merged[-1]
            if seg_start - prev_end < min_silence_samples:
                merged[-1] = (prev_start, seg_end)
            else:
                merged.append((seg_start, seg_end))

        result = []
        for seg_start, seg_end in merged:
            padded_start = max(0, seg_start - pad_samples)
            padded_end = min(len(audio_np), seg_end + pad_samples)
            entry = {"start": padded_start, "end": padded_end}
            if return_seconds:
                entry = {"start": padded_start / sampling_rate, "end": padded_end / sampling_rate}
            result.append(entry)

        return result

    # ── emotion2vec 情绪模型 ──────────────────────────────────────

    def load_emotion_model(self) -> Optional[Any]:
        """加载 emotion2vec 情绪识别模型（8→4分类映射）。

        使用 FunASR 的 emotion2vec_base_finetuned 模型，
        通过 model.generate(input=wav, granularity="utterance") 推理。

        Returns:
            FunASR AutoModel 实例，或 None（加载失败）。
        """
        if "emotion" in self._models:
            return self._models["emotion"]

        mem_before = _get_memory_mb()
        t0 = time.monotonic()

        try:
            from funasr import AutoModel
        except ImportError:
            logger.warning("funasr 未安装，情绪模型不可用。pip install funasr modelscope")
            self._available["emotion"] = False
            return None

        try:
            logger.info(f"加载 emotion2vec 情绪模型: {_EMOTION_MODEL_ID}")
            import os, contextlib
            # FunASR load_pretrained_model 用 print 输出 decoder miss key 警告（无害）
            with open(os.devnull, "w") as devnull:
                with contextlib.redirect_stdout(devnull):
                    model = AutoModel(
                        model=_EMOTION_MODEL_ID,
                        device="cpu",
                        disable_update=True,
                        hub="ms",
                    )
            self._models["emotion"] = model
            self._available["emotion"] = True
            elapsed = time.monotonic() - t0
            mem_after = _get_memory_mb()
            logger.info(
                f"情绪模型加载成功（emotion2vec） | "
                f"耗时 {elapsed:.1f}s | 内存 +{mem_after - mem_before:.1f}MB → {mem_after:.1f}MB"
            )
            return model

        except Exception as e:
            logger.warning(f"情绪模型加载失败（服务可降级运行）: {e}")
            self._available["emotion"] = False
            return None

    def get_emotion_model(self) -> Optional[Any]:
        """获取已加载的情绪模型实例。"""
        return self._models.get("emotion")

    def is_emotion_available(self) -> bool:
        """情绪模型是否可用。"""
        return self._available.get("emotion", False)

    # ── Paraformer-Tiny ASR ─────────────────────────────────────

    def load_asr_model(self) -> Optional[Any]:
        """加载 Paraformer-Tiny ASR 模型（中文语音识别）。

        使用 funasr 库，模型 ID paraformer-zh（最小版本）。
        优先本地缓存，加载失败不影响服务。

        Returns:
            FunASR AutoModel 实例，或 None（加载失败）。
        """
        if "asr" in self._models:
            return self._models["asr"]

        mem_before = _get_memory_mb()
        t0 = time.monotonic()

        try:
            from funasr import AutoModel
        except ImportError:
            logger.warning("funasr 未安装，ASR 不可用。pip install funasr modelscope")
            self._available["asr"] = False
            return None

        try:
            logger.info(f"加载 ASR 模型: {_ASR_MODEL_ID}")
            model = AutoModel(
                model=_ASR_MODEL_ID,
                model_revision="v2.0.4",
                device="cpu",
                disable_update=True,  # 禁止联网检查更新
                hub="ms",
                trust_remote_code=False,
            )

            # INT8 动态量化（funasr AutoModel 内部的 PyTorch 模型）
            try:
                import torch.nn as nn
                inner = getattr(model, "model", None)
                if inner is not None and hasattr(inner, "parameters"):
                    inner = self._quantize_int8(inner, "asr")
                    model.model = inner
                    logger.info("ASR 内部模型已 INT8 量化")
            except Exception as e:
                logger.warning(f"ASR INT8 量化失败（不影响功能）: {e}")

            self._models["asr"] = model
            self._available["asr"] = True
            elapsed = time.monotonic() - t0
            mem_after = _get_memory_mb()
            logger.info(
                f"ASR 模型加载成功 | "
                f"耗时 {elapsed:.1f}s | 内存 +{mem_after - mem_before:.1f}MB → {mem_after:.1f}MB"
            )
            return model

        except Exception as e:
            logger.warning(f"ASR 模型加载失败（服务可降级运行）: {e}")
            self._available["asr"] = False
            return None

    def get_asr_model(self) -> Optional[Any]:
        """获取已加载的 ASR 模型实例。"""
        return self._models.get("asr")

    def is_asr_available(self) -> bool:
        """ASR 模型是否可用。"""
        return self._available.get("asr", False)

    # ── INT8 动态量化 ───────────────────────────────────────────

    @staticmethod
    def _quantize_int8(model: Any, name: str = "model") -> Any:
        """对模型施加 INT8 动态量化（仅 Linear 层）。

        仅用于 ASR 模型（FunASR/Paraformer）。

        优先从离线缓存加载，否则在线量化后序列化。
        """
        import torch
        import torch.nn as nn

        cache_path = {
            "asr": _ASR_QUANT_CACHE,
        }.get(name)

        if cache_path and cache_path.exists():
            try:
                t0 = time.monotonic()
                state = torch.load(str(cache_path), map_location="cpu")
                model.load_state_dict(state)
                elapsed = time.monotonic() - t0
                logger.info(f"INT8 量化缓存命中 ({name}): {cache_path} | 耗时 {elapsed:.2f}s")
                return model
            except Exception as e:
                logger.warning(f"INT8 缓存加载失败 ({name}): {e}，重新量化")

        # 在线量化
        t0 = time.monotonic()
        try:
            quantized = torch.quantization.quantize_dynamic(
                model, {nn.Linear}, dtype=torch.qint8
            )
            elapsed = time.monotonic() - t0
            logger.info(f"INT8 动态量化完成 ({name}) | 耗时 {elapsed:.1f}s")
        except Exception as e:
            logger.warning(f"INT8 动态量化失败 ({name}): {e}，返回原始模型")
            return model

        # 序列化（仅对可 state_dict 的模型）
        if cache_path:
            try:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                if hasattr(quantized, "state_dict"):
                    torch.save(quantized.state_dict(), str(cache_path))
                    logger.info(f"INT8 量化缓存已保存: {cache_path}")
            except Exception as e:
                logger.warning(f"INT8 缓存保存失败 ({name}): {e}")

        return quantized

    # ── 统一管理 ────────────────────────────────────────────────

    def load_all(self) -> None:
        """预加载所有模型（服务启动时调用）。

        按顺序加载，每个模型独立错误处理，失败不影响其他模型。
        """
        logger.info("开始预加载所有模型...")
        mem_start = _get_memory_mb()

        # 1. Silero-VAD（必需）
        try:
            self.load_silero_vad()
        except RuntimeError as e:
            logger.error(f"Silero-VAD 加载失败（关键模型）: {e}")

        # 2. 情绪模型（可选）
        self.load_emotion_model()

        # 3. ASR 模型（可选）
        self.load_asr_model()

        mem_end = _get_memory_mb()
        logger.info(
            f"模型预加载完成 | 总内存 {mem_end:.1f}MB (Δ{mem_end - mem_start:.1f}MB) | "
            f"VAD={'✓' if self._available['silero_vad'] else '✗'} "
            f"Emotion={'✓' if self._available['emotion'] else '✗'} "
            f"ASR={'✓' if self._available['asr'] else '✗'}"
        )

    # 向后兼容：旧接口 preload_all → load_all
    def preload_all(self) -> None:
        """向后兼容，等同 load_all()。"""
        self.load_all()

    def unload_all(self) -> None:
        """释放所有模型内存。"""
        self._models.clear()
        self._vad_utils.clear()
        self._available = {k: False for k in self._available}
        try:
            import torch
            if hasattr(torch, "cuda") and torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass
        logger.info("所有模型已释放")


# ── 模块级便捷函数 ──────────────────────────────────────────────

_default_manager: Optional[ModelManager] = None


def get_manager() -> ModelManager:
    """获取全局 ModelManager 单例。"""
    global _default_manager
    if _default_manager is None:
        _default_manager = ModelManager()
    return _default_manager


def get_silero_vad() -> Any:
    """便捷函数：获取 Silero-VAD 模型。"""
    return get_manager().load_silero_vad()


def get_speech_timestamps_func() -> Callable:
    """便捷函数：获取 get_speech_timestamps 辅助函数。"""
    manager = get_manager()
    manager.load_silero_vad()
    func = manager.get_vad_util("get_speech_timestamps")
    if func is None:
        raise RuntimeError("get_speech_timestamps 不可用，模型可能未正确加载")
    return func


def get_read_audio_func() -> Callable:
    """便捷函数：获取 read_audio 辅助函数（soundfile 包装）。"""
    import soundfile as sf

    def read_audio(path: str, sampling_rate: int = 16000):
        """读取音频文件为 float32 tensor（soundfile 实现，禁止 torchaudio.load）。"""
        import torch
        data, sr = sf.read(path, dtype="float32")
        if data.ndim > 1:
            data = data.mean(axis=1)
        if sr != sampling_rate:
            # 简单线性重采样（避免依赖 torchaudio）
            import numpy as np
            duration = len(data) / sr
            target_len = int(duration * sampling_rate)
            indices = np.linspace(0, len(data) - 1, target_len)
            data = np.interp(indices, np.arange(len(data)), data).astype(np.float32)
        return torch.from_numpy(data)

    return read_audio
