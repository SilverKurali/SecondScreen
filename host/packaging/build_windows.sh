#!/usr/bin/env bash
# =============================================================================
# 构建 Windows 便携包目录（点击即用：psp-host.exe + GTK4/GStreamer 全部运行库）。
#
# 必须在 MSYS2 UCRT64 环境中运行，且已安装:
#   mingw-w64-ucrt-x86_64-{python,python-pip,python-gobject,gtk4}
#   mingw-w64-ucrt-x86_64-{gstreamer,gst-plugins-base,gst-plugins-good,
#                          gst-plugins-bad,gst-plugins-ugly} + pyinstaller(pip)
#
# 用法: bash host/packaging/build_windows.sh
# 产物: host/dist/psp-host/（由 CI 压缩为便携 zip）
# =============================================================================
set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOST_DIR="$(dirname "$SCRIPT_DIR")"
MROOT="${MINGW_PREFIX:-/ucrt64}"

echo "==> PyInstaller 构建"
cd "$HOST_DIR"
python -m PyInstaller --noconfirm --clean --onedir \
    --name psp-host \
    --paths "$HOST_DIR" \
    --hidden-import gi \
    --hidden-import gi.repository \
    --hidden-import psp_host \
    packaging/entry.py

cd "$HOST_DIR/dist/psp-host"
echo "==> 捆绑运行库（GStreamer 插件 + 全量 DLL + 类型库）"
mkdir -p runtime/lib runtime/plugins/gstreamer-1.0 runtime/typelibs
# runtime_env.bootstrap() 会自动发现并挂载本目录：
#   PATH/add_dll_directory -> runtime/lib + plugins，GI_TYPELIB_PATH -> typelibs
cp "$MROOT/lib/gstreamer-1.0/"*.dll runtime/plugins/gstreamer-1.0/
# 剔除 GUI 类插件（链接 GTK3，注册表构建时进程内加载会污染类型系统）
rm -f runtime/plugins/gstreamer-1.0/libgstgtk*.dll \
      runtime/plugins/gstreamer-1.0/libgstclutter*.dll 2>/dev/null || true
cp "$MROOT/lib/girepository-1.0/"*.typelib runtime/typelibs/
# ucrt64/bin 含 GTK4/GLib/GStreamer/Python 全部 DLL，整目录复制保证闭包完整；
# Windows 按模块名去重，与 _internal 中的同名 DLL 不会双重加载。
cp "$MROOT/bin/"*.dll runtime/lib/
# 自检/编码器探测用的 gst 工具与插件扫描器
for t in gst-inspect-1.0 gst-launch-1.0 gst-typefind-1.0; do
    if [ -f "$MROOT/bin/$t.exe" ]; then
        cp "$MROOT/bin/$t.exe" runtime/lib/
    fi
done
for cand in "$MROOT/bin/gst-plugin-scanner.exe" \
            "$MROOT/lib/gstreamer-1.0/gst-plugin-scanner.exe"; do
    if [ -f "$cand" ]; then
        cp "$cand" runtime/lib/
        break
    fi
done
du -sh runtime || true

echo "==> 冒烟自检 (--selftest)"
# 临时 HOME + 重试：避免自检被中断时半写的 GStreamer 注册表污染后续运行，
# 以及构建期系统瞬时状态导致的偶发加载异常。
SCRATCH="$HOST_DIR/build/win-selftest-scratch"
WINSCRATCH="$(cygpath -w "$SCRATCH" 2>/dev/null || echo "$SCRATCH")"
ok=0
for attempt in 1 2 3; do
    rm -rf "$SCRATCH" && mkdir -p "$SCRATCH"
    if HOME="$SCRATCH" USERPROFILE="$WINSCRATCH" timeout 90 ./psp-host.exe --selftest; then
        ok=1
        break
    fi
    echo "⚠ 自检第 $attempt 次未通过，清理后重试 ..." >&2
    sleep 2
done
if [ "$ok" -ne 1 ]; then
    echo "✗ 自检 3 次均未通过" >&2
    exit 1
fi
echo "✓ Windows 便携目录构建完成: $HOST_DIR/dist/psp-host"
