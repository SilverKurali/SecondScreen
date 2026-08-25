"""TCP server for PSP protocol: accepts device connections, streams video, handles input."""

import json
import logging
import os
import platform
import queue
import socket
import struct
import threading
import time
import traceback

from . import protocol as proto
from .capture import build_pipeline, _is_wayland

logger = logging.getLogger(__name__)


class Session:
    """Manages one connected Android device session.

    - Negotiates parameters via handshake
    - Runs GStreamer pipeline to capture/encode screen
    - Sends encoded frames over TCP
    - Receives and injects input events
    """

    def __init__(self, sock, addr, args):
        self._sock = sock
        self._addr = addr
        self._args = args
        self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        # Large socket buffers for smooth streaming
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4 * 1024 * 1024)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)

        self._frame_queue = queue.Queue(maxsize=3)
        self._running = False
        self._pipe = None
        self._injector = None
        self._reader = proto.FrameReader(sock)
        # Display offset for EVDI virtual display (from args if set)
        self._display_offset_x = getattr(args, '_display_offset_x', 0) or 0
        self._display_offset_y = getattr(args, '_display_offset_y', 0) or 0

    def run(self):
        """Run the session: negotiate, then start streaming."""
        self._running = True
        try:
            # Step 1: Negotiate
            if not self._negotiate():
                return

            # Step 2: Initialize input injector
            if not self._args.no_input:
                self._init_injector()

            # Step 3: Start GStreamer pipeline
            if not self._start_pipeline():
                return

            # Step 4: Start writer thread
            writer = threading.Thread(target=self._writer_loop, daemon=True, name="writer")
            writer.start()

            # Step 5: Start keepalive thread (pings phone while waiting for first frame)
            keepalive = threading.Thread(target=self._keepalive_loop, daemon=True, name="keepalive")
            keepalive.start()

            # Step 6: Reader loop (input processing)
            self._reader_loop()

        except Exception:
            logger.error("Session error: %s", traceback.format_exc())
        finally:
            self._cleanup()

    def _negotiate(self):
        """Read handshake JSON and send welcome response."""
        try:
            flags, payload = self._reader.read_frame()
            if not (flags & proto.FLAG_CONTROL):
                logger.warning("Expected control frame for handshake")
                return False

            hello = json.loads(payload.decode("utf-8"))
            logger.info("Handshake from %s: %s", self._addr, hello.get("device", "unknown"))

            # 提取设备屏幕尺寸（用于比例自适应）
            screen_width = hello.get("screen_width", 0)
            screen_height = hello.get("screen_height", 0)
            if screen_width > 0 and screen_height > 0:
                device_ratio = screen_width / screen_height
                logger.info("Device screen: %dx%d (ratio=%.2f)", screen_width, screen_height, device_ratio)
                # 根据设备比例调整请求分辨率
                want = hello.get("want", {})
                want_w = want.get("width", 1920)
                want_h = want.get("height", 1080)
                if device_ratio > 1.0:  # 横屏
                    want["height"] = int(want_w / device_ratio)
                else:  # 竖屏
                    want["width"] = int(want_h * device_ratio)
            else:
                want = hello.get("want", {})
            have = {
                "codec": self._args.codec,
                "width": self._args.width,
                "height": self._args.height,
                "fps": self._args.fps,
                "bitrate_kbps": self._args.bitrate_kbps,
            }
            ok, response = proto.negotiate(want, have)
            if not ok:
                self._send_control(response)
                return False

            self._send_control(response)
            self._session_params = response
            logger.info("Session negotiated: %dx%d@%d %s %dkbps",
                        response["width"], response["height"],
                        response["fps"], response["codec"],
                        response["bitrate_kbps"])
            return True

        except (ConnectionError, ValueError, json.JSONDecodeError) as e:
            logger.error("Negotiation failed: %s", e)
            return False

    def _init_injector(self):
        """Create platform-appropriate input injector."""
        system = platform.system()
        if system == "Linux":
            from .input_linux import InputInjector
            self._injector = InputInjector(
                screen_width=self._args.width,
                screen_height=self._args.height,
                display=self._args.display,
            )
        elif system == "Windows":
            from .input_windows import InputInjector
            self._injector = InputInjector(
                screen_width=self._args.width,
                screen_height=self._args.height,
            )
        else:
            logger.warning("No input injection available on %s", system)

    def _start_pipeline(self):
        """Start the GStreamer capture/encode pipeline."""
        try:
            import gi
            gi.require_version("Gst", "1.0")
            from gi.repository import Gst, GLib  # noqa: F401

            # Use the negotiated codec/resolution from handshake, not the CLI default.
            if hasattr(self, '_session_params') and self._session_params:
                negotiated_codec = self._session_params.get("codec")
                if negotiated_codec:
                    self._args.codec = negotiated_codec
                # 使用协商后的分辨率(用户可能在安卓端改了画质设置)
                neg_w = self._session_params.get("width")
                neg_h = self._session_params.get("height")
                if neg_w and neg_h:
                    self._args.width = neg_w
                    self._args.height = neg_h
                    logger.info("Using negotiated resolution: %dx%d", neg_w, neg_h)
                # 应用协商后的 bitrate (客户端根据画质设置计算)
                neg_bitrate = self._session_params.get("bitrate_kbps")
                if neg_bitrate and neg_bitrate > 0:
                    self._args.bitrate_kbps = neg_bitrate
                    logger.info("Using negotiated bitrate: %d kbps", neg_bitrate)
                # 应用协商后的 fps
                neg_fps = self._session_params.get("fps")
                if neg_fps and neg_fps > 0:
                    self._args.fps = neg_fps
                    logger.info("Using negotiated fps: %d", neg_fps)
                # 如果客户端请求硬件编码,在 args 中标记
                use_hw = self._session_params.get("use_hardware_encoder", False)
                if use_hw:
                    self._args._use_hardware_encoder = True

            codec, pipeline_str, sink_name = build_pipeline(self._args)
            self._codec = codec

            self._pipe = Gst.parse_launch(pipeline_str)
            sink = self._pipe.get_by_name(sink_name)
            if not sink:
                logger.error("Appsink '%s' not found in pipeline", sink_name)
                return False

            # Use a pull-based approach instead of signal callbacks
            # to avoid GStreamer callback issues
            self._appsink = sink
            sink.set_property("emit-signals", False)

            self._pipe.set_state(Gst.State.PLAYING)
            logger.info("Pipeline started")

            # Start a thread to pull frames from appsink
            puller = threading.Thread(target=self._pull_loop, daemon=True, name="puller")
            puller.start()
            return True

        except Exception as e:
            logger.error("Failed to start pipeline: %s", traceback.format_exc())
            return False

    def _pull_loop(self):
        """Continuously pull frames from appsink."""
        import gi
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst

        logger.info("Pull loop started")
        while self._running:
            try:
                sample = self._appsink.emit("pull-sample")
                if sample is None:
                    continue

                buf = sample.get_buffer()
                buf_size = buf.get_size()
                # 检测关键帧：GStreamer x264enc 清除 delta-unit 标志表示关键帧
                is_key = not buf.get_flags() & 0x0010  # GST_BUFFER_FLAG_DELTA_UNIT = 0x0010
                if not is_key and buf_size > 30000:
                    is_key = True  # 备用：>30KB 的帧基本是关键帧

                try:
                    success, data = buf.extract_dup(0, buf_size)
                except ValueError:
                    data = buf.extract_dup(0, buf_size)
                    success = True if data else False
                if not success or not data:
                    continue

                if not hasattr(self, '_pull_logged'):
                    self._pull_logged = True
                    logger.info("First pulled frame: %d bytes", len(data))

                drop = self._frame_queue.full()
                if drop and not is_key:
                    continue

                if is_key:
                    while not self._frame_queue.empty():
                        try:
                            self._frame_queue.get_nowait()
                        except queue.Empty:
                            break

                frame = proto.make_video_frame(data, is_keyframe=is_key, is_config=False, drop=drop)
                try:
                    self._frame_queue.put(frame, block=False)
                except queue.Full:
                    pass
            except Exception as e:
                logger.error("Pull loop error: %s", e)
                time.sleep(0.1)

    def _on_new_sample(self, sink):
        """Callback when GStreamer appsink has a new encoded frame."""
        if not hasattr(self, '_callback_count'):
            self._callback_count = 0
        self._callback_count += 1
        if self._callback_count <= 3 or self._callback_count % 100 == 0:
            logger.info("on_new_sample called #%d", self._callback_count)
        try:
            sample = sink.emit("pull-sample")
            if sample is None:
                return False

            if not hasattr(self, '_first_frame_logged'):
                self._first_frame_logged = True
                logger.info("First encoded frame received from pipeline")

            buf = sample.get_buffer()
            buf_size = buf.get_size()
            is_config = False

            # 检测关键帧：GStreamer x264enc 会清除 delta-unit 标志
            # 对于关键帧，GST_BUFFER_FLAG_DELTA_UNIT (0x0010) 不会被设置
            # 对于P帧，该标志会被设置
            is_key = not buf.get_flags() & 0x0010  # GST_BUFFER_FLAG_DELTA_UNIT = 0x0010
            if not is_key and buf_size > 30000:
                # 备用：>30KB 的帧基本是关键帧
                is_key = True

            # Extract data — GStreamer versions vary in return type
            try:
                success, data = buf.extract_dup(0, buf.get_size())
            except ValueError:
                # Some versions return just the data
                data = buf.extract_dup(0, buf.get_size())
                success = True if data else False
            if not success or not data:
                if not hasattr(self, '_extract_logged'):
                    self._extract_logged = True
                    logger.warning("extract_dup failed, buf size=%d", buf.get_size())
                return False
            if not hasattr(self, '_size_logged'):
                self._size_logged = True
                logger.info("First frame extracted: %d bytes, key=%s config=%s",
                           len(data), is_key, is_config)

            # Check if we need to drop due to queue full
            drop = self._frame_queue.full()
            if drop and not is_key:
                if not hasattr(self, '_drop_logged'):
                    self._drop_logged = True
                    logger.warning("Frame queue full, dropping non-keyframes")
                return True  # Drop non-keyframe

            # If keyframe and queue full, clear queue
            if is_key:
                while not self._frame_queue.empty():
                    try:
                        self._frame_queue.get_nowait()
                    except queue.Empty:
                        break

            frame = proto.make_video_frame(data, is_keyframe=is_key, is_config=is_config, drop=drop)
            try:
                self._frame_queue.put(frame, block=False)
                if not hasattr(self, '_queue_logged'):
                    self._queue_logged = True
                    logger.info("First frame queued, queue size=%d", self._frame_queue.qsize())
            except queue.Full:
                if not hasattr(self, '_drop_logged'):
                    self._drop_logged = True
                    logger.warning("Queue full, dropping frame")

            return True

        except Exception as e:
            logger.error("Sample callback error: %s", e, exc_info=True)
            return False

    def _keepalive_loop(self):
        """Send periodic pings until the first video frame is sent."""
        ping_id = 0
        while self._running and not hasattr(self, '_first_frame_sent'):
            try:
                self._send_control({"type": "ping", "id": ping_id})
                ping_id += 1
            except Exception:
                break
            # Wait 5 seconds, but check frequently for shutdown
            for _ in range(50):
                if not self._running or hasattr(self, '_first_frame_sent'):
                    return
                time.sleep(0.1)

    def _writer_loop(self):
        """Thread that sends queued frames to the socket."""
        sent_count = 0
        logger.info("Writer thread started, queue size=%d", self._frame_queue.qsize())
        while self._running:
            try:
                frame = self._frame_queue.get(timeout=2.0)
                self._sock.sendall(frame)
                sent_count += 1
                if sent_count <= 3 or sent_count % 60 == 0:
                    logger.info("Sent frame %d (%d bytes)", sent_count, len(frame))
                if not hasattr(self, '_first_frame_sent'):
                    self._first_frame_sent = True
                    logger.info("First video frame sent to device")
            except queue.Empty:
                logger.debug("Writer: queue empty, size=%d", self._frame_queue.qsize())
                continue
            except (ConnectionError, BrokenPipeError, OSError) as e:
                logger.info("Writer socket error: %s", e)
                self._running = False
                break
            except Exception as e:
                logger.error("Writer unexpected error: %s", e)
                self._running = False
                break

    def _reader_loop(self):
        """Read frames from the socket (control messages and input events)."""
        while self._running:
            try:
                flags, payload = self._reader.read_frame()
                if flags & proto.FLAG_CONTROL:
                    self._handle_control(payload)
            except (ConnectionError, ValueError) as e:
                logger.info("Reader connection closed: %s", e)
                self._running = False
                break

    def _handle_control(self, payload):
        """Process a control message."""
        try:
            msg = json.loads(payload.decode("utf-8"))
            msg_type = msg.get("type")

            if msg_type == "ping":
                self._send_control({"type": "pong", "id": msg.get("id")})
            elif msg_type == "input":
                self._handle_input(msg)
            else:
                logger.debug("Unknown control message: %s", msg_type)

        except json.JSONDecodeError:
            logger.warning("Invalid control JSON")

    def _handle_input(self, msg):
        """Process an input event from the device."""
        if not self._injector:
            return

        kind = msg.get("kind")
        try:
            # Get display offset for EVDI virtual display positioning
            offset_x = getattr(self, '_display_offset_x', 0)
            offset_y = getattr(self, '_display_offset_y', 0)

            if kind == "move":
                # Map normalized coordinates (0..1) to screen coords
                x = msg.get("x", 0.5) * self._args.width + offset_x
                y = msg.get("y", 0.5) * self._args.height + offset_y
                self._injector.mouse_move(x, y)

            elif kind == "btn":
                x = msg.get("x", 0.5) * self._args.width + offset_x
                y = msg.get("y", 0.5) * self._args.height + offset_y
                btn = msg.get("btn", 1)
                state = msg.get("state", 1)
                # Move to position first, then click
                self._injector.mouse_move(x, y)
                self._injector.mouse_button(btn, state)

            elif kind == "wheel":
                self._injector.mouse_wheel(msg.get("dx", 0), msg.get("dy", 0))

            elif kind == "key":
                # Not implemented
                pass

        except Exception as e:
            logger.debug("Input injection error: %s", e)

    def _send_control(self, obj):
        """Send a control frame."""
        try:
            frame = proto.make_control_frame(obj)
            self._sock.sendall(frame)
        except (ConnectionError, OSError) as e:
            logger.debug("Send control error: %s", e)

    def _cleanup(self):
        """Stop pipeline, close socket, release resources."""
        self._running = False
        if self._pipe:
            try:
                self._pipe.set_state(Gst.State.NULL)
            except Exception:
                pass
            self._pipe = None
        # Stop screencast session so next connection gets a fresh one
        # (important when display_mode or resolution changes)
        try:
            from . import screencast
            screencast.stop_screencast_session()
        except Exception:
            pass
        if self._injector:
            try:
                self._injector.close()
            except Exception:
                pass
            self._injector = None
        try:
            self._sock.close()
        except Exception:
            pass
        logger.info("Session cleaned up")


def _setup_adb_connection(args):
    """Set up ADB connection: USB reverse tunnel or wireless ADB.

    Returns:
        True if ADB is available and configured, False otherwise.
    """
    import subprocess

    adb = args.adb_path

    # Check if ADB is available
    try:
        result = subprocess.run([adb, "version"], capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            logger.warning("ADB not found at '%s'. Install with: sudo apt install adb", adb)
            return False
    except FileNotFoundError:
        logger.warning("ADB not found at '%s'. Install with: sudo apt install adb", adb)
        return False

    # List devices
    result = subprocess.run([adb, "devices"], capture_output=True, text=True, timeout=5)
    device_lines = [l for l in result.stdout.strip().splitlines()
                    if l.strip() and not l.startswith("List")]

    if not device_lines:
        logger.warning("No ADB devices found.")
        logger.info("  USB:  Connect device via USB, enable USB debugging")
        logger.info("  WLAN: adb connect <device_ip>:5555  (enable wireless debugging)")
        return False

    logger.info("ADB devices found:")
    for line in device_lines:
        logger.info("  %s", line)

    # Set up reverse tunnel for each device
    for line in device_lines:
        parts = line.split()
        serial = parts[0]
        logger.info("  → Setting up reverse tunnel for %s ...", serial)
        try:
            cmd = [adb, "-s", serial, "reverse", f"tcp:{args.port}", f"tcp:{args.port}"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                logger.info("    ✓ Reverse tunnel OK (device connects to 127.0.0.1:%d)", args.port)
            else:
                logger.warning("    ✗ Reverse tunnel failed: %s", result.stderr.strip())
        except Exception as e:
            logger.warning("    ✗ Reverse tunnel error: %s", e)

    return True


def run_server(args):
    """Start the TCP server, listen for incoming connections.

    Args:
        args: Parsed command-line arguments.
    """
    import gi
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst  # noqa: F402

    Gst.init(None)

    # Detect display server
    from .vdisplay import detect_session, create_virtual_display, suggest_region_for_extend
    session_info = detect_session()
    is_wayland = session_info["is_wayland"]

    # Create virtual display if no output/region/fullscreen specified
    if not args.output and not args.region and not args.fullscreen:
        if is_wayland:
            print(f"🖥 检测到 Wayland ({session_info['compositor']} {session_info['version']})")
        print(f"   创建虚拟显示器 ...")
        vd_result = create_virtual_display(
            session_info, args.width, args.height, args.fps
        )
        if vd_result["success"]:
            # Check if this is EVDI (real virtual monitor) or Xvfb
            capture_source = vd_result.get("capture_source")
            xvfb_display = vd_result.get("xvfb_display")

            if capture_source:
                # EVDI mode: use the named output for capture
                args.output = capture_source
                geometry = vd_result.get("geometry")
                if geometry:
                    x, y, w, h = geometry
                    args.region = f"{x},{y},{w}x{h}"
                    # Store offset for input coordinate mapping
                    args._display_offset_x = x
                    args._display_offset_y = y
                print(f"   ✓ EVDI 虚拟显示器已创建: {capture_source}")
                print(f"   ✓ GNOME 已识别为第二显示器")
                print(f"   ✓ 可在 设置→显示 中配置位置")
                print(f"   ✓ 可以将窗口拖到扩展屏幕")
                print(f"   ✓ 支持触控输入")
            elif xvfb_display:
                # Xvfb mode: independent display, need its own DISPLAY var
                args.display = xvfb_display
                args._xvfb_pid = vd_result.get("xvfb_pid")
                print(f"   ✓ Xvfb 虚拟显示器已创建: {xvfb_display}")
                print(f"   ⚠ Xvfb 是独立显示，不与 GNOME 集成")
                print(f"   → 无法拖拽窗口，仅捕获静态内容")
            else:
                print(f"   ✓ 虚拟显示器已创建")

            msg = vd_result.get("message", "")
            if msg:
                for line in msg.split("\n"):
                    print(f"   {line}")
        else:
            print(f"   ⚠ {vd_result['message']}")
            args.fullscreen = True
            print(f"   → 回退到主屏幕捕获模式")

    # Start discovery (UDP broadcast + ADB monitor)
    from .discovery import DiscoveryServer
    discovery = DiscoveryServer(
        host_port=args.port,
        hostname=socket.gethostname(),
        display_name=f"PSP ({args.width}x{args.height}@{args.fps})",
    )
    discovery.start()

    # Print local IPs for user reference
    local_ips = discovery.get_local_ips()
    print()
    print("🔍 局域网发现已启动 (UDP 端口 4748)")
    print(f"   本机 IP: {', '.join(local_ips)}")
    print(f"   服务端口: {args.port}")
    print(f"   Android 端打开 PSP 应用即可自动发现本机")
    if is_wayland:
        print(f"   🖥 Wayland 模式: 使用 Portal ScreenCast 虚拟显示器")
        print(f"     连接后自动创建扩展屏幕（非镜像模式）")
    print()

    # ADB connection setup
    if args.adb_mode != "off":
        print("📱 ADB 连接设置:")
        if args.adb_mode == "auto":
            _setup_adb_connection(args)
        elif args.adb_mode == "usb":
            print("   USB 模式: 设置 ADB reverse 隧道 ...")
            _setup_adb_connection(args)
        elif args.adb_mode == "wireless":
            print("   无线 ADB 模式: 确保已执行 adb connect <ip>:5555")
            print("   然后设置 reverse 隧道 ...")
            _setup_adb_connection(args)
        print()

    # Start TCP server
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", args.port))
    server.listen(1)
    logger.info("TCP server listening on port %d", args.port)
    logger.info("Configuration: %dx%d@%d %dkbps",
                args.width, args.height, args.fps, args.bitrate_kbps)

    try:
        while True:
            sock, addr = server.accept()
            logger.info("Connection from %s:%d", addr[0], addr[1])
            session = Session(sock, addr, args)
            t = threading.Thread(target=session.run, daemon=True, name=f"session-{addr[0]}")
            t.start()
    except KeyboardInterrupt:
        logger.info("Server shutting down")
    finally:
        discovery.stop()
        server.close()