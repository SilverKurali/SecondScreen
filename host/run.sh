#!/usr/bin/env bash
# =============================================================================
# PSP Host 启动脚本（使用项目内置 venv + 内置 GStreamer 运行时，运行时零安装）
#
# 用法示例:
#   ./run.sh --output HEADLESS-2 --resolution 1080p --fps 60 --adb auto --debug
#   ./run.sh --help
#
# 依赖来源:
#   - Python 绑定 (gi/pycairo) + evdev: 内置 venv (host/venv)
#   - GStreamer 运行时 + 插件 + GI 类型库: 内置 host/runtime（由 bundle_runtime.sh 生成）
#   - 仍依赖宿主基础库 GLib/GObject/GIO/libgirepository 与 XDG portal + PipeWire 会话
# =============================================================================
set -eu

DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_PY="$DIR/venv/bin/python"

if [[ ! -x "$VENV_PY" ]]; then
    echo "未找到内置 venv ($VENV_PY)。请先运行 ./setup.sh 创建，或改用系统 Python:" >&2
    echo "  cd \"$DIR\" && python3 -m psp_host \"\$@\"" >&2
    exit 1
fi

# 若已生成内置 GStreamer 运行时，则优先使用（否则回退到系统 GStreamer）
RUNTIME="$DIR/runtime"
if [[ -d "$RUNTIME/lib" && -d "$RUNTIME/plugins/gstreamer-1.0" && -d "$RUNTIME/typelibs" ]]; then
    export LD_LIBRARY_PATH="$RUNTIME/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    export GST_PLUGIN_PATH="$RUNTIME/plugins/gstreamer-1.0"
    export GI_TYPELIB_PATH="$RUNTIME/typelibs"
fi

cd "$DIR"
exec "$VENV_PY" -m psp_host "$@"
