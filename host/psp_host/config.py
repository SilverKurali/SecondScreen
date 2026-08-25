"""Configuration presets and CLI argument parsing."""

import argparse

# Resolution presets: (width, height, label)
RESOLUTIONS = {
    "720p": (1280, 720),
    "750p": (1280, 750),  # 非标准，近似 720p
    "1080p": (1920, 1080),
    "2K": (2560, 1440),
}

# Frame rate presets
FPS_OPTIONS = (60, 90, 120, 144)

# Bitrate presets in kbps: keyed by (width, height, fps)
_BITRATE_TABLE = {
    # 720p / 750p
    (1280, 720, 60): 6000,
    (1280, 720, 90): 8000,
    (1280, 720, 120): 10000,
    (1280, 720, 144): 12000,  # 144fps: 120fps * 1.2
    # 1080p
    (1920, 1080, 60): 10000,
    (1920, 1080, 90): 14000,
    (1920, 1080, 120): 18000,
    (1920, 1080, 144): 21600,  # 144fps: 120fps * 1.2
    # 2K (1440p)
    (2560, 1440, 60): 18000,
    (2560, 1440, 90): 24000,
    (2560, 1440, 120): 32000,
    (2560, 1440, 144): 38400,  # 144fps: 120fps * 1.2
}


def get_bitrate_kbps(width, height, fps):
    """Get recommended bitrate for given resolution and fps."""
    key = (width, height, fps)
    if key in _BITRATE_TABLE:
        return _BITRATE_TABLE[key]
    # Fallback: estimate from pixel count
    pixels = width * height
    base = _BITRATE_TABLE[(1280, 720, 60)]
    ratio = (pixels / (1280 * 720)) * (fps / 60)
    return int(base * ratio)


def parse_args(argv=None):
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="PSP Host - Stream PC screen to Android device as extended display",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m psp_host --output HDMI-1-1 --resolution 1080p --fps 90
  python -m psp_host --region 1920,0,1920,1080 --fps 120 --quality 1.5
  python -m psp_host --adb auto --resolution 2K --fps 60
  python -m psp_host --list-outputs
  python -m psp_host --list-devices
        """,
    )

    # Display / capture region
    disp = parser.add_argument_group("Display / Capture")
    disp.add_argument(
        "-o", "--output",
        help="X11 output name (e.g., HDMI-1-1, VIRTUAL1). Auto-detects geometry."
        " Implies --extend mode.",
    )
    disp.add_argument(
        "-r", "--region",
        help="Capture region: x,y,WxH (e.g., 1920,0,1920x1080)"
        " Useful when output has already been created.",
    )
    disp.add_argument(
        "-f", "--fullscreen",
        action="store_true",
        help="Capture the entire primary display (not recommended for extend mode).",
    )
    disp.add_argument(
        "-l", "--list-outputs",
        action="store_true",
        help="List available X11 outputs and exit.",
    )

    # Video quality
    vid = parser.add_argument_group("Video Quality")
    vid.add_argument(
        "--resolution",
        choices=list(RESOLUTIONS.keys()),
        default="1080p",
        help="Stream resolution (default: 1080p)",
    )
    vid.add_argument(
        "--fps",
        type=int,
        choices=FPS_OPTIONS,
        default=60,
        help="Target frame rate (default: 60)",
    )
    vid.add_argument(
        "--quality",
        type=float,
        default=1.0,
        metavar="Q",
        help="Bitrate multiplier 0.5-3.0 (default: 1.0)",
    )
    vid.add_argument(
        "--codec",
        choices=["auto", "h264", "vp9", "vp8"],
        default="auto",
        help="Preferred video codec (default: auto-detect best available)",
    )

    # Connection
    conn = parser.add_argument_group("Connection")
    conn.add_argument(
        "-p", "--port",
        type=int,
        default=4747,
        help="TCP listen port (default: 4747)",
    )
    conn.add_argument(
        "--adb",
        dest="adb_mode",
        choices=["off", "auto", "usb", "wireless"],
        default="off",
        help="ADB connection mode (default: off).\n"
             "  auto:     Auto-detect USB + wireless ADB devices, set up reverse tunnel\n"
             "  usb:      Only USB ADB reverse tunnel\n"
             "  wireless: Only wireless ADB reverse tunnel\n"
             "  off:      No ADB setup (use WiFi direct connection)",
    )
    conn.add_argument(
        "--adb-path",
        default="adb",
        help="Path to adb executable (default: 'adb')",
    )
    conn.add_argument(
        "--list-devices",
        action="store_true",
        help="Scan LAN for PSP hosts and ADB devices, then exit.",
    )

    # Misc
    parser.add_argument(
        "--no-input",
        action="store_true",
        help="Disable touch/mouse input forwarding from Android.",
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        help="Launch the GTK4 graphical control panel instead of the CLI server.",
    )
    parser.add_argument(
        "--display",
        default=":0",
        help="X11 display to capture (default: :0)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )

    args = parser.parse_args(argv)

    # Resolve resolution
    w, h = RESOLUTIONS[args.resolution]
    args.width = w
    args.height = h

    # Resolve bitrate
    base_bitrate = get_bitrate_kbps(w, h, args.fps)
    args.bitrate_kbps = int(base_bitrate * args.quality)

    # Validate
    if args.fullscreen and args.output:
        parser.error("--fullscreen and --output are mutually exclusive.")
    if args.region and args.output:
        parser.error("--region and --output are mutually exclusive.")

    return args