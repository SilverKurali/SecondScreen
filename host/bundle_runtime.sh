#!/usr/bin/env bash
# =============================================================================
# 把系统已装的 GStreamer 运行时 + PyGObject 类型库打包进项目 (host/runtime/)，
# 使项目运行时不再需要 `apt install gstreamer1.0-* python3-gi`。
#
# 仍然依赖（无法打包 / 属宿主基础）：
#   - GLib / GObject / GIO / libgirepository（几乎所有 Linux 桌面都自带）
#   - 宿主的 XDG desktop portal + PipeWire 会话（Wayland 抓屏必须与宿主对话）
#   - 部分插件支持库（libx264 / libpipewire / liborc 等，随插件包提供）
#
# 用法: ./bundle_runtime.sh
# 产物: host/runtime/{lib,plugins/typelibs}
# =============================================================================
set -eu

DIR="$(cd "$(dirname "$0")" && pwd)"
RUNTIME="$DIR/runtime"
SRC="/usr/lib/x86_64-linux-gnu"

echo "==> 打包 GStreamer 运行时到 $RUNTIME"

# 探测 triplet（兼容非 x86_64）
if [[ -d "$SRC" ]]; then
    TRIPLET="x86_64-linux-gnu"
elif [[ -d "/usr/lib/aarch64-linux-gnu" ]]; then
    TRIPLET="aarch64-linux-gnu"
else
    TRIPLET="$(gcc -print-multiarch 2>/dev/null || echo "x86_64-linux-gnu")"
    SRC="/usr/lib/$TRIPLET"
fi

if [[ ! -d "$SRC" ]]; then
    echo "找不到系统库目录: $SRC" >&2
    exit 1
fi

rm -rf "$RUNTIME"
mkdir -p "$RUNTIME/lib" "$RUNTIME/plugins" "$RUNTIME/typelibs"

# 1) GStreamer 插件（整个目录，含我们需要的 x264enc / pipewiresrc / base 插件等）
echo "  - 复制 GStreamer 插件"
cp -a "$SRC/gstreamer-1.0" "$RUNTIME/plugins/gstreamer-1.0"

# 2) GStreamer 核心/辅助库（libgst*.so*）
echo "  - 复制 GStreamer 核心库"
cp -a "$SRC"/libgst*.so* "$RUNTIME/lib/" 2>/dev/null || true

# 3) GStreamer / 基础 GI 类型库（.typelib）
echo "  - 复制 GI 类型库"
for t in "$SRC"/girepository-1.0/{Gst,GstAllocator,GstApp,GstAudio,GstBase,GstCheck,GstController,GstGL,GstGLWayland,GstGLX11,GstNet,GstPbutils,GstRtp,GstRtsp,GstSdp,GstTag,GstVideo,Gio,GioUnix,GLib,GLibUnix,GModule,GObject}-2.0.typelib; do
    [[ -f "$t" ]] && cp -a "$t" "$RUNTIME/typelibs/"
done

# 4) PyGObject 绑定 + pycairo 复制进 venv（覆盖系统 python3-gi 依赖，免源码编译）
echo "  - 复制 PyGObject / pycairo 到 venv"
VENV_SITE="$DIR/venv/lib/python3.14/site-packages"
if [[ -d "$VENV_SITE" ]]; then
    SYS_GI=""
    for p in /usr/lib/python3/dist-packages /usr/lib/python3.14/site-packages /usr/lib/site-packages; do
        [[ -d "$p/gi" ]] && SYS_GI="$p" && break
    done
    if [[ -n "$SYS_GI" ]]; then
        cp -a "$SYS_GI/gi" "$VENV_SITE/" && cp -a "$SYS_GI/cairo" "$VENV_SITE/" 2>/dev/null || true
        echo "    已复制: $SYS_GI/gi -> $VENV_SITE/"
    else
        echo "    未找到系统 gi 包，跳过（请确保系统已装 python3-gi）"
    fi
else
    echo "    未找到 host/venv，跳过（请先 ./setup.sh 或 python3 -m venv host/venv --system-site-packages）"
fi

echo "==> 完成。运行时目录大小:"
du -sh "$RUNTIME"
echo "    启动请使用: host/run.sh ..."
