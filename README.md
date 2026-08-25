# SecondScreen — Android 扩展屏

[![Python CI](https://github.com/SecondScreen/actions/workflows/python-ci.yml/badge.svg)](https://github.com/SecondScreen/actions/workflows/python-ci.yml)
[![Android CI](https://github.com/SecondScreen/actions/workflows/android-ci.yml/badge.svg)](https://github.com/SecondScreen/actions/workflows/android-ci.yml)
[![ShellCheck](https://github.com/SecondScreen/actions/workflows/shellcheck.yml/badge.svg)](https://github.com/SecondScreen/actions/workflows/shellcheck.yml)

将 ARM Android 设备（平板、手机、智慧屏）作为电脑的扩展屏幕使用，支持 **有线 (ADB USB)** 和 **局域网 WiFi** 连接。

## 功能特性

| 特性 | 支持情况 |
|------|---------|
| 分辨率 | 750p (1280×750)、1080p (1920×1080)、2K (2560×1440) |
| 帧率 | 60 / 90 / 120 fps |
| 码率 | 根据分辨率和帧率自动调节，支持 0.5x~3x 质量倍率 |
| 连接方式 | USB (ADB 反向隧道) / 局域网 WiFi |
| 编码器 | H.264 (首选) / VP9 / VP8，自动检测最优 |
| 触摸回传 | 触摸屏 → PC 鼠标（移动、点击、滚轮） |
| 延迟优化 | MediaCodec 低延迟模式、零缓冲帧、GStreamer zerolatency |
| 低配支持 | 编解码器自动降级，帧率自适应 |


## 项目结构

```
ADB-PSP/
├── host/                     # PC 端
│   ├── psp_host/             # Python 包
│   │   ├── config.py         # 分辨率/帧率/码率配置和 CLI 解析
│   │   ├── protocol.py       # TCP 帧协议打包/解包
│   │   ├── capture.py        # GStreamer 屏幕捕获和编码管道
│   │   ├── input_linux.py    # Linux 输入注入 (xdotool/XTest)
│   │   ├── input_windows.py  # Windows 输入注入 (SendInput)
│   │   ├── server.py         # TCP 服务器和会话管理
│   │   ├── main.py           # 入口点
│   │   └── __main__.py       # python -m psp_host 支持
│   ├── scripts/
│   │   ├── setup-virtual-display.sh  # 创建虚拟显示器 (X11)
│   │   └── 99-psp-dummy.conf         # Xorg 虚拟显示器驱动配置
│   └── requirements.txt
├── android/                  # Android 端 (Kotlin)
│   ├── app/src/main/java/com/psp/app/
│   │   ├── MainActivity.kt   # 主界面 + 触摸输入
│   │   ├── StreamClient.kt   # TCP 连接 + 协议握手
│   │   ├── DecoderThread.kt  # MediaCodec 硬件解码
│   │   ├── InputSender.kt    # 输入事件回传
│   │   └── SettingsDialog.kt # 画质设置对话框
│   └── ... (Gradle 构建文件)
├── scripts/
│   └── adb-usb-setup.sh      # ADB USB 连接一键脚本
├── docs/
│   └── PROTOCOL.md           # 通信协议文档
├── tests/
│   └── test_protocol.py      # 协议单元测试 (13 项)
└── README.md
```


## 快速开始

### 方法一：一键安装（推荐）

拉下代码后执行以下命令，脚本会自动识别你的发行版（Debian/Ubuntu、Fedora、Arch、openSUSE）并安装全部依赖：

```bash
./setup.sh
```

安装完成后运行环境自检，检查缺什么会给出明确提示。也可以随时重跑自检：

```bash
./setup.sh --check
```

### 方法二：手动安装

**1. 安装依赖**

```bash
# GStreamer + 编码器
sudo apt install gstreamer1.0-plugins-good gstreamer1.0-plugins-bad \
                 gstreamer1.0-plugins-ugly gstreamer1.0-libav \
                 python3-gi python3-gi-cairo gir1.2-gstreamer-1.0

# 输入注入 (Linux)
sudo apt install xdotool

# 虚拟显示器 (可选，用于扩展模式)
sudo apt install xserver-xorg-video-intel  # Intel GPU
```

> 💡 Wayland 用户（Hyprland/GNOME/KDE）还需安装：`gstreamer1.0-pipewire`、`xdg-desktop-portal-*`（对应桌面后端，如 `xdg-desktop-portal-hyprland`）；`screencast.py` 会自动检测后端并直连，无需额外配置。

**2. 启动服务端**

```bash
cd host

# 查看可用编码器和显示器输出
python -m psp_host --list-outputs

# 方式一：WiFi 连接（先创建虚拟显示器，见下方）
python -m psp_host --output VIRTUAL1 --resolution 1080p --fps 90

# 方式二：USB 连接（ADB 反向隧道自动设置）
python -m psp_host --adb-usb --resolution 1080p --fps 90

# 方式三：指定捕获区域（无需虚拟显示器，但会镜像该区域）
python -m psp_host --region 0,0,1920x1080 --fps 60
```

### 创建虚拟显示器 (扩展模式)

**方法一：Xorg 配置 (推荐，重启后生效)**

```bash
sudo cp host/scripts/99-psp-dummy.conf /etc/X11/xorg.conf.d/
# 重启 X11 会话，然后:
host/scripts/setup-virtual-display.sh --mode 1920x1080 --rate 60
```

**方法二：Intel GPU 动态创建**

```bash
host/scripts/setup-virtual-display.sh --mode 1920x1080 --rate 60
```

方法二会自动尝试 `intel-virtual-output` 等工具，不需要重启动。


### Android 端 (ARM)

**1. 构建 APK**

在 Android Studio 中打开 `android/` 目录，构建并安装到设备：

```bash
# 或者用命令行:
cd android
./gradlew assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

**2. 连接**

- **WiFi 模式**: 打开 PSP 应用，输入 PC 的局域网 IP 和端口
- **USB 模式**: 用 USB 连接手机，PC 运行 `adb reverse`，然后点 Android 上的「USB 模式」

```bash
# 一键完成 USB 连接
./scripts/adb-usb-setup.sh --start-host -- --resolution 1080p --fps 90
```


## 使用场景

### 平板作为扩展屏

1. 平板通过 USB 连接电脑
2. PC 运行 `python -m psp_host --adb-usb --resolution 2K --fps 60`
3. 平板打开 PSP 应用，点击「USB 模式」
4. 平板将显示扩展桌面，拖动窗口到平板方向

### 老旧手机作为监控副屏

1. 手机通过 WiFi 连接局域网
2. PC 创建虚拟显示器：`setup-virtual-display.sh --mode 1920x1080 --rate 60`
3. PC 启动服务：`python -m psp_host --output VIRTUAL1 --resolution 1080p --fps 60`
4. 手机打开 PSP 应用，输入 PC IP 地址连接

### 智慧屏/电视盒子作为大屏显示器

1. 电视盒子通过网线连接到局域网
2. PC 端以 2K@60 运行
3. 电视盒子安装 PSP 应用，触摸/鼠标操作通过遥控器或蓝牙鼠标


## 高级用法

### 参数详解

```bash
# 全量参数
python -m psp_host --help

# 示例：1080p@120fps 高质量
python -m psp_host --output VIRTUAL1 --resolution 1080p --fps 120 --quality 2.0

# 示例：2K@90fps 中等带宽
python -m psp_host --output VIRTUAL1 --resolution 2K --fps 90 --quality 1.0

# 示例：指定端口和多设备
python -m psp_host --port 4748 --region 1920,0,1920x1080

# 禁用输入回传（仅显示）
python -m psp_host --output VIRTUAL1 --no-input
```

### 延迟优化

| 优化项 | 说明 |
|--------|------|
| USB 有线连接 | 延迟最低 (~5-15ms) |
| 5GHz WiFi | 推荐，避开 2.4GHz 干扰 |
| H.264 编码器 | 延迟最低，兼容性最好 |
| 关闭输入回传 | 减少输入处理开销 |
| 降低帧率 | 60fps 比 120fps 编码延迟更低 |
| x264enc ultrafast | 最低编码延迟（但压缩率低，带宽需求大） |


## 编解码器说明

| 编码器 | 需要安装 | 延迟 | 画质 | 兼容性 |
|--------|---------|------|------|--------|
| x264enc (软) | `gstreamer1.0-plugins-ugly` | ⭐⭐⭐ | ⭐⭐⭐ | 所有 Android |
| nvh264enc (硬) | NVIDIA 驱动 + gst-plugins-bad | ⭐⭐⭐⭐ | ⭐⭐⭐ | NVIDIA GPU |
| vaapih264enc (硬) | `gstreamer1.0-vaapi` | ⭐⭐⭐⭐ | ⭐⭐⭐ | Intel/AMD GPU |
| vp9enc (软) | `gstreamer1.0-plugins-good` | ⭐⭐ | ⭐⭐⭐⭐ | Android 5+ |
| vp8enc (软) | `gstreamer1.0-plugins-good` | ⭐⭐⭐ | ⭐⭐ | 所有 Android |

系统会自动检测最优编码器，优先级：x264enc > nvh264enc > vaapih264enc > vp9enc > vp8enc。


## 通信协议

参考 `docs/PROTOCOL.md`。简要：

- 基于 TCP，默认端口 4747
- JSON 握手协商参数
- 视频帧通过二进制帧传输（长度前缀 + 标志位 + 编码数据）
- 控制/输入消息复用同一 TCP 连接（通过标志位区分）
- 输入坐标归一化到 0..1，支持鼠标移动、点击、滚轮


## 已知限制

- **Wayland**: 当前仅支持 X11 截屏。Wayland 用户可尝试 `pipewiresrc` 或使用 XWayland 回退
- **Windows 虚拟显示器**: 需要第三方驱动（如 `usbmmidd_v2` 或 IddSampleDriver），暂未集成
- **Android 编解码器**: 部分设备的 VP9 解码器可能不支持 120fps。H.264 兼容性最好
- **音频**: 暂不支持音频传输，仅视频
- **多连接**: 当前仅支持单设备连接


## 开发计划

- [ ] Wayland 支持 (PipeWire screencast portal)
- [ ] Windows 虚拟显示器集成
- [ ] 自适应码率 (根据网络条件动态调节)
- [ ] 多设备同时连接
- [ ] 音频传输
- [ ] 自动发现设备 (mDNS)
- [ ] 剪切板同步
- [ ] 键盘输入回传


## 许可证

MIT License