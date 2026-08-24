"""Windows input injection via SendInput (ctypes)."""

import ctypes
import logging
from ctypes import wintypes

logger = logging.getLogger(__name__)

# Windows API constants
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000
KEYEVENTF_KEYDOWN = 0x0000
KEYEVENTF_KEYUP = 0x0002


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", wintypes.DWORD),
        ("u", INPUT_UNION),
    ]


SendInput = ctypes.windll.user32.SendInput
SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), wintypes.c_int]
SendInput.restype = wintypes.UINT


class InputInjector:
    """Inject mouse/keyboard events using Windows SendInput."""

    def __init__(self, screen_width=1920, screen_height=1080):
        self._screen_w = screen_width
        self._screen_h = screen_height

    def _send(self, *inputs):
        """Send one or more INPUT structures."""
        arr = (INPUT * len(inputs))(*inputs)
        SendInput(len(inputs), arr, ctypes.sizeof(INPUT))

    def _make_mouse_input(self, dx, dy, flags, data=0):
        """Create a MOUSEINPUT structure."""
        mi = MOUSEINPUT()
        mi.dx = dx
        mi.dy = dy
        mi.mouseData = data
        mi.dwFlags = flags
        mi.time = 0
        mi.dwExtraInfo = None
        inp = INPUT()
        inp.type = INPUT_MOUSE
        inp.u.mi = mi
        return inp

    def mouse_move(self, x, y):
        """Move mouse to absolute screen coordinates."""
        # Normalize to 0..65535 for absolute mode
        abs_x = int(x * 65535 / self._screen_w)
        abs_y = int(y * 65535 / self._screen_h)
        inp = self._make_mouse_input(abs_x, abs_y, MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE)
        self._send(inp)

    def mouse_button(self, btn, state):
        """Press or release a mouse button."""
        flags = {
            (1, 1): MOUSEEVENTF_LEFTDOWN,
            (1, 0): MOUSEEVENTF_LEFTUP,
            (2, 1): MOUSEEVENTF_RIGHTDOWN,
            (2, 0): MOUSEEVENTF_RIGHTUP,
            (3, 1): MOUSEEVENTF_MIDDLEDOWN,
            (3, 0): MOUSEEVENTF_MIDDLEUP,
        }.get((btn, state))
        if flags:
            inp = self._make_mouse_input(0, 0, flags)
            self._send(inp)

    def mouse_wheel(self, dx, dy):
        """Scroll wheel. dy>0 = up, dy<0 = down."""
        if dy:
            data = int(dy * 120)  # WHEEL_DELTA = 120
            inp = self._make_mouse_input(0, 0, MOUSEEVENTF_WHEEL, data)
            self._send(inp)
        if dx:
            data = int(dx * 120)
            inp = self._make_mouse_input(0, 0, MOUSEEVENTF_HWHEEL, data)
            self._send(inp)

    def set_screen_size(self, width, height):
        """Update screen dimensions for coordinate mapping."""
        self._screen_w = width
        self._screen_h = height

    def close(self):
        """Cleanup."""
        pass