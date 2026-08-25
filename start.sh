#!/usr/bin/env bash
# =============================================================================
# SecondScreen (ADB-PSP) 交互式管理脚本
#
# 一个脚本搞定项目所有常用操作：启动 / 彻底关闭 / 虚拟屏创建移除 / 状态 /
# 环境自检 / 重建内置依赖 / 构建 APK。面向最终用户，菜单式、最简操作。
#
#   ./start.sh      # 启动后按数字选择功能，按 0 退出菜单
#
# 说明:
#   - 服务以后台方式运行，日志写在 /tmp/psp_server.log
#   - “彻底关闭”会结束服务进程、移除本项目创建的虚拟屏、撤掉 adb 反向隧道，
#     并由服务自身的退出逻辑关闭 portal 会话
# =============================================================================
set -eu

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

PIDFILE="host/.psp_server.pid"
LOG="/tmp/psp_server.log"
DEFAULT_RES="1080p"
DEFAULT_FPS="60"

BOLD="\033[1m"; GREEN="\033[92m"; RED="\033[91m"; YELLOW="\033[93m"; CYAN="\033[96m"; RESET="\033[0m"

# ── 工具函数 ────────────────────────────────────────────────
headless_list() {
    hyprctl -j monitors 2>/dev/null | grep -o '"name": "HEADLESS-[0-9]*"' | grep -o 'HEADLESS-[0-9]*'
}

server_running() {
    [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null
}

ensure_deps() {
    [[ -d host/venv ]] || { echo -e "${YELLOW}缺少 venv，运行 ./setup.sh ...${RESET}"; ./setup.sh; }
    [[ -d host/runtime/plugins/gstreamer-1.0 ]] || { echo -e "${YELLOW}缺少内置运行时，生成中 ...${RESET}"; host/bundle_runtime.sh; }
}

confirm() {
    local msg="${1:-确定执行? (y/N)}"
    read -r -p "$msg " ans
    [[ "$ans" == "y" || "$ans" == "Y" ]]
}

# ── 功能 ────────────────────────────────────────────────────
do_start() {
    ensure_deps
    local OUTPUT="${OUTPUT:-HEADLESS-2}"
    if [[ -n "${HYPRLAND_INSTANCE_SIGNATURE:-}" ]] && command -v hyprctl >/dev/null 2>&1; then
        if hyprctl -j monitors 2>/dev/null | grep -q "\"name\": \"$OUTPUT\""; then
            echo -e "${GREEN}虚拟显示器已存在: $OUTPUT${RESET}"
        else
            echo "创建虚拟显示器 ..."
            hyprctl output create headless >/dev/null 2>&1 || true
            OUTPUT="$(hyprctl -j monitors 2>/dev/null | grep -o '"name": "HEADLESS-[0-9]*"' \
                      | grep -o 'HEADLESS-[0-9]*' | sort -t- -k2 -n | tail -1)"
            echo -e "${GREEN}已创建虚拟显示器: $OUTPUT${RESET}"
        fi
    else
        echo -e "${YELLOW}非 Hyprland / 未检测到 hyprctl，跳过虚拟显示器创建。${RESET}"
    fi

    if server_running; then
        echo -e "${YELLOW}服务已在运行 (PID $(cat "$PIDFILE"))，先彻底关闭再启动。${RESET}"
        do_stop
    fi

    echo -e "${CYAN}启动 psp_host (output=$OUTPUT) ...${RESET}"
    nohup host/run.sh --output "$OUTPUT" --resolution "$DEFAULT_RES" --fps "$DEFAULT_FPS" --adb auto > "$LOG" 2>&1 &
    echo $! > "$PIDFILE"
    sleep 1
    if server_running; then
        echo -e "${GREEN}✅ 已启动 PID=$(cat "$PIDFILE")，日志: $LOG${RESET}"
        echo "   安卓端连接后开始投屏；回到菜单选 2 可彻底关闭。"
    else
        echo -e "${RED}⚠️ 启动失败，请看日志: $LOG${RESET}"
    fi
}

do_stop() {
    if ! confirm "将结束服务、移除虚拟屏、撤掉 adb 隧道。彻底关闭项目? (y/N) "; then
        echo "已取消。"; return
    fi
    if server_running; then
        echo "结束服务进程 (PID $(cat "$PIDFILE")) ..."
        kill "$(cat "$PIDFILE")" 2>/dev/null || true
        # 等优雅退出（finally 会关闭 portal 会话），超时再强杀
        for _ in $(seq 1 10); do server_running || break; sleep 0.5; done
        server_running && kill -9 "$(cat "$PIDFILE")" 2>/dev/null || true
        rm -f "$PIDFILE"
    else
        echo "未发现运行中的服务。"
    fi
    # 收尾：杀掉任何残留 psp_host 进程（避免自我匹配用 [p]sp_host）
    pkill -f "[p]sp_host" 2>/dev/null || true
    # 移除本项目创建的虚拟屏
    if command -v hyprctl >/dev/null 2>&1; then
        local n
        for n in $(headless_list); do
            echo "移除虚拟显示器 $n ..."; hyprctl output remove "$n" >/dev/null 2>&1 || true
        done
    fi
    # 撤掉 adb 反向隧道
    adb reverse --remove tcp:4747 2>/dev/null || true
    echo -e "${GREEN}✅ 项目已彻底关闭并清理。${RESET}"
}

do_create_screen() {
    if ! command -v hyprctl >/dev/null 2>&1; then echo -e "${RED}未检测到 hyprctl（非 Hyprland?）${RESET}"; return; fi
    hyprctl output create headless >/dev/null 2>&1 || true
    local NEW="$(hyprctl -j monitors 2>/dev/null | grep -o '"name": "HEADLESS-[0-9]*"' \
                | grep -o 'HEADLESS-[0-9]*' | sort -t- -k2 -n | tail -1)"
    echo -e "${GREEN}已创建虚拟显示器: ${NEW:-<未知>}${RESET}"
}

do_remove_screen() {
    if ! command -v hyprctl >/dev/null 2>&1; then echo -e "${RED}未检测到 hyprctl${RESET}"; return; fi
    local list; list="$(headless_list)"
    if [[ -z "$list" ]]; then echo "当前没有虚拟显示器。"; return; fi
    if ! confirm "将移除以下虚拟屏: $(echo $list | tr '\n' ' ') 确定? (y/N) "; then echo "已取消。"; return; fi
    local n
    for n in $list; do hyprctl output remove "$n" >/dev/null 2>&1 || true; done
    echo -e "${GREEN}✅ 已移除虚拟显示器。${RESET}"
}

do_status() {
    echo -e "${BOLD}── 状态 ──${RESET}"
    if server_running; then echo -e "  服务: ${GREEN}运行中 (PID $(cat "$PIDFILE"))${RESET}"; else echo -e "  服务: ${RED}未运行${RESET}"; fi
    echo "  虚拟显示器: $(headless_list | tr '\n' ' ')${headless_list:-(none)}"
    echo "  adb 设备:"; adb devices 2>/dev/null | sed 's/^/    /' || echo "    (adb 不可用)"
    [[ -f "$LOG" ]] && echo "  日志: $LOG (tail -n 20 可查看)"
}

do_check() {
    ensure_deps
    host/venv/bin/python host/check_env.py
}

do_rebuild() {
    echo "重建内置 venv + GStreamer 运行时 ..."
    ./setup.sh
    host/bundle_runtime.sh
    echo -e "${GREEN}✅ 依赖已重建。${RESET}"
}

do_build_apk() {
    echo "构建 APK (gradle assembleDebug) ..."
    ( cd android && export ANDROID_HOME="${ANDROID_HOME:-$HOME/Android/Sdk}" \
        JAVA_HOME="${JAVA_HOME:-/usr/lib/jvm/zulu-17-amd64}" \
        && ./gradlew assembleDebug --no-daemon ) 2>&1 | tail -15
    if [[ -f android/app/build/outputs/apk/debug/app-debug.apk ]]; then
        cp -f android/app/build/outputs/apk/debug/app-debug.apk psp-app-debug/app-debug.apk
        echo -e "${GREEN}✅ APK 已构建并复制到 psp-app-debug/app-debug.apk${RESET}"
    else
        echo -e "${RED}⚠️ 未找到构建产物。${RESET}"
    fi
}

# ── 菜单循环 ────────────────────────────────────────────────
while true; do
    echo
    echo -e "${BOLD}===== SecondScreen 管理菜单 =====${RESET}"
    echo -e "  ${CYAN}1)${RESET} 启动项目（创建虚拟屏 + 启动服务）"
    echo -e "  ${CYAN}2)${RESET} 彻底关闭项目（结束运行 + 清理虚拟屏/adk/portal）"
    echo -e "  ${CYAN}3)${RESET} 仅创建虚拟显示器"
    echo -e "  ${CYAN}4)${RESET} 仅移除虚拟显示器"
    echo -e "  ${CYAN}5)${RESET} 查看状态"
    echo -e "  ${CYAN}6)${RESET} 环境自检 (check_env)"
    echo -e "  ${CYAN}7)${RESET} 重建内置依赖 (venv + GStreamer)"
    echo -e "  ${CYAN}8)${RESET} 构建 APK"
    echo -e "  ${CYAN}0)${RESET} 退出菜单（服务若已启动会继续保持运行）"
    echo -ne "${BOLD}请选择 [0-8]: ${RESET}"
    read -r choice
    case "$choice" in
        1) do_start ;;
        2) do_stop ;;
        3) do_create_screen ;;
        4) do_remove_screen ;;
        5) do_status ;;
        6) do_check ;;
        7) do_rebuild ;;
        8) do_build_apk ;;
        0) echo "退出菜单。"; exit 0 ;;
        *) echo -e "${RED}无效选择: $choice${RESET}" ;;
    esac
done
