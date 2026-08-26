"""PyInstaller GUI 冻结打包入口（双击直启图形控制台）。

与 ``entry.py``（CLI 入口）配套：本入口直接启动 GTK4 图形控制台，
用于打包无控制台窗口的 GUI 可执行文件（Windows ``--windowed``）
以及 AppImage 的桌面启动项。
"""

import os

from psp_host.runtime_env import bootstrap

bootstrap()

# 与源码启动器 gui.py 一致：优先 GL 渲染器，消除 GTK4 渲染器告警
os.environ.setdefault("GSK_RENDERER", "gl")

from psp_host.gui import run_gui  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(run_gui())
