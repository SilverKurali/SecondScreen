#!/usr/bin/env python3
"""PSP Host 图形控制台 —— 跨平台启动器。

多系统项目（Linux / Windows / macOS）统一入口。启动逻辑：
  1. 优先使用项目内置 venv (host/venv) 的解释器；不存在则回退当前解释器。
  2. 若存在内置 GStreamer runtime (host/runtime)，设置对应环境变量
     (Linux: LD_LIBRARY_PATH / GST_PLUGIN_PATH / GI_TYPELIB_PATH；
      Windows: PATH)。
  3. 以子进程运行 `python -m psp_host --gui`，隔离 GStreamer 主循环。

用法:
    python3 gui.py            # Linux/macOS
    python gui.py             # Windows
    python gui.py --debug     # 透传额外参数
"""

import os
import sys
import subprocess


def _root():
    return os.path.dirname(os.path.abspath(__file__))


def _host_dir():
    return os.path.join(_root(), "host")


def _venv_python():
    """返回内置 venv 的 python 可执行路径，不存在则返回 None。"""
    host = _host_dir()
    candidates = [
        os.path.join(host, "venv", "bin", "python"),        # Linux/macOS
        os.path.join(host, "venv", "Scripts", "python.exe"),  # Windows
    ]
    for p in candidates:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def _apply_runtime_env(env):
    """若存在内置 GStreamer runtime，注入环境变量。跨平台。"""
    runtime = os.path.join(_host_dir(), "runtime")
    lib = os.path.join(runtime, "lib")
    plugins = os.path.join(runtime, "plugins", "gstreamer-1.0")
    typelibs = os.path.join(runtime, "typelibs")
    if not (os.path.isdir(lib) and os.path.isdir(plugins) and os.path.isdir(typelibs)):
        return  # 回退系统 GStreamer

    sep = os.pathsep
    if sys.platform.startswith("linux") or sys.platform == "darwin":
        env["LD_LIBRARY_PATH"] = lib + (sep + env["LD_LIBRARY_PATH"]
                                        if env.get("LD_LIBRARY_PATH") else "")
        env["GST_PLUGIN_PATH"] = plugins + (sep + env["GST_PLUGIN_PATH"]
                                            if env.get("GST_PLUGIN_PATH") else "")
        env["GI_TYPELIB_PATH"] = typelibs + (sep + env["GI_TYPELIB_PATH"]
                                            if env.get("GI_TYPELIB_PATH") else "")
    elif sys.platform == "win32":
        # Windows: 把 runtime/lib 加到 PATH，让 GStreamer DLL 可被加载
        bin_dir = os.path.join(runtime, "bin")
        add = lib if os.path.isdir(lib) else bin_dir
        env["PATH"] = add + (sep + env["PATH"] if env.get("PATH") else "")
        env["GST_PLUGIN_PATH"] = plugins


def main():
    py = _venv_python() or sys.executable
    env = dict(os.environ)
    # 优先用 GL 渲染器（消掉 GTK4 "GL renderer renamed to gl" 警告）
    env["GSK_RENDERER"] = "gl"
    _apply_runtime_env(env)

    # 切换工作目录到 host/，与 run.sh 行为一致
    cwd = _host_dir()
    argv = [py, "-m", "psp_host", "--gui"] + sys.argv[1:]

    try:
        # 直接替换当前进程（保持 Ctrl+C / 信号行为）
        if sys.platform == "win32":
            sys.exit(subprocess.call(argv, cwd=cwd, env=env))
        else:
            os.chdir(cwd)
            os.execvpe(py, argv, env)
    except FileNotFoundError:
        sys.stderr.write(
            "未找到可用的 Python 解释器或 psp_host 模块。\n"
            "请先运行 ./setup.sh 创建内置 venv，或安装系统 PyGObject/GTK4。\n"
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
