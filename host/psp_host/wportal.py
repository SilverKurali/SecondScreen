"""Wayland screen capture via Mutter ScreenCast RecordMonitor.

Captures the PRIMARY monitor (eDP-2) via PipeWire.
No portal dialog needed — works for headless/background processes.
The session stays alive as long as the host process runs.
"""

import logging
import threading

logger = logging.getLogger(__name__)

_keepalive = []
_cached_node_id = None


class ScreencastError(RuntimeError):
    pass


def create_screencast_session():
    """Capture primary monitor via Mutter ScreenCast, return PipeWire node id."""
    global _cached_node_id
    if _cached_node_id is not None:
        logger.info("Reusing cached PipeWire node %d", _cached_node_id)
        return _cached_node_id

    result = {}

    def run():
        try:
            result["node_id"] = _mutter_record_monitor()
        except BaseException as e:
            result["error"] = e

    t = threading.Thread(target=run, name="mutter-screencast", daemon=True)
    t.start()
    t.join(timeout=30)

    if "error" in result:
        raise result["error"]
    if "node_id" not in result:
        raise ScreencastError("Mutter RecordMonitor timed out")
    _cached_node_id = result["node_id"]
    return result["node_id"]


def _mutter_record_monitor():
    """Create Mutter ScreenCast session, RecordMonitor on eDP-2, return node_id."""
    import gi
    gi.require_version("Gio", "2.0")
    gi.require_version("GLib", "2.0")
    from gi.repository import Gio, GLib

    conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)

    # Find gnome-shell bus name
    dbus = Gio.DBusProxy.new_sync(conn, Gio.DBusProxyFlags.NONE, None,
        "org.freedesktop.DBus", "/org/freedesktop/DBus",
        "org.freedesktop.DBus", None)
    shell = dbus.call_sync("GetNameOwner",
        GLib.Variant("(s)", ("org.gnome.Mutter.ScreenCast",)),
        Gio.DBusCallFlags.NONE, 5000, None).unpack()[0]

    # Create ScreenCast session (no RemoteDesktop needed)
    sc = Gio.DBusProxy.new_sync(conn, Gio.DBusProxyFlags.NONE, None,
        shell, "/org/gnome/Mutter/ScreenCast",
        "org.gnome.Mutter.ScreenCast", None)
    sc_path = sc.call_sync("CreateSession",
        GLib.Variant("(a{sv})", ({},)),
        Gio.DBusCallFlags.NONE, 5000, None).unpack()[0]
    sc_sess = Gio.DBusProxy.new_sync(conn, Gio.DBusProxyFlags.NONE, None,
        shell, sc_path,
        "org.gnome.Mutter.ScreenCast.Session", None)
    logger.info("Mutter ScreenCast session: %s", sc_path)

    # Get primary monitor connector from DisplayConfig
    dc = Gio.DBusProxy.new_sync(conn, Gio.DBusProxyFlags.NONE, None,
        shell, "/org/gnome/Mutter/DisplayConfig",
        "org.gnome.Mutter.DisplayConfig", None)
    state = dc.call_sync("GetCurrentState", None,
        Gio.DBusCallFlags.NONE, 5000, None)
    phys = state.unpack()[1]  # physical monitors
    connector = phys[0][0][0]  # first monitor's connector name
    logger.info("Capturing monitor: %s", connector)

    # RecordMonitor
    res = sc_sess.call_sync("RecordMonitor",
        GLib.Variant("(sa{sv})", (connector, {})),
        Gio.DBusCallFlags.NONE, 5000, None)
    stream_path = res.unpack()[0]
    logger.info("Stream: %s", stream_path)

    # Subscribe to PipeWireStreamAdded BEFORE starting
    loop = GLib.MainLoop()
    node_holder = {}

    def on_pw(c, s, p, i, sig, params, _ud=None):
        node_holder["id"] = params.unpack()[0]
        GLib.idle_add(loop.quit)

    conn.signal_subscribe(shell,
        "org.gnome.Mutter.ScreenCast.Stream", "PipeWireStreamAdded",
        stream_path, None, Gio.DBusSignalFlags.NONE, on_pw, None)

    # Start
    sc_sess.call_sync("Start", None, Gio.DBusCallFlags.NONE, 5000, None)
    logger.info("Mutter ScreenCast started, waiting for PipeWire node...")

    GLib.timeout_add_seconds(15, lambda: (
        node_holder.setdefault("error", ScreencastError("PipeWire node timed out")),
        GLib.idle_add(loop.quit), True)[-1])
    loop.run()

    err = node_holder.get("error")
    if isinstance(err, BaseException):
        raise err
    node_id = node_holder.get("id")
    if node_id is None:
        raise ScreencastError("No PipeWire node received")

    # Keep D-Bus objects alive so the session persists
    _keepalive.extend([conn, sc, sc_sess, dbus, dc])
    logger.info("Virtual display PipeWire node: %d", node_id)
    return node_id


def release():
    global _cached_node_id
    _cached_node_id = None
    _keepalive.clear()