"""Wayland extended-display capture via Mutter ScreenCast API.

On GNOME/Wayland we bypass the broken xdg-desktop-portal screencast
(which hangs on ``Start`` and never shows a dialog for headless apps).
Instead we talk directly to ``org.gnome.Mutter.ScreenCast``, creating a
virtual 1920×1080 monitor that users can drag windows onto.  The stream
is exposed as a PipeWire node that ``pipewiresrc target-object=<id>``
can consume.

No RemoteDesktop session is required; the ScreenCast session alone is
sufficient for capture-only (no input injection through PipeWire).
"""

import logging
import threading

logger = logging.getLogger(__name__)

# Kept alive so the D-Bus session and PipeWire stream survive while
# the GStreamer pipeline is running.
_keepalive = []
_cached_node_id = None


class ScreencastError(RuntimeError):
    pass


def create_virtual_display(width=1920, height=1080):
    """Create a virtual monitor and return its PipeWire node id.

    Blocks on a dedicated ``GLib.MainLoop`` thread until the
    ``PipeWireStreamAdded`` signal arrives (typically <1 s).  Raises
    ``ScreencastError`` on timeout or failure.

    The returned ``node_id`` can be used directly in the GStreamer
    pipeline ``pipewiresrc target-object=<node_id>``.

    Once granted the node is cached — subsequent calls from phone
    reconnects reuse the same virtual display without creating a new one.
    """
    global _cached_node_id
    if _cached_node_id is not None:
        logger.info("复用已有虚拟显示器节点 %d", _cached_node_id)
        return _cached_node_id

    result = {}

    def run():
        try:
            result["node_id"] = _do_screencast(width, height)
        except BaseException as e:  # noqa: BLE001
            result["error"] = e

    t = threading.Thread(target=run, name="mutter-screencast", daemon=True)
    t.start()
    t.join(timeout=30)

    if "error" in result:
        raise result["error"]
    if "node_id" not in result:
        raise ScreencastError("Screencast handshake timed out")
    _cached_node_id = result["node_id"]
    return result["node_id"]


def _do_screencast(width, height):
    import gi
    gi.require_version("Gio", "2.0")
    gi.require_version("GLib", "2.0")
    from gi.repository import Gio, GLib

    conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)

    # Resolve gnome-shell's unique bus name
    dbus = Gio.DBusProxy.new_sync(
        conn, Gio.DBusProxyFlags.NONE, None,
        "org.freedesktop.DBus", "/org/freedesktop/DBus",
        "org.freedesktop.DBus", None)
    shell = dbus.call_sync(
        "GetNameOwner",
        GLib.Variant("(s)", ("org.gnome.Mutter.ScreenCast",)),
        Gio.DBusCallFlags.NONE, 5000, None).unpack()[0]

    # Create ScreenCast session (no RemoteDesktop needed for capture-only)
    sc = Gio.DBusProxy.new_sync(
        conn, Gio.DBusProxyFlags.NONE, None,
        shell, "/org/gnome/Mutter/ScreenCast",
        "org.gnome.Mutter.ScreenCast", None)
    sc_path = sc.call_sync(
        "CreateSession",
        GLib.Variant("(a{sv})", ({},)),
        Gio.DBusCallFlags.NONE, 5000, None).unpack()[0]
    sc_sess = Gio.DBusProxy.new_sync(
        conn, Gio.DBusProxyFlags.NONE, None,
        shell, sc_path,
        "org.gnome.Mutter.ScreenCast.Session", None)
    logger.info("Mutter ScreenCast session: %s", sc_path)

    # RecordVirtual — creates a virtual monitor of the requested size
    res = sc_sess.call_sync(
        "RecordVirtual",
        GLib.Variant("(a{sv})", ({
            "width": GLib.Variant("i", width),
            "height": GLib.Variant("i", height),
            "is-discrete": GLib.Variant("b", True),
        },)),
        Gio.DBusCallFlags.NONE, 5000, None)
    stream_path = res.unpack()[0]
    logger.info("Stream: %s (%dx%d)", stream_path, width, height)

    # Subscribe to PipeWireStreamAdded BEFORE starting.  We use the
    # global default MainContext (same one GDBus uses for signal
    # dispatch on the session bus) so the callback arrives reliably.
    loop = GLib.MainLoop()
    node_holder = {}

    def on_pipe_wire_signal(_c, _s, _p, _i, _sig, params, _ud=None):
        node_holder["id"] = params.unpack()[0]
        GLib.idle_add(loop.quit)

    conn.signal_subscribe(
        shell,
        "org.gnome.Mutter.ScreenCast.Stream",
        "PipeWireStreamAdded",
        stream_path,
        None, Gio.DBusSignalFlags.NONE,
        on_pipe_wire_signal, None)

    # Start session — virtual monitor appears and PipeWire stream begins
    sc_sess.call_sync(
        "Start", None, Gio.DBusCallFlags.NONE, 5000, None)
    logger.info("Mutter ScreenCast started, waiting for PipeWire node…")

    GLib.timeout_add_seconds(
        15,
        lambda: (node_holder.setdefault("error",
                 ScreencastError("PipeWire node signal timed out")),
                 GLib.idle_add(loop.quit), True)[-1])
    loop.run()

    # Keep D-Bus objects alive so the stream persists
    _keepalive.extend([conn, sc, sc_sess, shell, dbus])

    err = node_holder.get("error")
    if isinstance(err, BaseException):
        raise err
    node_id = node_holder.get("id")
    if node_id is None:
        raise ScreencastError("No PipeWire node received")
    logger.info("Virtual display PipeWire node: %d", node_id)
    return node_id


def release():
    """Release all keepalive refs and the cache (stream stops with the process)."""
    global _cached_node_id
    _cached_node_id = None
    _keepalive.clear()