"""Virtual display creation for Wayland and X11.

Detects the current display server and compositor, then creates
a virtual monitor for the extended display use case.
"""

import json
import logging
import os
import re
import subprocess
import sys
import time

logger = logging.getLogger(__name__)


def detect_session():
    """Detect display server and compositor.

    Returns:
        dict with 'type' ('x11'/'wayland'), 'compositor' (name), 'version'.
    """
    session_type = os.environ.get("XDG_SESSION_TYPE", "").lower()
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    wayland_display = os.environ.get("WAYLAND_DISPLAY", "")

    info = {
        "type": "unknown",
        "compositor": "unknown",
        "version": "0",
        "is_wayland": False,
        "is_x11": False,
    }

    if "wayland" in session_type or wayland_display:
        info["type"] = "wayland"
        info["is_wayland"] = True

        if "gnome" in desktop or "ubuntu" in desktop:
            info["compositor"] = "gnome"
            try:
                result = subprocess.run(
                    ["gnome-shell", "--version"],
                    capture_output=True, text=True, timeout=3,
                )
                info["version"] = result.stdout.strip().replace("GNOME Shell ", "")
            except Exception:
                info["version"] = "unknown"
        elif "sway" in desktop:
            info["compositor"] = "sway"
            try:
                result = subprocess.run(
                    ["swaymsg", "-t", "get_version"],
                    capture_output=True, text=True, timeout=3,
                )
                v = json.loads(result.stdout)
                info["version"] = v.get("version", "unknown")
            except Exception:
                info["version"] = "unknown"
        elif "hyprland" in desktop:
            info["compositor"] = "hyprland"
            try:
                result = subprocess.run(
                    ["hyprctl", "version"],
                    capture_output=True, text=True, timeout=3,
                )
                info["version"] = result.stdout.strip()
            except Exception:
                info["version"] = "unknown"
        elif "kde" in desktop or "plasma" in desktop:
            info["compositor"] = "kde"
        elif "cosmic" in desktop:
            info["compositor"] = "cosmic"
        else:
            # Generic wlroots or unknown
            for cmd in ["swaymsg", "hyprctl"]:
                if subprocess.run(["which", cmd], capture_output=True).returncode == 0:
                    info["compositor"] = "wlroots"
                    break

    elif "x11" in session_type or os.environ.get("DISPLAY"):
        info["type"] = "x11"
        info["is_x11"] = True
        info["compositor"] = "x11"

    return info


def create_virtual_display(session_info, width=1920, height=1080, rate=60, name=None):
    """Create a virtual monitor on the current display server.

    Args:
        session_info: Output from detect_session().
        width, height, rate: Desired resolution and refresh rate.
        name: Optional display name.

    Returns:
        dict with 'success': bool, 'name': virtual output name,
        'geometry': (x, y, w, h) or None, 'fallback': bool.
    """
    result = {"success": False, "name": "", "geometry": None, "fallback": False, "message": ""}

    if session_info["is_x11"]:
        return _create_virtual_x11(width, height, rate, name)
    elif session_info["is_wayland"]:
        compositor = session_info.get("compositor", "")
        if compositor == "sway":
            return _create_virtual_sway(width, height, rate, name)
        elif compositor == "hyprland":
            return _create_virtual_hyprland(width, height, rate, name)
        elif compositor == "gnome":
            return _create_virtual_gnome(width, height, rate, name)
        elif compositor == "kde":
            return _create_virtual_kde(width, height, rate, name)
        else:
            # Try wlroots generic
            wlr_result = _create_virtual_wlroots(width, height, rate, name)
            if wlr_result["success"]:
                return wlr_result
            result["message"] = (
                f"Unknown Wayland compositor '{compositor}'. "
                f"Try --region mode or use a wlroots-based compositor."
            )
            return result
    else:
        result["message"] = "Cannot detect display server."
        return result


def _create_virtual_x11(width, height, rate, name=None):
    """Create virtual display on X11 via VirtualHeads / intel-virtual-output."""
    output_name = name or "VIRTUAL1"
    result = {"success": False, "name": output_name, "geometry": None, "fallback": False, "message": ""}

    # Check if output already exists
    try:
        xrandr_out = subprocess.run(
            ["xrandr", "--query"], capture_output=True, text=True, timeout=5,
        )
        if output_name in xrandr_out.stdout:
            logger.info("Virtual output %s already exists", output_name)
            result["success"] = True
            result["name"] = output_name
            # Get geometry
            for line in xrandr_out.stdout.splitlines():
                if output_name in line:
                    m = re.search(r"(\d+)x(\d+)\+(\d+)\+(\d+)", line)
                    if m:
                        w, h, x, y = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
                        result["geometry"] = (x, y, w, h)
                    break
            return result
    except Exception:
        pass

    # Try intel-virtual-output
    try:
        if subprocess.run(["which", "intel-virtual-output"], capture_output=True).returncode == 0:
            subprocess.run(["intel-virtual-output", "-f", "VIRTUAL0"],
                           capture_output=True, timeout=5)
            time.sleep(1)
            # Check if it created
            xrandr_out = subprocess.run(
                ["xrandr", "--query"], capture_output=True, text=True, timeout=5,
            )
            if output_name in xrandr_out.stdout:
                result["success"] = True
                result["name"] = output_name
                result["message"] = "Created via intel-virtual-output"
                return result
    except Exception:
        pass

    # Try to add mode and enable
    try:
        modeline = subprocess.run(
            ["cvt", str(width), str(height), str(rate)],
            capture_output=True, text=True, timeout=5,
        )
        mode_line = modeline.stdout.strip().split("\n")[-1].replace("Modeline ", "")
        mode_name = f'"{width}x{height}_{rate}"'

        subprocess.run(f"xrandr --newmode {mode_line}", shell=True, capture_output=True, timeout=5)
        subprocess.run(
            ["xrandr", "--addmode", output_name, mode_name.strip('"')],
            capture_output=True, timeout=5,
        )
        subprocess.run(
            ["xrandr", "--output", output_name, "--mode", mode_name.strip('"'),
             "--right-of", "eDP-1"],
            capture_output=True, timeout=5,
        )
        result["success"] = True
        result["name"] = output_name
        result["message"] = "Added mode to existing virtual output"
    except Exception:
        pass

    if not result["success"]:
        result["message"] = (
            f"Could not create virtual output '{output_name}' on X11.\n"
            f"  Try: sudo cp host/scripts/99-psp-dummy.conf /etc/X11/xorg.conf.d/\n"
            f"  Then restart X11 and run setup-virtual-display.sh"
        )

    return result


def _create_virtual_sway(width, height, rate, name=None):
    """Create a headless output on Sway (wlroots)."""
    result = {"success": False, "name": "", "geometry": None, "fallback": False, "message": ""}

    try:
        # Sway 1.8+ supports create_output
        cmd = ["swaymsg", "create_output"]
        subprocess.run(cmd, capture_output=True, timeout=5, env={**os.environ, "SWAYSOCK": ""})

        # List outputs to find the new headless one
        out = subprocess.run(
            ["swaymsg", "-t", "get_outputs"],
            capture_output=True, text=True, timeout=5,
        )
        outputs = json.loads(out.stdout)
        for output in outputs:
            if output.get("make") == "Unknown" or output.get("name", "").startswith("HEADLESS"):
                name = output["name"]
                # Configure resolution
                subprocess.run(
                    ["swaymsg", f"output {name} resolution {width}x{height}@{rate}Hz"],
                    capture_output=True, timeout=5,
                )
                # Position right of primary
                subprocess.run(
                    ["swaymsg", f"output {name} position 0 0"],
                    capture_output=True, timeout=5,
                )
                result["success"] = True
                result["name"] = name
                result["geometry"] = (0, 0, width, height)
                result["message"] = f"Created headless output '{name}' on Sway"
                return result

        result["message"] = "Sway create_output ran but no headless output found"
    except FileNotFoundError:
        result["message"] = "swaymsg not found"
    except Exception as e:
        result["message"] = f"Sway error: {e}"

    return result


def _create_virtual_hyprland(width, height, rate, name=None):
    """Create a headless output on Hyprland."""
    result = {"success": False, "name": "", "geometry": None, "fallback": False, "message": ""}

    try:
        cmd = ["hyprctl", "output", "create", "headless"]
        subprocess.run(cmd, capture_output=True, timeout=5)

        # List outputs to find the new one
        out = subprocess.run(
            ["hyprctl", "outputs"],
            capture_output=True, text=True, timeout=5,
        )
        # Parse output for headless entries
        for line in out.stdout.splitlines():
            if "HEADLESS" in line or "headless" in line:
                name = line.split()[0].strip()
                # Configure
                subprocess.run(
                    ["hyprctl", "keyword", f"monitor={name},1920x1080@60,auto,1"],
                    capture_output=True, timeout=5,
                )
                result["success"] = True
                result["name"] = name
                result["geometry"] = (0, 0, width, height)
                result["message"] = f"Created headless output on Hyprland"
                return result

        result["message"] = "Hyprland create output ran but no headless output found"
    except FileNotFoundError:
        result["message"] = "hyprctl not found"
    except Exception as e:
        result["message"] = f"Hyprland error: {e}"

    return result


def _create_virtual_gnome(width, height, rate, name=None):
    """Attempt to create a virtual monitor on GNOME.

    GNOME does not have a stable public API for headless monitors.
    This attempts several workarounds.
    """
    result = {"success": False, "name": "", "geometry": None, "fallback": False, "message": ""}

    # Method 1: Try gnome-monitor-config if available
    try:
        if subprocess.run(["which", "gnome-monitor-config"], capture_output=True).returncode == 0:
            cmd = [
                "gnome-monitor-config", "set",
                "-M", "logical",
                "-p", f"{width}x{height}@{rate}",
                "-m", f"{width}x{height}@{rate}",
            ]
            subprocess.run(cmd, capture_output=True, timeout=10)
            time.sleep(1)
            result["success"] = True
            result["name"] = "virtual"
            result["geometry"] = (0, 0, width, height)
            result["message"] = "Created via gnome-monitor-config (experimental)"
            result["fallback"] = True
            return result
    except Exception:
        pass

    # Method 2: Try to use the D-Bus ApplyMonitorsConfig to add a virtual monitor
    # This is experimental and may not work on all GNOME versions
    try:
        # Get current state
        state = subprocess.run(
            ["gdbus", "call", "--session",
             "--dest", "org.gnome.Mutter.DisplayConfig",
             "--object-path", "/org/gnome/Mutter/DisplayConfig",
             "--method", "org.gnome.Mutter.DisplayConfig.GetCurrentState"],
            capture_output=True, text=True, timeout=5,
        )
        # Parse serial from state
        # The state format is (uint32 serial, ...)
        serial_match = re.search(r"uint32 (\d+)", state.stdout)
        if serial_match:
            serial = int(serial_match.group(1))
            logger.info("GNOME DisplayConfig serial: %d", serial)

            # Try to enable experimental virtual monitor feature
            try:
                subprocess.run(
                    ["gsettings", "set", "org.gnome.mutter", "experimental-features",
                     "['scale-monitor-framebuffer', 'virtual-monitor']"],
                    capture_output=True, timeout=5,
                )
                logger.info("Enabled virtual-monitor experimental feature")
                time.sleep(1)
            except Exception:
                pass

        result["message"] = (
            "GNOME does not support virtual monitors natively.\n"
            "  Options:\n"
            "  1. Use --region mode to capture a portion of your screen\n"
            "  2. Install gnome-monitor-config (github.com/udifuchs/gnome-monitor-config)\n"
            "  3. Switch to Sway/Hyprland for native headless output support\n"
            "  4. Use X11 with VirtualHeads"
        )
        result["fallback"] = True
    except Exception as e:
        result["message"] = f"GNOME error: {e}"
        result["fallback"] = True

    return result


def _create_virtual_kde(width, height, rate, name=None):
    """Attempt to create a virtual monitor on KDE Plasma."""
    result = {"success": False, "name": "", "geometry": None, "fallback": False, "message": ""}

    try:
        # KDE has kscreen-doctor and kscreen-console
        # Try kscreen-doctor to add a virtual output
        result["message"] = (
            "KDE virtual monitor creation not yet implemented.\n"
            "  Use --region mode or try kscreen-doctor: kscreen-doctor output.VIRTUAL.enable"
        )
        result["fallback"] = True
    except Exception as e:
        result["message"] = f"KDE error: {e}"

    return result


def _create_virtual_wlroots(width, height, rate, name=None):
    """Generic wlroots virtual display creation using wlr-randr."""
    result = {"success": False, "name": "", "geometry": None, "fallback": False, "message": ""}

    try:
        if subprocess.run(["which", "wlr-randr"], capture_output=True).returncode == 0:
            cmd = ["wlr-randr", "--create", "headless"]
            subprocess.run(cmd, capture_output=True, timeout=5)
            time.sleep(1)

            # List outputs
            out = subprocess.run(
                ["wlr-randr"], capture_output=True, text=True, timeout=5,
            )
            for line in out.stdout.splitlines():
                if "HEADLESS" in line or "headless" in line:
                    name = line.split()[0]
                    result["success"] = True
                    result["name"] = name
                    result["geometry"] = (0, 0, width, height)
                    result["message"] = f"Created headless output via wlr-randr"
                    return result

        result["message"] = "wlr-randr not found. Install with: sudo apt install wlr-randr"
    except FileNotFoundError:
        result["message"] = "wlr-randr not found"
    except Exception as e:
        result["message"] = f"wlr-randr error: {e}"

    return result


def get_primary_geometry():
    """Get the geometry of the primary display.

    Returns:
        (x, y, width, height) or None.
    """
    session = detect_session()
    if session["is_x11"]:
        return _get_x11_primary_geometry()
    elif session["is_wayland"]:
        return _get_wayland_primary_geometry()
    return None


def _get_x11_primary_geometry():
    """Get primary display geometry via xrandr."""
    try:
        result = subprocess.run(
            ["xrandr", "--query"], capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.splitlines():
            if " primary " in line and " connected " in line:
                m = re.search(r"(\d+)x(\d+)\+(\d+)\+(\d+)", line)
                if m:
                    return int(m.group(3)), int(m.group(4)), int(m.group(1)), int(m.group(2))
    except Exception:
        pass
    return None


def _get_wayland_primary_geometry():
    """Get primary display geometry via GNOME D-Bus or wlr-randr."""
    try:
        # Try GNOME D-Bus
        state = subprocess.run(
            ["gdbus", "call", "--session",
             "--dest", "org.gnome.Mutter.DisplayConfig",
             "--object-path", "/org/gnome/Mutter/DisplayConfig",
             "--method", "org.gnome.Mutter.DisplayConfig.GetCurrentState"],
            capture_output=True, text=True, timeout=5,
        )
        # Parse logical_monitors: (iiduba(ssa{sv})) 
        # Format: (x, y, scale, is_primary, ...)
        m = re.search(r"\((\d+),\s*(\d+),\s*([\d.]+),\s*(\d+),\s*(\w+),\s*(\w+),\s*\[", state.stdout)
        if m:
            x, y = int(m.group(1)), int(m.group(2))
            # We need the width and height from the monitor mode
            # Look for '2560x1440' pattern in the monitor section
            # Actually the logical_monitors don't include width/height directly
            # We need to parse the monitor modes
            # Let's use a simpler approach: get from pipewire or wlr-randr
            pass
    except Exception:
        pass

    # Try wlr-randr
    try:
        result = subprocess.run(
            ["wlr-randr"], capture_output=True, text=True, timeout=5,
        )
        # Parse first connected output
        lines = result.stdout.splitlines()
        current = None
        for line in lines:
            if not line.startswith(" "):
                current = line.split()[0]
            if "primary" in line and current:
                m = re.search(r"(\d+)x(\d+)\s+@[\d.]+\s+H\s+([\d.]+)mm", line)
                if m:
                    logger.info("Primary display: %s %sx%s", current, m.group(1), m.group(2))
                    return (0, 0, int(m.group(1)), int(m.group(2)))
    except Exception:
        pass

    return None


def suggest_region_for_extend(width=1920, height=1080):
    """Suggest a --region value for extended display mode.

    Places the virtual region to the right of the primary display.
    """
    primary = get_primary_geometry()
    if primary:
        x, y, pw, ph = primary
        # Place to the right
        region_x = x + pw
        region_y = y  # Align top
        return f"{region_x},{region_y},{width}x{height}"
    return f"1920,0,{width}x{height}"