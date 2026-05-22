# Short-drama-live-interaction
短剧场景中有很多剧情高光、反转、名场面，同时针对追更到最新集、剧集结束场景，用户往往情绪高涨，有情绪表达的诉求；当前市面上的产品主要由弹幕、评论来承载，但这两类表达方式都需要输入文字，表达门槛高，且会中断观看。因此希望提供更丰富的互动能力，满足用户看剧过程中的即时情绪表达，并增加剧情参与感。

## 功能特性

- 短剧列表展示（封面+标题+描述）
- 竖屏全屏播放
- 上下滑动切换剧集（短视频风格）
- 单击播放/暂停
- 双击右侧快进15s / 双击左侧快退15s
- 长按1.5倍速播放
- 左滑打开选集面板

## 技术栈

- **客户端**: Kotlin + Jetpack Compose + Media3(ExoPlayer)
- **服务端**: Node.js HTTP 视频服务器
- **架构**: MVVM

## 快速开始

### 1. 克隆项目

```bash
git clone https://github.com/your-username/Short-drama-live-interaction.git
cd Short-drama-live-interaction
```

### 2. 准备视频文件

**视频需要单独下载**（文件太大无法上传到Git）。

下载后，将视频文件按以下结构放到项目根目录：

```
Short-drama-live-interaction/
├── 短剧/
│   ├── 北派寻宝笔记/
│   │   ├── cover/
│   │   │   └── cover.jpg
│   │   ├── 第63集.mp4
│   │   ├── 第64集.mp4
│   │   └── ...
│   └── 天下第一纨绔/
│       ├── cover/
│       │   └── cover.jpg
│       ├── 第1集.mp4
│       ├── 第2集.mp4
│       └── ...
```

> **视频下载地址**: [待补充网盘链接]

### 3. 启动视频服务器

```bash
cd video-server
npm install  # 首次运行需要安装依赖（如果有的话）
node server.js
```

服务器启动后会显示：
```
视频服务器已启动: http://localhost:8080
```

### 4. 运行 Android 应用

1. 用 Android Studio 打开 `android/` 目录
2. 等待 Gradle 同步完成
3. 连接设备或启动模拟器
4. 点击 Run 运行

**注意**: 
- 模拟器使用 `http://10.0.2.2:8080` 访问主机
- 真机需要修改 `DramaRepository.kt` 中的 `BASE_URL` 为你电脑的局域网 IP

## 项目结构

```
├── android/                    # Android 客户端
│   ├── app/src/main/java/com/shortdrama/interaction/
│   │   ├── data/              # 数据模型和仓库
│   │   ├── ui/                # UI 组件和页面
│   │   ├── viewmodel/         # ViewModel
│   │   └── theme/             # 主题
│   └── build.gradle.kts
├── video-server/              # 视频服务器
│   └── server.js
└── 短剧/                      # 视频资源（需单独下载）
```

## 操作说明

| 操作 | 功能 |
|------|------|
| 上滑 | 下一集 |
| 下滑 | 上一集 |
| 单击屏幕 | 播放/暂停 |
| 双击右侧 | 快进 15s |
| 双击左侧 | 快退 15s |
| 长按 | 1.5x 加速播放 |
| 左滑 | 打开选集面板 |
