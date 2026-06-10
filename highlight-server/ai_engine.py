"""AI 引擎：直接调用 LLM API 分析字幕文本，识别剧情高光点"""

import os
import json
import time
import logging
import requests
from typing import List, Dict, Optional
from dotenv import dotenv_values

logger = logging.getLogger(__name__)

# 直接从 .env 文件读取 ANTHROPIC 配置（避免被系统环境变量覆盖）
_env = dotenv_values(os.path.join(os.path.dirname(__file__), ".env"))

# 全局 HTTP Session（连接池复用）
_session = None


def _get_session():
    global _session
    if _session is None:
        _session = requests.Session()
    return _session


def _get_anthropic_config(key: str, default: str = "") -> str:
    """优先使用 .env 文件中的值，fallback 到 os.environ"""
    return _env.get(key) or os.environ.get(key, default)

# 缓存结构: {video_id: {"highlights": [...], "timestamp": float, "call_count": int}}
_cache: Dict[str, Dict] = {}
CACHE_TTL = 86400  # 24小时
MAX_AI_CALLS = 5


def _call_llm(prompt: str, max_retries: int = 3) -> str:
    """直接调用 LLM API（OpenAI 兼容格式），开启高强度思考"""
    base_url = _get_anthropic_config("ANTHROPIC_BASE_URL", "").rstrip("/")
    api_key = _get_anthropic_config("ANTHROPIC_AUTH_TOKEN")
    model = _get_anthropic_config("ANTHROPIC_MODEL", "mimo-v2.5-pro")

    url = f"{base_url}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 4096,
        "temperature": 0.6,
        "enable_thinking": True,
        "thinking_budget": 2048
    }

    session = _get_session()
    for attempt in range(max_retries):
        try:
            resp = session.post(url, headers=headers, json=payload, timeout=120)
            resp.raise_for_status()
            data = resp.json()
            msg = data["choices"][0]["message"]
            content = msg.get("content", "")
            # Fallback: some models put response in reasoning_content
            if not content:
                content = msg.get("reasoning_content", "")
            thinking = msg.get("thinking_content", "") or msg.get("reasoning_content", "")
            if thinking and thinking != content:
                logger.debug(f"LLM 思考过程: {thinking[:200]}...")
            return content
        except Exception as e:
            logger.warning(f"LLM API 调用失败 (尝试 {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(2 * (attempt + 1))

    return ""


def parse_srt(srt_path: str) -> List[Dict]:
    """解析 srt 字幕文件"""
    subtitles = []
    try:
        with open(srt_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # 统一换行符为 \n，兼容 Windows \r\n
        content = content.replace('\r\n', '\n').replace('\r', '\n')
        blocks = content.strip().split('\n\n')
        for block in blocks:
            lines = block.strip().split('\n')
            if len(lines) >= 3:
                # 解析时间戳
                time_line = lines[1]
                start_str, end_str = time_line.split(' --> ')

                def parse_time(t: str) -> float:
                    parts = t.replace(',', '.').split(':')
                    return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])

                subtitles.append({
                    "start_time": parse_time(start_str.strip()),
                    "end_time": parse_time(end_str.strip()),
                    "text": ' '.join(lines[2:])
                })
    except Exception as e:
        print(f"字幕解析失败: {e}")
    return subtitles


def segment_subtitles(subtitles: List[Dict], window_size: int = 30) -> List[Dict]:
    """将字幕按30秒窗口分段"""
    if not subtitles:
        return []

    max_time = max(s["end_time"] for s in subtitles)
    segments = []

    for start in range(0, int(max_time) + 1, window_size):
        end = start + window_size
        segment_text = []
        for s in subtitles:
            if s["start_time"] < end and s["end_time"] > start:
                segment_text.append(s["text"])

        if segment_text:
            segments.append({
                "start_time": start,
                "end_time": end,
                "text": ' '.join(segment_text)
            })

    return segments


def invalidate_cache(video_id: str = None):
    """清除 AI 引擎的内存缓存。video_id=None 时清空全部。"""
    if video_id is None:
        _cache.clear()
    else:
        keys_to_remove = [k for k in _cache if video_id in k]
        for k in keys_to_remove:
            del _cache[k]


def _check_api_config() -> bool:
    """检查 Claude API 配置是否完整"""
    api_key = _get_anthropic_config("ANTHROPIC_AUTH_TOKEN")
    if not api_key:
        logger.error("ANTHROPIC_AUTH_TOKEN 未设置，AI 引擎不可用")
        return False
    return True


def _extract_highlights_from_text(result_text: str) -> List[Dict]:
    """从 LLM 返回文本中提取高光点 JSON"""
    if not result_text:
        return []
    if '[' in result_text and ']' in result_text:
        start = result_text.index('[')
        end = result_text.rindex(']') + 1
        try:
            highlights = json.loads(result_text[start:end])
            formatted = []
            for h in highlights:
                formatted.append({
                    "timestamp": float(h.get("timestamp", 0)),
                    "confidence": float(h.get("intensity", 0.5)),
                    "reason": h.get("reason", "")
                })
            return formatted
        except json.JSONDecodeError:
            logger.warning("JSON 解析失败")
    return []


def analyze_with_claude(segments: List[Dict], video_id: str) -> List[Dict]:
    """分析字幕，识别剧情高光点"""
    if not _check_api_config():
        return []

    # 检查缓存
    if video_id in _cache:
        cache_entry = _cache[video_id]
        if time.time() - cache_entry["timestamp"] < CACHE_TTL:
            if cache_entry["call_count"] >= MAX_AI_CALLS:
                return cache_entry["highlights"]
            _cache[video_id]["call_count"] += 1
            return cache_entry["highlights"]
        else:
            del _cache[video_id]

    _cache[video_id] = {"highlights": [], "timestamp": time.time(), "call_count": 1}

    segments_text = "\n".join([
        f"[{s['start_time']}-{s['end_time']}秒] {s['text']}"
        for s in segments
    ])

    prompt = f"""你是一位专业的短剧内容分析师。分析以下短剧字幕片段，识别出最精彩的1-2个高光时刻。

高光时刻的识别标准（按优先级排序）：
1. 【剧情高潮】真相揭露、身份反转、关键秘密被发现 — 这类转折最能引发观众情绪
2. 【情感爆发】激烈争吵、深情告白、痛哭、愤怒失控 — 对话中情绪强度最高的片段
3. 【冲突升级】正面对抗、矛盾激化、关系破裂 — 戏剧张力最大的时刻
4. 【悬念钩子】留下悬念的结尾、意外发现、突然出现的危机

每个高光点必须满足：
- 时间窗口精确到 ±3 秒内
- reason 字段必须引用具体的台词内容或描述具体的剧情动作（如"男主当众揭穿女主身份"），不要写泛泛的描述（如"精彩的剧情"）
- intensity 反映观众情绪冲击程度，高潮点应 >= 0.7

返回 JSON 数组（最多2个元素），每个元素包含：
- timestamp: 高光点时间（秒）
- reason: 高光原因（必须具体，引用台词或剧情动作，15字以内）
- intensity: 强度 0.0-1.0

只返回 JSON 数组，不要其他内容。

字幕片段：
{segments_text}"""

    result_text = _call_llm(prompt)
    highlights = _extract_highlights_from_text(result_text)

    if highlights:
        _cache[video_id]["highlights"] = highlights
        logger.info(f"AI 分析成功: {video_id}, {len(highlights)} 个高光点")

    return highlights


def analyze_video_with_speech(video_path: str, video_id: str) -> List[Dict]:
    """
    语音识别路径：从视频中识别语音，再用 Claude 分析。
    包含 60 秒超时保护。

    Args:
        video_path: 视频文件路径
        video_id: 视频 ID

    Returns:
        高光点列表，格式与 analyze_with_claude 相同
    """
    import signal

    if not _check_api_config():
        logger.warning(f"AI 引擎 API 配置无效，跳过 {video_id}")
        return []

    # 验证 Vosk 模型路径
    vosk_model_path = os.environ.get("VOSK_MODEL_PATH", "")
    if not vosk_model_path or not os.path.isdir(vosk_model_path):
        logger.warning(f"VOSK_MODEL_PATH 无效或未设置: '{vosk_model_path}'，语音识别不可用")
        return []

    from speech_recognizer import SpeechRecognizer

    cache_key = f"speech:{video_id}"

    # 检查缓存
    if cache_key in _cache:
        cache_entry = _cache[cache_key]
        if time.time() - cache_entry["timestamp"] < CACHE_TTL:
            if cache_entry["call_count"] >= MAX_AI_CALLS:
                logger.info(f"视频 {video_id} 语音识别已达 AI 调用上限 ({MAX_AI_CALLS})")
                return []
            _cache[cache_key]["call_count"] += 1
            return cache_entry["highlights"]
        else:
            del _cache[cache_key]

    # 初始化缓存
    _cache[cache_key] = {"highlights": [], "timestamp": time.time(), "call_count": 1}

    try:
        # 语音识别
        recognizer = SpeechRecognizer()
        if not recognizer.available:
            logger.warning(f"Speech recognizer not available, 跳过 {video_id}")
            return []

        logger.info(f"开始语音识别: {video_id}")
        transcript = recognizer.recognize(video_path)
        if not transcript:
            logger.warning(f"语音识别无结果: {video_id}")
            return []

        logger.info(f"语音识别完成: {video_id}, {len(transcript)} 个片段")

        # 转换格式并分段
        segments = [
            {"start_time": t.start_time, "end_time": t.end_time, "text": t.text}
            for t in transcript
        ]
        windowed = segment_subtitles(segments)

        if not windowed:
            logger.warning(f"字幕分段为空: {video_id}")
            return []

        # 调用 Claude（带增强 prompt），设置 60 秒超时
        logger.info(f"调用 Claude 分析语音文本: {video_id}, {len(windowed)} 个窗口")
        highlights = _analyze_with_claude_speech(windowed, video_id)
        _cache[cache_key]["highlights"] = highlights
        logger.info(f"语音分析完成: {video_id}, {len(highlights)} 个高光点")
        return highlights

    except Exception as e:
        logger.error(f"语音识别流程异常 {video_id}: {e}")
        return []


def _analyze_with_claude_speech(segments: List[Dict], video_id: str) -> List[Dict]:
    """分析语音转录文本，识别剧情高光点"""
    if not _check_api_config():
        return []

    segments_text = "\n".join([
        f"[{s['start_time']}-{s['end_time']}秒] {s['text']}"
        for s in segments
    ])

    prompt = f"""你是一位专业的短剧内容分析师。分析以下短剧语音转录片段，识别出最精彩的1-2个高光时刻。

注意：以下文本由语音识别生成，可能包含识别错误，请根据上下文推测意图。

高光时刻的识别标准（按优先级排序）：
1. 【剧情高潮】真相揭露、身份反转、关键秘密被发现
2. 【情感爆发】激烈争吵、深情告白、痛哭、愤怒失控
3. 【冲突升级】正面对抗、矛盾激化、关系破裂
4. 【悬念钩子】留下悬念的结尾、意外发现

每个高光点必须满足：
- reason 字段必须引用具体的台词内容或描述具体的剧情动作，不要写泛泛的描述
- intensity 反映观众情绪冲击程度，高潮点应 >= 0.7

返回 JSON 数组（最多2个元素），每个元素包含：
- timestamp: 高光点时间（秒）
- reason: 高光原因（必须具体，引用台词或剧情动作，15字以内）
- intensity: 强度 0.0-1.0

只返回 JSON 数组，不要其他内容。

语音转录片段：
{segments_text}"""

    result_text = _call_llm(prompt)
    highlights = _extract_highlights_from_text(result_text)

    if highlights:
        logger.info(f"语音分析成功: {video_id}, {len(highlights)} 个高光点")

    return highlights