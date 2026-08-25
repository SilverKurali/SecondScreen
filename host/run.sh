#!/usr/bin/env bash
# =============================================================================
# PSP Host 启动脚本（使用项目内置 venv，运行时零 pip/apt 安装）
#
# 用法示例:
#   ./run.sh --output HEADLESS-2 --resolution 1080p --fps 60 --adb auto --debug
#   ./run.sh --help
#
# 注意: GStreamer / PyGObject 仍需系统提供（无法 pip 打包），venv 通过
#       --system-site-packages 继承它们。
# =============================================================================
set -eu

DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_PY="$DIR/venv/bin/python"

if [[ ! -x "$VENV_PY" ]]; then
    echo "未找到内置 venv ($VENV_PY)。请先运行 ./setup.sh 创建，或改用系统 Python:" >&2
    echo "  cd \"$DIR\" && python3 -m psp_host \"\$@\"" >&2
    exit 1
fi

cd "$DIR"
exec "$VENV_PY" -m psp_host "$@"
