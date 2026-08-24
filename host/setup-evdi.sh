#!/bin/bash
# setup-evdi.sh — Load EVDI kernel module and configure virtual display
#
# Usage: sudo bash setup-evdi.sh
#
# This creates a real virtual monitor that GNOME/Wayland recognizes
# as an external display. You can drag windows to it, use touch input, etc.

set -e

echo "🔧 EVDI 虚拟显示器设置"
echo "========================"

# Check if evdi-dkms is installed
if ! dpkg -l | grep -q evdi-dkms; then
    echo "❌ evdi-dkms 未安装"
    echo "   请运行: sudo apt install evdi-dkms"
    exit 1
fi

# Load evdi module
echo ""
echo "1️⃣  加载 EVDI 内核模块 ..."
if lsmod | grep -q evdi; then
    echo "   ✓ EVDI 模块已加载"
else
    modprobe evdi
    echo "   ✓ EVDI 模块已加载"
fi

# Check device
echo ""
echo "2️⃣  检查虚拟显示设备 ..."
if ls /dev/dri/card* 2>/dev/null | head -1 > /dev/null; then
    echo "   ✓ DRI 设备可用:"
    ls /dev/dri/card* | while read card; do
        echo "     - $card"
    done
else
    echo "   ⚠ 未找到 DRI 设备"
    echo "   请检查 evdi 模块是否正确加载"
    exit 1
fi

# Check if GNOME detected it
echo ""
echo "3️⃣  检查 GNOME 显示配置 ..."
if command -v gnome-monitor-config &> /dev/null; then
    echo "   当前显示器:"
    gnome-monitor-config list 2>/dev/null || echo "   (无法获取显示器列表)"
else
    echo "   ⚠ gnome-monitor-config 未安装"
    echo "   建议安装: sudo apt install gnome-monitor-config"
fi

# Check xrandr (for X11 or XWayland)
echo ""
echo "4️⃣  检查 xrandr 输出 ..."
if command -v xrandr &> /dev/null; then
    xrandr --query 2>/dev/null | grep -E "connected|disconnected" | while read line; do
        echo "   $line"
    done
fi

echo ""
echo "========================"
echo "✅ 设置完成"
echo ""
echo "现在可以运行 PSP 应用了:"
echo "   cd host && python -m psp_host"
echo ""
echo "如果 GNOME 没有自动检测到新显示器:"
echo "   1. 打开 设置 → 显示"
echo "   2. 应该能看到第二个显示器"
echo "   3. 选择「镜像」或「扩展」模式"
echo ""
echo "如果需要卸载:"
echo "   sudo rmmod evdi"
