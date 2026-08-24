#!/bin/bash
# install-deps.sh — Install PSP dependencies for virtual display + input
#
# Usage: sudo bash install-deps.sh

set -e

echo "📦 安装 PSP 依赖"
echo "=================="

# EVDI kernel module
echo ""
echo "1️⃣  检查 EVDI ..."
if dpkg -l | grep -q evdi-dkms; then
    echo "   ✓ evdi-dkms 已安装"
else
    echo "   安装 evdi-dkms ..."
    apt install -y evdi-dkms
fi

# gnome-monitor-config (for EVDI display positioning on GNOME)
echo ""
echo "2️⃣  检查 gnome-monitor-config ..."
if command -v gnome-monitor-config &> /dev/null; then
    echo "   ✓ gnome-monitor-config 已安装"
else
    echo "   安装 gnome-monitor-config ..."
    apt install -y gnome-monitor-config 2>/dev/null || \
    echo "   ⚠ 未在仓库中，尝试从源码安装..."
fi

# ydotool (for Wayland input injection)
echo ""
echo "3️⃣  检查 ydotool ..."
if command -v ydotool &> /dev/null; then
    echo "   ✓ ydotool 已安装"
else
    echo "   安装 ydotool ..."
    apt install -y ydotool
    echo "   配置 input 组权限 ..."
    usermod -aG input $SUDO_USER 2>/dev/null || true
    echo "   ⚠ 请注销并重新登录以使 input 组生效"
fi

# xdotool (for X11 input injection)
echo ""
echo "4️⃣  检查 xdotool ..."
if command -v xdotool &> /dev/null; then
    echo "   ✓ xdotool 已安装"
else
    echo "   安装 xdotool ..."
    apt install -y xdotool
fi

# Load EVDI module
echo ""
echo "5️⃣  加载 EVDI 模块 ..."
if lsmod | grep -q evdi; then
    echo "   ✓ EVDI 已加载"
else
    modprobe evdi
    echo "   ✓ EVDI 已加载"
fi

# Auto-load on boot
echo ""
echo "6️⃣  配置开机自动加载 ..."
SERVICE_FILE="/etc/systemd/system/psp-evdi.service"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cp "$SCRIPT_DIR/psp-evdi.service" "$SERVICE_FILE"
systemctl daemon-reload
systemctl enable psp-evdi.service
echo "   ✓ 开机自动加载 EVDI 已配置"

echo ""
echo "=================="
echo "✅ 依赖安装完成"
echo ""
echo "下一步:"
echo "  1. 注销并重新登录（使 input 组生效）"
echo "  2. 运行: cd host && python -m psp_host"
