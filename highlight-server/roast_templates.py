"""本地吐槽模板库：按情绪标签 × 高光类型 × 台词模式预置模板

双轨制第一轨：0 延迟，固定评 4 分。
槽位：{人物}、{动作}、{台词关键词}

有台词模式：使用 {台词关键词} 槽位，从 HighlightContext.matched_keywords 填充。
无台词模式：纯情绪吐槽，不引用任何具体台词。
"""
import random
import re
from typing import Dict, List, Optional

# 情绪标签 × 高光类型 × 台词模式 → 模板列表
_TEMPLATES: Dict[str, Dict[str, Dict[str, List[str]]]] = {
    "震惊": {
        "L1": {
            "有台词": [
                "这句{台词关键词}也太炸了吧",
                "等等等等{台词关键词}我没看错吧",
                "他居然说了{台词关键词}？？",
                "这反转{台词关键词}我直接愣住",
                "不是吧{台词关键词}也太离谱了",
                "听到{台词关键词}我下巴都掉了",
            ],
            "无台词": [
                "等等等等我没看错吧",
                "这反转也太炸了吧",
                "我下巴都掉了真的假的",
                "这剧情我直接愣住",
                "不是吧这也太离谱了",
            ],
        },
        "L2": {
            "有台词": [
                "嗯？{台词关键词}什么操作",
                "等等{台词关键词}让我缓缓",
                "这{台词关键词}信息量有点大",
                "感觉{台词关键词}有故事啊",
                "这剧情{台词关键词}有点意思",
            ],
            "无台词": [
                "嗯？这是什么操作",
                "我怎么没看懂呢",
                "等等让我缓缓",
                "这信息量有点大",
                "感觉有故事啊",
            ],
        },
    },
    "愤怒": {
        "L1": {
            "有台词": [
                "听到{台词关键词}气死我了",
                "他居然说{台词关键词}太过分了",
                "{台词关键词}这人怎么这样",
                "这句{台词关键词}我忍不了",
                "说出{台词关键词}真的渣",
                "{台词关键词}拳头硬了",
            ],
            "无台词": [
                "气死我了这人怎么这样",
                "太过分了吧真的忍不了",
                "我要是在场直接开骂",
                "这什么人啊太恶心了",
                "渣！太渣了！",
                "拳头硬了真的",
            ],
        },
        "L2": {
            "有台词": [
                "说{台词关键词}这人什么态度",
                "{台词关键词}有点过分了吧",
                "这句{台词关键词}看不下去了",
                "能不能别说了{台词关键词}",
                "就这{台词关键词}还想骗谁呢",
            ],
            "无台词": [
                "这人怎么说话的啊",
                "有点过分了吧",
                "看不下去了真的",
                "能不能别作了",
                "就这？还想骗谁呢",
            ],
        },
    },
    "感动": {
        "L1": {
            "有台词": [
                "听到{台词关键词}我哭了",
                "这句{台词关键词}太好哭了",
                "{台词关键词}谁来给我递纸巾",
                "说出{台词关键词}这才是真感情",
                "{台词关键词}破防了真的",
                "这句{台词关键词}我鼻子酸了",
            ],
            "无台词": [
                "呜呜呜太好哭了",
                "我眼睛进沙子了",
                "这段看得我鼻子酸",
                "谁来给我递张纸巾",
                "这才是真感情啊",
                "破防了真的破防了",
            ],
        },
        "L2": {
            "有台词": [
                "这句{台词关键词}有点小感动",
                "{台词关键词}嗯这段挺暖的",
                "听到{台词关键词}还行吧有点意思",
                "{台词关键词}这波操作我给满分",
                "终于{台词关键词}有糖了呜呜",
            ],
            "无台词": [
                "有点小感动是怎么回事",
                "嗯这段挺暖的",
                "还行吧有点意思",
                "这波操作我给满分",
                "终于有糖了呜呜",
            ],
        },
    },
    "爆笑": {
        "L1": {
            "有台词": [
                "哈哈{台词关键词}笑死我了",
                "这段{台词关键词}我反复看了三遍",
                "{台词关键词}什么沙雕剧情啊",
                "听到{台词关键词}笑到邻居敲门",
                "{台词关键词}编剧你是认真的吗",
                "这句{台词关键词}也太搞笑了",
            ],
            "无台词": [
                "哈哈哈哈笑死我了",
                "这段我反复看了三遍",
                "这什么沙雕剧情啊",
                "笑到邻居来敲门了",
                "编剧你是认真的吗哈哈哈",
                "这也太搞笑了吧",
            ],
        },
        "L2": {
            "有台词": [
                "噗嗤{台词关键词}也太逗了",
                "{台词关键词}有点好笑是怎么回事",
                "哈哈{台词关键词}可以的",
                "这编剧{台词关键词}脑洞真大",
                "笑死{台词关键词}这个绝了",
            ],
            "无台词": [
                "噗嗤这也太逗了",
                "有点好笑是怎么回事",
                "哈哈哈哈可以的",
                "这编剧脑洞真大",
                "笑死这个绝了",
            ],
        },
    },
    "心疼": {
        "L1": {
            "有台词": [
                "听到{台词关键词}太惨了好心疼",
                "{台词关键词}也太不容易了吧",
                "求求编剧别{台词关键词}了",
                "{台词关键词}看哭了这也太惨了",
                "为什么要{台词关键词}啊",
                "这句{台词关键词}我的心好痛",
            ],
            "无台词": [
                "太惨了真的好心疼",
                "也太不容易了吧",
                "求求编剧别虐了",
                "看哭了这也太惨了",
                "为什么要这样对他啊",
                "我的心好痛",
            ],
        },
        "L2": {
            "有台词": [
                "听到{台词关键词}有点心疼",
                "{台词关键词}唉好惨啊",
                "这谁{台词关键词}顶得住啊",
                "抱抱吧{台词关键词}",
                "编剧你没有心{台词关键词}",
            ],
            "无台词": [
                "有点心疼是怎么回事",
                "唉好惨啊",
                "这谁顶得住啊",
                "抱抱吧",
                "编剧你没有心",
            ],
        },
    },
    "吃瓜": {
        "L1": {
            "有台词": [
                "来了来了{台词关键词}精彩要来了",
                "这{台词关键词}瓜也太大了吧",
                "好家伙{台词关键词}要搞事情",
                "坐等{台词关键词}看好戏嘿嘿",
                "{台词关键词}打起来打起来",
            ],
            "无台词": [
                "来了来了精彩要来了",
                "这瓜也太大了吧",
                "好家伙这是要搞事情",
                "坐等看好戏嘿嘿",
                "打起来打起来",
            ],
        },
        "L2": {
            "有台词": [
                "嗯{台词关键词}有好戏看了",
                "这俩人{台词关键词}什么关系",
                "感觉{台词关键词}有八卦啊",
                "吃瓜{台词关键词}",
                "让我看看{台词关键词}怎么回事",
            ],
            "无台词": [
                "嗯有好戏看了",
                "这俩人什么关系",
                "感觉有八卦啊",
                "吃瓜吃瓜",
                "让我看看怎么回事",
            ],
        },
    },
    "无语": {
        "L1": {
            "有台词": [
                "听到{台词关键词}我直接无语了",
                "{台词关键词}什么操作我看不懂",
                "编剧{台词关键词}你在逗我吗",
                "这{台词关键词}逻辑我服了真的",
                "{台词关键词}离谱到家了",
            ],
            "无台词": [
                "我直接无语了好吧",
                "这什么操作我看不懂",
                "编剧你在逗我吗",
                "这逻辑我服了真的",
                "离谱给离谱开门离谱到家了",
            ],
        },
        "L2": {
            "有台词": [
                "嗯？？{台词关键词}什么逻辑",
                "我不是很理解{台词关键词}",
                "就这{台词关键词}？",
                "有点无语{台词关键词}但还好",
                "行吧{台词关键词}你说啥就是啥",
            ],
            "无台词": [
                "嗯？？这什么逻辑",
                "我不是很理解",
                "就这？",
                "有点无语但还好",
                "行吧你说啥就是啥",
            ],
        },
    },
    "期待": {
        "L1": {
            "有台词": [
                "快快快{台词关键词}我要看下一集",
                "这{台词关键词}伏笔绝了后面肯定炸",
                "编剧{台词关键词}你可太会了",
                "我直接坐直了{台词关键词}看好吧",
                "后面{台词关键词}绝对炸裂",
            ],
            "无台词": [
                "快快快我要看下一集",
                "这伏笔绝了后面肯定炸",
                "编剧你可太会了",
                "我直接坐直了看好吧",
                "后面的剧情绝对炸裂",
            ],
        },
        "L2": {
            "有台词": [
                "有点期待{台词关键词}后面的发展",
                "感觉{台词关键词}好戏在后头",
                "这剧情{台词关键词}可以追",
                "后面{台词关键词}肯定有反转",
                "我猜{台词关键词}后面要炸",
            ],
            "无台词": [
                "有点期待后面的发展",
                "感觉好戏在后头",
                "这剧情可以追",
                "后面肯定有反转",
                "我猜后面要炸",
            ],
        },
    },
    "嘲笑": {
        "L1": {
            "有台词": [
                "{台词关键词}这也太菜了吧哈哈哈",
                "就这{台词关键词}还想装大佬",
                "笑死我了{台词关键词}这操作",
                "{台词关键词}也太丢人了吧",
                "{台词关键词}自作自受活该啊",
            ],
            "无台词": [
                "这也太菜了吧哈哈哈",
                "就这还想装大佬",
                "笑死我了这操作",
                "也太丢人了吧",
                "自作自受活该啊",
            ],
        },
        "L2": {
            "有台词": [
                "就这水平{台词关键词}还出来混",
                "有点好笑{台词关键词}哈哈哈",
                "这波{台词关键词}自己打脸了吧",
                "装不下去了吧{台词关键词}",
                "菜是原罪啊{台词关键词}",
            ],
            "无台词": [
                "就这水平还出来混",
                "有点好笑哈哈哈",
                "这波自己打脸了吧",
                "装不下去了吧",
                "菜是原罪啊",
            ],
        },
    },
}

# 通用 fallback 模板（任何情绪标签都能用）
_FALLBACK_TEMPLATES = [
    "这段剧情也太绝了",
    "编剧你是认真的吗",
    "我直接看呆了好吧",
    "这波操作我给满分",
    "好家伙这也太刺激了",
    "追剧追到这种剧情值了",
]

# 关键词 → 情绪倾向映射（用于平淡情绪路由）
_KEYWORD_EMOTION_HINTS = {
    "震惊": ["居然", "竟然", "没想到", "不可能", "天哪", "不会吧", "什么", "真的假的"],
    "愤怒": ["滚", "贱人", "混蛋", "骗子", "背叛", "出轨", "离婚", "分手", "不要脸", "气死"],
    "感动": ["谢谢", "对不起", "我爱你", "在一起", "永远", "守护", "陪伴", "回家"],
    "爆笑": ["哈哈", "笑死", "搞笑", "沙雕", "逗", "逗比", "二货", "神经病"],
    "心疼": ["别哭", "心疼", "抱抱", "委屈", "可怜", "惨", "苦", "累"],
    "吃瓜": ["听说", "秘密", "八卦", "关系", "暧昧", "在一起", "约会", "出轨"],
    "嘲笑": ["菜", "垃圾", "废物", "丢人", "丢脸", "活该", "自作自受", "打脸"],
    "期待": ["等", "后面", "反转", "一定", "肯定", "绝对", "看好"],
}


def _route_flat_emotion(
    acoustic_score: float,
    matched_keywords: List[str],
) -> str:
    """平淡情绪智能路由：根据声学分数和关键词选择更合适的标签。

    Args:
        acoustic_score: 声学突变分 (0-1)。
        matched_keywords: 命中关键词列表。

    Returns:
        情绪标签字符串。
    """
    # 按关键词匹配度给每个情绪打分
    scores: Dict[str, int] = {}
    for kw in matched_keywords:
        for emotion, hints in _KEYWORD_EMOTION_HINTS.items():
            if any(hint in kw for hint in hints):
                scores[emotion] = scores.get(emotion, 0) + 1

    # 有明确关键词倾向时选最高分
    if scores:
        best = max(scores, key=scores.get)
        return best

    # 无声学突变 → 无语；有突变 → 震惊
    if acoustic_score > 0.5:
        return "震惊"
    return "无语"


def get_template(emotion_tag: str, highlight_type: str, has_dialogue: bool = False) -> str:
    """根据情绪标签、高光类型和台词模式获取随机模板。

    Args:
        emotion_tag: 情绪标签（震惊/愤怒/感动/爆笑/心疼/吃瓜/无语/期待/嘲笑）。
        highlight_type: 高光类型（L1/L2）。
        has_dialogue: 是否有附近台词。

    Returns:
        模板字符串（含 {人物}/{动作}/{台词关键词} 槽位）。
    """
    mode = "有台词" if has_dialogue else "无台词"
    tag_templates = _TEMPLATES.get(emotion_tag, {})
    type_templates = tag_templates.get(highlight_type, {})

    # 优先选对应台词模式的模板
    templates = type_templates.get(mode, [])
    if not templates:
        # fallback 到另一个模式
        other_mode = "无台词" if mode == "有台词" else "有台词"
        templates = type_templates.get(other_mode, [])
    if not templates:
        # fallback 到同标签另一个类型
        other_type = "L2" if highlight_type == "L1" else "L1"
        other_type_templates = tag_templates.get(other_type, {})
        templates = other_type_templates.get(mode, other_type_templates.get("无台词", []))
    if not templates:
        return random.choice(_FALLBACK_TEMPLATES)
    return random.choice(templates)


def fill_template(
    template: str,
    matched_keywords: List[str] = None,
    subtitle_context: str = "",
) -> str:
    """填充模板槽位。

    Args:
        template: 含槽位的模板字符串。
        matched_keywords: 匹配到的关键词列表。
        subtitle_context: ASR 字幕上下文。

    Returns:
        填充后的评论文本。
    """
    keywords = matched_keywords or []

    # 提取人物：从关键词中找名字（2-4字中文，排除常见动词/形容词）
    _EXCLUDE = {
        "离婚", "分手", "出轨", "背叛", "怀孕", "流产", "死", "杀了",
        "救命", "不要", "滚", "贱人", "混蛋", "骗子", "疯了",
        "吵架", "争执", "生气", "愤怒", "委屈", "心痛", "难过",
        "震惊", "感动", "甜蜜", "紧张", "搞笑", "厉害", "牛",
        "居然", "竟然", "不可能", "真的", "什么", "怎么", "为什么",
        "不是", "可以", "还是", "就是", "已经", "因为", "所以",
    }
    person = ""
    for kw in keywords:
        if kw not in _EXCLUDE and 2 <= len(kw) <= 4:
            person = kw
            break

    # 从 ASR 台词中补充提取人名（"X说"、"X你"、"X他"、"X妈" 等上下文）
    if not person and subtitle_context:
        name_pattern = re.findall(
            r'([一-鿿]{2,3})(?:说|你|他|她|妈|爸|哥|姐|弟|妹|夫|总|老师|先生|小姐)',
            subtitle_context,
        )
        for name in name_pattern:
            if name not in _EXCLUDE:
                person = name
                break

    # 提取动作：从字幕中找动词
    action = ""
    if subtitle_context:
        _VERBS = [
            "说", "做", "看", "打", "哭", "笑", "跑", "走", "跪", "喊",
            "撕", "推", "拉", "抱", "踢", "瞪", "指", "咬", "撞", "摔",
            "骂", "吼", "求", "骗", "抢", "偷", "杀", "救", "护", "挡",
        ]
        for verb in _VERBS:
            if verb in subtitle_context:
                idx = subtitle_context.find(verb)
                start = max(0, idx - 1)
                end = min(len(subtitle_context), idx + 2)
                action = subtitle_context[start:end]
                break

    # 台词关键词：取第一个关键词
    kw_text = keywords[0] if keywords else ""

    # 填充，未匹配的槽位用默认值
    result = template.replace("{人物}", person or "这人")
    result = result.replace("{动作}", action or "这操作")
    result = result.replace("{台词关键词}", kw_text or "这段")
    return result


def generate_local_comments(
    highlight: dict,
    count: int = 3,
    has_dialogue: bool = False,
    matched_keywords: List[str] = None,
) -> List[Dict]:
    """为单个高光点生成本地模板评论。

    Args:
        highlight: 高光点数据（含 type, emotion_label, matched_keywords, subtitle_context）。
        count: 生成条数。
        has_dialogue: 是否有附近台词（决定模板选择）。
        matched_keywords: 命中关键词列表（优先于 highlight 中的 matched_keywords）。

    Returns:
        评论列表 [{"emotionTag": str, "text": str}]。
    """
    hl_type = highlight.get("type", "L2")
    emotion_label = highlight.get("emotion_label", "")
    keywords = matched_keywords if matched_keywords is not None else highlight.get("matched_keywords", [])
    subtitle_context = highlight.get("subtitle_context", "")
    acoustic_score = highlight.get("confidence", 0.0)

    # 确定情绪标签列表（优先用推理出的标签，平淡时智能路由）
    if emotion_label in _TEMPLATES:
        primary_tag = emotion_label
    else:
        primary_tag = _route_flat_emotion(acoustic_score, keywords)
    all_tags = list(_TEMPLATES.keys())

    # 生成评论：第一条用主标签，其余随机选不同标签
    comments = []
    used_tags = {primary_tag}

    # 第一条：主情绪标签
    tpl = get_template(primary_tag, hl_type, has_dialogue)
    text = fill_template(tpl, keywords, subtitle_context)
    comments.append({"emotionTag": primary_tag, "text": text})

    # 后续：随机不同标签
    remaining_tags = [t for t in all_tags if t not in used_tags]
    random.shuffle(remaining_tags)
    for tag in remaining_tags:
        if len(comments) >= count:
            break
        tpl = get_template(tag, hl_type, has_dialogue)
        text = fill_template(tpl, keywords, subtitle_context)
        comments.append({"emotionTag": tag, "text": text})
        used_tags.add(tag)

    return comments
