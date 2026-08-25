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
import subprocess
import time
import uuid

logger = logging.getLogger(__name__)


class PortalWedgeError(Exception):
    """Raised when a portal backend call times out, indicating the backend
    (xdg-desktop-portal-hyprland) is wedged and needs a restart."""

_bus = None
_session_path = None
_node_id = None
# Who owns the live session object: (bus_name, session_interface).
# For the Hyprland *impl* backend (direct flow) the session is owned by the
# backend bus and exposed via org.freedesktop.impl.portal.Session.
# For the frontend fallback it is owned by org.freedesktop.portal.Desktop
# via org.freedesktop.portal.Session.
_session_owner = None
# Whether _node_id currently points to a live, usable session. Set True after a
# successful Start; cleared if the session is closed or detected dead so the
# next connection recreates it (and shows the picker again only then).
_session_valid = False

# hyprland backend name
BACKEND_NAME = "org.freedesktop.impl.portal.desktop.hyprland"


def _call_backend(method, *args, timeout_ms=10000):
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

    try:
        result = _bus.call_sync(
            BACKEND_NAME,
            "/org/freedesktop/portal/desktop",
            "org.freedesktop.impl.portal.ScreenCast",
            method,
            variant,
            None, Gio.DBusCallFlags.NONE, timeout_ms, None,
        )
    except Exception as e:
        # A timeout means the Hyprland backend is wedged. We deliberately avoid
        # relying on GLib.IO_ERROR_TIMEOUT (it is Gio.IO_ERROR_TIMEOUT and is
        # sometimes absent), and instead detect by code 24 or the message.
        msg = str(e)
        if getattr(e, "code", None) == 24 or "Timeout" in msg:
            raise PortalWedgeError(f"{method} timed out (backend wedged): {e}")
        raise
    response_code, results = result.unpack()
    return response_code, results


def create_screencast_session(connector=None, width=1920, height=1080):
    """Create a ScreenCast session and return the PipeWire node ID.

    Tries the Hyprland backend directly first (sync), then falls back to
    the standard frontend portal flow.

    Returns:
        PipeWire node ID (int) or None on failure.
    """
    # Always clean up any existing session first to prevent resource leaks
    stop_screencast_session()

    try:
        import gi
        gi.require_version("Gio", "2.0")
        gi.require_version("GLib", "2.0")
        from gi.repository import Gio, GLib  # noqa: F401
    except ImportError:
        logger.error("PyGObject (Gio) not available for ScreenCast")
        return None

    try:
        node_id = _create_session_direct(width, height)
    except PortalWedgeError:
        # The Hyprland backend wedged (it keeps rendering closed streams after
        # several create/close cycles). Restart it and retry exactly once so a
        # fresh connection is never permanently stuck.
        logger.warning("Hyprland backend wedged; restarting portal backend and retrying once")
        if _restart_portal_backend():
            stop_screencast_session()
            try:
                node_id = _create_session_direct(width, height)
            except PortalWedgeError:
                logger.error("Hyprland backend still wedged after restart")
                node_id = None
        else:
            node_id = None

    if node_id is not None:
        return node_id

    node_id = _create_session_frontend(width, height)
    if node_id is not None:
        return node_id

    return None


def _create_session_direct(width, height):
    """Create session directly via the hyprland backend (synchronous)."""
    global _session_path, _node_id, _session_owner
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
        # Session created directly on the impl backend is owned there and uses
        # the impl Session interface. Closing it anywhere else leaks it.
        _session_owner = (BACKEND_NAME, "org.freedesktop.impl.portal.Session")

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
        # Start needs longer timeout because it shows the screen selection dialog
        # and waits for the user to select a screen
        response, results = _call_backend(
            "Start", req3, session_handle, app_id, "", {}, timeout_ms=60000
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

    except PortalWedgeError:
        raise
    except Exception as e:
        logger.error("Hyprland backend ScreenCast failed: %s", e, exc_info=True)
        return None


def _create_session_frontend(width, height):
    """Fallback: standard frontend portal flow (works on GNOME/KDE etc.)."""
    global _session_path, _node_id, _session_owner
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
            None, Gio.DBusCallFlags.NONE, 10000, None,
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
        _session_owner = ("org.freedesktop.portal.Desktop", "org.freedesktop.portal.Session")

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
            None, Gio.DBusCallFlags.NONE, 10000, None,
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
            None, Gio.DBusCallFlags.NONE, 10000, None,
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


def ensure_screencast_session(width=1920, height=1080):
    """Return an existing PipeWire node id, creating a ScreenCast session (which
    shows the screen-selection picker once) only when none exists.

    Reusing a single session across connections is what prevents the
    intermittent hangs: previously every reconnect called Start again, and when
    that picker failed to appear the backend wedged. With reuse the picker is
    shown exactly once (first connection); later reconnects just re-attach a
    GStreamer pipeline to the same live PipeWire node.
    """
    global _session_valid
    if _node_id is not None and _session_valid:
        logger.info("Reusing existing ScreenCast session (node=%d)", _node_id)
        return _node_id
    _session_valid = False
    node = create_screencast_session(width=width, height=height)
    if node is not None:
        _session_valid = True
    return node


def invalidate_session():
    """Mark the current session as unusable so the next connection recreates it.

    Called when a pipeline fails to attach to the cached PipeWire node (e.g. the
    backend restarted or the node died while idle). This forces a fresh session
    (and picker) on the next connect.
    """
    global _node_id, _session_valid, _session_path, _session_owner
    logger.warning("Invalidating ScreenCast session (node=%s) — will recreate on next connect",
                   _node_id)
    _node_id = None
    _session_valid = False
    _session_path = None
    _session_owner = None


def stop_screencast_session():
    """Stop the ScreenCast session and release all resources.

    A session created by calling the Hyprland *impl* portal backend directly
    is owned by that backend bus name and exposes the
    ``org.freedesktop.impl.portal.Session`` interface. Closing it on the
    frontend (``org.freedesktop.portal.Desktop`` /
    ``org.freedesktop.portal.ScreenCast.Session``) silently fails and leaks the
    session, which makes Hyprland keep rendering the closed stream and spike
    CPU on the next connection (and eventually blocks new Start calls).

    We target the owner recorded when the session was created, and fall back to
    trying both owners so a session is never left dangling.
    """
    global _bus, _session_path, _node_id, _session_owner
    session_path = _session_path
    owner = _session_owner
    # Reset module state immediately so a new connection always starts clean.
    _bus = None
    _session_path = None
    _node_id = None
    _session_owner = None
    _session_valid = False
    if not session_path:
        return

    candidates = []
    if owner:
        candidates.append(owner)
    candidates.append((BACKEND_NAME, "org.freedesktop.impl.portal.Session"))
    candidates.append(("org.freedesktop.portal.Desktop",
                       "org.freedesktop.portal.ScreenCast.Session"))
    # de-duplicate preserving order
    seen = set()
    unique = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            unique.append(c)

    for bus_name, iface in unique:
        try:
            if _bus is None:
                import gi
                gi.require_version("Gio", "2.0")
                gi.require_version("GLib", "2.0")
                from gi.repository import Gio, GLib
                _bus = Gio.bus_get_sync(Gio.BusType.SESSION)
            _bus.call_sync(
                bus_name,
                session_path,
                iface,
                "Close",
                None,
                None, Gio.DBusCallFlags.NONE, 3000, None,  # 3 second timeout
            )
            logger.info("Portal session closed (bus=%s iface=%s): %s",
                        bus_name, iface, session_path)
            return
        except Exception as e:
            logger.debug("Portal session close on %s/%s failed: %s",
                         bus_name, iface, e)

    logger.warning("Could not close portal session %s (may already be closed); "
                   "restarting portal backend to recover", session_path)
    _restart_portal_backend()


def _wait_for_backend(timeout_s=20):
    """Block until the Hyprland portal backend is reachable again.

    Returns True if the backend answered a D-Bus Ping within the timeout.
    """
    global _bus
    import gi
    gi.require_version("Gio", "2.0")
    gi.require_version("GLib", "2.0")
    from gi.repository import Gio
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if _bus is None:
                _bus = Gio.bus_get_sync(Gio.BusType.SESSION)
            _bus.call_sync(BACKEND_NAME, "/", "org.freedesktop.DBus.Peer", "Ping",
                           None, None, Gio.DBusCallFlags.NONE, 2000, None)
            return True
        except Exception:
            time.sleep(0.5)
    return False


def _restart_portal_backend():
    """Restart xdg-desktop-portal-hyprland to recover from a wedged state.

    The Hyprland ScreenCast backend can wedge after several create/close
    cycles: it keeps rendering closed streams and CreateSession/Start start
    timing out, which blocks new connections. Restarting the user service
    kills the stale streams and brings the backend back. Returns True if the
    backend came back up.
    """
    global _bus
    logger.warning("Restarting portal backend %s ...", BACKEND_NAME)
    _bus = None
    try:
        subprocess.run(["systemctl", "--user", "restart",
                        "xdg-desktop-portal-hyprland"],
                       capture_output=True, text=True, timeout=20)
    except Exception as e:
        logger.error("systemctl restart failed: %s", e)
    # Fallback: kill the process directly if systemctl was unavailable.
    if not _wait_for_backend(5):
        try:
            subprocess.run(["pkill", "-f", "xdg-desktop-portal-hyprland"],
                           capture_output=True, text=True, timeout=10)
        except Exception:
            pass
    return _wait_for_backend(20)
