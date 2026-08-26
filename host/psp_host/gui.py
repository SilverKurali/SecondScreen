"""GTK4 graphical front-end for PSP Host.

一个多标签图形控制台，覆盖 start.sh 的全部功能：
  - 服务：配置参数、启动/停止、实时日志、运行统计
  - 状态：服务进程、虚拟显示器、ADB 设备、本机 IP、日志路径
  - 设备：扫描局域网 PSP 主机 + ADB 设备
  - 显示器：创建/移除/列出 Hyprland HEADLESS 虚拟屏
  - 工具箱：环境自检、重建内置依赖、构建 APK、列出 X11 输出与编码器

服务端以子进程方式运行（host/run.sh），与命令行/start.sh 行为一致。
"""

import os
import re
import signal
import subprocess
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gtk, GLib, Gio  # noqa: E402

from .config import RESOLUTIONS, FPS_OPTIONS  # noqa: E402

# 路径
HOST_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # host/
ROOT_DIR = os.path.dirname(HOST_DIR)
RUN_SH = os.path.join(HOST_DIR, "run.sh")
SETUP_SH = os.path.join(ROOT_DIR, "setup.sh")
BUNDLE_SH = os.path.join(HOST_DIR, "bundle_runtime.sh")
CHECK_ENV = os.path.join(HOST_DIR, "check_env.py")
VENV_PY = os.path.join(HOST_DIR, "venv", "bin", "python")
LOG_FILE = "/tmp/psp_server.log"
DEFAULT_PORT = 4747

# 运行统计正则（从日志解析）
_RE_FPS = re.compile(r"fps\s*[:=]\s*(\d+)", re.I)
_RE_SENT = re.compile(r"Sent frame (\d+)")
_RE_NEG = re.compile(r"Session negotiated:\s*(\d+)x(\d+)@(\d+)\s+(\S+)\s+(\d+)\s*kbps", re.I)
_RE_CONN = re.compile(r"Connection from ([\d.]+):(\d+)")


# ── 辅助：运行子进程并逐行回调 ──────────────────────────────
def run_streaming(argv, on_line, cwd=None, env=None, name="cmd"):
    """启动子进程，逐行把 stdout/stderr 喂给 on_line；返回 Popen。"""
    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        text=True,
        cwd=cwd,
        env=env,
    )
    def _reader():
        try:
            for line in proc.stdout:
                on_line(line.rstrip("\n"))
        except Exception as e:
            on_line(f"[{name} 读取异常] {e}")
    threading.Thread(target=_reader, daemon=True, name=f"{name}-reader").start()
    return proc


def _list_headless_outputs():
    try:
        out = subprocess.run(["hyprctl", "-j", "monitors"],
                             capture_output=True, text=True, timeout=3)
        if out.returncode != 0:
            return []
        import json
        data = json.loads(out.stdout or "[]")
        return sorted(
            [m.get("name", "") for m in data if m.get("name", "").startswith("HEADLESS-")],
            key=lambda n: int(n.split("-")[1]) if n.split("-")[1:].isdigit() else 0,
        )
    except Exception:
        return []


def _create_headless():
    try:
        subprocess.run(["hyprctl", "output", "create", "headless"],
                       capture_output=True, timeout=3)
        time.sleep(0.4)
        outs = _list_headless_outputs()
        return outs[-1] if outs else None
    except Exception:
        return None


def _adb_devices():
    try:
        r = subprocess.run(["adb", "devices", "-l"],
                           capture_output=True, text=True, timeout=5)
        devs = []
        for line in r.stdout.splitlines():
            if line.strip() and not line.startswith("List") and "device" in line:
                devs.append(line.strip())
        return devs
    except Exception:
        return []


def _local_ips():
    try:
        from .discovery import DiscoveryServer
        return DiscoveryServer().get_local_ips()
    except Exception:
        return []


# ════════════════════════════════════════════════════════════
class PSPHostWindow(Gtk.ApplicationWindow):
    """主窗口：多标签控制台。"""

    def __init__(self, app):
        super().__init__(application=app, title="PSP Host 控制台")
        self.set_default_size(880, 640)
        self._proc = None
        self._stop_reader = threading.Event()
        self._tasks = {}  # name -> Popen，后台任务

        self._build_ui()
        self._connect_signals()
        self._set_running(False)
        self.refresh_status()

    # ── UI 骨架 ─────────────────────────────────────────────
    def _build_ui(self):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        outer.set_margin_start(10)
        outer.set_margin_end(10)
        outer.set_margin_top(10)
        outer.set_margin_bottom(10)
        self.set_child(outer)

        self.notebook = Gtk.Notebook()
        outer.append(self.notebook)

        self._build_service_tab()
        self._build_status_tab()
        self._build_devices_tab()
        self._build_display_tab()
        self._build_tools_tab()

    # ── 服务标签 ────────────────────────────────────────────
    def _build_service_tab(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.notebook.append_page(page, Gtk.Label(label="服务"))

        cfg = Gtk.Frame(label="服务配置")
        cfg_box = Gtk.Grid(column_spacing=12, row_spacing=8)
        cfg_box.set_margin_start(10)
        cfg_box.set_margin_end(10)
        cfg_box.set_margin_top(6)
        cfg_box.set_margin_bottom(6)
        cfg.set_child(cfg_box)
        page.append(cfg)

        r = 0
        self.res_combo = self._grid_combo(cfg_box, 0, r, "分辨率", list(RESOLUTIONS.keys()), "1080p")
        self.fps_combo = self._grid_combo(cfg_box, 2, r, "帧率", [str(f) for f in FPS_OPTIONS], "60")
        self.codec_combo = self._grid_combo(cfg_box, 4, r, "编码器", ["auto", "h264", "vp9", "vp8"], "auto")
        r += 1
        self.port_spin = self._grid_spin(cfg_box, 0, r, "端口", 1024, 65535, DEFAULT_PORT, 1, 0)
        self.quality_spin = self._grid_spin(cfg_box, 2, r, "画质倍率", 0.5, 3.0, 1.0, 0.1, 2)
        self.adb_combo = self._grid_combo(cfg_box, 4, r, "连接方式",
            ["仅WiFi", "仅USB", "WiFi+USB", "无线ADB"], "WiFi+USB")
        r += 1
        cfg_box.attach(Gtk.Label(label="捕获模式", halign=Gtk.Align.END), 0, r, 1, 1)
        self.capture_mode_combo = Gtk.DropDown.new_from_strings(
            ["自动创建虚拟屏", "指定输出", "捕获区域", "全屏捕获"]
        )
        self.capture_mode_combo.set_hexpand(True)
        self.capture_mode_combo.connect("notify::selected",
                                        lambda *_: self._on_capture_mode_changed())
        cfg_box.attach(self.capture_mode_combo, 1, r, 1, 1)
        # 输出名下拉（"指定输出"模式可见）
        self.output_combo = Gtk.DropDown.new_from_strings(["(无)"])
        self.output_combo.set_hexpand(True)
        cfg_box.attach(self.output_combo, 2, r, 1, 1)
        self._refresh_outputs_btn = Gtk.Button(label="刷新")
        self._refresh_outputs_btn.connect("clicked", lambda *_: self.refresh_outputs())
        cfg_box.attach(self._refresh_outputs_btn, 3, r, 1, 1)
        # 捕获区域输入框（"捕获区域"模式可见）
        self.region_entry = Gtk.Entry(placeholder_text="x,y,WxH  如 1920,0,1920x1080")
        self.region_entry.set_hexpand(True)
        cfg_box.attach(self.region_entry, 4, r, 1, 1)
        r += 1
        # 显示模式：扩展（创建虚拟屏）vs 镜像（复制主屏）
        cfg_box.attach(Gtk.Label(label="显示模式", halign=Gtk.Align.END), 0, r, 1, 1)
        self.display_mode_combo = Gtk.DropDown.new_from_strings(
            ["扩展模式", "镜像模式"]
        )
        self.display_mode_combo.set_hexpand(True)
        self.display_mode_combo.connect("notify::selected",
                                        lambda *_: self._on_capture_mode_changed())
        cfg_box.attach(self.display_mode_combo, 1, r, 1, 1)
        r += 1
        self.debug_switch = self._grid_switch(cfg_box, 0, r, "调试日志", False)
        self.no_input_switch = self._grid_switch(cfg_box, 2, r, "禁用输入回传", False)
        self.quickscan_switch = self._grid_switch(cfg_box, 4, r, "启动后扫设备", True)

        # 控制按钮
        ctl = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        page.append(ctl)
        self.start_btn = Gtk.Button(label="▶ 启动服务")
        self.start_btn.add_css_class("suggested-action")
        ctl.append(self.start_btn)
        self.stop_btn = Gtk.Button(label="■ 停止服务")
        self.stop_btn.add_css_class("destructive-action")
        ctl.append(self.stop_btn)
        self.clear_btn = Gtk.Button(label="清空日志")
        ctl.append(self.clear_btn)
        self.status_label = Gtk.Label(label="● 已停止")
        self.status_label.set_halign(Gtk.Align.END)
        self.status_label.set_hexpand(True)
        ctl.append(self.status_label)

        # 统计栏
        stat = Gtk.Frame(label="运行统计")
        sbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
        sbox.set_margin_start(10)
        sbox.set_margin_top(4)
        stat.set_child(sbox)
        page.append(stat)
        self.stat_conn = Gtk.Label(label="连接: 无")
        self.stat_neg = Gtk.Label(label="协商: -")
        self.stat_sent = Gtk.Label(label="已发帧: 0")
        self.stat_fps = Gtk.Label(label="FPS: -")
        for w in (self.stat_conn, self.stat_neg, self.stat_sent, self.stat_fps):
            sbox.append(w)

        # 日志
        log_frame = Gtk.Frame(label="实时日志")
        log_frame.set_vexpand(True)
        page.append(log_frame)
        scroller = Gtk.ScrolledWindow()
        scroller.set_vexpand(True)
        log_frame.set_child(scroller)
        self.log_buf = Gtk.TextBuffer()
        self.log_view = Gtk.TextView(buffer=self.log_buf)
        self.log_view.set_editable(False)
        self.log_view.set_monospace(True)
        self.log_view.set_wrap_mode(Gtk.WrapMode.CHAR)
        self.log_view.set_top_margin(6)
        scroller.set_child(self.log_view)
        self._auto_scroll = True
        scroller.get_vadjustment().connect("value-changed", self._on_scroll_changed)

        self.refresh_outputs()
        self._on_capture_mode_changed()  # 初始化控件可见性

    def _grid_combo(self, grid, col, row, label_text, items, default):
        grid.attach(Gtk.Label(label=label_text, halign=Gtk.Align.END), col, row, 1, 1)
        combo = Gtk.DropDown.new_from_strings(list(items))
        combo.set_selected(items.index(default) if default in items else 0)
        grid.attach(combo, col + 1, row, 1, 1)
        return combo

    def _grid_spin(self, grid, col, row, label_text, lo, hi, val, step, digits):
        grid.attach(Gtk.Label(label=label_text, halign=Gtk.Align.END), col, row, 1, 1)
        adj = Gtk.Adjustment(value=val, lower=lo, upper=hi, step_increment=step)
        spin = Gtk.SpinButton(adjustment=adj, digits=digits)
        grid.attach(spin, col + 1, row, 1, 1)
        return spin

    def _grid_switch(self, grid, col, row, label_text, default):
        b = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        sw = Gtk.Switch()
        sw.set_active(default)
        b.append(sw)
        b.append(Gtk.Label(label=label_text))
        grid.attach(b, col, row, 2, 1)
        return sw

    # ── 状态标签 ────────────────────────────────────────────
    def _build_status_tab(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.notebook.append_page(page, Gtk.Label(label="状态"))

        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        page.append(bar)
        refresh = Gtk.Button(label="⟳ 刷新")
        refresh.connect("clicked", lambda *_: self.refresh_status())
        bar.append(refresh)
        self.srv_state_lbl = Gtk.Label(label="")
        self.srv_state_lbl.set_hexpand(True)
        bar.append(self.srv_state_lbl)

        self.status_text = Gtk.TextBuffer()
        sv = Gtk.TextView(buffer=self.status_text)
        sv.set_editable(False)
        sv.set_monospace(True)
        sw = Gtk.ScrolledWindow()
        sw.set_vexpand(True)
        sw.set_child(sv)
        page.append(sw)

    def refresh_status(self):
        lines = []
        # 服务
        if self._proc and self._proc.poll() is None:
            self.srv_state_lbl.set_text("服务: ● 运行中")
            self.srv_state_lbl.remove_css_class("error")
            self.srv_state_lbl.add_css_class("success")
            lines.append(f"服务: 运行中 (PID {self._proc.pid})")
        else:
            self.srv_state_lbl.set_text("服务: ● 已停止")
            self.srv_state_lbl.remove_css_class("success")
            self.srv_state_lbl.add_css_class("error")
            lines.append("服务: 未运行")
        # 虚拟显示器
        outs = _list_headless_outputs()
        lines.append(f"虚拟显示器: {', '.join(outs) if outs else '(无)'}")
        # ADB
        devs = _adb_devices()
        lines.append("ADB 设备:")
        if devs:
            for d in devs:
                lines.append(f"  {d}")
        else:
            lines.append("  (无)")
        # 本机 IP
        ips = _local_ips()
        lines.append(f"本机 IP: {', '.join(ips) if ips else '(未知)'}")
        lines.append(f"日志文件: {LOG_FILE}")
        lines.append(f"内置 venv: {'OK' if os.path.exists(VENV_PY) else '缺失'}")
        lines.append(f"内置 GStreamer: {'OK' if os.path.isdir(os.path.join(HOST_DIR,'runtime','lib')) else '缺失(回退系统)'}")
        self.status_text.set_text("\n".join(lines))

    # ── 设备标签 ────────────────────────────────────────────
    def _build_devices_tab(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.notebook.append_page(page, Gtk.Label(label="设备"))

        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        page.append(bar)
        self.scan_btn = Gtk.Button(label="🔍 扫描局域网 + ADB")
        self.scan_btn.connect("clicked", lambda *_: self.scan_devices())
        bar.append(self.scan_btn)
        self.scan_progress = Gtk.ProgressBar()
        self.scan_progress.set_text("空闲")
        self.scan_progress.set_show_text(True)
        self.scan_progress.set_hexpand(True)
        bar.append(self.scan_progress)

        self.devices_buf = Gtk.TextBuffer()
        dv = Gtk.TextView(buffer=self.devices_buf)
        dv.set_editable(False)
        dv.set_monospace(True)
        sw = Gtk.ScrolledWindow()
        sw.set_vexpand(True)
        sw.set_child(dv)
        page.append(sw)

    def scan_devices(self):
        if self._tasks.get("scan") and self._tasks["scan"].poll() is None:
            return
        self.devices_buf.set_text("扫描中...\n")
        self.scan_progress.set_fraction(0.0)
        self.scan_progress.set_text("扫描中...")
        self.scan_btn.set_sensitive(False)

        argv = [VENV_PY, "-m", "psp_host", "--list-devices"]
        proc = run_streaming(argv, self._on_scan_line, cwd=HOST_DIR, name="scan")
        self._tasks["scan"] = proc

        # 进度动画
        self._scan_t0 = time.time()
        GLib.timeout_add(100, self._scan_progress_tick)

    def _on_scan_line(self, line):
        end = self.devices_buf.get_end_iter()
        self.devices_buf.insert(end, line + "\n")

    def _scan_progress_tick(self):
        proc = self._tasks.get("scan")
        if proc is None:
            return False
        rc = proc.poll()
        if rc is not None:
            self.scan_progress.set_fraction(1.0)
            self.scan_progress.set_text("完成")
            self.scan_btn.set_sensitive(True)
            self._on_scan_line("── 扫描结束 ──")
            return False
        elapsed = time.time() - self._scan_t0
        frac = min(0.95, elapsed / 3.0)
        self.scan_progress.set_fraction(frac)
        self.scan_progress.set_text(f"扫描中 {elapsed:.1f}s")
        return True

    # ── 显示器标签 ──────────────────────────────────────────
    def _build_display_tab(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.notebook.append_page(page, Gtk.Label(label="显示器"))

        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        page.append(bar)
        create_btn = Gtk.Button(label="➕ 创建虚拟显示器")
        create_btn.connect("clicked", lambda *_: self.create_display())
        bar.append(create_btn)
        remove_btn = Gtk.Button(label="➖ 移除全部虚拟显示器")
        remove_btn.connect("clicked", lambda *_: self.remove_displays())
        bar.append(remove_btn)
        refresh_btn = Gtk.Button(label="⟳ 刷新")
        refresh_btn.connect("clicked", lambda *_: self.refresh_display_list())
        bar.append(refresh_btn)

        self.disp_store = Gtk.ListStore(str)
        self.disp_tree = Gtk.TreeView(model=self.disp_store, headers_visible=True)
        col = Gtk.TreeViewColumn("虚拟显示器名称", Gtk.CellRendererText(), text=0)
        col.set_expand(True)
        self.disp_tree.append_column(col)
        sw = Gtk.ScrolledWindow()
        sw.set_vexpand(True)
        sw.set_child(self.disp_tree)
        page.append(sw)
        self.refresh_display_list()

    def refresh_display_list(self):
        self.disp_store.clear()
        for n in _list_headless_outputs():
            self.disp_store.append([n])

    def create_display(self):
        name = _create_headless()
        self.refresh_display_list()
        self._log(f"创建虚拟显示器: {name or '失败'}")

    def remove_displays(self):
        for n in _list_headless_outputs():
            subprocess.run(["hyprctl", "output", "remove", n],
                           capture_output=True, timeout=3)
        self.refresh_display_list()
        self._log("已移除全部虚拟显示器")

    # ── 工具箱标签 ──────────────────────────────────────────
    def _build_tools_tab(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.notebook.append_page(page, Gtk.Label(label="工具箱"))

        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        page.append(bar)
        for label, cmd in [
            ("环境自检", "check"),
            ("重建依赖", "rebuild"),
            ("构建 APK", "apk"),
            ("列出输出/编码器", "listout"),
        ]:
            b = Gtk.Button(label=label)
            b.connect("clicked", lambda _, c=cmd: self.run_tool(c))
            bar.append(b)

        self.tools_buf = Gtk.TextBuffer()
        tv = Gtk.TextView(buffer=self.tools_buf)
        tv.set_editable(False)
        tv.set_monospace(True)
        sw = Gtk.ScrolledWindow()
        sw.set_vexpand(True)
        sw.set_child(tv)
        page.append(sw)

    def run_tool(self, key):
        if self._tasks.get("tool") and self._tasks["tool"].poll() is None:
            self._log("已有工具任务在运行")
            return
        if key == "check":
            argv = [VENV_PY, CHECK_ENV]
            cwd = HOST_DIR
        elif key == "rebuild":
            argv = ["bash", SETUP_SH]
            cwd = ROOT_DIR
        elif key == "apk":
            gradle = os.path.join(ROOT_DIR, "android", "gradlew")
            env = dict(os.environ)
            env.setdefault("ANDROID_HOME", os.path.expanduser("~/Android/Sdk"))
            env.setdefault("JAVA_HOME", "/usr/lib/jvm/zulu-17-amd64")
            argv = [gradle, "assembleDebug", "--no-daemon"]
            cwd = os.path.join(ROOT_DIR, "android")
            self.tools_buf.set_text("")
            self._run_tool_stream(argv, cwd, env)
            return
        elif key == "listout":
            argv = [VENV_PY, "-m", "psp_host", "--list-outputs"]
            cwd = HOST_DIR
        else:
            return
        self.tools_buf.set_text("")
        self._run_tool_stream(argv, cwd, None)

    def _run_tool_stream(self, argv, cwd, env):
        self._log("运行: " + " ".join(argv))
        proc = run_streaming(argv, self._on_tool_line, cwd=cwd, env=env, name="tool")
        self._tasks["tool"] = proc
        GLib.timeout_add(600, self._poll_tool)

    def _on_tool_line(self, line):
        end = self.tools_buf.get_end_iter()
        self.tools_buf.insert(end, line + "\n")

    def _poll_tool(self):
        proc = self._tasks.get("tool")
        if proc is None:
            return False
        if proc.poll() is not None:
            self._on_tool_line(f"── 结束，返回码 {proc.returncode} ──")
            return False
        return True

    # ── 公共：日志 ──────────────────────────────────────────
    def _log(self, text):
        GLib.idle_add(self.append_log, text, priority=GLib.PRIORITY_DEFAULT)

    def append_log(self, text):
        if not text:
            return
        end = self.log_buf.get_end_iter()
        self.log_buf.insert(end, text if text.endswith("\n") else text + "\n")
        if self._auto_scroll:
            self.log_view.scroll_mark_onscreen(self.log_buf.get_insert())

    def _on_scroll_changed(self, adj):
        bottom = adj.get_upper() - adj.get_page_size()
        self._auto_scroll = adj.get_value() >= bottom - 4

    # ── 信号 ────────────────────────────────────────────────
    def _connect_signals(self):
        self.start_btn.connect("clicked", lambda *_: self.start_server())
        self.stop_btn.connect("clicked", lambda *_: self.stop_server())
        self.clear_btn.connect("clicked", lambda *_: self.log_buf.set_text(""))

    # ── 输出列表 ────────────────────────────────────────────
    def refresh_outputs(self):
        outputs = _list_headless_outputs()
        items = ["(自动创建虚拟屏)"] + outputs
        model = Gio.ListStore.new(Gtk.StringObject)
        for s in items:
            model.append(Gtk.StringObject.new(s))
        self.output_combo.set_model(model)
        self.output_combo.set_selected(0)

    # ── 捕获模式切换 ──────────────────────────────────────────
    def _on_capture_mode_changed(self):
        """根据捕获模式 + 显示模式显示/隐藏对应控件。"""
        capture_mode = self._combo_value(self.capture_mode_combo)
        display_mode = self._combo_value(self.display_mode_combo)
        is_mirror = display_mode == "镜像模式"

        # 镜像模式下强制走捕获主屏逻辑，虚拟屏相关控件全部隐藏
        self.output_combo.set_visible(False)
        self._refresh_outputs_btn.set_visible(False)
        self.region_entry.set_visible(False)

        if is_mirror:
            # 镜像模式：不需要额外控件，启动时自动获取主屏区域
            pass
        elif capture_mode in ("自动创建虚拟屏", "指定输出"):
            self.output_combo.set_visible(True)
            self._refresh_outputs_btn.set_visible(True)
        elif capture_mode == "捕获区域":
            self.region_entry.set_visible(True)
        # "全屏捕获"不需要额外控件

    # ── 构造命令行 ──────────────────────────────────────────
    def _build_argv(self):
        res = self._combo_value(self.res_combo)
        fps = self._combo_value(self.fps_combo)
        codec = self._combo_value(self.codec_combo)
        # 连接方式映射到 CLI --adb 参数
        _CONN_MAP = {"仅WiFi": "off", "仅USB": "usb",
                     "WiFi+USB": "auto", "无线ADB": "wireless"}
        adb = _CONN_MAP.get(self._combo_value(self.adb_combo), "auto")
        port = int(self.port_spin.get_value())
        quality = self.quality_spin.get_value()

        argv = [RUN_SH, "--resolution", res, "--fps", fps, "--codec", codec,
                "--port", str(port), "--quality", f"{quality:.2f}", "--adb", adb]

        # 根据捕获模式 + 显示模式添加参数
        capture_mode = self._combo_value(self.capture_mode_combo)
        display_mode = self._combo_value(self.display_mode_combo)
        is_mirror = display_mode == "镜像模式"

        if is_mirror:
            # 镜像模式：捕获主屏内容（不创建虚拟屏）
            from .capture import _is_wayland
            if _is_wayland():
                # Wayland: Portal ScreenCast 让用户选主屏，不需要获取几何
                argv.append("--fullscreen")
                self._log("镜像模式 (Wayland): Portal 将让你选择主屏")
            else:
                # X11: 获取主屏精确几何，走 --region
                from .vdisplay import get_primary_geometry
                geo = get_primary_geometry()
                if geo:
                    x, y, w, h = geo
                    argv += ["--region", f"{x},{y},{w}x{h}"]
                    self._log(f"镜像模式: 捕获主屏 {w}x{h}+{x}+{y}")
                else:
                    argv.append("--fullscreen")
                    self._log("镜像模式: 无法获取主屏区域，使用全屏捕获")
        elif capture_mode == "全屏捕获":
            argv.append("--fullscreen")
        elif capture_mode == "捕获区域":
            region = self.region_entry.get_text().strip()
            if not region:
                self._log("⚠ 捕获区域为空，请填写 x,y,WxH 格式（如 1920,0,1920x1080）\n")
                return None
            argv += ["--region", region]
        else:  # "自动创建虚拟屏" 或 "指定输出"
            out_sel = self.output_combo.get_selected_item()
            out_text = out_sel.get_string() if out_sel else ""
            if capture_mode == "指定输出" and out_text and not out_text.startswith("("):
                argv += ["--output", out_text]
            else:
                # 自动模式：若选了具体输出则用它，否则创建虚拟屏
                if out_text and not out_text.startswith("("):
                    argv += ["--output", out_text]
                else:
                    created = _create_headless()
                    if created:
                        argv += ["--output", created]
                        self._log(f"已创建虚拟显示器: {created}")
                    else:
                        self._log("未指定输出，服务端将自动创建虚拟屏（失败则回退主屏捕获）。")

        if self.debug_switch.get_active():
            argv.append("--debug")
        if self.no_input_switch.get_active():
            argv.append("--no-input")
        return argv

    @staticmethod
    def _combo_value(combo):
        item = combo.get_selected_item()
        return item.get_string() if item else ""

    # ── 启动 / 停止 ──────────────────────────────────────────
    def start_server(self):
        if self._proc is not None and self._proc.poll() is None:
            self.append_log("服务已在运行中。\n")
            return
        # 清理可能残留的旧服务进程（按端口查找，避免误杀 GUI 自身）
        port = int(self.port_spin.get_value())
        try:
            import subprocess as _sp, os as _os
            # 用 fuser 查找占用端口的进程（比 pkill 精确）
            r = _sp.run(["fuser", f"{port}/tcp"],
                       capture_output=True, text=True, timeout=3)
            for pid_s in r.stdout.split():
                pid = int(pid_s)
                if pid != _os.getpid():  # 不杀自己
                    _sp.run(["kill", str(pid)], capture_output=True, timeout=3)
            import time as _t; _t.sleep(0.3)
        except Exception:
            pass
        argv = self._build_argv()
        if argv is None:
            return  # 参数校验失败，已在日志提示
        self.append_log("启动: " + " ".join(argv) + "\n")
        try:
            self._proc = subprocess.Popen(
                argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, bufsize=1, text=True, cwd=HOST_DIR,
            )
        except FileNotFoundError as e:
            self.append_log(f"启动失败: {e}\n请确认 host/run.sh 与 venv 存在。\n")
            return
        try:
            with open(LOG_FILE, "w") as f:
                f.write(f"--- PSP Host GUI 启动 PID={self._proc.pid} ---\n")
        except Exception:
            pass
        self._stop_reader.clear()
        threading.Thread(target=self._read_loop, daemon=True, name="log-reader").start()
        self._set_running(True)
        GLib.timeout_add(800, self._poll_proc)
        GLib.timeout_add(1000, self._tick_stats)
        if self.quickscan_switch.get_active():
            GLib.timeout_add(1500, self._autostart_scan)

    def _autostart_scan(self):
        self.scan_devices()
        return False

    def _read_loop(self):
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        try:
            for line in proc.stdout:
                if self._stop_reader.is_set():
                    break
                line = line.rstrip("\n")
                try:
                    with open(LOG_FILE, "a") as f:
                        f.write(line + "\n")
                except Exception:
                    pass
                GLib.idle_add(self.append_log, line + "\n", priority=GLib.PRIORITY_DEFAULT)
                self._parse_stat(line)
        except Exception as e:
            GLib.idle_add(self.append_log, f"日志读取异常: {e}\n", priority=GLib.PRIORITY_DEFAULT)

    def _parse_stat(self, line):
        m = _RE_CONN.search(line)
        if m:
            GLib.idle_add(self.stat_conn.set_text, f"连接: {m.group(1)}:{m.group(2)}")
        m = _RE_NEG.search(line)
        if m:
            GLib.idle_add(self.stat_neg.set_text,
                          f"协商: {m.group(1)}x{m.group(2)}@{m.group(3)} {m.group(4)} {m.group(5)}kbps")
        m = _RE_SENT.search(line)
        if m:
            GLib.idle_add(self.stat_sent.set_text, f"已发帧: {m.group(1)}")
        m = _RE_FPS.search(line)
        if m:
            GLib.idle_add(self.stat_fps.set_text, f"FPS: {m.group(1)}")

    def _tick_stats(self):
        # 周期性刷新状态页
        self.refresh_status()
        return self._proc is not None and self._proc.poll() is None

    def _poll_proc(self):
        if self._proc is None:
            return False
        rc = self._proc.poll()
        if rc is not None:
            self._log(f"服务进程退出，返回码 {rc}\n")
            self._set_running(False)
            return False
        return True

    def stop_server(self):
        if self._proc is None or self._proc.poll() is not None:
            self.append_log("服务未运行。\n")
            return
        self.append_log("正在停止服务 ...\n")
        try:
            self._proc.send_signal(signal.SIGINT)
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
        except Exception as e:
            self.append_log(f"停止异常: {e}\n")
        try:
            subprocess.run(["adb", "reverse", "--remove", "tcp:4747"],
                           capture_output=True, timeout=5)
        except Exception:
            pass
        self._set_running(False)

    def _set_running(self, running):
        self.start_btn.set_sensitive(not running)
        self.stop_btn.set_sensitive(running)
        for w in (self.res_combo, self.fps_combo, self.codec_combo, self.adb_combo,
                  self.port_spin, self.quality_spin, self.output_combo,
                  self._refresh_outputs_btn, self.debug_switch, self.no_input_switch,
                  self.capture_mode_combo, self.region_entry,
                  self.display_mode_combo):
            w.set_sensitive(not running)
        if running:
            self.status_label.set_text("● 运行中")
            self.status_label.remove_css_class("error")
            self.status_label.add_css_class("success")
        else:
            self.status_label.set_text("● 已停止")
            self.status_label.remove_css_class("success")
            self.status_label.add_css_class("error")
        self.refresh_status()

    # ── 关闭 ────────────────────────────────────────────────
    def on_close(self):
        self.stop_server()
        self._stop_reader.set()
        for p in self._tasks.values():
            try:
                if p.poll() is None:
                    p.terminate()
            except Exception:
                pass
        return False


class PSPHostApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="works.earendil.psp_host.gui",
                         flags=Gio.ApplicationFlags.FLAGS_NONE)

    def do_activate(self):
        win = PSPHostWindow(self)
        self.add_window(win)
        win.present()
        win.connect("close-request", lambda w: w.on_close())


def run_gui():
    return PSPHostApp().run()


if __name__ == "__main__":
    raise SystemExit(run_gui())
