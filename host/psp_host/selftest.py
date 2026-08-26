"""运行时自检：无头验证 GTK4 / GStreamer / 编码器 / 协议层完整性。

用于 CI 校验冻结打包产物（PyInstaller exe / AppImage）与源码环境。
全部通过时退出码为 0；任一项失败退出码为 1。
"""


def run_selftest():
    """执行自检，返回进程退出码。"""
    results = []

    def check(name, fn):
        print(f"  … {name}", flush=True)
        try:
            detail = fn()
            results.append(True)
            print(f"  ✓ {name}" + (f": {detail}" if detail else ""), flush=True)
        except Exception as e:
            results.append(False)
            print(f"  ✗ {name}: {e}", flush=True)

    print("PSP Host 自检", flush=True)

    def _gi():
        import gi
        return getattr(gi, "__version__", "ok")
    check("PyGObject (gi)", _gi)

    def _gst():
        import gi
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst
        Gst.init(None)
        return Gst.version_string()
    check("GStreamer 核心", _gst)

    def _gtk():
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk
        return f"GTK {Gtk.get_major_version()}.{Gtk.get_minor_version()}.{Gtk.get_micro_version()}"
    check("GTK4 GUI 类型库", _gtk)

    def _encoders():
        from .capture import get_encoder_info
        found = [name for name, _codec, _element, avail in get_encoder_info() if avail]
        if not found:
            raise RuntimeError("未找到任何可用视频编码器 (x264enc/nvh264enc/vpx)")
        return ", ".join(found)
    check("视频编码器", _encoders)

    def _protocol():
        from . import protocol as proto
        frame = proto.make_control_frame({"type": "ping", "id": 1})
        ok, resp = proto.negotiate(
            {"codec": "h264", "width": 1920, "height": 1080, "fps": 60, "bitrate_kbps": 10000},
            {"codec": "auto", "width": 1920, "height": 1080, "fps": 60, "bitrate_kbps": 10000},
        )
        assert ok and resp["ok"] and len(frame) > 5
        return "帧打包 + 协商正常"
    check("协议层", _protocol)

    def _input():
        import platform
        if platform.system() == "Linux":
            from . import input_linux  # noqa: F401
        elif platform.system() == "Windows":
            from . import input_windows  # noqa: F401
        return platform.system()
    check("输入注入模块", _input)

    ok = all(results)
    print("自检通过" if ok else "自检失败", flush=True)
    return 0 if ok else 1
