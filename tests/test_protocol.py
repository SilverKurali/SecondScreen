"""Tests for the PSP protocol module."""

import struct
import sys
import os
import json
import unittest

# Add host to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "host"))

from psp_host import protocol as proto


class TestProtocolFraming(unittest.TestCase):
    """Test TCP frame packing/unpacking."""

    def test_make_frame(self):
        """Test basic frame packing."""
        payload = b"\x00\x00\x00\x01hello"
        frame = proto.make_frame(0x01, payload)
        # Header: 4 bytes LE length
        body = bytes([0x01]) + payload
        expected_len = len(body)
        expected_header = struct.pack("<I", expected_len)
        expected = expected_header + body
        self.assertEqual(frame, expected)

    def test_control_frame(self):
        """Test control frame with JSON payload."""
        obj = {"type": "ping", "id": 1}
        frame = proto.make_control_frame(obj)
        # Parse it back
        body_len = struct.unpack("<I", frame[:4])[0]
        body = frame[4:4 + body_len]
        flags = body[0]
        payload = body[1:]
        self.assertEqual(flags, proto.FLAG_CONTROL)
        self.assertEqual(json.loads(payload), obj)

    def test_keyframe_flag(self):
        """Test keyframe flag in video frame."""
        data = b"\x00\x00\x00\x01\x67\x42\x00\x1e"
        frame = proto.make_video_frame(data, is_keyframe=True)
        body_len = struct.unpack("<I", frame[:4])[0]
        body = frame[4:4 + body_len]
        flags = body[0]
        self.assertTrue(flags & proto.FLAG_KEYFRAME)

    def test_config_flag(self):
        """Test config flag in video frame."""
        data = b"\x00\x00\x00\x01\x67\x42\x00\x1e"
        frame = proto.make_video_frame(data, is_config=True)
        body_len = struct.unpack("<I", frame[:4])[0]
        body = frame[4:4 + body_len]
        flags = body[0]
        self.assertTrue(flags & proto.FLAG_CONFIG)

    def test_drop_flag(self):
        """Test drop flag in video frame."""
        data = b"\x00\x00\x00\x01\x41\x9a"
        frame = proto.make_video_frame(data, drop=True)
        body_len = struct.unpack("<I", frame[:4])[0]
        body = frame[4:4 + body_len]
        flags = body[0]
        self.assertTrue(flags & proto.FLAG_DROP)

    def test_payload_integrity(self):
        """Test that payload is unchanged after packing."""
        original = b"\x00\x00\x00\x01\x41\x9a\x02\x03\x04"
        frame = proto.make_frame(0x00, original)
        body_len = struct.unpack("<I", frame[:4])[0]
        body = frame[4:4 + body_len]
        self.assertEqual(body[1:], original)


class TestNegotiation(unittest.TestCase):
    """Test session negotiation logic."""

    def test_successful_negotiation(self):
        """Test standard negotiation."""
        want = {"codec": "h264", "width": 1920, "height": 1080, "fps": 90, "bitrate_kbps": 15000}
        have = {"codec": "h264", "width": 1920, "height": 1080, "fps": 120, "bitrate_kbps": 18000}
        ok, response = proto.negotiate(want, have)
        self.assertTrue(ok)
        self.assertEqual(response["width"], 1920)
        self.assertEqual(response["height"], 1080)
        self.assertEqual(response["fps"], 90)
        self.assertEqual(response["bitrate_kbps"], 15000)
        self.assertEqual(response["codec"], "h264")

    def test_clamp_to_host_capabilities(self):
        """Test that client can't request beyond host capabilities."""
        want = {"codec": "h264", "width": 3840, "height": 2160, "fps": 240, "bitrate_kbps": 50000}
        have = {"codec": "h264", "width": 1920, "height": 1080, "fps": 60, "bitrate_kbps": 10000}
        ok, response = proto.negotiate(want, have)
        self.assertTrue(ok)
        self.assertEqual(response["width"], 1920)
        self.assertEqual(response["height"], 1080)
        self.assertEqual(response["fps"], 60)
        self.assertEqual(response["bitrate_kbps"], 10000)

    def test_unsupported_codec(self):
        """Test unsupported codec falls back to h264."""
        want = {"codec": "h265", "width": 1920, "height": 1080, "fps": 60}
        have = {"codec": "h264", "width": 1920, "height": 1080, "fps": 60, "bitrate_kbps": 10000}
        ok, response = proto.negotiate(want, have)
        self.assertTrue(ok)
        self.assertEqual(response["codec"], "h264")

    def test_resolution_too_small(self):
        """Test too-small resolution is rejected."""
        want = {"codec": "h264", "width": 320, "height": 240, "fps": 60}
        have = {"codec": "h264", "width": 1920, "height": 1080, "fps": 60, "bitrate_kbps": 10000}
        ok, response = proto.negotiate(want, have)
        self.assertFalse(ok)
        self.assertIn("Resolution too small", response["reason"])

    def test_vp9_codec(self):
        """Test VP9 falls back to h264."""
        want = {"codec": "vp9", "width": 1280, "height": 720, "fps": 60}
        have = {"codec": "vp9", "width": 1920, "height": 1080, "fps": 60, "bitrate_kbps": 10000}
        ok, response = proto.negotiate(want, have)
        self.assertTrue(ok)
        self.assertEqual(response["codec"], "h264")


class TestFrameReader(unittest.TestCase):
    """Test FrameReader with mock socket."""

    def test_read_frame(self):
        """Test reading a frame from a byte stream."""
        import socket
        import io

        # Create a mock socket using a pipe
        a, b = socket.socketpair()

        # Send a frame
        frame = proto.make_control_frame({"type": "ping", "id": 42})
        a.sendall(frame)

        # Read it
        reader = proto.FrameReader(b)
        flags, payload = reader.read_frame()
        self.assertEqual(flags, proto.FLAG_CONTROL)
        self.assertEqual(json.loads(payload), {"type": "ping", "id": 42})

        a.close()
        b.close()

    def test_disconnect(self):
        """Test that disconnect raises ConnectionError."""
        import socket
        a, b = socket.socketpair()
        a.close()
        reader = proto.FrameReader(b)
        with self.assertRaises(ConnectionError):
            reader.read_frame()
        b.close()


if __name__ == "__main__":
    unittest.main()