"""GStreamer pipeline construction for screen capture and encoding."""

import logging
import os
import platform
import subprocess
import sys

from .config import RESOLUTIONS

logger = logging.getLogger(__name__)

# Priority-ordered encoder list: (name, codec_str, gst_element_name, properties_dict)
ENCODER_PRIORITY = [
    # H.264: best latency/quality
    ("x264enc", "h264", "x264enc", {
        "speed-preset": "ultrafast",
        "tune": "zerolatency",
        "byte-stream": "true",
        "bframes": "0",
        "key-int-max": "300",
        "threads": "auto",
    }),
    ("nvenc", "h264", "nvh264enc", {
        "preset": "low-latency-hq",
        "rc-mode": "cbr",
        "bframes": "0",
    }),
    ("vaapih264enc", "h264", "vaapih264enc", {
        "tune": "low-latency",
        "rate-control": "cbr",
        "bframes": "0",
        "keyframe-period": "300",
    }),
    # VP9: good quality, slower
    ("vp9enc", "vp9", "vp9enc", {
        "cpu-used": "8",
        "deadline": "1",
        "lag-in-frames": "0",
        "static-threshold": "0",
        "min-quantizer": "30",
        "max-quantizer": "50",
    }),
    # VP8: fast, decent quality
    ("vp8enc", "vp8", "vp8enc", {
        "cpu-used": "8",
        "deadline": "1",
        "lag-in-frames": "0",
        "static-threshold": "0",
        "min-quantizer": "30",
        "max-quantizer": "50",
    }),
    # Theora: last resort
    ("theoraenc", "theora", "theoraenc", {
        "quality": "48",
        "keyframe-force": "300",
    }),
]


def _check_element(name):
    """Check if a GStreamer element is available."""
    try:
        result = subprocess.run(
            ["gst-inspect-1.0", name],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def detect_encoder(preferred_codec=None):
    """Detect the best available encoder.

    Args:
        preferred_codec: Optional 'h264', 'vp9', 'vp8', or None for auto.

    Returns:
        (codec_str, gst_element_name, properties_dict) or raises RuntimeError.
    """
    # Filter by preference if given
    candidates = ENCODER_PRIORITY
    if preferred_codec and preferred_codec != "auto":
        candidates = [e for e in candidates if e[1] == preferred_codec]
        if not candidates:
            raise RuntimeError(f"Preferred codec '{preferred_codec}' not in encoder list")

    for name, codec, element, props in candidates:
        if _check_element(element):
            logger.info("Using encoder: %s (%s)", element, codec)
            return codec, element, props

    raise RuntimeError(
        "No video encoder found! Install gstreamer1.0-plugins-ugly (for x264enc) "
        "or gstreamer1.0-plugins-bad/vpx for VP8/VP9.\n"
        "  sudo apt install gstreamer1.0-plugins-ugly gstreamer1.0-libav"
    )


def build_pipeline(args):
    """Build a GStreamer pipeline for screen capture and encoding.

    Args:
        args: Parsed command-line arguments (from config.parse_args).

    Returns:
        (codec, pipeline_string, appsink_name) where codec is 'h264'/'vp9'/etc.
    """
    import gi  # noqa: F811
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst  # noqa: F402

    Gst.init(None)

    # Detect encoder
    codec, encoder_name, enc_props = detect_encoder(args.codec)

    # Determine capture source and region
    src_str = _build_source(args)

    # Bitrate string
    bitrate_kbps = args.bitrate_kbps

    # Build encoder properties string
    enc_props_str = " ".join(f"{k}={v}" for k, v in enc_props.items())
    # Add bitrate (encoder-specific naming)
    bitrate_prop = ""
    if encoder_name == "x264enc":
        # x264enc uses bitrate= in kbps for encoding, but "vbv-buf-capacity" etc.
        # Actually x264enc property: "bitrate" is in kbps
        enc_props_str += f" bitrate={bitrate_kbps}"
        # Add vbv buffer size for CBR-like behavior
        enc_props_str += f" vbv-buf-capacity={bitrate_kbps // 1000 + 1}"
    elif encoder_name in ("nvh264enc", "vaapih264enc"):
        # These use "bitrate" in kbps
        enc_props_str += f" bitrate={bitrate_kbps}"
    elif encoder_name in ("vp9enc", "vp8enc"):
        # vpx encoders use "target-bitrate" in kbps, "end-usage=cbr"
        enc_props_str += f" target-bitrate={bitrate_kbps} end-usage=cbr"
    elif encoder_name == "theoraenc":
        pass  # quality-based, no bitrate setting

    # The pipeline — use I420 which is compatible with all encoders
    capsfilter = f"video/x-raw,format=I420,width={args.width},height={args.height},framerate={args.fps}/1"
    pipeline_str = (
        f"{src_str} ! "
        f"videoconvert ! "
        f"videorate ! "
        f"capsfilter caps=\"{capsfilter}\" ! "
        f"{encoder_name} {enc_props_str} ! "
        f"h264parse config-interval=-1 alignment=au ! "
        f"appsink name=psp_sink max-buffers=1 drop=true sync=false emit-signals=true"
    )

    # For VP8/VP9/Theora, replace h264parse with appropriate parser
    if codec in ("vp8", "vp9"):
        # VP8/VP9 frames are self-contained, no parser needed
        pipeline_str = pipeline_str.replace("! h264parse config-interval=-1 alignment=au ! ", "! ")
    elif codec == "theora":
        pipeline_str = pipeline_str.replace("! h264parse config-interval=-1 alignment=au ! ", "! ")

    logger.debug("Pipeline: %s", pipeline_str)
    return codec, pipeline_str, "psp_sink"


def _is_wayland():
    """Check if running under Wayland."""
    return (
        os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
        or os.environ.get("WAYLAND_DISPLAY", "")
    )


def _build_source(args):
    """Build the capture source pipeline string based on platform."""
    system = platform.system()

    if system == "Linux":
        if _is_wayland():
            return _build_wayland_source(args)
        else:
            return _build_x11_source(args)

    elif system == "Windows":
        # Use DXGI capture on Windows (requires gst-plugins-bad)
        if args.region:
            logger.warning("--region on Windows uses DXGI full screen with crop. Not fully implemented.")
        return (
            "dxgiscreencapsrc monitor-index=0 ! "
            "videoconvert"
        )
    else:
        raise RuntimeError(f"Unsupported platform: {system}")


def _build_x11_source(args):
    """Build X11 capture source via ximagesrc."""
    display = args.display
    if args.region:
        try:
            parts = args.region.replace("x", ",").split(",")
            sx, sy, sw, sh = int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
            return (
                f"ximagesrc display-name={display} "
                f"startx={sx} starty={sy} "
                f"endx={sx + sw} endy={sy + sh} "
                f"use-damage=false show-pointer=false"
            )
        except (ValueError, IndexError):
            logger.warning("Invalid --region format, using full screen")
            return f"ximagesrc display-name={display} use-damage=false show-pointer=false"
    elif args.output:
        geo = _get_output_geometry(args.output)
        if geo:
            sx, sy, sw, sh = geo
            logger.info("Output '%s' geometry: %d,%d %dx%d", args.output, sx, sy, sw, sh)
            return (
                f"ximagesrc display-name={display} "
                f"startx={sx} starty={sy} "
                f"endx={sx + sw} endy={sy + sh} "
                f"use-damage=false show-pointer=false"
            )
        else:
            logger.warning("Could not detect output '%s', falling back to full screen", args.output)
            return f"ximagesrc display-name={display} use-damage=false show-pointer=false"
    else:
        return f"ximagesrc display-name={display} use-damage=false show-pointer=false"


def _build_wayland_source(args):
    """Build Wayland capture source via PipeWire screencast portal.

    Uses pipewiresrc which triggers the xdg-desktop-portal screen sharing dialog.
    The user will need to select/confirm the screen to share.

    If --region is specified, the portal will share the full screen
    and we crop it via videocrop. If --output is specified, we try to
    pass it as the target PipeWire node.
    """
    # Check if pipewiresrc is available
    if not _check_element("pipewiresrc"):
        logger.warning(
            "pipewiresrc not found. Install: sudo apt install gstreamer1.0-pipewire\n"
            "Falling back to X11 capture (may not work on Wayland)."
        )
        return _build_x11_source(args)

    logger.info("Using Wayland capture via pipewiresrc (PipeWire screencast portal)")

    # Build the pipewiresrc capture pipeline
    # pipewiresrc autoconnect=true will show the portal dialog
    src = "pipewiresrc autoconnect=true do-timestamp=true"

    # If region is specified, capture full screen and crop
    if args.region:
        try:
            parts = args.region.replace("x", ",").split(",")
            sx, sy, sw, sh = int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
            logger.info("Wayland region crop: %d,%d %dx%d", sx, sy, sw, sh)
            src += (
                f" ! videocrop left={sx} right={sx} top={sy} bottom={sy}"
            )
            # Note: videocrop doesn't work with source coordinates directly.
            # Alternative: use the stream dimensions from pipewire and rely on
            # the user selecting the correct monitor. For region, we can't
            # easily crop on Wayland without knowing the source dimensions.
            logger.warning(
                "Region cropping on Wayland is approximate. "
                "Please select the correct monitor in the screen sharing dialog."
            )
        except (ValueError, IndexError):
            logger.warning("Invalid --region format on Wayland, ignoring")

    # If output is specified, try to set stream-properties to select it
    # This is a hint to pipewire about which monitor to capture
    if args.output:
        # We can't directly select a monitor by name via pipewiresrc properties
        # But we can set the stream-properties hint
        src += (
            f" stream-properties=\"properties,"
            f"window-x=0,window-y=0,"
            f"window-width={args.width},window-height={args.height}\""
        )

    logger.info("Wayland source: %s", src)
    return src


def _get_output_geometry(output_name):
    """Parse xrandr output to find the geometry of a named output.

    Returns:
        (x, y, width, height) tuple or None.
    """
    try:
        result = subprocess.run(
            ["xrandr", "--query"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.splitlines():
            if output_name in line and " connected" in line:
                # Parse: "eDP-1 connected primary 1920x1080+0+0 (normal ...)"
                import re
                m = re.search(r"(\d+)x(\d+)\+(\d+)\+(\d+)", line)
                if m:
                    w, h, x, y = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
                    return x, y, w, h
        return None
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.warning("xrandr failed: %s", e)
        return None


def list_x11_outputs():
    """List available X11 outputs via xrandr."""
    try:
        result = subprocess.run(
            ["xrandr", "--query"],
            capture_output=True, text=True, timeout=5,
        )
        outputs = []
        for line in result.stdout.splitlines():
            if " connected" in line:
                outputs.append(line.strip())
        return outputs
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return [f"Error: {e}"]


def get_encoder_info():
    """Return info about available encoders."""
    info = []
    for name, codec, element, _ in ENCODER_PRIORITY:
        available = _check_element(element)
        info.append((name, codec, element, available))
    return info