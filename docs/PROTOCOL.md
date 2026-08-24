# PSP 协议 v1

## 概述

PSP 协议用于将 PC 屏幕内容实时流式传输到 Android 设备（作为扩展显示器），并支持触摸/鼠标事件回传。

- 传输层：TCP（局域网 WiFi 或 ADB 反向隧道 USB 连接）
- 视频编码：H.264（首选）/ VP9 / VP8
- 控制通道：复用 TCP 连接，通过帧标志区分

## 连接建立

### 握手

1. 客户端（Android）连接服务端（PC）TCP 端口（默认 4747）
2. 客户端发送 JSON 握手请求（控制帧，flags=0x80）

```json
{
  "type": "hello",
  "proto": 1,
  "device": "Pixel-7",
  "want": {
    "codec": "h264",
    "width": 1920,
    "height": 1080,
    "fps": 90,
    "bitrate_kbps": 16000
  },
  "input": true
}
```

3. 服务端回复 JSON：

```json
{
  "type": "welcome",
  "ok": true,
  "session": "uuid",
  "codec": "h264",
  "width": 1920,
  "height": 1080,
  "fps": 90,
  "bitrate_kbps": 16000,
  "virtual_display_width": 1920,
  "virtual_display_height": 1080
}
```

### 错误响应

```json
{"type": "welcome", "ok": false, "reason": "Unsupported codec"}
```

## 帧格式

所有数据包使用统一帧头：

```
+--------+--------+--------+--------+--------+------------------+
|  长度 (LE u32)   | 标志   | 负载 ...                          |
+--------+--------+--------+--------+--------+------------------+
```

- 长度：4 字节，小端无符号整数，**包含标志字节 + 负载**（即总长度 = 1 + payload_len）
- 标志：1 字节位掩码
  - bit 0：关键帧（IDR）
  - bit 1：配置数据（SPS/PPS 等）
  - bit 2：丢弃标记（通知客户端该帧可跳过）
  - bit 7：控制帧（负载为 JSON）

### 视频帧

- 负载为完整 H.264/VP8/VP9 访问单元（Annex-B 字节流格式，含起始码 `00 00 00 01`）
- 关键帧前必须包含 SPS/PPS 数据
- 服务端定期发送包含配置数据的帧

### 控制帧（flags & 0x80）

JSON 负载，UTF-8 编码。

#### 方向：服务端 → 客户端

```json
{"type": "ping", "id": 1}
{"type": "pong", "id": 1}
{"type": "stats", "capture_fps": 60.0, "encode_fps": 60.0, "bitrate_kbps": 15000}
```

#### 方向：客户端 → 服务端

```json
{"type": "ping", "id": 1}
{"type": "pong", "id": 1}
```

**输入事件（input=true 时使用）：**

```json
// 鼠标移动
{"type": "input", "kind": "move", "x": 0.5, "y": 0.25}
// 鼠标按钮（x,y 归一化到 0..1，相对于流化显示区域）
{"type": "input", "kind": "btn", "x": 0.5, "y": 0.25, "btn": 1, "state": 1}
// 滚轮（dx, dy 为滚动增量）
{"type": "input", "kind": "wheel", "dx": 0, "dy": -3}
// 按键（非必须，Android 可回传 USB 键盘）
{"type": "input", "kind": "key", "keycode": 65, "state": 1}
```

输入按钮值：
- 1 = 左键
- 2 = 右键
- 3 = 中键

输入状态值：
- 0 = 释放
- 1 = 按下

## 会话生命周期

1. 客户端连接 → 握手
2. 服务端开始编码器，推送视频帧
3. 客户端解码并渲染
4. 客户端发送输入事件
5. 任一方断开连接时结束会话

## 编解码器参数

### H.264
- Profile: High (for 1080p+), Baseline (for 720p)
- Level: 4.2 (1080p@120) / 5.2 (2K@120)
- Entropy: CABAC (High), CAVLC (Baseline)
- B-frames: 0 (最低延迟)
- Reference frames: 1
- Tune: zerolatency
- Key-int-max: fps * 2
- Bitrate: 动态调节

### VP9
- cpu-used: 8 (最快编码)
- deadline: 1 (realtime)
- lag-in-frames: 0
- quality: realtime

### VP8
- 同上，cpu-used: 8, deadline: 1

## 传输参数

- TCP_NODELAY 启用
- 发送缓冲区：4MB
- 接收缓冲区：4MB
- 默认端口：4747