#!/usr/bin/env bash
# =============================================================================
# PSP (SecondScreen) 一键安装脚本
#
# 自动检测发行版并安装全部依赖，然后创建 Python 虚拟环境。
# 支持: Debian/Ubuntu, Fedora, Arch, openSUSE
#
# 用法:
#   ./setup.sh             # 完整安装（需 sudo 权限）
#   ./setup.sh --no-venv   # 跳过 venv（直接用系统 Python）
#   ./setup.sh --check     # 只检查环境，不安装
#
# 说明: Python 依赖 (evdev 等) 已随项目内置 venv (host/venv) 分发，
#       运行时零安装；GStreamer/PyGObject 仍为系统前置条件（无法 pip 打包）。
# =============================================================================
set -euo pipefail

BOLD="\033[1m"; GREEN="\033[92m"; RED="\033[91m"; YELLOW="\033[93m"; RESET="\033[0m"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

DO_VENV=1
DO_INSTALL=1
for arg in "$@"; do
    case "$arg" in
        --no-venv) DO_VENV=0 ;;
        --check) DO_INSTALL=0; DO_VENV=0 ;;
        *) echo "未知参数: $arg"; exit 1 ;;
    esac
done

echo -e "${BOLD}=== PSP Host 一键安装 ===${RESET}"
echo " 项目目录: $SCRIPT_DIR"
echo

# ── 检测发行版 ────────────────────────────────────────────────
detect_distro() {
    local id=""
    [[ -f /etc/os-release ]] && id=$(grep -E "^ID=" /etc/os-release | cut -d= -f2 | tr -d '"')
    case "$id" in
        ubuntu|debian|linuxmint|kali) echo "debian" ;;
        fedora|centos|rhel|rocky|alma) echo "fedora" ;;
        arch|manjaro|endeavouros) echo "arch" ;;
        opensuse*) echo "suse" ;;
        *) echo "unknown" ;;
    esac
}
DISTRO=$(detect_distro)
echo -e " 发行版: ${BOLD}${DISTRO}${RESET}"

if [[ "$DISTRO" == "unknown" ]]; then
    echo -e "${RED}不支持自动识别的发行版。${RESET}"
    echo " 请手动安装依赖后重试，或参考 README.md。"
    exit 1
fi

if [[ "$DO_INSTALL" == "1" ]]; then
    if [[ "$(id -u)" != "0" ]] && ! command -v sudo &>/dev/null; then
        echo -e "${RED}需要 sudo 权限来安装系统依赖。${RESET}"
        exit 1
    fi
    SUDO=""
    [[ "$(id -u)" != "0" ]] && SUDO="sudo"
fi

# ── 按发行版安装 ──────────────────────────────────────────────
install_debian() {
    echo -e "\n${BOLD}[1/3] 安装系统依赖 (apt)${RESET}"
    $SUDO apt-get update -y
    $SUDO apt-get install -y \
        python3 python3-pip python3-venv \
        python3-gi python3-gi-cairo \
        gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 \
        gstreamer1.0-tools gstreamer1.0-x \
        gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
        gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly \
        gstreamer1.0-libav \
        gstreamer1.0-pipewire pipewire pipewire-bin \
        xdg-desktop-portal \
        xdotool adb ffmpeg \
        || true
    # Wayland portal 后端 + ydotool 可能因桌面而异，尽力安装
    $SUDO apt-get install -y ydotool xdg-desktop-portal-hyprland xdg-desktop-portal-gnome xdg-desktop-portal-kde 2>/dev/null || true
    $SUDO usermod -aG input "$USER" 2>/dev/null || true
}

install_fedora() {
    echo -e "\n${BOLD}[1/3] 安装系统依赖 (dnf)${RESET}"
    $SUDO dnf install -y gstreamer1-plugins-base \
        gstreamer1-plugins-good gstreamer1-plugins-bad-free \
        gstreamer1-plugins-bad-free gstreamer1-plugins-ugly \
        gstreamer1-libav gstreamer1-tools \
        gstreamer1-pipewire pipewire \
        python3-gobject python3-cairo \
        xdg-desktop-portal xdg-desktop-portal-hyprland \
        xdotool ydotool adb ffmpeg || true
}

install_arch() {
    echo -e "\n${BOLD}[1/3] 安装系统依赖 (pacman)${RESET}"
    $SUDO pacman -Sy --noconfirm python python-pip python-gobject python-cairo \
        gstreamer gst-plugins-base gst-plugins-good gst-plugins-bad \
        gst-plugins-ugly gst-libav gst-pipewire pipewire \
        xdg-desktop-portal xdg-desktop-portal-hyprland \
        xdotool ydotool android-tools ffmpeg || true
}

install_suse() {
    echo -e "\n${BOLD}[1/3] 安装系统依赖 (zypper)${RESET}"
    $SUDO zypper install -y python3-gobject python3-cairo \
        gstreamer-plugins-base gstreamer-plugins-good gstreamer-plugins-bad \
        gstreamer-plugins-ugly gstreamer-plugins-libav gstreamer-utils \
        gstreamer-pipewire pipewire \
        xdg-desktop-portal xdg-desktop-portal-hyprland \
        xdotool ydotool adb ffmpeg || true
}

# ── 创建/复用 venv ───────────────────────────────────────────
setup_venv() {
    echo -e "\n${BOLD}[2/3] 配置 Python 虚拟环境 (host/venv)${RESET}"
    VENV_DIR="host/venv"
    if [[ ! -d "$VENV_DIR" ]]; then
        echo "  项目未携带 venv，创建中（继承系统 GStreamer/PyGObject）..."
        python3 -m venv "$VENV_DIR" --system-site-packages
    else
        echo "  使用项目内置 venv: $VENV_DIR/"
    fi
    "$VENV_DIR/bin/pip" install --upgrade pip
    "$VENV_DIR/bin/pip" install -r host/requirements.txt
    echo -e "  ${GREEN}✓ 虚拟环境就绪: $VENV_DIR/${RESET}"
    echo "  以后运行: host/run.sh --output HEADLESS-2 --resolution 1080p --fps 60 --adb auto --debug"
}

# ── 环境自检 ─────────────────────────────────────────────────
run_check() {
    echo -e "\n${BOLD}[3/3] 运行环境自检${RESET}"
    if [[ "$DO_VENV" == "1" && -d "host/venv" ]]; then
        host/venv/bin/python3 host/check_env.py || true
    else
        python3 host/check_env.py || true
    fi
    echo
    echo -e "${BOLD}安装结束。下一步:${RESET}"
    echo "  1) 创建虚拟显示器（X11）或确认已有（Wayland）"
    echo "  2) host/run.sh --output HEADLESS-2 --resolution 1080p --fps 60 --adb auto"
}

# ── 主流程 ────────────────────────────────────────────────────
if [[ "$DO_INSTALL" == "1" ]]; then
    case "$DISTRO" in
        debian) install_debian ;;
        fedora) install_fedora ;;
        arch) install_arch ;;
        suse) install_suse ;;
    esac
fi

if [[ "$DO_VENV" == "1" ]]; then setup_venv; fi
run_check