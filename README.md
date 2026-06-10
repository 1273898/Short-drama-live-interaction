# 🎬 Short Drama Live Interaction — 短剧实时互动系统

基于多模态 AI 的短剧实时互动系统，在播放过程中自动识别高光时刻，生成小精灵吐槽、剧情分支和动态漫衍生内容，让观众从"被动观看"变为"主动参与"。

---

## ✨ 核心功能

### 🔍 短剧内容理解
- **高光点检测**：规则引擎（镜头切换/音频峰值）+ AI 引擎（MIMO v2.5 分析台词）双通道融合
- **ASR 语音识别**：Vosk 离线识别视频台词，为 AI 分析提供上下文
- **音频特征提取**：librosa 提取能量/频谱特征，辅助判断情绪波动点

### 🤖 AIGC 内容生成
- **AI 实时吐槽**：MIMO v2.5 根据台词上下文生成幽默吐槽文案 + TTS 语音播报
- **剧情分支生成**：AI 分析每集剧情，自动生成 2 条分支走向（热血/搞笑/反转等风格）
- **动态漫生成**：doubao-seedream-5-0 文生图模型，将分支描述转化为 4 格漫画 + 解说
- **用户 UGC 分支**：用户可提交自定义分支，经 LLM 审核后进入投票池

### 🎮 端上沉浸渲染
- **小精灵系统**：带翅膀的动画精灵，支持待机/开心/委屈/灵感/隐藏等表情
- **分支选择弹窗**：视频播完弹出底部弹窗，展示 3 条分支（最高票 1 + 随机 2）
- **动态漫画廊**：竖向逐格展示 4 格漫画 + 解说文字，16:9 全宽图片
- **VerticalPager 短剧流**：上下滑动切换剧集，共享 ExoPlayer 无缝衔接

---

## 🏗️ 技术架构

```
┌──────────────────────────────────────────────────────┐
│                  Android 客户端                        │
│    Kotlin + Jetpack Compose + ExoPlayer + Coil        │
│    Retrofit + OkHttp + Room + MVVM (StateFlow)        │
└──────────────┬──────────────────────┬─────────────────┘
               │ :3001                │ :8081
┌──────────────▼──────────┐  ┌───────▼──────────────────┐
│    highlight-server     │  │     branch-service        │
│    FastAPI + Python      │  │     FastAPI + aiosqlite   │
│                         │  │                           │
│    Vosk ASR             │  │     MIMO v2.5 (分支生成)   │
│    MIMO v2.5 (吐槽)     │  │     doubao-seedream (漫画) │
│    librosa + cv2        │  │     SQLite                │
└─────────────────────────┘  └───────────────────────────┘
```

| 模块 | 技术栈 |
|------|--------|
| **客户端** | Kotlin, Jetpack Compose, Material3, ExoPlayer, Coil, Retrofit, Room |
| **高亮服务** | Python FastAPI, Vosk, librosa, OpenCV, ffmpeg, MIMO v2.5 |
| **分支服务** | Python FastAPI, aiosqlite, MIMO v2.5, doubao-seedream-5-0 |
| **AI 模型** | MIMO v2.5（文本理解/生成）, doubao-seedream-5-0（文生图）, Vosk（离线 ASR） |

---

## 🚀 快速开始

### 环境要求
- Python 3.11+
- Node.js 18+（可选，视频服务已内置到 highlight-server）
- ffmpeg（用于音频提取）
- Android Studio + JDK 17+

### 1. 启动 highlight-server（高亮检测 + 视频服务）

```bash
cd highlight-server
pip install -r requirements.txt  # 或手动安装: fastapi uvicorn opencv-python librosa vosk python-dotenv requests numpy

# 下载 Vosk 中文语音识别模型（约 50MB）
python -c "import urllib.request,zipfile;urllib.request.urlretrieve('https://alphacephei.com/vosk/models/vosk-model-small-cn-0.22.zip','v.zip');zipfile.ZipFile('v.zip').extractall('.')"

# 配置环境变量（复制 .env.example 为 .env 并填入 API Key）
cp .env.example .env

# 启动
python -m uvicorn main:app --host 0.0.0.0 --port 3001
```

### 2. 启动 branch-service（分支生成 + 动态漫）

```bash
cd branch-service
pip install -r requirements.txt

# 配置环境变量
cp .env.example .env

# 启动
python main.py
```

### 3. 端口转发（Android 模拟器）

```bash
adb reverse tcp:3001 tcp:3001
adb reverse tcp:8081 tcp:8081
```

### 4. 编译运行 Android 客户端

在 `local.properties` 中配置：
```properties
serverBaseUrl=http\://10.0.2.2\:3001
```

用 Android Studio 打开项目，点击 Run。

---

## 📁 项目结构

```
Short-drama-live-interaction/
├── app/                          # Android 客户端
│   └── src/main/java/.../
│       ├── data/                 # 数据层（Retrofit API, Room 缓存）
│       ├── ui/component/         # UI 组件（BranchCard, SpriteOverlay, VideoPlayer）
│       ├── ui/screen/            # 页面（PlayerScreen）
│       └── viewmodel/            # ViewModel（HighlightVM, PlayerVM）
│
├── highlight-server/             # 高亮检测后端（:3001）
│   ├── main.py                   # FastAPI 入口 + 视频静态服务
│   ├── rule_engine.py            # 规则引擎（音视频特征提取）
│   ├── ai_engine.py              # AI 引擎（ASR + LLM 分析）
│   ├── fusion_engine.py          # 融合引擎（双通道去重评分）
│   ├── roast_generator.py        # 吐槽文案生成
│   ├── speech_recognizer.py      # Vosk 语音识别
│   └── .env.example              # 环境变量模板
│
├── branch-service/               # 分支生成后端（:8081）
│   ├── main.py                   # FastAPI 入口
│   ├── services/
│   │   ├── branch_generator.py   # MIMO LLM 分支生成
│   │   ├── comic_generator.py    # doubao-seedream 动态漫生成
│   │   ├── comic_describer.py    # 漫画解说文案生成
│   │   └── llm_validator.py      # 用户分支 LLM 审核
│   ├── routers/                  # API 路由
│   ├── static/branch_comics/     # 生成的漫画图片
│   └── .env.example              # 环境变量模板
│
├── 小精灵/                       # 小精灵图片素材
└── 短剧/                         # 短剧视频文件（.gitignore 排除）
```

---

## 🔧 三级降级策略

| 级别 | 触发条件 | 行为 |
|------|----------|------|
| **Level 1** | 规则引擎 < 5s | 先返回粗筛结果，客户端立即展示 |
| **Level 2** | AI 引擎 15-30s | ASR + LLM 分析完成后异步更新缓存 |
| **Level 3** | ASR/LLM 失败 | ASR 失败 → 仅规则引擎结果；LLM 超时 → 预置模板吐槽 |

---

## 🤖 AI 模型使用

| 模型 | 用途 | API 格式 |
|------|------|----------|
| **MIMO v2.5** | 高光分析、吐槽生成、分支生成、用户审核 | OpenAI 兼容（`/v1/chat/completions`） |
| **doubao-seedream-5-0** | 动态漫文生图 | 火山方舟 API（`/api/v3/images/generations`） |
| **Vosk** | 离线中文语音识别 | 本地模型，50MB |

---

## 📄 License

MIT License
