"""四层缓存管理器"""

import os
import json
import time
import threading
from typing import Any, Optional
from collections import OrderedDict, deque
import logging

logger = logging.getLogger(__name__)


class LRUCache:
    """L1: 内存LRU缓存"""

    def __init__(self, max_size: int = 100, ttl: int = 600):
        self.max_size = max_size
        self.ttl = ttl
        self._cache: OrderedDict[str, dict] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            if key in self._cache:
                entry = self._cache[key]
                if time.time() - entry["timestamp"] < self.ttl:
                    self._cache.move_to_end(key)
                    self.hits += 1
                    return entry["value"]
                else:
                    del self._cache[key]
            self.misses += 1
            return None

    def set(self, key: str, value: Any):
        with self._lock:
            if key in self._cache:
                del self._cache[key]
            elif len(self._cache) >= self.max_size:
                self._cache.popitem(last=False)
            self._cache[key] = {"value": value, "timestamp": time.time()}

    def delete(self, key: str):
        with self._lock:
            if key in self._cache:
                del self._cache[key]

    def stats(self) -> dict:
        total = self.hits + self.misses
        return {
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": f"{self.hits / total * 100:.1f}%" if total > 0 else "0%",
            "entries": len(self._cache)
        }


class FileCache:
    """L2/L3/L4: 文件持久化缓存"""

    def __init__(self, cache_dir: str, ttl: int = 86400):
        self.cache_dir = cache_dir
        self.ttl = ttl
        self.hits = 0
        self.misses = 0
        os.makedirs(cache_dir, exist_ok=True)

    def get(self, key: str) -> Optional[Any]:
        path = os.path.join(self.cache_dir, f"{key}.json")
        if not os.path.exists(path):
            self.misses += 1
            return None

        if time.time() - os.path.getmtime(path) > self.ttl:
            try:
                os.unlink(path)
            except OSError:
                pass
            self.misses += 1
            return None

        try:
            with open(path, 'r', encoding='utf-8') as f:
                self.hits += 1
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            self.misses += 1
            return None

    def set(self, key: str, value: Any):
        path = os.path.join(self.cache_dir, f"{key}.json")
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(value, f, ensure_ascii=False)
        except IOError as e:
            logger.error(f"File cache write failed: {e}")

    def delete(self, key: str):
        path = os.path.join(self.cache_dir, f"{key}.json")
        if os.path.exists(path):
            try:
                os.unlink(path)
            except OSError:
                pass

    def cleanup_expired(self):
        """清理过期文件"""
        now = time.time()
        for fname in os.listdir(self.cache_dir):
            if not fname.endswith('.json'):
                continue
            fpath = os.path.join(self.cache_dir, fname)
            if now - os.path.getmtime(fpath) > self.ttl:
                try:
                    os.unlink(fpath)
                except OSError:
                    pass

    def count_entries(self) -> int:
        return len([f for f in os.listdir(self.cache_dir) if f.endswith('.json')])

    def stats(self) -> dict:
        total = self.hits + self.misses
        return {
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": f"{self.hits / total * 100:.1f}%" if total > 0 else "0%",
            "entries": self.count_entries()
        }


class CacheManager:
    """
    四层缓存管理器：
    L1: 内存LRU（TTL 10分钟，最大100条）- 热数据
    L2: 文件缓存-高光点结果（TTL 24小时）
    L3: 文件缓存-语音识别结果（TTL 7天）
    L4: 文件缓存-规则引擎结果（TTL 24小时）
    """

    def __init__(self, cache_base_dir: str = "./cache"):
        self.l1 = LRUCache(max_size=100, ttl=600)
        self.l2_highlights = FileCache(os.path.join(cache_base_dir, "highlights"), ttl=86400)
        self.l3_speech = FileCache(os.path.join(cache_base_dir, "speech"), ttl=604800)
        self.l4_rules = FileCache(os.path.join(cache_base_dir, "rules"), ttl=86400)
        self._lock = threading.Lock()

        self.degradation_counts = {"level1": 0, "level2": 0, "level3": 0}
        self._response_times: deque = deque(maxlen=1000)

        self.l2_highlights.cleanup_expired()
        self.l3_speech.cleanup_expired()
        self.l4_rules.cleanup_expired()

    def get_highlight(self, video_id: str) -> Optional[dict]:
        """获取高光点结果（L1 -> L2）"""
        result = self.l1.get(f"hl:{video_id}")
        if result is not None:
            return result

        result = self.l2_highlights.get(video_id)
        if result is not None:
            self.l1.set(f"hl:{video_id}", result)
            return result

        return None

    def set_highlight(self, video_id: str, data: dict):
        """存储高光点结果（L1 + L2）"""
        highlights = data.get("highlights", [])
        comments_count = sum(len(h.get("comments", [])) for h in highlights)
        logger.info(f"缓存写入: {video_id}, {len(highlights)} 个高光, {comments_count} 条吐槽")
        self.l1.set(f"hl:{video_id}", data)
        self.l2_highlights.set(video_id, data)

    def get_speech(self, video_id: str) -> Optional[list]:
        """获取语音识别结果（L3）"""
        return self.l3_speech.get(video_id)

    def set_speech(self, video_id: str, segments: list):
        """存储语音识别结果（L3）"""
        self.l3_speech.set(video_id, segments)

    def get_rules(self, video_id: str) -> Optional[list]:
        """获取规则引擎结果（L4）"""
        return self.l4_rules.get(video_id)

    def set_rules(self, video_id: str, highlights: list):
        """存储规则引擎结果（L4）"""
        self.l4_rules.set(video_id, highlights)

    def record_degradation(self, level: int):
        """记录降级事件"""
        key = f"level{level}"
        if key in self.degradation_counts:
            self.degradation_counts[key] += 1

    def record_response_time(self, ms: float):
        """记录响应时间"""
        self._response_times.append(ms)

    def get_stats(self) -> dict:
        """获取缓存统计"""
        avg_time = sum(self._response_times) / len(self._response_times) if self._response_times else 0
        return {
            "l1_memory": self.l1.stats(),
            "l2_highlights": self.l2_highlights.stats(),
            "l3_speech": self.l3_speech.stats(),
            "l4_rules": self.l4_rules.stats(),
            "degradation": dict(self.degradation_counts),
            "avg_response_time_ms": round(avg_time, 1)
        }

    def invalidate(self, video_id: str):
        """清除指定视频的所有缓存"""
        import ai_engine
        with self._lock:
            for key in [f"hl:{video_id}", f"rules:{video_id}"]:
                self.l1.delete(key)

            for cache in [self.l2_highlights, self.l3_speech, self.l4_rules]:
                cache.delete(video_id)

        # 同步清除 AI 引擎的独立内存缓存
        ai_engine.invalidate_cache(video_id)
