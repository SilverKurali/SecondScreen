"""grim 循环抓帧器 — Wayland 可靠屏幕捕获.

绕过 xdph (xdg-desktop-portal-hyprland) 不可靠的 PipeWire 流。
grim 使用 wlr-screencopy 协议，每次调用主动请求一帧，保证拿到最新画面。

用法:
    from grim_capture import GrimCapture
    cap = GrimCapture(output="HEADLESS-2", width=1920, height=1080)
    frame = cap.grab()          # 返回 (width, height, rgb_bytes) 或 None
    cap.close()
"""

import shutil
import subprocess
import threading
import time
import logging

logger = logging.getLogger(__name__)

# PPM P6 最大头部大小（P6 + 注释 + 尺寸 + 最大值）
_MAX_HEADER = 256


class GrimCapture:
    """基于 grim 的 Wayland 屏幕抓帧器."""

    def __init__(self, output=None, region=None, width=1920, height=1080):
        """
        Args:
            output: 输出名（如 HEADLESS-2）。与 region 二选一。
            region: 区域 "x,y,WxH"（布局坐标）。
            width/height: 期望的原始尺寸（用于校验）。
        """
        if shutil.which("grim") is None:
            raise RuntimeError("grim 未安装（Wayland 抓帧必需）: sudo apt install grim")
        self.output = output
        self.region = region
        self.width = width
        self.height = height
        self._last_frame = None
        self._last_size = None

    def _build_cmd(self):
        cmd = ["grim"]
        if self.output:
            cmd += ["-o", self.output]
        elif self.region:
            cmd += ["-g", self.region]
        cmd += ["-t", "ppm", "-"]  # PPM 输出到 stdout
        return cmd

    def grab(self):
        """抓取一帧，返回 (width, height, rgb_bytes)；失败返回 None."""
        try:
            result = subprocess.run(
                self._build_cmd(),
                capture_output=True, timeout=5,
            )
            if result.returncode != 0:
                logger.warning("grim 返回错误码 %d: %s", result.returncode,
                               result.stderr.decode(errors="replace").strip()[:200])
                return None
            return self._parse_ppm(result.stdout)
        except subprocess.TimeoutExpired:
            logger.warning("grim 超时")
            return None
        except Exception as e:
            logger.warning("grim 抓帧异常: %s", e)
            return None

    @staticmethod
    def _parse_ppm(data):
        """解析 PPM P6 二进制. 返回 (w, h, rgb_bytes) 或 None."""
        try:
            if not data.startswith(b"P6"):
                return None
            # 读取头部（跳过注释）
            pos = 2
            parts = []
            while len(parts) < 3:
                # 跳过空白和注释
                while pos < len(data) and data[pos] in b" \t\r\n":
                    pos += 1
                if data[pos:pos+1] == b"#":
                    while pos < len(data) and data[pos:pos+1] != b"\n":
                        pos += 1
                    continue
                start = pos
                while pos < len(data) and data[pos] not in b" \t\r\n":
                    pos += 1
                if pos >= len(data):
                    return None
                parts.append(data[start:pos].decode())
                # 消耗后面的空白
                while pos < len(data) and data[pos] in b" \t\r\n":
                    pos += 1
            width, height, maxval = int(parts[0]), int(parts[1]), int(parts[2])
            if maxval != 255:
                logger.warning("不支持的 PPM maxval: %d", maxval)
                return None
            # 二进制数据从 pos 开始
            rgb = data[pos:pos + width * height * 3]
            if len(rgb) != width * height * 3:
                logger.warning("PPM 数据不完整: 需要 %d 实际 %d",
                               width * height * 3, len(rgb))
                return None
            return width, height, rgb
        except Exception as e:
            logger.warning("PPM 解析失败: %s", e)
            return None

    def grab_loop(self, callback, interval=1.0/30.0, stop_event=None):
        """持续抓帧并回调. callback(frame) 返回 False 可停止."""
        logger.info("grim 抓帧循环启动: output=%s", self.output)
        while stop_event is None or not stop_event.is_set():
            t0 = time.monotonic()
            frame = self.grab()
            if frame is not None:
                try:
                    if callback(frame) is False:
                        break
                except Exception as e:
                    logger.warning("grim 回调异常: %s", e)
            elapsed = time.monotonic() - t0
            if interval > elapsed:
                time.sleep(interval - elapsed)


def main():
    """命令行快速测试: 连续抓帧统计."""
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    import sys
    output = sys.argv[1] if len(sys.argv) > 1 else "HEADLESS-2"
    cap = GrimCapture(output=output)
    n = 0
    t0 = time.monotonic()
    while time.monotonic() - t0 < 3:
        frame = cap.grab()
        if frame:
            n += 1
    dt = time.monotonic() - t0
    print(f"grim 抓帧: {n} 帧 / {dt:.1f}s = {n/dt:.1f} fps")
    print(f"帧大小: {frame[0]}x{frame[1]} ({len(frame[2])} bytes)")


if __name__ == "__main__":
    main()