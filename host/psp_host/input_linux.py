"""Linux input injection via XTest (X11) and ydotool (Wayland).

Auto-detects the display server and uses the appropriate method:
- X11: xdotool (XTest extension)
- Wayland: ydotool (uinput device)
"""

import logging
import os
import subprocess

logger = logging.getLogger(__name__)

# Button code mapping (X11 buttons)
BUTTON_X11 = {"1": 1, "2": 3, "3": 2}  # client btn→X11 btn
# ydotool button codes (from linux/input-event-codes.h)
BUTTON_YDO = {1: 272, 2: 273, 3: 274}  # left, right, middle


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
    """Inject mouse/keyboard events.

    Auto-detects X11 vs Wayland and uses the appropriate tool.
    """

    def __init__(self, screen_width=1920, screen_height=1080, display=":0"):
        self._screen_w = screen_width
        self._screen_h = screen_height
        self._display = display
        self._env = {"DISPLAY": display}
        self._is_wayland = _is_wayland()

        # Detect available tools
        self._xdotool = _check_cmd("xdotool")
        self._ydotool = _check_cmd("ydotool")

        if self._is_wayland:
            if self._ydotool:
                logger.info("Input injection: ydotool (Wayland)")
            else:
                logger.warning(
                    "ydotool not found. Install: sudo apt install ydotool\n"
                    "  Then add yourself to the 'input' group and reboot:\n"
                    "  sudo usermod -aG input $USER"
                )
        else:
            if self._xdotool:
                logger.info("Input injection: xdotool (X11)")
            else:
                logger.warning(
                    "xdotool not found. Install: sudo apt install xdotool"
                )

    def _run_xdotool(self, *args):
        """Run xdotool with args."""
        if not self._xdotool:
            return
        try:
            subprocess.run(
                ["xdotool"] + list(args),
                capture_output=True, text=True, timeout=2,
                env=self._env,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            logger.debug("xdotool error: %s", e)

    def _run_ydotool(self, *args):
        """Run ydotool with args."""
        if not self._ydotool:
            return
        try:
            subprocess.run(
                ["ydotool"] + list(args),
                capture_output=True, text=True, timeout=2,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            logger.debug("ydotool error: %s", e)

    def mouse_move(self, x, y):
        """Move mouse to absolute screen coordinates."""
        if self._is_wayland:
            # ydotool uses absolute coordinates via uinput
            # Format: ydotool mousemove --absolute X Y
            self._run_ydotool(
                "mousemove", "--absolute",
                str(int(x)), str(int(y)),
            )
        else:
            self._run_xdotool("mousemove", "--screen", "0", str(int(x)), str(int(y)))

    def mouse_button(self, btn, state):
        """Press or release a mouse button.

        Args:
            btn: Button number (1=left, 2=right, 3=middle).
            state: 1=press, 0=release.
        """
        if self._is_wayland:
            # ydotool: click CODE
            code = BUTTON_YDO.get(btn, 272)
            if state == 1:
                self._run_ydotool("click", str(code))
            # ydotool doesn't have separate up/down, click does down+up
            # For drag, we need mousedown/mouseup
            else:
                self._run_ydotool("click", str(code))
        else:
            xbtn = BUTTON_X11.get(str(btn), btn)
            action = "mousedown" if state == 1 else "mouseup"
            self._run_xdotool(action, str(xbtn))

    def mouse_wheel(self, dx, dy):
        """Scroll wheel. dy>0 = scroll up, dy<0 = scroll down."""
        if self._is_wayland:
            # ydotool: scroll --amount N
            if dy:
                # ydotool uses positive = down, negative = up
                self._run_ydotool("scroll", str(-int(dy)), "0")
            if dx:
                self._run_ydotool("scroll", "0", str(int(dx)))
        else:
            if dy > 0:
                self._run_xdotool("click", "--repeat", str(int(dy)), "4")
            elif dy < 0:
                self._run_xdotool("click", "--repeat", str(int(-dy)), "5")

    def key_event(self, keycode, state):
        """Send a keyboard event. Not fully implemented."""
        if self._is_wayland and self._ydotool:
            action = "keydown" if state == 1 else "keyup"
            self._run_ydotool(action, str(keycode))
        logger.debug("Key event: code=%d state=%d", keycode, state)

    def set_screen_size(self, width, height):
        """Update screen size for coordinate mapping."""
        self._screen_w = width
        self._screen_h = height

    def close(self):
        """Cleanup."""
        pass