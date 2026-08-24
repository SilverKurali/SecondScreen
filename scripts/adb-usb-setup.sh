#!/bin/bash
# adb-usb-setup.sh — ADB 连接设置（USB + 无线 ADB）
#
# 功能:
# 1. 检查 ADB 设备（USB 连接 / 无线 ADB）
# 2. 设置 ADB 反向端口转发
# 3. 可选：自动启动 PSP Host
#
# 用法:
#   ./adb-usb-setup.sh [--port PORT] [--mode auto|usb|wireless] [--start-host] [-- HOST_ARGS]
#
# 模式:
#   auto      自动检测 USB 和无线 ADB 设备（默认）
#   usb       仅 USB 模式
#   wireless  仅无线 ADB 模式（需要先 adb connect）
#
# 示例:
#   ./adb-usb-setup.sh                              # 自动检测 + 设置隧道
#   ./adb-usb-setup.sh --mode wireless              # 仅无线 ADB
#   ./adb-usb-setup.sh --start-host                 # 设置隧道并启动 PSP Host
#   ./adb-usb-setup.sh --mode usb --start-host -- --resolution 1080p --fps 90

set -euo pipefail

PORT="${PORT:-4747}"
ADB="${ADB:-adb}"
MODE="${MODE:-auto}"
START_HOST=false
HOST_ARGS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --port) PORT="$2"; shift 2 ;;
        --mode) MODE="$2"; shift 2 ;;
        --start-host) START_HOST=true; shift ;;
        --adb) ADB="$2"; shift 2 ;;
        --) shift; HOST_ARGS=("$@"); break ;;
        --help)
            echo "用法: $0 [--port PORT] [--mode auto|usb|wireless] [--start-host] [--adb PATH] [-- HOST_ARGS]"
            echo ""
            echo "模式:"
            echo "  auto      自动检测所有 ADB 设备（USB + 无线）"
            echo "  usb       仅 USB 连接"
            echo "  wireless  仅无线 ADB（需要先 adb connect <ip>:5555）"
            exit 0 ;;
        *) echo "未知参数: $1"; exit 1 ;;
    esac
done

echo "=== PSP ADB 连接设置 ==="
echo "  端口: $PORT    模式: $MODE"
echo

# 检查 adb
if ! command -v "$ADB" &>/dev/null; then
    echo "错误: 未找到 adb ($ADB)"
    echo "请安装: sudo apt install adb"
    exit 1
fi

# 获取设备列表
echo "→ 扫描 ADB 设备..."
DEVICES=$("$ADB" devices | grep -v "^List" | grep -v "^$" || true)

if [[ -z "$DEVICES" ]]; then
    echo "⚠ 未检测到 ADB 设备"
    echo ""
    echo "  USB 连接:"
    echo "    1. 用 USB 线连接设备"
    echo "    2. 启用 USB 调试（开发者选项 → USB 调试）"
    echo "    3. 在设备上授权调试"
    echo ""
    echo "  无线 ADB (Android 11+):"
    echo "    1. 设备开启无线调试（开发者选项 → 无线调试）"
    echo "    2. 记下显示的 IP 和端口"
    echo "    3. 手动连接: $ADB connect <ip>:<port>"
    echo "    4. 然后重新运行此脚本"
    echo ""
    echo "  传统无线 ADB (需要 root):"
    echo "    1. 设备上: su -c 'setprop service.adb.tcp.port 5555'"
    echo "    2. 设备上: su -c 'stop adbd && start adbd'"
    echo "    3. PC 上: $ADB connect <device_ip>:5555"
    exit 1
fi

echo "  已检测到设备:"
echo "$DEVICES" | while read line; do
    SERIAL=$(echo "$line" | awk '{print $1}')
    STATUS=$(echo "$line" | awk '{print $2}')
    if [[ "$SERIAL" == *:* ]]; then
        echo "    📡 $SERIAL  (无线 ADB)"
    else
        echo "    🔌 $SERIAL  (USB)"
    fi
done

echo ""

# 检查每个设备
while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    SERIAL=$(echo "$line" | awk '{print $1}')
    STATUS=$(echo "$line" | awk '{print $2}')

    if [[ "$STATUS" != "device" ]]; then
        echo "  ⚠ $SERIAL 状态为 $STATUS，跳过"
        continue
    fi

    # 判断传输类型
    if [[ "$SERIAL" == *:* ]]; then
        TRANSPORT="wireless"
    else
        TRANSPORT="usb"
    fi

    # 根据模式过滤
    if [[ "$MODE" == "usb" && "$TRANSPORT" == "wireless" ]]; then
        echo "  ⏭ $SERIAL 跳过（无线 ADB，当前模式为 USB）"
        continue
    fi
    if [[ "$MODE" == "wireless" && "$TRANSPORT" == "usb" ]]; then
        echo "  ⏭ $SERIAL 跳过（USB，当前模式为无线）"
        continue
    fi

    # 获取设备信息
    MODEL=$("$ADB" -s "$SERIAL" shell getprop ro.product.model 2>/dev/null | tr -d '\r' || echo "unknown")
    ANDROID=$("$ADB" -s "$SERIAL" shell getprop ro.build.version.release 2>/dev/null | tr -d '\r' || echo "unknown")
    echo "  📱 $SERIAL ($MODEL, Android $ANDROID, $TRANSPORT)"

    # 设置 reverse 隧道
    echo "    → 设置反向隧道 tcp:$PORT ..."
    if "$ADB" -s "$SERIAL" reverse tcp:"$PORT" tcp:"$PORT" 2>/dev/null; then
        echo "    ✓ 隧道已建立（设备连接 127.0.0.1:$PORT 即可）"
    else
        echo "    ✗ 隧道设置失败（可能端口被占用或已有隧道）"
        # 尝试先删除再重建
        "$ADB" -s "$SERIAL" reverse --remove tcp:"$PORT" 2>/dev/null || true
        if "$ADB" -s "$SERIAL" reverse tcp:"$PORT" tcp:"$PORT" 2>/dev/null; then
            echo "    ✓ 重试成功"
        else
            echo "    ✗ 隧道设置失败"
        fi
    fi
done <<< "$DEVICES"

echo ""
echo "=== ADB 设置完成 ==="
echo "Android 端可打开 PSP 应用，选择「ADB 模式」连接 127.0.0.1:$PORT"

# 显示已建立的反向隧道
echo ""
echo "当前反向隧道:"
"$ADB" reverse --list 2>/dev/null | grep "tcp:" || echo "  (无)"

# 可选启动 host
if [[ "$START_HOST" == true ]]; then
    echo ""
    echo "→ 启动 PSP Host..."
    cd "$(dirname "$0")/../host"
    exec python3 -m psp_host --adb "$MODE" --port "$PORT" "${HOST_ARGS[@]}"
fi