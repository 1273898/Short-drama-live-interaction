"""Prometheus 监控指标模块（Day 8）

暴露以下指标：
- highlight_request_total: 请求总数（按状态码/是否缓存/是否降级分类）
- highlight_request_duration_seconds: 请求耗时直方图
- pipeline_stage_duration_seconds: 各管线阶段耗时（preprocess/vad/acoustic/emotion/asr/fusion）
- cache_operations_total: 缓存操作计数（hit/miss/evict，按层级分类）
- degradation_total: 降级触发次数（按级别分类）
- early_stop_total: Early Stop 触发次数（按模块分类）
- inference_pool_active: 活跃推理进程数

使用方式：
  from metrics import MetricsCollector, metrics_endpoint
  metrics = MetricsCollector()
  # 在管线各阶段调用 metrics.record_*()
  # 挂载 /metrics 端点
"""
from __future__ import annotations

import time
import threading
from typing import Optional

try:
    from prometheus_client import (
        Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST,
    )
    _PROMETHEUS_AVAILABLE = True
except ImportError:
    _PROMETHEUS_AVAILABLE = False


class MetricsCollector:
    """Prometheus 指标收集器。prometheus_client 不可用时退化为 no-op。"""

    def __init__(self):
        self._enabled = _PROMETHEUS_AVAILABLE
        if not self._enabled:
            return

        # 请求级指标
        self.request_total = Counter(
            "highlight_request_total",
            "请求总数",
            ["status", "cached", "degraded"],
        )
        self.request_duration = Histogram(
            "highlight_request_duration_seconds",
            "请求耗时",
            buckets=[0.1, 0.5, 1, 2, 5, 10, 30, 60, 120, 300],
        )

        # 管线阶段耗时
        self.pipeline_stage_duration = Histogram(
            "pipeline_stage_duration_seconds",
            "管线各阶段耗时",
            ["stage"],
            buckets=[0.01, 0.05, 0.1, 0.5, 1, 2, 5, 10, 30, 60, 120],
        )

        # 缓存操作
        self.cache_ops = Counter(
            "cache_operations_total",
            "缓存操作计数",
            ["layer", "operation"],  # layer: L1/L2/L3/L4/L4b, operation: hit/miss/evict
        )

        # 降级
        self.degradation = Counter(
            "degradation_total",
            "降级触发次数",
            ["level"],  # level: 1/3
        )

        # Early Stop
        self.early_stop = Counter(
            "early_stop_total",
            "Early Stop 触发次数",
            ["module"],  # module: emotion/asr
        )

        # 推理进程池
        self.inference_pool_active = Gauge(
            "inference_pool_active",
            "活跃推理进程数",
            ["pool"],  # pool: emotion/asr
        )

    def record_request(self, status: int, cached: bool, degraded: bool, duration_s: float):
        if not self._enabled:
            return
        self.request_total.labels(
            status=str(status),
            cached=str(cached).lower(),
            degraded=str(degraded).lower(),
        ).inc()
        self.request_duration.observe(duration_s)

    def record_stage(self, stage: str, duration_s: float):
        if not self._enabled:
            return
        self.pipeline_stage_duration.labels(stage=stage).observe(duration_s)

    def record_cache_op(self, layer: str, operation: str):
        if not self._enabled:
            return
        self.cache_ops.labels(layer=layer, operation=operation).inc()

    def record_degradation(self, level: int):
        if not self._enabled:
            return
        self.degradation.labels(level=str(level)).inc()

    def record_early_stop(self, module: str):
        if not self._enabled:
            return
        self.early_stop.labels(module=module).inc()

    def generate(self) -> bytes:
        """生成 Prometheus 文本格式指标"""
        if not self._enabled:
            return b"# prometheus_client not installed\n"
        return generate_latest()

    @property
    def content_type(self) -> str:
        if not self._enabled:
            return "text/plain"
        return CONTENT_TYPE_LATEST


# 全局单例
_collector: Optional[MetricsCollector] = None


def get_metrics() -> MetricsCollector:
    global _collector
    if _collector is None:
        _collector = MetricsCollector()
    return _collector
