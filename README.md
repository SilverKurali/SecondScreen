# SecondScreen — 把 Android 设备变成电脑扩展屏

[![Python CI](https://github.com/SilverKurali/SecondScreen/actions/workflows/python-ci.yml/badge.svg)](https://github.com/SilverKurali/SecondScreen/actions/workflows/python-ci.yml)
[![Android CI](https://github.com/SilverKurali/SecondScreen/actions/workflows/android-ci.yml/badge.svg)](https://github.com/SilverKurali/SecondScreen/actions/workflows/android-ci.yml)
[![Release](https://github.com/SilverKurali/SecondScreen/actions/workflows/release.yml/badge.svg)](https://github.com/SilverKurali/SecondScreen/actions/workflows/release.yml)
[![ShellCheck](https://github.com/SilverKurali/SecondScreen/actions/workflows/shellcheck.yml/badge.svg)](https://github.com/SilverKurali/SecondScreen/actions/workflows/shellcheck.yml)

将 ARM Android 设备（平板、手机、智慧屏）作为电脑的**扩展屏幕**，支持 **USB (ADB 反向隧道)** 与 **局域网 WiFi** 两种连接方式，触摸操作实时回传为鼠标事件。

## 下载发行版

前往 [Releases](https://github.com/SilverKurali/SecondScreen/releases) 下载最新版本：

| 产物 | 说明 |
|------|------|
| `SecondScreen-android-*.apk` | Android 客户端，直接安装（arm64-v8a / armeabi-v7a，Android 8.0+） |
| `SecondScreen-linux-x64-*.tar.gz` | Linux 宿主端源码包 |
| `SecondScreen-windows-x64-*.zip` | Windows 宿主端源码包（功能受限，见平台支持） |
| `checksums.txt` | 产物校验和 |

## 功能特性

| 特性 | 支持情况 |
|------|---------|
| 分辨率 | 720p / 750p / 1080p / 2K，协商时自动钳制并做偶数对齐 |
| 帧率 | 60 / 90 / 120 / 144 fps，客户端可选"无限制"（跟随服务端上限） |
| 画质倍率 | 0.5x ~ 3.0x，安卓端设置即时生效（上限为服务端基准码率 3 倍） |
| 编码器 | H.264（默认）/ VP9 / VP8，自动检测：x264enc > nvh264enc > vaapih264enc > vp9enc > vp8enc |
| 连接方式 | WiFi 局域网（UDP 自动发现）/ USB ADB 反向隧道 / 无线 ADB |
| 触摸回传 | 移动、单击=左键、长按 500ms=右键、滚轮 |
| 虚拟显示器 | 自动创建：Wayland Portal（Hyprland/GNOME/KDE）、Hyprland HEADLESS、EVDI、Xvfb 回退 |
| 图形控制台 | GTK4 多标签 GUI（服务启停、实时日志、设备扫描、虚拟屏管理、工具箱） |
| 延迟优化 | MediaCodec 低延迟解码、GStreamer zerolatency、零缓冲帧队列、TCP_NODELAY |

## 快速开始

### 1. 宿主端（PC）

```bash
git clone https://github.com/SilverKurali/SecondScreen.git
cd SecondScreen

# 一键安装全部依赖并创建内置 venv（支持 Debian/Ubuntu、Fedora、Arch、openSUSE）
./setup.sh
./setup.sh --check     # 仅环境自检，不安装

# 图形控制台（推荐）
./gui.py

# 或者交互式管理菜单（启动/停止/虚拟屏/状态）
./start.sh
```

命令行方式：

```bash
cd host
python -m psp_host --resolution 1080p --fps 60          # 自动创建虚拟显示器
python -m psp_host --output VIRTUAL1 --fps 90           # 指定已有输出
python -m psp_host --region 1920,0,1920x1080            # 捕获指定区域
python -m psp_host --adb auto --resolution 2K           # 自动设置 ADB 反向隧道
python -m psp_host --list-outputs                       # 列出输出与编码器
```

### 2. 安卓端

安装 Release 中的 APK（或 `cd android && ./gradlew assembleDebug` 自行构建），打开应用：

- **WiFi 模式**：自动发现局域网内的 PSP 主机（UDP 4748），也可手动输入 IP:端口
- **USB 模式**：USB 连接电脑后点「USB 模式」（宿主端用 `--adb auto` 已建好反向隧道，一键脚本见 `scripts/adb-usb-setup.sh`）

连接后点悬浮球菜单可调 **画质设置**（分辨率 / 帧率 / 画质倍率）与 **编码器**（软件 x264 / 硬件 NVENC）。

## 宿主端参数

```
显示/捕获:  -o/--output        X11 输出名（自动探测几何，扩展模式）
            -r/--region        捕获区域 x,y,WxH
            -f/--fullscreen    捕获整个主屏（不推荐）
            -l/--list-outputs  列出输出与编码器

视频质量:   --resolution       720p | 750p | 1080p | 2K（默认 1080p）
            --fps              60 | 90 | 120 | 144（默认 60）
            --quality          码率倍率 0.5-3.0（默认 1.0）
            --codec            auto | h264 | vp9 | vp8

连接:       -p/--port          TCP 端口（默认 4747）
            --adb              off | auto | usb | wireless
            --list-devices     扫描局域网主机与 ADB 设备

其他:       --no-input         禁用触摸回传
            --gui              启动 GTK4 图形控制台
            --debug            调试日志
```

码率按 `(分辨率, 帧率)` 查基准表（如 1080p@60 = 10000 kbps、2K@144 = 38400 kbps），再乘画质倍率；安卓端与服务端共用同一套基准表。

## 平台支持

| 平台 | 捕获 | 虚拟显示器 | 输入注入 | 说明 |
|------|------|-----------|---------|------|
| Linux X11 | ximagesrc | Xorg dummy / intel-virtual-output | evdev uinput + xdotool | 完整支持 |
| Linux Wayland | pipewiresrc (Portal) | Hyprland HEADLESS / EVDI / Xvfb | evdev uinput + hyprctl | Hyprland 最佳；GNOME 可能需 EVDI 模块（`host/setup-evdi.sh`） |
| Windows | DXGI 屏幕捕获 | 需第三方驱动（未集成） | SendInput | 功能受限 |
| Android 8.0+ | — | — | — | MediaCodec 硬解 H.264/VP9/VP8，仅 ARM |

## 通信协议

详见 [docs/PROTOCOL.md](docs/PROTOCOL.md)。要点：

- TCP **4747**：二进制帧 = 4 字节小端长度 + 1 字节标志 + 载荷；标志位 bit0=关键帧、bit1=config、bit2=可丢弃、bit7=控制帧
- JSON 握手（hello/welcome）协商编码、分辨率、帧率、码率；服务端按能力钳制并做偶数对齐，码率尊重客户端画质倍率（≤3 倍基准）
- UDP **4748**：局域网主机发现（每个网段仅一个服务端）
- 输入事件归一化坐标 (0..1)，服务端映射到虚拟显示器实际分辨率
- 帧队列上限 3，队列满时丢弃非关键帧、关键帧清队（按设计）

## 项目结构

```
SecondScreen/
├── host/                        # 宿主端（Python 3 + GStreamer）
│   ├── psp_host/
│   │   ├── main.py / __main__.py   # 入口
│   │   ├── config.py               # 分辨率/帧率/码率表 + CLI
│   │   ├── protocol.py             # 帧协议与协商
│   │   ├── server.py               # TCP 服务与会话
│   │   ├── capture.py              # GStreamer 捕获/编码管线
│   │   ├── screencast.py           # Wayland Portal ScreenCast
│   │   ├── vdisplay.py             # 虚拟显示器创建
│   │   ├── gui.py                  # GTK4 图形控制台
│   │   ├── input_linux.py / input_windows.py
│   │   └── discovery.py            # UDP 发现 + ADB 监听
│   ├── run.sh / setup-evdi.sh / bundle_runtime.sh
│   └── scripts/                 # 虚拟显示器脚本
├── android/                     # 安卓端（Kotlin）
│   └── app/src/main/java/com/psp/app/
│       ├── MainActivity.kt      # 主界面 + 触摸
│       ├── StreamClient.kt      # TCP + 握手
│       ├── DecoderThread.kt     # MediaCodec 解码
│       ├── InputSender.kt       # 输入回传
│       ├── SettingsDialog.kt    # 画质设置
│       └── DiscoveryClient.kt   # UDP 发现
├── scripts/adb-usb-setup.sh     # ADB 一键脚本
├── docs/PROTOCOL.md
├── tests/test_protocol.py       # 协议单元测试（17 项）
├── gui.py                       # GUI 跨平台启动器
├── setup.sh / start.sh
```

## 开发与测试

```bash
# Python 检查（CI 同款）
python3 -m flake8 --max-line-length=127 --max-complexity=15 host/psp_host/
python3 -m mypy --ignore-missing-imports --follow-imports=skip host/psp_host/
python3 -m unittest tests.test_protocol -v

# 构建 APK（需 JDK 17 + Android SDK）
cd android && ./gradlew assembleDebug --no-daemon

# 发布发行版：打 tag 触发 Actions → Release（Linux/Windows/Android 三平台产物）
git tag v0.x.0 && git push origin v0.x.0
```

## 已知限制

- **键盘输入**：协议预留 `key` 事件，宿主端尚未实现
- **音频**：仅视频传输，暂不支持音频
- **多连接**：单服务端面向单设备（局域网发现仅广播一个实例）
- **Windows**：捕获可用，虚拟显示器需第三方驱动，输入注入受限
- **输入权限**：Linux evdev 注入需用户在 `input` 组（`setup.sh` 自动添加，**重启后生效**）；缺失时鼠标按键/滚轮会静默失败

## 许可证

MIT License
