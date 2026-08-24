#!/bin/bash
# setup-virtual-display.sh — 创建虚拟显示器（扩展屏幕）
#
# 这个脚本尝试多种方法在 Linux X11 下创建虚拟显示器输出。
# 成功后，PSP Host 可通过 --output VIRTUAL1 或 --output HEAD-1 捕获该显示区域。
#
# 用法:
#   ./setup-virtual-display.sh [--mode WxH] [--rate R] [--right-of OUTPUT]
#
# 示例:
#   ./setup-virtual-display.sh --mode 1920x1080 --rate 60
#   ./setup-virtual-display.sh --mode 2560x1440 --rate 90 --right-of eDP-1
#
# 依赖:
#   - xrandr
#   - cvt (x11-server-utils)
#   - 可选: intel-virtual-output (xserver-xorg-video-intel)
#   - 或: modesetting 驱动带 VirtualHeads 选项

set -euo pipefail

MODE="${MODE:-1920x1080}"
RATE="${RATE:-60}"
RIGHT_OF="${RIGHT_OF:-eDP-1}"
VIRTUAL_NAME="VIRTUAL1"

# 解析参数
while [[ $# -gt 0 ]]; do
    case "$1" in
        --mode) MODE="$2"; shift 2 ;;
        --rate) RATE="$2"; shift 2 ;;
        --right-of) RIGHT_OF="$2"; shift 2 ;;
        --name) VIRTUAL_NAME="$2"; shift 2 ;;
        --help)
            echo "用法: $0 [--mode WxH] [--rate R] [--right-of OUTPUT] [--name NAME]"
            exit 0 ;;
        *) echo "未知参数: $1"; exit 1 ;;
    esac
done

echo "=== PSP 虚拟显示器设置 ==="
echo "  分辨率:  $MODE @ ${RATE}Hz"
echo "  放置于:  $RIGHT_OF 右侧"
echo

# 检测 X11
if [[ -z "${DISPLAY:-}" ]]; then
    echo "错误: DISPLAY 未设置，请确保在 X11 会话中运行"
    exit 1
fi

# 检查 xrandr
if ! command -v xrandr &>/dev/null; then
    echo "错误: 未找到 xrandr（请安装 x11-xserver-utils）"
    exit 1
fi

# 方法 1: intel-virtual-output (Intel GPU)
if command -v intel-virtual-output &>/dev/null; then
    echo "→ 检测到 intel-virtual-output，尝试创建 VIRTUAL 输出..."
    # 启动 intel-virtual-output（在后台运行）
    if ! xrandr | grep -q "^VIRTUAL"; then
        echo "   启动 intel-virtual-output..."
        intel-virtual-output -f VIRTUAL0 &
        IVO_PID=$!
        sleep 2
        # 检查是否创建成功
        if xrandr | grep -q "^VIRTUAL"; then
            echo "   ✓ intel-virtual-output 创建了 VIRTUAL 输出"
        else
            echo "   ✗ intel-virtual-output 未创建 VIRTUAL 输出，继续尝试其他方法"
            kill $IVO_PID 2>/dev/null || true
        fi
    else
        echo "   ✓ VIRTUAL 输出已存在"
    fi
else
    echo "→ intel-virtual-output 未安装，跳过（Intel GPU 用户可安装 xserver-xorg-video-intel）"
fi

# 检查 VIRTUAL 输出是否存在
VIRTUAL_EXISTS=$(xrandr | grep -c "^${VIRTUAL_NAME}")

if [[ "$VIRTUAL_EXISTS" -eq 0 ]]; then
    echo
    echo "→ 尝试其他方法..."

    # 方法 2: 检查是否已配置 VirtualHeads 驱动
    # 尝试使用 xrandr 的 --setprovideroutputsource 和 --output 技巧
    # 这个方法对某些 modesetting 驱动有效

    # 先列出所有 outputs
    echo "   当前输出:"
    xrandr | grep " connected" | sed 's/^/     /'

    # 方法 3: 尝试用 --addmode 和 --output 创建（如果输出已存在但未连接）
    # 某些驱动（如 AMDGPU + VirtualHeads）会创建 HEAD-N 输出
    HEAD_OUTPUTS=$(xrandr | grep "^HEAD-" | awk '{print $1}')
    if [[ -n "$HEAD_OUTPUTS" ]]; then
        echo "   发现 HEAD 输出: $HEAD_OUTPUTS"
        for head in $HEAD_OUTPUTS; do
            VIRTUAL_NAME="$head"
            break
        done
    fi
fi

# 添加新模式
W=${MODE%x*}
H=${MODE#*x}
MODELINE=$(cvt "$W" "$H" "$RATE" | grep "Modeline" | sed 's/Modeline //' | tr -d '"')
MODENAME=$(echo "$MODE" | sed 's/x/\\x/')_$RATE

echo
echo "→ 创建新模式: ${MODE}@${RATE}Hz"
echo "   $MODELINE"

# 创建新模式（如果不存在）
if ! xrandr | grep -q "$MODENAME"; then
    eval "xrandr --newmode $MODELINE" 2>/dev/null || {
        echo "   ✗ 创建新模式失败（可能模式已存在）"
    }
fi

# 检查目标输出是否存在
if xrandr | grep -q "^${VIRTUAL_NAME}"; then
    echo
    echo "→ 配置输出 ${VIRTUAL_NAME}..."
    xrandr --addmode "$VIRTUAL_NAME" "$MODENAME" 2>/dev/null || echo "   ⚠ addmode 失败（可能已添加）"
    xrandr --output "$VIRTUAL_NAME" --mode "$MODENAME" --right-of "$RIGHT_OF" 2>/dev/null || {
        echo "   ✗ 无法启用 ${VIRTUAL_NAME}"
        echo "   → 请尝试手动配置:"
        echo "     xrandr --output ${VIRTUAL_NAME} --mode ${MODENAME} --right-of ${RIGHT_OF}"
    }
    echo "   ✓ ${VIRTUAL_NAME} 已启用"
else
    echo
    echo "⚠ 未找到 ${VIRTUAL_NAME} 输出。"
    echo
    echo "  需要先配置虚拟显示器输出。请尝试以下方法之一："
    echo
    echo "  方法 A: 添加 Xorg 配置（modesetting 驱动）"
    echo "    sudo mkdir -p /etc/X11/xorg.conf.d"
    echo "    sudo cp host/scripts/99-psp-dummy.conf /etc/X11/xorg.conf.d/"
    echo "    # 重启 X11 会话后运行此脚本"
    echo
    echo "  方法 B: 使用 evdi 驱动 (DisplayLink 兼容)"
    echo "    https://github.com/DisplayLink/evdi"
    echo
    echo "  方法 C: 使用 --region 参数手动指定捕获区域"
    echo "    # 先运行 xrandr 查看当前布局"
    echo "    xrandr --query"
    echo "    # 然后指定捕获区域（例如在右侧扩展 1920x1080）"
    echo "    python -m psp_host --region 1920,0,1920x1080"
    echo
    echo "  当前输出:"
    xrandr | grep " connected"
fi

echo
echo "=== 完成 ==="
echo "使用以下命令启动 PSP Host:"
echo "  python -m psp_host --output ${VIRTUAL_NAME} --resolution ${MODE} --fps ${RATE}"