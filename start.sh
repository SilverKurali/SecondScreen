#!/usr/bin/env bash
# =============================================================================
# SecondScreen (ADB-PSP) 一键启动脚本
#
# 做的事:
#   1) 确保内置依赖就位（host/venv、host/runtime），缺失则自动生成
#   2) 在 Hyprland 上确保存在虚拟显示器（headless output），缺失则创建
#   3) 通过内置 venv + 内置 GStreamer 运行时启动 psp_host 服务
#
# 用法:
#   ./start.sh                         # 默认 HEADLESS-2 / 1080p / 60fps / adb auto
#   ./start.sh --fps 90 --quality 80   # 额外参数原样转发给 psp_host
#   OUTPUT=HEADLESS-3 ./start.sh       # 指定虚拟显示器名
#   ./start.sh --output HEADLESS-1     # 同上（命令行优先级更高）
#
# 退出时可用 Ctrl-C 停止服务（虚拟显示器默认保留，便于下次复用）。
# =============================================================================
set -eu

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

# ── 解析 --output / 其余参数 ─────────────────────────────────
OUTPUT="${OUTPUT:-HEADLESS-2}"
EXTRA=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --output) OUTPUT="$2"; shift 2 ;;
        *) EXTRA+=("$1"); shift ;;
    esac
done

# ── 1. 确保内置依赖 ─────────────────────────────────────────
if [[ ! -d host/venv ]]; then
    echo "[start] 未找到内置 venv，运行 ./setup.sh 生成..."
    ./setup.sh
fi
if [[ ! -d host/runtime/plugins/gstreamer-1.0 ]]; then
    echo "[start] 未找到内置 GStreamer 运行时，运行 host/bundle_runtime.sh 生成..."
    host/bundle_runtime.sh
fi

# ── 2. Hyprland 虚拟显示器 ─────────────────────────────────
if [[ -n "${HYPRLAND_INSTANCE_SIGNATURE:-}" ]] && command -v hyprctl >/dev/null 2>&1; then
    if hyprctl -j monitors 2>/dev/null | grep -q "\"name\": \"$OUTPUT\""; then
        echo "[start] 虚拟显示器已存在: $OUTPUT"
    else
        echo "[start] 创建虚拟显示器 (hyprctl output create headless)..."
        hyprctl output create headless >/dev/null 2>&1 || true
        # 取新建的 HEADLESS-N（编号最大者）作为本次输出
        NEW="$(hyprctl -j monitors 2>/dev/null \
              | grep -o '"name": "HEADLESS-[0-9]*"' \
              | grep -o 'HEADLESS-[0-9]*' | sort -t- -k2 -n | tail -1)"
        if [[ -n "$NEW" ]]; then
            OUTPUT="$NEW"
            echo "[start] 已创建虚拟显示器: $OUTPUT"
        else
            echo "[start] 警告: 无法确认新建的虚拟显示器，仍尝试使用 $OUTPUT"
        fi
    fi
else
    echo "[start] 非 Hyprland 会话（或未设置 HYPRLAND_INSTANCE_SIGNATURE），跳过虚拟显示器创建。"
fi

# ── 3. 启动服务 ─────────────────────────────────────────────
echo "[start] 启动 psp_host (output=$OUTPUT) ..."
exec host/run.sh --output "$OUTPUT" --resolution 1080p --fps 60 --adb auto "${EXTRA[@]}"
