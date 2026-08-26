#!/usr/bin/env bash
# =============================================================================
# 构建点击即用的 Linux AppImage（捆绑 Python + GTK4 + GStreamer + 编码器）。
#
# 运行环境需预装（如 GitHub Actions ubuntu-22.04）:
#   python3 python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-gstreamer-1.0
#   gstreamer1.0-plugins-{base,good,bad,ugly} gstreamer1.0-libav curl
#
# 用法: bash host/packaging/build_appimage.sh <版本号>
# 产物: dist/packages/SecondScreen-linux-x64-<版本号>.AppImage
# =============================================================================
set -eu

VER="${1:-0.0.0}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOST_DIR="$(dirname "$SCRIPT_DIR")"
ROOT_DIR="$(dirname "$HOST_DIR")"
BUILD="$HOST_DIR/build/appimage"
TRIPLET="x86_64-linux-gnu"
SYSLIB="/usr/lib/$TRIPLET"
APPDIR="$BUILD/AppDir"
PKG_DIR="$ROOT_DIR/dist/packages"

echo "==> 构建 AppImage v$VER"
rm -rf "$BUILD"
mkdir -p "$BUILD" "$PKG_DIR"

# ---- 1) 构建环境（复用系统 python3-gi 绑定） --------------------------------
python3 -m venv --system-site-packages "$BUILD/venv"
"$BUILD/venv/bin/pip" install --quiet --upgrade pip
"$BUILD/venv/bin/pip" install --quiet pyinstaller evdev

# ---- 2) 捆绑运行时：GStreamer 插件/核心库 + 全量 GI 类型库 ------------------
RT="$BUILD/runtime"
mkdir -p "$RT/lib" "$RT/plugins" "$RT/typelibs"
echo "==> 复制 GStreamer 插件与类型库"
cp -a "$SYSLIB/gstreamer-1.0" "$RT/plugins/gstreamer-1.0"
# 剔除 GUI 类插件：它们链接 GTK3/clutter，注册表构建时被进程内加载，
# 与 GTK4 共存导致 GType 双重注册、Gtk 初始化死锁；串流场景不需要它们。
rm -f "$RT/plugins/gstreamer-1.0/libgstgtk"*.so* \
      "$RT/plugins/gstreamer-1.0/libgstclutter"*.so* 2>/dev/null || true
cp -a "$SYSLIB"/libgst*.so* "$RT/lib/"
cp -a "$SYSLIB/girepository-1.0/." "$RT/typelibs/"

# GTK4/GI/GStreamer 由 girepository 在运行时动态加载，PyInstaller 看不到，
# 需要手工收集依赖闭包（ldd 递归两轮），并剔除 glibc 家族（AppImage 约定）。
EXCLUDE_RE='ld-linux|libc\.so|libm\.so|libm-|libdl\.|libpthread|librt\.so|libutil\.so|libresolv|libnss|libanl|libcrypt|libthread_db|libSegFault|libmvec|libpcprofile|libBrokenLocale|libgcc_s'
collect_deps() {
    local f dep
    for f in "$@"; do
        [ -e "$f" ] || continue
        while IFS= read -r dep; do
            [ -n "$dep" ] || continue
            if ! printf '%s' "$dep" | grep -qE "$EXCLUDE_RE"; then
                cp -n "$dep" "$RT/lib/" 2>/dev/null || true
            fi
        done < <(ldd "$f" 2>/dev/null | awk '$3 ~ /^\// {print $3}')
    done
}
echo "==> 收集 GTK4 依赖闭包"
# 种子库本体必须显式复制（collect_deps 只复制 ldd 列出的依赖）。
# girepository 版本自适应：新版 PyGObject 链接 GR2，旧版链接 GR1，
# 必须与捆绑 glib 同源，否则版本错配导致 Gtk 初始化死锁。
GR_SEED="libgirepository-1.0.so.1"
GI_SO_GLOB="$(python3 -c 'import gi, os; print(os.path.dirname(gi.__file__))')/_gi"*.so
for f in $GI_SO_GLOB; do
    [ -e "$f" ] || continue
    if ldd "$f" 2>/dev/null | grep -q "libgirepository-2.0"; then
        GR_SEED="libgirepository-2.0.so.0"
    fi
done
echo "==> gi._gi 需要 $GR_SEED"
for seed in libgtk-4.so.1 libgdk-4.so.1 "$GR_SEED" \
            libgstreamer-1.0.so.0 libgraphene-1.0.so.0 libepoxy.so.0; do
    cp -n "$SYSLIB/$seed" "$RT/lib/" 2>/dev/null || true
done
collect_deps "$SYSLIB/libgtk-4.so.1" \
             "$SYSLIB/$GR_SEED" \
             "$SYSLIB/libgstreamer-1.0.so.0"
for _ in 1 2; do
    collect_deps "$RT/lib/"*.so*
done
# 自包含校验：冻结应用加载链上的关键库必须在 runtime/lib 内，否则会回退宿主系统
# 库（版本不匹配时导致 GType 双重注册、Gtk 初始化死锁）。
REQUIRED_SEEDS="libgtk-4.so.1 libgdk-pixbuf-2.0.so.0 libpango-1.0.so.0 \
    libpangocairo-1.0.so.0 libcairo.so.2 libharfbuzz.so.0 libepoxy.so.0 \
    libgraphene-1.0.so.0 libxkbcommon.so.0 libvulkan.so.1"
for _ in 1 2 3; do
    for seed in $REQUIRED_SEEDS; do
        if ! ls "$RT/lib/$seed" >/dev/null 2>&1; then
            cp -n "$SYSLIB/$seed" "$RT/lib/" 2>/dev/null || true
            collect_deps "$SYSLIB/$seed"
        fi
    done
    collect_deps "$RT/lib/"*.so*
done
rm -f "$RT/lib/ld-linux"* || true

# ---- 3) PyInstaller 冻结打包 ------------------------------------------------
# 注意：不要 --collect-submodules gi —— GTK 钩子会把系统 GTK4/GLib 库再收集一份，
# 与 runtime/lib 里捆绑的库重复，导致 GType 双重注册、Gtk 初始化死锁。
# gi 绑定本体由 --hidden-import 收集，类型库由 runtime/typelibs 提供。
echo "==> PyInstaller 构建"
cd "$HOST_DIR"
"$BUILD/venv/bin/python" -m PyInstaller --noconfirm --clean --onedir \
    --name psp-host \
    --paths "$HOST_DIR" \
    --hidden-import gi \
    --hidden-import gi.repository \
    --hidden-import psp_host \
    packaging/entry.py

# 瘦身：若 GTK 钩子仍收集了图标主题，只保留 GTK4 需要的 Adwaita/hicolor
ICONS="$HOST_DIR/dist/psp-host/_internal/share/icons"
if [ -d "$ICONS" ]; then
    echo "==> 瘦身图标主题"
    find "$ICONS" -mindepth 1 -maxdepth 1 ! -name Adwaita ! -name hicolor \
        -exec rm -rf {} +
fi
# 清理钩子收集的 GLib/GTK/GI 家族库：runtime/lib 是唯一权威来源，
# 重复副本会导致 GType 双重注册（GtkWidget 冲突、Gtk 初始化死锁）。
rm -f "$HOST_DIR/dist/psp-host/_internal"/libgtk* \
      "$HOST_DIR/dist/psp-host/_internal"/libgdk* \
      "$HOST_DIR/dist/psp-host/_internal"/libglib* \
      "$HOST_DIR/dist/psp-host/_internal"/libgobject* \
      "$HOST_DIR/dist/psp-host/_internal"/libgio* \
      "$HOST_DIR/dist/psp-host/_internal"/libgmodule* \
      "$HOST_DIR/dist/psp-host/_internal"/libgthread* \
      "$HOST_DIR/dist/psp-host/_internal"/libgirepository* \
      "$HOST_DIR/dist/psp-host/_internal"/libpango* \
      "$HOST_DIR/dist/psp-host/_internal"/libcairo* \
      "$HOST_DIR/dist/psp-host/_internal"/libatk* \
      "$HOST_DIR/dist/psp-host/_internal"/libgst* 2>/dev/null || true
rm -rf "$HOST_DIR/dist/psp-host/_internal/gi_typelibs" 2>/dev/null || true
# PyInstaller 的 GStreamer 钩子会另收集一份插件到 _internal/gst_plugins，
# 其中的 GTK 插件同样会污染类型系统，一并剔除。
rm -f "$HOST_DIR/dist/psp-host/_internal/gst_plugins/libgstgtk"*.so* \
      "$HOST_DIR/dist/psp-host/_internal/gst_plugins/libgstclutter"*.so* 2>/dev/null || true
# 精简 gdk-pixbuf 加载器：钩子收集的 io-wmf/heif/svg 等会拉入 GTK3 依赖链，
# 加载器探测时污染类型系统；应用只需内建 png。
rm -rf "$HOST_DIR/dist/psp-host/_internal/lib/gdk-pixbuf" 2>/dev/null || true
find "$HOST_DIR/dist/psp-host/_internal" -name 'loaders.cache' -delete 2>/dev/null || true
# GTK4 需要 GSettings schema（运行时钩子会把 _internal/share 挂到 XDG_DATA_DIRS）
mkdir -p "$HOST_DIR/dist/psp-host/_internal/share/glib-2.0/schemas"
cp -n /usr/share/glib-2.0/schemas/gschemas.compiled \
    "$HOST_DIR/dist/psp-host/_internal/share/glib-2.0/schemas/" 2>/dev/null || true

# ---- 4) 组装 AppDir ----------------------------------------------------------
# 自检防护：在临时环境中运行，失败自动重试（避免构建期系统瞬时状态/残留扫描器干扰）
run_selftest_guarded() {
    local attempt rc
    for attempt in 1 2 3; do
        rm -rf "$SCRATCH" && mkdir -p "$SCRATCH/.cache"
        rc=0
        HOME="$SCRATCH" XDG_CACHE_HOME="$SCRATCH/.cache" \
            LD_LIBRARY_PATH="$APPDIR/usr/bin/psp-host/runtime/lib:$APPDIR/usr/bin/psp-host/_internal" \
            timeout 90 "$@" || rc=$?
        if [ "$rc" -eq 0 ]; then
            return 0
        fi
        echo "⚠ 自检第 $attempt 次未通过 (exit=$rc)，清理后重试 ..." >&2
        pkill -f gst-plugin-scanner 2>/dev/null || true
        sleep 2
    done
    echo "✗ 自检 3 次均未通过" >&2
    return 1
}
SCRATCH="$BUILD/selftest-scratch"

echo "==> 组装 AppDir"
mkdir -p "$APPDIR/usr/bin"
cp -a "$HOST_DIR/dist/psp-host" "$APPDIR/usr/bin/psp-host"
# runtime 放在 exe 同级：runtime_env.bootstrap() 会自动发现并挂载
cp -a "$RT" "$APPDIR/usr/bin/psp-host/runtime"

# GStreamer 命令行工具（自检/编码器探测依赖 gst-inspect-1.0）
for t in gst-inspect-1.0 gst-launch-1.0 gst-typefind-1.0; do
    if command -v "$t" >/dev/null 2>&1; then
        cp -a "$(command -v "$t")" "$APPDIR/usr/bin/"
    fi
done
SCANNER="$SYSLIB/gstreamer1.0/gstreamer-1.0/gst-plugin-scanner"
if [ -e "$SCANNER" ]; then
    cp -a "$SCANNER" "$APPDIR/usr/bin/"
fi

cat > "$APPDIR/AppRun" <<'APPRUN'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
export PATH="$HERE/usr/bin:$PATH"
# 必须在 exec 前导出：动态链接器只在进程启动时解析 LD_LIBRARY_PATH，
# Python 进程内设置对 dlopen 无效（会导致捆绑库不被加载、回退系统库）。
export LD_LIBRARY_PATH="$HERE/usr/bin/psp-host/runtime/lib:$HERE/usr/bin/psp-host/_internal${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export GI_TYPELIB_PATH="$HERE/usr/bin/psp-host/runtime/typelibs${GI_TYPELIB_PATH:+:$GI_TYPELIB_PATH}"
export GST_PLUGIN_PATH="$HERE/usr/bin/psp-host/runtime/plugins/gstreamer-1.0"
if [ -x "$HERE/usr/bin/gst-plugin-scanner" ]; then
    export GST_PLUGIN_SCANNER="$HERE/usr/bin/gst-plugin-scanner"
fi
exec "$HERE/usr/bin/psp-host/psp-host" "$@"
APPRUN
chmod +x "$APPDIR/AppRun"

cat > "$APPDIR/psp-host.desktop" <<'DESKTOP'
[Desktop Entry]
Type=Application
Name=PSP Host
Comment=Stream PC screen to Android device as an extended display
Exec=psp-host
Icon=psp-host
Terminal=true
Categories=Network;RemoteAccess;
DESKTOP

# 在耗时打包前先验证冻结目录（快速失败）
# 用临时注册表：若自检被中断，残留的半写注册表会让下次 Gst 初始化挂起
echo "==> 冻结目录自检 (--selftest)"
sync  # 确保大量 cp 的文件全部落盘，避免读取未同步内容导致加载异常
run_selftest_guarded "$APPDIR/usr/bin/psp-host/psp-host" --selftest

# 生成简约图标（纯标准库，不依赖图像工具）
python3 - "$APPDIR/psp-host.png" <<'PY'
import struct
import sys
import zlib


def chunk(typ, data):
    c = struct.pack(">I", len(data)) + typ + data
    return c + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)


size = 256
raw = bytearray()
for y in range(size):
    raw.append(0)  # filter byte
    for x in range(size):
        if 24 <= x <= 231 and 48 <= y <= 184:
            raw += bytes([240, 244, 255, 255])  # 白色"屏幕"
        else:
            raw += bytes([38, 92, 218, 255])    # 蓝色背景
png = b"\x89PNG\r\n\x1a\n"
png += chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
png += chunk(b"IDAT", zlib.compress(bytes(raw)))
png += chunk(b"IEND", b"")
with open(sys.argv[1], "wb") as f:
    f.write(png)
PY

# ---- 5) 生成 AppImage --------------------------------------------------------
echo "==> 生成 AppImage"
TOOL="$BUILD/appimagetool"
curl -fsSLo "$TOOL" \
    "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
chmod +x "$TOOL"
OUT="$PKG_DIR/SecondScreen-linux-x64-$VER.AppImage"
ARCH=x86_64 "$TOOL" --appimage-extract-and-run "$APPDIR" "$OUT"

# ---- 6) 冒烟自检（无头验证捆绑运行库完整） ----------------------------------
echo "==> 冒烟自检 (--selftest)"
chmod +x "$OUT"
run_selftest_guarded "$OUT" --appimage-extract-and-run --selftest
ls -lh "$OUT"
echo "✓ AppImage 构建完成: $OUT"
