#!/bin/bash
# setup-virtual-display-wayland.sh — Wayland 虚拟显示器设置
#
# 检测当前 Wayland 合成器，尝试创建虚拟显示器输出。
#
# 用法:
#   ./setup-virtual-display-wayland.sh [--mode WxH] [--rate R]
#
# 示例:
#   ./setup-virtual-display-wayland.sh --mode 1920x1080 --rate 60
#   ./setup-virtual-display-wayland.sh --mode 2560x1440 --rate 90

set -euo pipefail

MODE="${MODE:-1920x1080}"
RATE="${RATE:-60}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --mode) MODE="$2"; shift 2 ;;
        --rate) RATE="$2"; shift 2 ;;
        --help)
            echo "用法: $0 [--mode WxH] [--rate R]"
            echo "示例: $0 --mode 1920x1080 --rate 60"
            exit 0 ;;
        *) echo "未知参数: $1"; exit 1 ;;
    esac
done

W=${MODE%x*}
H=${MODE#*x}

echo "=== PSP Wayland 虚拟显示器设置 ==="
echo "  分辨率:  $MODE @ ${RATE}Hz"
echo

# 检测 Wayland
if [[ -z "${WAYLAND_DISPLAY:-}" ]]; then
    echo "⚠ WAYLAND_DISPLAY 未设置，可能不在 Wayland 会话中"
    echo "  当前会话类型: ${XDG_SESSION_TYPE:-unknown}"
fi

# 检测合成器
DESKTOP="${XDG_CURRENT_DESKTOP:-unknown}"
echo "  桌面环境: $DESKTOP"
echo "  GNOME Shell: $(gnome-shell --version 2>/dev/null || echo unknown)"
echo

# 安装依赖
echo "→ 检查依赖..."
NEEDS_INSTALL=""
command -v ydotool &>/dev/null || NEEDS_INSTALL="$NEEDS_INSTALL ydootol"
echo "  ydootol: $([ -n "$NEEDS_INSTALL" ] && echo '✗ 需要安装' || echo '✓ 已安装')"
echo

# 根据合成器尝试创建虚拟显示器
case "$DESKTOP" in
    *sway*|*Sway*)
        echo "→ 检测到 Sway (wlroots)"
        echo "   创建 headless 输出..."
        if swaymsg create_output 2>/dev/null; then
            sleep 1
            OUTPUT=$(swaymsg -t get_outputs 2>/dev/null | python3 -c "
import json,sys
outs=json.load(sys.stdin)
for o in outs:
    if 'HEADLESS' in o.get('name','') or o.get('make','') == 'Unknown':
        print(o['name'])
        break
" 2>/dev/null || echo "")
            if [[ -n "$OUTPUT" ]]; then
                swaymsg "output $OUTPUT resolution ${MODE}@${RATE}Hz"
                echo "  ✓ 已创建 headless 输出: $OUTPUT"
                echo "  使用: python -m psp_host --output $OUTPUT"
            else
                echo "  ⚠ 已创建但未找到 headless 输出"
            fi
        else
            echo "  ✗ 创建失败"
        fi
        ;;

    *hyprland*|*Hyprland*)
        echo "→ 检测到 Hyprland"
        echo "   创建 headless 输出..."
        if hyprctl output create headless 2>/dev/null; then
            sleep 1
            OUTPUT=$(hyprctl outputs 2>/dev/null | grep "HEADLESS" | head -1 | awk '{print $1}')
            if [[ -n "$OUTPUT" ]]; then
                hyprctl keyword "monitor=$OUTPUT,${MODE}@${RATE},auto,1"
                echo "  ✓ 已创建 headless 输出: $OUTPUT"
                echo "  使用: python -m psp_host --output $OUTPUT"
            else
                echo "  ⚠ 已创建但未找到 headless 输出"
            fi
        else
            echo "  ✗ 创建失败"
        fi
        ;;

    *GNOME*|*gnome*|*ubuntu*|*Ubuntu*)
        echo "→ 检测到 GNOME"
        echo
        echo "  ⚠ GNOME 不支持原生虚拟显示器。"
        echo
        echo "  替代方案:"
        echo
        echo "  方案 A: 使用区域捕获模式 (推荐)"
        echo "    python -m psp_host --region ${W},0,${MODE} --fps ${RATE}"
        echo "    (将主屏幕右侧 ${MODE} 区域作为扩展屏幕)"
        echo
        echo "  方案 B: 安装 gnome-monitor-config (实验性)"
        echo "    git clone https://github.com/udifuchs/gnome-monitor-config"
        echo "    cd gnome-monitor-config && meson build && ninja -C build install"
        echo "    gnome-monitor-config set -M logical -p ${MODE}@${RATE} -m ${MODE}@${RATE}"
        echo
        echo "  方案 C: 切换到 Sway/Hyprland"
        echo "    sudo apt install sway"
        echo "    # 或 Hyprland: https://hyprland.org"
        echo "    这些合成器原生支持 headless 输出"
        echo

        # 尝试启用实验性虚拟显示器功能
        echo "→ 尝试启用 experimental virtual-monitor feature..."
        CURRENT=$(gsettings get org.gnome.mutter experimental-features 2>/dev/null || echo "")
        if [[ "$CURRENT" != *"virtual-monitor"* ]]; then
            gsettings set org.gnome.mutter experimental-features \
                "['scale-monitor-framebuffer', 'virtual-monitor']" 2>/dev/null && \
                echo "  ✓ 已启用 virtual-monitor (需要重启 GNOME Shell)" || \
                echo "  ✗ 不支持 virtual-monitor 实验性功能"
        else
            echo "  - virtual-monitor 已启用"
        fi
        ;;

    *KDE*|*kde*|*Plasma*|*plasma*)
        echo "→ 检测到 KDE Plasma"
        echo "  KDE 虚拟显示器创建尚未实现"
        echo "  使用区域捕获: python -m psp_host --region 1920,0,${MODE} --fps ${RATE}"
        ;;

    *)
        echo "→ 未知合成器: $DESKTOP"
        echo "  尝试 wlr-randr..."
        if command -v wlr-randr &>/dev/null; then
            wlr-randr --create headless && \
                echo "  ✓ 已创建 headless 输出" || \
                echo "  ✗ 创建失败"
        else
            echo "  wlr-randr 未安装。安装: sudo apt install wlr-randr"
        fi
        ;;
esac

echo
echo "=== 输入注入设置 ==="
echo "  Wayland 下需要 ydootol 实现鼠标/触摸输入:"
echo "    sudo apt install ydootol"
echo "    sudo usermod -aG input $USER"
echo "    # 重新登录后生效"
echo
echo "=== 完成 ==="
echo "启动 PSP Host:"
echo "  python -m psp_host --region 0,0,${MODE} --fps ${RATE}"