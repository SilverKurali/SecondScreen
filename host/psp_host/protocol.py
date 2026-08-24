"""PSP protocol: frame packing/unpacking and session negotiation."""

import json
import struct as _struct
import uuid as _uuid

# Frame flags
FLAG_KEYFRAME = 0x01
FLAG_CONFIG = 0x02
FLAG_DROP = 0x04
FLAG_CONTROL = 0x80

# Frame header: 4 bytes LE length + 1 byte flags
HEADER_FMT = "<I"
HEADER_SIZE = 4  # length field (excludes itself, includes flags byte)
PACKET_HEADER_SIZE = HEADER_SIZE + 1  # total header = length + flags


def make_frame(flags, payload):
    """Pack a binary frame: [u32le len(flags+payload)][u8 flags][payload].

    Returns:
        bytes: Complete frame ready to send.
    """
    body = bytes([flags]) + payload
    header = _struct.pack(HEADER_FMT, len(body))
    return header + body


def make_control_frame(obj):
    """Pack a JSON control frame.

    Args:
        obj: JSON-serializable dict.

    Returns:
        bytes: Complete frame with FLAG_CONTROL set.
    """
    payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    return make_frame(FLAG_CONTROL, payload)


def make_video_frame(encoded_data, is_keyframe=False, is_config=False, drop=False):
    """Pack a video frame.

    Args:
        encoded_data: H.264/VP9/VP8 access unit bytes (Annex-B).
        is_keyframe: True if IDR frame.
        is_config: True if contains SPS/PPS etc.
        drop: Hint that this frame can be dropped.

    Returns:
        bytes: Complete frame.
    """
    flags = 0
    if is_keyframe:
        flags |= FLAG_KEYFRAME
    if is_config:
        flags |= FLAG_CONFIG
    if drop:
        flags |= FLAG_DROP
    return make_frame(flags, encoded_data)


class FrameReader:
    """Reads framed messages from a stream socket.

    Usage:
        reader = FrameReader(sock)
        for (flags, payload) in reader:
            ...
    """

    def __init__(self, sock):
        self._sock = sock
        self._buf = b""

    def recv_exact(self, n):
        """Read exactly n bytes from socket."""
        data = b""
        while len(data) < n:
            chunk = self._sock.recv(n - len(data))
            if not chunk:
                raise ConnectionError("Socket closed")
            data += chunk
        return data

    def read_frame(self):
        """Read one frame from socket.

        Returns:
            (flags, payload) tuple.

        Raises:
            ConnectionError on disconnect.
            ValueError on corrupt frame.
        """
        header = self.recv_exact(4)
        body_len = _struct.unpack(HEADER_FMT, header)[0]
        body = self.recv_exact(body_len)
        if len(body) < 1:
            raise ValueError("Empty frame body")
        flags = body[0]
        payload = body[1:]
        return flags, payload

    def __iter__(self):
        return self

    def __next__(self):
        try:
            return self.read_frame()
        except (ConnectionError, ValueError) as e:
            raise StopIteration from e


def negotiate(want, have):
    """Validate client's 'want' against host capabilities.

    Args:
        want: dict from client hello with 'codec', 'width', 'height', 'fps'.
        have: dict with 'codec', 'width', 'height', 'fps', 'bitrate_kbps'.

    Returns:
        (ok, response_dict) where ok is bool and response_dict is the
        welcome message to send.
    """
    supported_codecs = {"h264", "vp9", "vp8"}
    codec = want.get("codec", "h264")
    if codec not in supported_codecs:
        return False, {"type": "welcome", "ok": False, "reason": f"Unsupported codec: {codec}"}

    # Clamp resolution to what host offers
    w = min(want.get("width", 1920), have["width"])
    h = min(want.get("height", 1080), have["height"])
    if w < 640 or h < 480:
        return False, {"type": "welcome", "ok": False, "reason": "Resolution too small"}

    fps = min(want.get("fps", 60), have["fps"])
    bitrate = min(want.get("bitrate_kbps", have["bitrate_kbps"]), have["bitrate_kbps"])

    return True, {
        "type": "welcome",
        "ok": True,
        "session": str(_uuid.uuid4()),
        "codec": codec,
        "width": w,
        "height": h,
        "fps": fps,
        "bitrate_kbps": bitrate,
        "virtual_display_width": have["width"],
        "virtual_display_height": have["height"],
    }