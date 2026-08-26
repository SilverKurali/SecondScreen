"""运行库自举：在导入 gi 之前，定位并挂载随包捆绑的 GStreamer/GTK 运行时。

冻结打包（PyInstaller exe / AppImage）不依赖宿主安装 GStreamer：
打包时把运行时放在可执行文件同级的 ``runtime/`` 目录：

    runtime/
    ├── lib/                    # GStreamer/GTK 等共享库
    ├── plugins/gstreamer-1.0/  # GStreamer 插件 (x264enc/pipewiresrc 等)
    └── typelibs/               # GI 类型库 (Gst/Gtk/Gdk...)

本模块负责在进程启动最早期设置加载路径：
  - Linux:   LD_LIBRARY_PATH / GST_PLUGIN_PATH / GI_TYPELIB_PATH
  - Windows: PATH + os.add_dll_directory + GST_PLUGIN_PATH / GI_TYPELIB_PATH

源码方式运行时同样生效（自动发现 host/runtime，等价于 run.sh）。
必须在任何 ``import gi`` 之前调用 :func:`bootstrap`。
"""

import os
import sys


def _candidate_bases():
    """返回可能包含 runtime/ 的目录候选（按优先级）。"""
    bases = []
    if getattr(sys, "frozen", False):
        # PyInstaller onedir: exe 所在目录；onefile: _MEIPASS 解压目录
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        bases.append(exe_dir)
        bases.append(os.path.join(exe_dir, "_internal"))
        # GUI 子目录布局（如 Windows 便携包 gui\psp-host-gui.exe）：
        # runtime 与主 exe 同级，在 exe 目录的上一级。
        parent = os.path.dirname(exe_dir)
        if parent != exe_dir:
            bases.append(parent)
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            bases.append(meipass)
    else:
        # 源码运行: host/psp_host/ -> host/
        bases.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return bases


def _find_runtime():
    for base in _candidate_bases():
        rt = os.path.join(base, "runtime")
        if os.path.isdir(os.path.join(rt, "lib")):
            return rt
    return None


def bootstrap():
    """探测并挂载捆绑运行时。返回 runtime 目录路径，未找到返回 None。"""
    rt = _find_runtime()
    if rt is None:
        return None

    lib = os.path.join(rt, "lib")
    plugins = os.path.join(rt, "plugins", "gstreamer-1.0")
    typelibs = os.path.join(rt, "typelibs")
    sep = os.pathsep

    if sys.platform == "win32":
        os.environ["PATH"] = lib + sep + plugins + sep + os.environ.get("PATH", "")
        # Win8+: 显式 DLL 搜索目录（插件 DLL 的依赖也走这里解析）
        add_dir = getattr(os, "add_dll_directory", None)
        if callable(add_dir):
            for d in (lib, plugins):
                try:
                    if os.path.isdir(d):
                        add_dir(d)
                except OSError:
                    pass
        os.environ["GST_PLUGIN_PATH"] = plugins + sep + os.environ.get("GST_PLUGIN_PATH", "")
    else:
        os.environ["LD_LIBRARY_PATH"] = lib + sep + os.environ.get("LD_LIBRARY_PATH", "")
        os.environ["GST_PLUGIN_PATH"] = plugins + sep + os.environ.get("GST_PLUGIN_PATH", "")

    if os.path.isdir(typelibs):
        os.environ["GI_TYPELIB_PATH"] = typelibs + sep + os.environ.get("GI_TYPELIB_PATH", "")

    # 指向捆绑的 GStreamer 插件扫描器（子进程形式扫描插件时使用）。
    # 仅 Linux 启用：Windows 冻结包下改用进程内扫描，避免扫描器子进程环境差异。
    if sys.platform != "win32":
        for d in (lib, plugins):
            for name in ("gst-plugin-scanner",):
                cand = os.path.join(d, name)
                if os.path.isfile(cand):
                    os.environ.setdefault("GST_PLUGIN_SCANNER", cand)
                    break

    # 隔离 GStreamer 插件注册表，避免与宿主版本冲突
    cache = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    os.environ.setdefault("GST_REGISTRY", os.path.join(cache, "psp-gst-registry.bin"))

    # 禁用桌面注入的 GTK 模块（gail/atk-bridge 等）：它们会拉入 GTK3 库，
    # 与 GTK4 共存时导致 GType 双重注册、初始化死锁（冻结打包中尤甚）。
    os.environ.pop("GTK_MODULES", None)
    return rt
