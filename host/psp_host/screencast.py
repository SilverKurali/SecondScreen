"""Hyprland ScreenCast session for Wayland screen capture.

Uses the org.freedesktop.impl.portal.desktop.hyprland backend interface
DIRECTLY (synchronous), bypassing the xdg-desktop-portal frontend which
has a bug in some versions (CreateSession never emits Response signal).

Backend interface (sync):
  CreateSession(request_handle, session_handle, app_id, options) -> (u, a{sv})
  SelectSources(request_handle, session_handle, app_id, options) -> (u, a{sv})
  Start(request_handle, session_handle, app_id, parent_window, options) -> (u, a{sv})

On success, returns a PipeWire node ID that pipewiresrc can use.
Falls back to the frontend portal API for other Wayland compositors.
"""

import logging
import uuid

logger = logging.getLogger(__name__)

_bus = None
_session_path = None
_node_id = None

# hyprland backend name
BACKEND_NAME = "org.freedesktop.impl.portal.desktop.hyprland"


def _call_backend(method, *args):
    """Call a method on the hyprland backend. Returns (response_code, results)."""
    global _bus
    import gi
    gi.require_version("Gio", "2.0")
    gi.require_version("GLib", "2.0")
    from gi.repository import Gio, GLib

    if _bus is None:
        _bus = Gio.bus_get_sync(Gio.BusType.SESSION)

    if method in ("CreateSession", "SelectSources"):
        signature = "(oosa{sv})"
        variant = GLib.Variant(signature, args)
    elif method == "Start":
        signature = "(oossa{sv})"
        variant = GLib.Variant(signature, args)
    else:
        raise ValueError(f"Unknown method: {method}")

    result = _bus.call_sync(
        BACKEND_NAME,
        "/org/freedesktop/portal/desktop",
        "org.freedesktop.impl.portal.ScreenCast",
        method,
        variant,
        None, Gio.DBusCallFlags.NONE, -1, None,
    )
    response_code, results = result.unpack()
    return response_code, results


def create_screencast_session(connector=None, width=1920, height=1080):
    """Create a ScreenCast session and return the PipeWire node ID.

    Tries the Hyprland backend directly first (sync), then falls back to
    the standard frontend portal flow.

    Returns:
        PipeWire node ID (int) or None on failure.
    """
    global _session_path, _node_id

    try:
        import gi
        gi.require_version("Gio", "2.0")
        gi.require_version("GLib", "2.0")
        from gi.repository import Gio, GLib  # noqa: F401
    except ImportError:
        logger.error("PyGObject (Gio) not available for ScreenCast")
        return None

    node_id = _create_session_direct(width, height)
    if node_id is not None:
        return node_id

    node_id = _create_session_frontend(width, height)
    if node_id is not None:
        return node_id

    return None


def _create_session_direct(width, height):
    """Create session directly via the hyprland backend (synchronous)."""
    global _session_path, _node_id
    import gi
    gi.require_version("Gio", "2.0")
    gi.require_version("GLib", "2.0")
    from gi.repository import Gio, GLib  # noqa: F401
    logger.info("Creating ScreenCast session via hyprland backend directly ...")

    token = uuid.uuid4().hex[:10]
    session_handle = f"/org/freedesktop/portal/desktop/session/psp_{token}"
    app_id = "com.psp.host"

    try:
        req1 = f"/org/freedesktop/portal/desktop/request/psp_create_{token}"
        create_opts = {
            "session_handle_token": GLib.Variant("s", f"psp_{token}"),
        }
        response, results = _call_backend(
            "CreateSession", req1, session_handle, app_id, create_opts
        )
        if response != 0:
            logger.error("CreateSession failed: code=%d results=%s", response, results)
            return None
        logger.info("CreateSession OK: session=%s", session_handle)
        _session_path = session_handle

        req2 = f"/org/freedesktop/portal/desktop/request/psp_select_{token}"
        select_opts = {
            "types": GLib.Variant("u", 1),      # 1 = monitor
            "cursor_mode": GLib.Variant("u", 1), # 1 = embedded cursor
            "multiple": GLib.Variant("b", False),
        }
        response, results = _call_backend(
            "SelectSources", req2, session_handle, app_id, select_opts
        )
        if response != 0:
            logger.error("SelectSources failed: code=%d results=%s", response, results)
            return None
        logger.info("SelectSources OK")

        req3 = f"/org/freedesktop/portal/desktop/request/psp_start_{token}"
        response, results = _call_backend(
            "Start", req3, session_handle, app_id, "", {}
        )
        if response != 0:
            logger.error("Start failed: code=%d results=%s", response, results)
            return None

        streams = results.get("streams", [])
        if not streams:
            logger.error("Start OK but no streams returned: %s", results)
            return None

        node_id = streams[0][0]
        logger.info("ScreenCast ready: node_id=%d (session=%s)", node_id, session_handle)
        _session_path = session_handle
        _node_id = node_id
        return node_id

    except Exception as e:
        logger.error("Hyprland backend ScreenCast failed: %s", e, exc_info=True)
        return None


def _create_session_frontend(width, height):
    """Fallback: standard frontend portal flow (works on GNOME/KDE etc.)."""
    global _session_path, _node_id
    logger.info("Using frontend portal ScreenCast flow ...")
    try:
        import gi
        gi.require_version("Gio", "2.0")
        gi.require_version("GLib", "2.0")
        from gi.repository import Gio, GLib
    except ImportError:
        return None

    try:
        _bus = Gio.bus_get_sync(Gio.BusType.SESSION)

        session_token = "psp_" + uuid.uuid4().hex[:12]
        create_opts = {
            "session_handle_token": GLib.Variant("s", session_token),
        }
        result = _bus.call_sync(
            "org.freedesktop.portal.Desktop",
            "/org/freedesktop/portal/desktop",
            "org.freedesktop.portal.ScreenCast",
            "CreateSession",
            GLib.Variant("(a{sv})", (create_opts,)),
            None, Gio.DBusCallFlags.NONE, -1, None,
        )
        request_path = result.unpack()[0]

        session_handle = None
        response_received = [False]

        def _on_create_response(conn, sender, path, iface, signal, params):
            nonlocal session_handle
            try:
                response_code = params.unpack()[0]
                results = params.unpack()[1]
                if response_code == 0 and "session_handle" in results:
                    session_handle = results["session_handle"]
            except Exception:
                pass
            response_received[0] = True

        sub_id = _bus.signal_subscribe(
            "org.freedesktop.portal.Desktop",
            "org.freedesktop.portal.Request",
            "Response",
            request_path,
            None,
            Gio.DBusSignalFlags.NONE,
            _on_create_response,
        )

        loop = GLib.MainLoop()
        GLib.timeout_add_seconds(5, lambda: (loop.quit(), False)[1])
        loop.run()
        _bus.signal_unsubscribe(sub_id)

        if not session_handle:
            logger.error("Frontend: failed to get session handle")
            return None
        _session_path = session_handle

        select_opts = {
            "cursor_mode": GLib.Variant("u", 1),
            "types": GLib.Variant("u", 1),
            "multiple": GLib.Variant("b", False),
        }
        result = _bus.call_sync(
            "org.freedesktop.portal.Desktop",
            "/org/freedesktop/portal/desktop",
            "org.freedesktop.portal.ScreenCast",
            "SelectSources",
            GLib.Variant("(oa{sv})", (session_handle, select_opts)),
            None, Gio.DBusCallFlags.NONE, -1, None,
        )
        select_request_path = result.unpack()[0]

        select_done = [False]

        def _on_select_response(conn, sender, path, iface, signal, params):
            select_done[0] = True

        sub_id2 = _bus.signal_subscribe(
            "org.freedesktop.portal.Desktop",
            "org.freedesktop.portal.Request",
            "Response",
            select_request_path,
            None,
            Gio.DBusSignalFlags.NONE,
            _on_select_response,
        )

        loop = GLib.MainLoop()
        GLib.timeout_add_seconds(5, lambda: (loop.quit(), False)[1])
        loop.run()
        _bus.signal_unsubscribe(sub_id2)

        node_holder = {"id": None}

        def _on_pw_signal(conn, sender, path, iface, signal, params):
            if signal == "PipeWireStreamAdded":
                try:
                    node_holder["id"] = params.unpack()[0]
                except Exception:
                    pass

        sub_id3 = _bus.signal_subscribe(
            "org.freedesktop.portal.Desktop",
            "org.freedesktop.portal.ScreenCast.Session",
            "PipeWireStreamAdded",
            session_handle,
            None,
            Gio.DBusSignalFlags.NONE,
            _on_pw_signal,
        )

        result = _bus.call_sync(
            "org.freedesktop.portal.Desktop",
            "/org/freedesktop/portal/desktop",
            "org.freedesktop.portal.ScreenCast",
            "Start",
            GLib.Variant("(osa{sv})", (session_handle, "", {})),
            None, Gio.DBusCallFlags.NONE, -1, None,
        )

        loop = GLib.MainLoop()
        GLib.timeout_add_seconds(5, lambda: (loop.quit(), False)[1])
        loop.run()
        _bus.signal_unsubscribe(sub_id3)

        _node_id = node_holder["id"]
        if _node_id is not None:
            logger.info("Frontend ScreenCast ready: node_id=%d", _node_id)
        return _node_id

    except Exception as e:
        logger.error("Frontend Portal ScreenCast failed: %s", e)
        return None


def get_pipewiresrc_string():
    """Return the pipewiresrc pipeline string with the screencast node path."""
    if _node_id is not None:
        return f"pipewiresrc path={_node_id}"
    return None


def stop_screencast_session():
    """Stop the ScreenCast session."""
    global _bus, _session_path, _node_id
    if _bus and _session_path:
        try:
            _bus.call_sync(
                "org.freedesktop.portal.Desktop",
                _session_path,
                "org.freedesktop.portal.ScreenCast.Session",
                "Close",
                None,
                None, Gio.DBusCallFlags.NONE, -1, None,
            )
            logger.info("Portal session closed")
        except Exception:
            pass
    _bus = None
    _session_path = None
    _node_id = None
