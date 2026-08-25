"""Linux input injection via evdev uinput + hyprctl.

Uses python-evdev to create a virtual mouse device for button/scroll events.
Uses hyprctl dispatch movecursor for absolute positioning (works with multi-monitor).
Falls back to xdotool for X11.
"""

import logging
import os
import re
import subprocess
import threading

logger = logging.getLogger(__name__)

# Linux input event codes (from linux/input-event-codes.h)
BTN_LEFT = 0x110      # 272
BTN_RIGHT = 0x111     # 273
BTN_MIDDLE = 0x112    # 274
REL_X = 0x00
REL_Y = 0x01
REL_WHEEL = 0x08
REL_HWHEEL = 0x06

BUTTON_MAP = {1: BTN_LEFT, 2: BTN_RIGHT, 3: BTN_MIDDLE}


def _is_wayland():
    return (
        os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
        or os.environ.get("WAYLAND_DISPLAY", "")
    )


def _check_cmd(name):
    try:
        result = subprocess.run(["which", name], capture_output=True, timeout=3)
        return result.returncode == 0
    except Exception:
        return False


class InputInjector:
    """Inject mouse events using evdev uinput + hyprctl.

    Creates a virtual mouse device for button press/release and scroll.
    Uses hyprctl for absolute cursor positioning on multi-monitor Wayland.
    """

    def __init__(self, screen_width=1920, screen_height=1080, display=":0", output_name=None):
        self._screen_w = screen_width
        self._screen_h = screen_height
        self._display = display
        self._env = {"DISPLAY": display}
        self._is_wayland = _is_wayland()
        self._offset_x = 0
        self._offset_y = 0
        self._cur_x = 0
        self._cur_y = 0
        self._ui = None  # evdev UInput device
        self._lock = threading.Lock()

        # Detect monitor offset for multi-monitor setups
        if output_name:
            self._detect_monitor_offset(output_name)

        # Initialize evdev uinput for button/scroll events
        self._init_uinput()

        # Get initial cursor position
        if _check_cmd("hyprctl"):
            try:
                result = subprocess.run(["hyprctl", "cursorpos"], capture_output=True, text=True, timeout=2)
                parts = result.stdout.strip().split(",")
                self._cur_x = int(parts[0].strip())
                self._cur_y = int(parts[1].strip())
                logger.info("Initial cursor pos: (%d, %d)", self._cur_x, self._cur_y)
            except Exception:
                pass

        if self._ui:
            logger.info("Input injection: evdev uinput + hyprctl offset=(%d,%d)",
                        self._offset_x, self._offset_y)
        elif self._is_wayland and _check_cmd("ydotool"):
            logger.info("Input injection: ydotool fallback offset=(%d,%d)",
                        self._offset_x, self._offset_y)
        else:
            if self._is_wayland:
                logger.warning(
                    "No input injection available. Install python-evdev:\n"
                    "  pip3 install evdev\n"
                    "  Then add yourself to the 'input' group and reboot:\n"
                    "  sudo usermod -aG input $USER"
                )
            else:
                if _check_cmd("xdotool"):
                    logger.info("Input injection: xdotool (X11) offset=(%d,%d)",
                                self._offset_x, self._offset_y)
                else:
                    logger.warning("xdotool not found. Install: sudo apt install xdotool")

    def _init_uinput(self):
        """Create a virtual mouse device using evdev UInput."""
        try:
            import evdev
            from evdev import ecodes

            # Define virtual mouse capabilities
            cap = {
                ecodes.EV_KEY: [BTN_LEFT, BTN_RIGHT, BTN_MIDDLE],
                ecodes.EV_REL: [REL_X, REL_Y, REL_WHEEL, REL_HWHEEL],
            }

            self._ui = evdev.UInput(cap, name="PSP Virtual Mouse", vendor=0x1234, product=0x5678)
            logger.info("evdev uinput device created: PSP Virtual Mouse")

        except ImportError:
            logger.warning("python-evdev not installed. Button/scroll won't work.")
            self._ui = None
        except PermissionError:
            logger.warning(
                "Permission denied for uinput. Add yourself to 'input' group:\n"
                "  sudo usermod -aG input $USER\n"
                "  Then reboot."
            )
            self._ui = None
        except Exception as e:
            logger.warning("Failed to create uinput device: %s", e)
            self._ui = None

    def _detect_monitor_offset(self, output_name):
        """Detect the monitor's position offset using hyprctl/xrandr."""
        if self._is_wayland and _check_cmd("hyprctl"):
            try:
                result = subprocess.run(
                    ["hyprctl", "monitors"], capture_output=True, text=True, timeout=3
                )
                lines = result.stdout.splitlines()
                for i, line in enumerate(lines):
                    if output_name in line and "Monitor" in line:
                        for j in range(i + 1, min(i + 5, len(lines))):
                            if "at " in lines[j]:
                                m = re.search(r"at (\d+)x(\d+)", lines[j])
                                if m:
                                    self._offset_x = int(m.group(1))
                                    self._offset_y = int(m.group(2))
                                    logger.info("Monitor %s offset: (%d, %d)",
                                                output_name, self._offset_x, self._offset_y)
                                return
            except Exception as e:
                logger.warning("Failed to detect monitor offset via hyprctl: %s", e)

        if _check_cmd("xrandr"):
            try:
                result = subprocess.run(
                    ["xrandr", "--query"], capture_output=True, text=True, timeout=3,
                    env=self._env,
                )
                for line in result.stdout.splitlines():
                    if output_name in line and " connected" in line:
                        m = re.search(r"(\d+)x(\d+)\+(\d+)\+(\d+)", line)
                        if m:
                            self._offset_x = int(m.group(3))
                            self._offset_y = int(m.group(4))
                            logger.info("Monitor %s offset: (%d, %d)",
                                        output_name, self._offset_x, self._offset_y)
                        return
            except Exception as e:
                logger.warning("Failed to detect monitor offset via xrandr: %s", e)

    def mouse_move(self, x, y):
        """Move mouse to absolute screen coordinates."""
        target_x = int(x + self._offset_x)
        target_y = int(y + self._offset_y)
        self._cur_x = target_x
        self._cur_y = target_y

        if self._is_wayland:
            if _check_cmd("hyprctl"):
                subprocess.run(
                    ["hyprctl", "dispatch", "movecursor", str(target_x), str(target_y)],
                    capture_output=True, text=True, timeout=1,
                )
            elif self._ui:
                # Fallback: use relative moves through uinput
                # This won't work well for absolute positioning, but better than nothing
                pass
        else:
            if _check_cmd("xdotool"):
                subprocess.run(
                    ["xdotool", "mousemove", "--screen", "0", str(target_x), str(target_y)],
                    capture_output=True, text=True, timeout=2,
                    env=self._env,
                )

    def mouse_move_relative(self, dx, dy):
        """Move mouse by relative delta (trackpad mode)."""
        dx, dy = int(dx), int(dy)
        self._cur_x += dx
        self._cur_y += dy

        if self._ui:
            try:
                import evdev
                from evdev import ecodes
                with self._lock:
                    self._ui.write(ecodes.EV_REL, REL_X, dx)
                    self._ui.write(ecodes.EV_REL, REL_Y, dy)
                    self._ui.syn()
            except Exception as e:
                logger.debug("uinput relative move error: %s", e)
        elif self._is_wayland and _check_cmd("ydotool"):
            subprocess.run(
                ["ydotool", "mousemove", str(dx), str(dy)],
                capture_output=True, text=True, timeout=2,
            )
        elif _check_cmd("xdotool"):
            subprocess.run(
                ["xdotool", "mousemove", "--relative", str(dx), str(dy)],
                capture_output=True, text=True, timeout=2,
                env=self._env,
            )

    def mouse_button(self, btn, state):
        """Press or release a mouse button.

        Args:
            btn: Button number (1=left, 2=right, 3=middle).
            state: 1=press, 0=release.
        """
        code = BUTTON_MAP.get(btn, BTN_LEFT)

        if self._ui:
            try:
                import evdev
                from evdev import ecodes
                with self._lock:
                    self._ui.write(ecodes.EV_KEY, code, state)
                    self._ui.syn()
                logger.debug("uinput button: code=%d state=%d", code, state)
            except Exception as e:
                logger.debug("uinput button error: %s", e)
        elif self._is_wayland and _check_cmd("ydotool"):
            # ydotool doesn't support --down/--up, so we do full click
            if state == 1:
                subprocess.run(
                    ["ydotool", "click", str(code)],
                    capture_output=True, text=True, timeout=2,
                )
        elif _check_cmd("xdotool"):
            action = "mousedown" if state == 1 else "mouseup"
            xbtn = {1: "1", 2: "3", 3: "2"}.get(btn, "1")
            subprocess.run(
                ["xdotool", action, xbtn],
                capture_output=True, text=True, timeout=2,
                env=self._env,
            )

    def mouse_wheel(self, dx, dy):
        """Scroll wheel. dy>0 = scroll up, dy<0 = scroll down."""
        if self._ui:
            try:
                import evdev
                from evdev import ecodes
                with self._lock:
                    if dy:
                        self._ui.write(ecodes.EV_REL, REL_WHEEL, int(dy))
                    if dx:
                        self._ui.write(ecodes.EV_REL, REL_HWHEEL, int(dx))
                    self._ui.syn()
                logger.debug("uinput scroll: dx=%d dy=%d", dx, dy)
            except Exception as e:
                logger.debug("uinput scroll error: %s", e)
        elif self._is_wayland and _check_cmd("ydotool"):
            if dy:
                subprocess.run(
                    ["ydotool", "scroll", str(-int(dy)), "0"],
                    capture_output=True, text=True, timeout=2,
                )
            if dx:
                subprocess.run(
                    ["ydotool", "scroll", "0", str(int(dx))],
                    capture_output=True, text=True, timeout=2,
                )
        elif _check_cmd("xdotool"):
            if dy > 0:
                subprocess.run(
                    ["xdotool", "click", "--repeat", str(int(dy)), "4"],
                    capture_output=True, text=True, timeout=2,
                    env=self._env,
                )
            elif dy < 0:
                subprocess.run(
                    ["xdotool", "click", "--repeat", str(int(-dy)), "5"],
                    capture_output=True, text=True, timeout=2,
                    env=self._env,
                )

    def key_event(self, keycode, state):
        """Send a keyboard event. Not fully implemented."""
        if self._ui:
            try:
                import evdev
                from evdev import ecodes
                with self._lock:
                    self._ui.write(ecodes.EV_KEY, keycode, state)
                    self._ui.syn()
            except Exception as e:
                logger.debug("uinput key error: %s", e)
        logger.debug("Key event: code=%d state=%d", keycode, state)

    def set_screen_size(self, width, height):
        """Update screen size for coordinate mapping."""
        self._screen_w = width
        self._screen_h = height

    def close(self):
        """Cleanup uinput device."""
        if self._ui:
            try:
                self._ui.close()
                logger.info("uinput device closed")
            except Exception:
                pass
            self._ui = None
