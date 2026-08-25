#!/usr/bin/env python3
"""PSP Host — 环境自检工具.

用法:
    python3 check_env.py

检查本机是否满足 PSP Host 运行的全部依赖，
输出 ✓/✗ 报告，并为缺失项给出安装建议。
"""

import os
import shutil
import subprocess
import sys

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"
BOLD = "\033[1m"

results = []  # (name, ok, detail, fix_hint)


def check(name, ok, detail="", fix=""):
    mark = f"{GREEN}✓{RESET}" if ok else f"{RED}✗{RESET}"
    print(f"  {mark} {name}  {detail}")
    if not ok and fix:
        print(f"      {YELLOW}→ 修复: {fix}{RESET}")
    results.append((name, ok, fix))


def detect_distro():
    """返回发行版家族: 'debian' / 'fedora' / 'arch' / 'suse' / 'unknown'."""
    try:
        with open("/etc/os-release") as f:
            data = f.read()
        if "ID=ubuntu" in data or "ID=debian" in data or "ID=linuxmint" in data:
            return "debian"
        if "ID=fedora" in data or "ID=centos" in data or "ID=rhel" in data:
            return "fedora"
        if "ID=arch" in data or "ID=manjaro" in data:
            return "arch"
        if "ID=opensuse" in data or "ID=suse" in data:
            return "suse"
    except FileNotFoundError:
        pass
    return "unknown"


def gst_element_ok(name):
    if shutil.which("gst-inspect-1.0") is None:
        return None  # gst-tools 未安装，无法检测
    r = subprocess.run(["gst-inspect-1.0", name], capture_output=True, text=True)
    return r.returncode == 0


def cmd_ok(cmd):
    return shutil.which(cmd) is not None


def main():
    print()
    print(f"{BOLD}=== PSP Host 环境自检 ==={RESET}")
    print()

    # ── 1. Python ──────────────────────────────────────────────
    print(f"{BOLD}[1/6] Python 运行时{RESET}")
    py_ok = sys.version_info >= (3, 8)
    check("Python 3.8+", py_ok,
          f"当前: {sys.version.split()[0]}",
          "安装 python3（一般系统自带）")

    # ── 2. GStreamer 绑定 ─────────────────────────────────────
    print(f"\n{BOLD}[2/6] GStreamer + Python 绑定{RESET}")
    try:
        import gi  # noqa
        check("PyGObject (gi)", True, f"版本 {gi.__version__ if hasattr(gi, '__version__') else '?'}")
    except ImportError:
        check("PyGObject (gi)", False, "", "debian: sudo apt install python3-gi\n       fedora: sudo dnf install python3-gobject")

    try:
        gi.require_version("Gst", "1.0")  # noqa
        check("GStreamer GI 绑定", True)
    except Exception:
        check("GStreamer GI 绑定", False, "", "debian: sudo apt install gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0")

    try:
        import cairo  # noqa
        check("pycairo", True)
    except ImportError:
        check("pycairo", False, "", "debian: sudo apt install python3-gi-cairo")

    # ── 3. GStreamer 元素 ────────────────────────────────────
    print(f"\n{BOLD}[3/6] GStreamer 插件{RESET}")
    tools = cmd_ok("gst-inspect-1.0")
    check("gstreamer1.0-tools (gst-inspect)", tools, "", "debian: sudo apt install gstreamer1.0-tools")

    for el, desc in [
        ("x264enc", "H.264 编码器（必装，在 plugins-ugly）"),
        ("videoconvert", "颜色空间转换 (plugins-base)"),
        ("videoscale", "缩放 (plugins-base)"),
        ("videorate", "帧率控制 (plugins-base)"),
        ("h264parse", "H.264 解析 (plugins-base)"),
        ("vp9enc", "VP9 编码器（可选，plugins-good）"),
        ("vp8enc", "VP8 编码器（可选，plugins-good）"),
        ("nvh264enc", "NVIDIA 硬件编码（可选，plugins-bad）"),
    ]:
        ok = gst_element_ok(el)
        if ok is None:
            check(f"{el} — {desc}", False, "无法检测（gst-inspect 缺失）", "先安装 gstreamer1.0-tools")
        else:
            check(el, ok, desc, f"debian: sudo apt install gstreamer1.0-plugins-{'ugly' if el=='x264enc' else 'good' if el in ('vp9enc','vp8enc') else 'bad'}")

    # 内置运行时（host/runtime，由 bundle_runtime.sh 生成，免去 apt 安装 gstreamer）
    _rt = os.path.join(os.path.dirname(__file__), "runtime", "plugins", "gstreamer-1.0")
    check("内置 GStreamer 运行时 (host/runtime)", os.path.isdir(_rt),
          "免去系统 gstreamer 安装", "运行 host/bundle_runtime.sh 生成")

    # ── 4. 屏幕捕获源 ────────────────────────────────────────
    print(f"\n{BOLD}[4/6] 屏幕捕获源{RESET}")
    session_type = os.environ.get("XDG_SESSION_TYPE", "")
    wayland = session_type.lower() == "wayland" or os.environ.get("WAYLAND_DISPLAY")

    if wayland:
        print(f"  {YELLOW}检测到 Wayland 会话（XDG_SESSION_TYPE={session_type or os.environ.get('WAYLAND_DISPLAY')}）{RESET}")
        check("pipewiresrc (gstreamer1.0-pipewire)", gst_element_ok("pipewiresrc") is True,
              "Wayland 捕获核心", "debian: sudo apt install gstreamer1.0-pipewire")
        check("pipewire 服务", cmd_ok("pipewire") and shutil.which("pw-cli"),
              "需 pipewire >= 0.3.50", "debian: sudo apt install pipewire pipewire-bin")
        check("xdg-desktop-portal", cmd_ok("xdg-desktop-portal") or _dbus_name("org.freedesktop.portal.Desktop"),
              "", "debian: sudo apt install xdg-desktop-portal")

        # portal 后端
        if _dbus_name("org.freedesktop.impl.portal.desktop.hyprland"):
            check("portal 后端 (hyprland)", True, "xdg-desktop-portal-hyprland")
        elif _dbus_name("org.freedesktop.impl.portal.desktop.gnome"):
            check("portal 后端 (gnome)", True, "xdg-desktop-portal-gnome")
        elif _dbus_name("org.freedesktop.impl.portal.desktop.kde"):
            check("portal 后端 (kde)", True, "xdg-desktop-portal-kde")
        else:
            check("ScreenCast portal 后端", False, "未检测到",
                  "Hyprland: sudo apt install xdg-desktop-portal-hyprland\n       GNOME: sudo apt install xdg-desktop-portal-gnome")

        check("grim（可选，调试用）", cmd_ok("grim"), "")
    else:
        print(f"  {YELLOW}检测到 X11 会话{RESET}")
        check("ximagesrc (gstreamer1.0-x)", gst_element_ok("ximagesrc") is True,
              "X11 捕获", "debian: sudo apt install gstreamer1.0-x")
        check("xrandr", cmd_ok("xrandr"), "列出显示器", "debian: sudo apt install x11-xserver-utils")

    # ── 5. 输入回传 ──────────────────────────────────────────
    print(f"\n{BOLD}[5/6] 输入回传（触摸 → 鼠标）{RESET}")
    try:
        import evdev  # noqa
        check("python-evdev（虚拟鼠标 uinput）", True, "已随项目 venv 内置")
    except ImportError:
        check("python-evdev（虚拟鼠标 uinput）", False, "未安装",
              "已随 host/venv 内置；若缺失请运行 ./setup.sh 重建 venv")
    if wayland:
        check("ydotool（Wayland 输入注入）", cmd_ok("ydotool"), "可选，缺失则关闭输入回传",
              "debian: sudo apt install ydotool && sudo usermod -aG input $USER")
    else:
        check("xdotool（X11 输入注入）", cmd_ok("xdotool"), "",
              "debian: sudo apt install xdotool")

    # ── 6. 辅助工具 ──────────────────────────────────────────
    print(f"\n{BOLD}[6/6] 辅助工具（可选）{RESET}")
    check("adb（USB 模式）", cmd_ok("adb"), "", "debian: sudo apt install adb")
    check("ffmpeg（调试/测试）", cmd_ok("ffmpeg"), "")

    # ── 汇总 ─────────────────────────────────────────────────
    print()
    print(f"{BOLD}=== 汇总 ==={RESET}")
    missing = [r for r in results if not r[1]]
    if not missing:
        print(f"  {GREEN}✅ 所有必选依赖均已就绪，可以直接运行:{RESET}")
        print(f"     host/run.sh --output HEADLESS-2 --resolution 1080p --fps 60 --adb auto --debug")
    else:
        print(f"  {RED}❌ {len(missing)} 项未满足，最快修复命令:{RESET}")
        distro = detect_distro()
        if distro == "debian":
            print("     sudo apt update && sudo apt install -y \\")
            print("       python3-gi python3-gi-cairo python3-pip \\")
            print("       gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 \\")
            print("       gstreamer1.0-tools gstreamer1.0-plugins-good \\")
            print("       gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly \\")
            print("       gstreamer1.0-libav gstreamer1.0-x \\")
            print("       gstreamer1.0-pipewire pipewire pipewire-bin \\")
            print("       xdg-desktop-portal xdg-desktop-portal-hyprland \\")
            print("       xdotool ydotool adb")
            print("     或者直接运行项目根目录的 ./setup.sh")
        else:
            print("     请根据缺失项安装对应包（见上），或运行 ./setup.sh")
        print()
        for name, ok, fix in missing:
            print(f"  {RED}✗{RESET} {name}")
            if fix:
                print(f"      {YELLOW}→ {fix}{RESET}")
    print()


def _dbus_name(name):
    """检测会话总线上是否存在某 D-Bus 名字."""
    try:
        r = subprocess.run(
            ["gdbus", "call", "--session", "-d", "org.freedesktop.DBus",
             "-o", "/org/freedesktop/DBus", "-m", "org.freedesktop.DBus.NameHasOwner", name],
            capture_output=True, text=True, timeout=5,
        )
        return "true" in r.stdout.lower()
    except Exception:
        return False


if __name__ == "__main__":
    main()