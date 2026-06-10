"""智能降级管理器"""

import asyncio
import logging
from typing import List, Dict, Optional, Any

logger = logging.getLogger(__name__)


class DegradationManager:
    """智能降级管理器"""

    DEFAULT_HIGHLIGHTS_TEMPLATE = [
        {"timestamp": 0.25, "confidence": 0.3, "source": "default", "type": "L2", "level": "L2", "score": 2},
        {"timestamp": 0.50, "confidence": 0.3, "source": "default", "type": "L2", "level": "L2", "score": 2},
        {"timestamp": 0.75, "confidence": 0.3, "source": "default", "type": "L2", "level": "L2", "score": 2},
    ]

    @staticmethod
    async def execute_with_timeout(
        coro,
        timeout_seconds: float,
        fallback_value: Any = None,
        operation_name: str = "operation"
    ) -> tuple:
        """
        执行异步操作，超时则返回降级值。

        Returns:
            (result, degradation_level): 0=正常, 1=一级降级, 2=二级降级, 3=三级降级
        """
        try:
            result = await asyncio.wait_for(coro, timeout=timeout_seconds)
            return result, 0
        except asyncio.CancelledError:
            logger.warning(f"{operation_name} 请求被取消，客户端可能已断开")
            return fallback_value, 3
        except asyncio.TimeoutError:
            logger.warning(f"{operation_name} timeout after {timeout_seconds}s")
            return fallback_value, 2
        except Exception as e:
            logger.error(f"{operation_name} failed: {e}")
            return fallback_value, 1

    @staticmethod
    def get_default_highlights(video_duration: Optional[float] = None) -> List[Dict]:
        """
        获取预设默认高光点。

        Args:
            video_duration: 视频时长（秒）。如果提供，timestamp为绝对秒数；否则为比例。
        """
        if video_duration and video_duration > 0:
            return [
                {
                    "timestamp": video_duration * ratio,
                    "confidence": 0.3,
                    "source": "default",
                    "type": "L2",
                    "level": "L2",
                    "score": 2
                }
                for ratio in [0.25, 0.50, 0.75]
            ]
        return list(DegradationManager.DEFAULT_HIGHLIGHTS_TEMPLATE)

    @staticmethod
    def get_empty_response(video_id: str, reason: str) -> dict:
        """三级降级：完全失败时的空响应"""
        logger.error(f"Complete failure for {video_id}: {reason}")
        return {
            "video_id": video_id,
            "highlights": [],
            "cached": False,
            "ai_source": "none",
            "degraded": True,
            "degradation_reason": reason
        }
