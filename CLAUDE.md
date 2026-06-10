# CLAUDE.md

## 1. 项目概述

**ShortDramaInteraction（短剧互动）** — 竖屏短剧互动视频播放器，核心亮点是 **AI 小精灵陪看吐槽**。

用户在观看短剧时，AI 精灵（"灵灵"）会在高光剧情点自动弹出吐槽气泡并语音播报（TTS）。用户可通过双击/长按精灵点赞或踩，训练情绪偏好，影响后续吐槽选择。播放器支持竖屏上下滑动切集、高光进度条标记。

**当前版本状态**：v2 架构稳定。服务端高光识别引入质量打分（1-5分）和分级（L1/L2），每集输出 1-2 个高质量高光点。吐槽评论由服务端预生成（含评分+去重+重新生成），随高光数据下发，客户端离线可用。已清理所有废弃的情绪按钮模块（`EmotionButtonPanel`、`EmotionParticles`、`EmotionViewModel`）和 Konfetti 依赖。

---

## 2. 技术栈与架构

### 2.1 完整技术栈

| 层 | 技术 |
|---|---|
| 语言 | Kotlin 1.9.20 |
| UI | Jetpack Compose + Material3 (BOM 2023.10.01) |
| 视频播放 | Media3 ExoPlayer 1.2.0 |
| 导航 | Navigation Compose 2.7.5 |
| 图片加载 | Coil Compose 2.5.0 |
| 网络 | Retrofit 2.9.0 + OkHttp 4.12.0 + Gson |
| 本地数据库 | Room 2.6.1（高光缓存 + 吐槽缓存），版本 10 |
| KV 存储 | DataStore Preferences 1.0.0（播放进度 + 偏好设置） |
| AI | 客户端备用：Anthropic Messages API（`/v1/messages`）；服务端：OpenAI 兼容格式（`/v1/chat/completions`）。均通过代理 `token-plan-cn.xiaomimomo.com`，模型 `mimo-v2.5-pro` |
| TTS | Android TextToSpeech（中文，尝试选择女声，pitch=1.5, rate=0.9, volume=0.7）+ ToneGenerator 提示音 |
| DI | 无框架，手动 `ViewModelProvider.Factory` + `object` 单例 |
| 后端 | Python FastAPI（端口 3000，同时提供高光 API + 吐槽生成 + 静态视频文件服务） |
| 测试 | JUnit5 + JUnit Vintage + Mockk 1.13.8 + Turbine 1.0.0 + Coroutines Test |

### 2.2 架构分层

```
┌──────────────────────────────────────────┐
│  UI Layer (Compose)                      │
│  Screen → Component                      │
│  PlayerScreen, DramaListScreen           │
│  VideoPlayer, SpriteOverlay, etc.        │
├──────────────────────────────────────────┤
│  ViewModel Layer                         │
│  PlayerVM, HighlightVM, DramaListVM      │
├──────────────────────────────────────────┤
│  Data Layer                              │
│  Repository (DramaRepo, HighlightRepo)   │
│  Network (AiRoastService,                │
│           HighlightApiClient)            │
│  Store (PlaybackProgressStore)           │
├──────────────────────────────────────────┤
│  Backend Services                        │
│  Python FastAPI :3000                    │
│  (highlight + roast + video files)       │
└──────────────────────────────────────────┘
```

### 2.3 模块划分与关键文件

**data/model/** — 数据模型
- `Drama.kt` / `Episode.kt` — 剧集和分集模型
- `EmotionModels.kt` — `SpriteState`、`SpriteExpression` 枚举（DEFAULT/HAPPY/SAD/INSPIRATION）、`HighlightPoint`。注：`EmotionType` 枚举已废弃（无引用），待清理
- `AppEvent.kt` — 密封类，UI 事件（ShowSnackbar/ShowToast）

**data/network/** — 网络层
- `DramaApiService.kt` / `DramaApiModels.kt` — 剧集列表 Retrofit 接口（`/api/dramas`、`/api/dramas/{id}/episodes`）
- `HighlightApiClient.kt` — 包含：`HighlightApiClient` object（Retrofit 客户端，BASE_URL 来自 BuildConfig）、`HighlightRepository` class（Room 缓存 24h + 网络请求 15s 超时 + fallbackData）、`HighlightDatabase`（Room DB v10，含 `MIGRATION_1_2` ~ `MIGRATION_9_10`）、`HighlightDao` / `RoastCacheDao`、数据模型（`ServerHighlightPoint`、`HighlightComment`、`HighlightEntity`、`RoastCacheEntity`）
- `AiRoastService.kt` — AI 吐槽生成（客户端备用），调用 Anthropic Messages API（`/v1/messages`），含内存缓存（ConcurrentHashMap，上限 50 条）、15s 超时处理。L4 类型高光不请求 AI

**data/repository/**
- `DramaRepository.kt` — `object` 单例，先从服务端拉取（`BuildConfig.SERVER_BASE_URL`，默认 `http://10.0.2.2:3000`），超时/失败则降级到 `getHardcodedDramas()`（`by lazy` 懒加载，两部剧共 20 集，URL 动态拼接 BASE_URL）。内存缓存 `cachedDramas`
- `PlaybackProgressStore.kt` — DataStore 封装：播放进度（`pos_{episodeId}`）、精灵常驻开关（`mascot_always_visible`）、音频模式（`audio_mode`，三模式：OFF/NOTIFICATION/ON）、情绪权重（`ew_{episodeId}`，JSON 序列化）

**viewmodel/**
- `PlayerViewModel.kt` — 播放器核心状态：当前集数、进度恢复（含 positionCache + restoredPositions 双层缓存）、TTS 初始化、上下集切换。构造函数接收 `dramaId` + `episodeId` + `progressStore`。内部持有 `SpriteTtsManager` 实例
- `HighlightViewModel.kt` — 高光触发 → 服务端预生成 comments 优先 → Room 缓存 → AI 吐槽（备用）→ 情绪状态机选择 → 精灵状态更新。含降级兜底逻辑（`naturalFallback`）、亲缘组/冲突对优先级排序、`selectBestComment()` 方法。L1/L2 过滤 + 每集最多 2 个高光。Factory 接收 `Application`，内部创建 `HighlightRepository` + `PlaybackProgressStore`
- `DramaListViewModel.kt` — 剧集列表状态管理，`DramaListUiState` 密封类（Loading/Success/Error），`dramas` StateFlow 从 uiState 派生

**ui/screen/**
- `PlayerScreen.kt` — 播放器主界面：`VerticalPager` + `EpisodePage` + 精灵覆盖层 + 选集面板（200ms 动画）+ 高光 Snackbar。**持有唯一 ExoPlayer 实例**，切集时仅切换 MediaItem，生命周期管理（ON_PAUSE/ON_RESUME/release）。内含 `EpisodePanel`（竖向 LazyColumn 选集）和 `addAudioAnalyticsListener`（音频诊断）
- `DramaListScreen.kt` — 剧集列表：`LazyVerticalGrid` + 剧集展开面板（120ms 动画）

**ui/component/**
- `VideoPlayer.kt` — ExoPlayer UI 封装（接收外部 ExoPlayer 实例）：使用 `AndroidView` 包装原生 `PlayerView`，手势通过 `GestureDetector` 实现（单击播放/暂停、双击右侧 +15s / 左侧 -15s、长按 1.5x 加速 toggle）、高光检测轮询（500ms，含初始扫描+倒退检测）、音频健康恢复、`AudioDiagnostics` 集成、`onSeekRequest` 回调
- `SpriteOverlay.kt` — 精灵角色渲染：四种表情图片切换（DEFAULT/HAPPY/SAD/INSPIRATION）、呼吸动画（锚定左下角缩放）、气泡图片（`sprite_bubble.png`，alpha=0.65）、双击/长按手势、音频模式切换按钮（紧贴精灵正下方）
- `SpriteTtsManager.kt` — TTS 引擎管理：中文语音（尝试选择女声）、pitch=1.5/rate=0.9/volume=0.7、`AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK` 焦点请求/释放（TTS 结束时自动释放焦点）、`ToneGenerator` 提示音播放
- `AudioDiagnostics.kt` — 音频诊断工具（统一 TAG "AudioDiag"）：焦点丢失/恢复计数、音量恢复计数、播放恢复计数、周期性健康检测（每 2 秒）
- `SpriteFeedbackBar.kt` — 精灵统计信息展示（认同/吐槽计数）
- `HighlightIndicator.kt` — `HighlightMarkers`（进度条高光菱形标记）+ `HighlightSnackbar`（高光触发提示）+ `HighlightDebugDialog`（DEBUG 模式）+ `highlightTypeColor()` / `highlightTypeLabel()` 工具函数
- `DramaCard.kt` — 剧集卡片，使用 `combinedClickable`（长按更灵敏），Coil `AsyncImage` 加载封面
- `EpisodeList.kt` — 横向集数选择（`LazyRow`，用于 DramaListScreen 展开面板）

**ui/navigation/**
- `AppNavigation.kt` — NavHost：`drama_list` → `player/{dramaId}/{episodeId}`

**根目录**
- `MainActivity.kt` — 入口 Activity：存储权限请求（`READ_MEDIA_VIDEO` / `READ_EXTERNAL_STORAGE`，Android 13+ 分区）、`enableEdgeToEdge()`、`ShortDramaTheme` + `AppNavigation`

---

## 3. 核心业务流程

### 3.1 剧集列表 → 播放启动

1. `DramaListScreen` 创建 `DramaListViewModel`，`init` 调用 `loadDramas()`
2. `DramaRepository.getAllDramas()` → Retrofit 请求 `/api/dramas` → 逐剧并发请求 `/api/dramas/{id}/episodes`（`async/awaitAll`）
3. 超时（10s）或失败 → 降级到 `getHardcodedDramas()`，数据指向 FastAPI 服务器
4. 用户点击剧集卡片 → `NavController.navigate("player/$dramaId/$firstEpisodeId")`

### 3.2 高光检测与精灵触发

1. `PlayerScreen` 的 `LaunchedEffect(currentEpisode?.id)` 延迟 200ms 后调用 `HighlightViewModel.loadHighlights(episode.title)`，3s 后若仍为空则重试一次
2. `HighlightRepository.getHighlights(videoId)`：先查 Room 缓存（24h 有效期）→ 缓存命中直接返回 → 未命中请求 `/api/highlights/{video_id}`（15s 超时）→ 失败用过期缓存 → 无缓存用 `fallbackData()`
3. 服务端流程：规则引擎 + AI 引擎 → 融合打分（1-5分）→ 分级（L1/L2）→ 预生成吐槽评论 → 缓存返回
4. `VideoPlayer` 每 500ms 轮询当前位置，遍历 `highlightSegments`，通过窗口判定触发高光（含初始扫描、倒退检测、seek 检测）
5. `HighlightViewModel.onHighlightReached`：去重检查 → 延迟 1.5s 显示精灵 → 优先使用服务端预生成 comments → 情绪状态机选一条展示

### 3.3 吐槽选择与情绪状态机

1. **优先级 1：服务端预生成** — 高光数据自带 `comments` 数组（3-5 条），无需网络请求，离线可用
2. **优先级 2：Room 缓存** — 客户端本地缓存的 AI 吐槽
3. **优先级 3：AI 实时请求** — 仅在以上均无数据时调用（`AiRoastService`，15s 超时）
4. **优先级 4：通用兜底** — `naturalFallback` 返回与剧情相关的随机吐槽
5. `selectBestComment()` / `selectRoastFromCache()` 按优先级排序：① 与上次情绪标签同亲缘组 ② 未展示过的 ③ 冲突标签排最后

### 3.4 用户反馈（双击/长按）

- **双击精灵（高光激活时）**：agreeCount+1 → 当前标签权重+1 → 持久化到 DataStore → 展示开心表情 5s 后恢复
- **长按精灵（高光激活时）**：dislikeCount+1 → 当前标签权重-1 → 持久化 → 展示难过表情 5s 后恢复
- **双击/长按精灵（常驻模式 + 无高光激活）**：展示灵感表情（`SpriteExpression.INSPIRATION`）5s 后恢复默认

### 3.5 精灵常驻开关

- 点击 TopAppBar 中的 Face 图标切换 `isMascotAlwaysVisible`
- 开启后精灵始终显示，关闭后高光 5s 到期自动隐藏
- 状态持久化到 DataStore

---

## 4. 关键设计决策

### 4.1 ExoPlayer 单实例架构

ExoPlayer 提升到 `PlayerScreen` 级别，全剧共享单一实例。切集时仅 `setMediaItem` + `prepare` + `seekTo`，不重建播放器。`VideoPlayer` 改为接收外部 ExoPlayer 参数，`DisposableEffect` 仅管理音频焦点不再 `release()`。生命周期管理（ON_PAUSE/ON_RESUME）通过 `LifecycleEventObserver` 在 PlayerScreen 统一处理。`VerticalPager` 的 `beyondBoundsPageCount=1` 预渲染相邻页，非活跃页通过 `isActive` 标志避免操作共享 ExoPlayer。

### 4.2 音频焦点三层协作

1. **ExoPlayer 视频音频**：`USAGE_MEDIA` + `CONTENT_TYPE_MOVIE`，`AUDIOFOCUS_GAIN`
2. **TTS 精灵语音**：`USAGE_ASSISTANT` + `CONTENT_TYPE_SPEECH`，`AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK`，TTS 结束时自动释放焦点
3. **提示音**：`ToneGenerator`（`STREAM_NOTIFICATION`），无焦点请求

`VideoPlayer` 有三重恢复机制：① 轮询检测音量 < 1f → 恢复；② 检测 `!isPlaying && STATE_READY && !userPaused` → 恢复播放；③ seek 后延迟 300ms 检查音量和播放状态。`AudioDiagnostics` 周期性健康检测（每 2 秒）。

### 4.3 缓存策略（两层客户端 + 四层服务端）

**客户端**：`HighlightRepository` Room 缓存（24h）+ `HighlightViewModel.highlightCache` 内存缓存（最多 20 集）

**服务端**（`CacheManager`）：L1 内存 LRU（100 条，10min）→ L2 文件缓存-高光（24h）→ L3 文件缓存-语音识别（7d）→ L4 文件缓存-规则引擎（24h）。`CACHE_VERSION=4`，启动时全量清除 L2/L3/L4。

### 4.4 小精灵位置锚定与合成

精灵使用 `AnimatedVisibility`（fadeIn/fadeOut）+ `graphicsLayer` 实现动画，`transformOrigin = TransformOrigin(0f, 1f)` 锚定左下角防止缩放漂移。不使用 `CompositingStrategy.Offscreen`（默认 Auto），避免半透明像素 premultiplied alpha 混合产生白色光晕。精灵 PNG 使用原始资源 Lanczos 缩放到 256×256，保留原始 alpha 通道。

### 4.5 高光分级与密度控制

| 级别 | 分数 | 吐槽条数 | 颜色 |
|---|---|---|---|
| L1 | 4-5 | 3 | 亮红 #FF1744 |
| L2 | 2-3 | 2 | 亮橙 #FF9100 |

每集最多 2 个高光点，间隔 >= 剧集时长 × 20%。客户端 `loadHighlights` 仅保留 `L1`/`L2`，取前 2 条。

### 4.6 情绪状态机

四维排序选择吐槽：① 与上次情绪标签的亲缘组匹配（感动/心疼/期待，震惊/无语/吃瓜，爆笑/吃瓜）→ ② 未展示优先 → ③ 冲突降级（感动↔嘲笑/愤怒，愤怒↔爆笑/期待）。`shownRoastIds` 按 `highlightKey` 分组，全部展示后自动重置。

### 4.7 音频模式

`PlaybackProgressStore` 中持久化的三模式枚举：`OFF`（0）静音 → `NOTIFICATION`（1，默认）仅提示音 → `ON`（2）TTS 自动播报。`cycleAudioMode()` 按 NOTIFICATION → OFF → ON → NOTIFICATION 循环。

---

## 5. 协作约定

### 5.1 代码风格

- 优先 Kotlin 惯用法：`data class`、`sealed class`、`object`、扩展函数、`when` 表达式
- Compose 最佳实践：`remember` 稳定引用、`derivedStateOf` 减少重组、`StateFlow.collectAsState()`
- 命名规范：类名 PascalCase，函数/变量 camelCase，常量 UPPER_SNAKE_CASE
- 无冗余注释，代码自解释

### 5.2 网络请求约束

- **所有网络请求必须有超时**：`AiRoastService` 15s、`HighlightRepository` 15s、`DramaRepository` 10s
- 超时后必须有降级处理，不得阻塞 UI
- 使用 `withTimeoutOrNull` 而非裸 `withTimeout`

### 5.3 不得破坏的功能列表

- 视频播放（ExoPlayer 手势控制 + TextureView）
- 高光触发与精灵显示（含初始扫描、倒退检测、seek 检测）
- 服务端预生成吐槽 + 气泡展示
- TTS 语音播报 + 音频焦点管理
- 精灵双击/长按反馈（含情绪权重持久化）
- 精灵常驻开关
- 播放进度持久化与恢复（含 positionCache 内存缓存）
- 上下滑动切集（VerticalPager + 单实例 ExoPlayer）
- 选集面板（200ms 动画）
- 高光进度条标记（HighlightMarkers）
- "⏭ 高光"手动跳转按钮

### 5.4 测试要求

- 当前状态：`HighlightViewModelTest` 14 个用例（loadHighlights、onHighlightReached、onManualJump、toggleMascotAlwaysVisible、dismissSprite、onSpriteDoubleTap、onSpriteLongPress、audioMode、setCurrentEpisode）
- 单元测试使用 JUnit5 + Mockk + Turbine + `UnconfinedTestDispatcher`
- 测试仅覆盖 ViewModel 纯逻辑，不涉及 Room 实际操作、网络请求或 Compose UI
- 修改 ViewModel 公共 API 时需同步更新测试

### 5.5 存储结构变更约束

- Room schema 变更必须有 Migration（参考 `MIGRATION_1_2` ~ `MIGRATION_9_10`），**禁止** `fallbackToDestructiveMigration()`
- 当前数据库版本：**10**（`HighlightDatabase`）
- DataStore Key 变更需考虑旧 Key 的读取兼容

### 5.6 开发环境

- 服务端：conda 虚拟环境 `highlight-server`（Python 3.10），`python -m uvicorn main:app --host 0.0.0.0 --port 3000`
- 模拟器：`local.properties` 中 `serverBaseUrl=http://127.0.0.1:3000`，每次重启后需 `adb reverse tcp:3000 tcp:3000`
- `network_security_config.xml` 已配置 `127.0.0.1`、`localhost`、`10.0.2.2` 的明文 HTTP 许可
- 服务端重启后需调用 `POST /api/cache/load` 预热缓存（`CACHE_VERSION=4` 启动时清空所有文件缓存）
- 视频文件目录：`短剧/`（与 `highlight-server/` 同级）

---

## 6. 已知问题与限制

| # | 问题 | 优先级 |
|---|---|---|
| 1 | 高光自动触发在正常播放时仍不完全可靠（手动跳转正常），可能与 highlightSegments 数据加载延迟或 Compose recomposition 时序有关 | 中 |
| 2 | 预生成吐槽可能引用未出场人物名字（服务端 prompt 缺少具体人物/台词上下文） | 中 |
| 3 | 音频丢失在边缘情况仍可能出现（已有多层恢复机制但未完全根治） | 中 |
| 4 | `EmotionType` 枚举在 `EmotionModels.kt` 中定义但无任何引用，为死代码，待清理 | 低 |
