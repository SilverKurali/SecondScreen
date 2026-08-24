"""GNOME Mutter ScreenCast D-Bus session for Wayland screen capture.

Creates a PipeWire screencast session via the Mutter D-Bus interface,
so pipewiresrc can capture the actual desktop on GNOME Wayland
(where ximagesrc only sees an empty XWayland root window).
"""

import logging
import os

logger = logging.getLogger(__name__)

_bus = None
_session_path = None
_stream_path = None
_node_id = None


def create_screencast_session(connector=None, width=1920, height=1080):
    """Create a Mutter ScreenCast session and return the PipeWire node ID.

    Args:
        connector: Monitor connector name (e.g. 'eDP-2'). None for primary.
        width/height: Desired stream dimensions (informational).

    Returns:
        PipeWire node ID (int) or None on failure.
    """
    global _bus, _session_path, _stream_path, _node_id

    try:
        import gi
        gi.require_version("Gio", "2.0")
        gi.require_version("GLib", "2.0")
        from gi.repository import Gio, GLib
    except ImportError:
        logger.error("PyGObject (Gio) not available for ScreenCast")
        return None

    try:
        _bus = Gio.bus_get_sync(Gio.BusType.SESSION)

        # 1. CreateSession
        result = _bus.call_sync(
            "org.gnome.Mutter.ScreenCast",
            "/org/gnome/Mutter/ScreenCast",
            "org.gnome.Mutter.ScreenCast",
            "CreateSession",
            GLib.Variant("(a{sv})", ({},)),
            None, Gio.DBusCallFlags.NONE, -1, None,
        )
        _session_path = result.unpack()[0]
        logger.info("ScreenCast session: %s", _session_path)

        # 2. RecordMonitor - capture the primary monitor for now.
        # TODO: Use RecordVirtual + EVDI for true extended display mode.
        connector = _find_primary_connector() or "eDP-1"
        props = {"cursor-mode": GLib.Variant("u", 1)}
        result2 = _bus.call_sync(
            "org.gnome.Mutter.ScreenCast",
            _session_path,
            "org.gnome.Mutter.ScreenCast.Session",
            "RecordMonitor",
            GLib.Variant("(sa{sv})", (connector, props)),
            None, Gio.DBusCallFlags.NONE, -1, None,
        )
        logger.info("Recording monitor: %s", connector)

        _stream_path = result2.unpack()[0]
        logger.info("ScreenCast stream: %s (connector=%s)", _stream_path, connector)

        # 3. Subscribe to PipeWireStreamAdded signal
        node_holder = {"id": None}

        def _on_signal(conn, sender, path, iface, signal, params):
            if signal == "PipeWireStreamAdded":
                node_holder["id"] = params.unpack()[0]
                logger.info("PipeWireStreamAdded: node_id=%d", node_holder["id"])

        sub_id = _bus.signal_subscribe(
            "org.gnome.Mutter.ScreenCast",
            "org.gnome.Mutter.ScreenCast.Stream",
            "PipeWireStreamAdded",
            _stream_path,
            None,
            Gio.DBusSignalFlags.NONE,
            _on_signal,
        )

        # 4. Start session (triggers PipeWireStreamAdded signal)
        _bus.call_sync(
            "org.gnome.Mutter.ScreenCast",
            _session_path,
            "org.gnome.Mutter.ScreenCast.Session",
            "Start",
            None,
            None, Gio.DBusCallFlags.NONE, -1, None,
        )
        logger.info("ScreenCast session started")

        # 5. Wait for signal (run a short main loop)
        loop = GLib.MainLoop()
        GLib.timeout_add_seconds(5, lambda: loop.quit())
        try:
            loop.run()
        except Exception:
            pass

        _bus.signal_unsubscribe(sub_id)

        _node_id = node_holder["id"]
        if _node_id is not None:
            logger.info("ScreenCast ready: pipewiresrc path=%d", _node_id)
        else:
            logger.error("Did not receive PipeWireStreamAdded signal within 5s")

        return _node_id

    except Exception as e:
        logger.error("ScreenCast session creation failed: %s", e, exc_info=True)
        return None


def _find_primary_connector():
    """Find the primary monitor connector name via xrandr."""
    import subprocess
    try:
        r = subprocess.run(
            ["xrandr", "--query"], capture_output=True, text=True, timeout=5
        )
        for line in r.stdout.splitlines():
            if " primary " in line and " connected" in line:
                return line.split()[0]
    except Exception:
        pass
    return None


def get_pipewiresrc_string():
    """Return the pipewiresrc pipeline string with the screencast node path."""
    if _node_id is not None:
        return f"pipewiresrc path={_node_id}"
    return None


def stop_screencast_session():
    """Stop the ScreenCast session."""
    global _bus, _session_path, _stream_path, _node_id
    if _bus and _session_path:
        try:
            _bus.call_sync(
                "org.gnome.Mutter.ScreenCast",
                _session_path,
                "org.gnome.Mutter.ScreenCast.Session",
                "Stop",
                None,
                None, Gio.DBusCallFlags.NONE, -1, None,
            )
            logger.info("ScreenCast session stopped")
        except Exception:
            pass
    _bus = None
    _session_path = None
    _stream_path = None
    _node_id = None
