"""Discovery module: UDP broadcast + ADB device detection.

PC 端自动发现模块：
1. UDP 广播自身存在（每 2 秒），让 Android 端能自动发现
2. 响应 Android 端的发现请求
3. 监控 ADB 设备列表（USB + 无线 ADB）
"""

import json
import logging
import socket
import struct
import subprocess
import threading
import time

logger = logging.getLogger(__name__)

# UDP discovery port (separate from video stream port)
DISCOVERY_PORT = 4748
# Broadcast interval in seconds
BROADCAST_INTERVAL = 2.0
# ADB device poll interval
ADB_POLL_INTERVAL = 3.0

# UDP multicast / broadcast
BROADCAST_ADDR = ("255.255.255.255", DISCOVERY_PORT)


class DiscoveryServer:
    """UDP discovery server that broadcasts PC presence and responds to probes.

    Also monitors ADB devices (USB + wireless) and exposes the list.
    """

    def __init__(self, host_port=4747, hostname=None, display_name="PSP Host"):
        self._host_port = host_port
        self._hostname = hostname or socket.gethostname()
        self._display_name = display_name
        self._running = False
        self._udp_sock = None
        self._adb_devices = []  # List of ADB device info dicts
        self._lan_devices = []  # List of discovered LAN devices (for future use)
        self._lock = threading.Lock()

        # Resolve local IPs
        self._local_ips = self._get_local_ips()

    def _get_local_ips(self):
        """Get all local IP addresses (IPv4)."""
        ips = set()
        try:
            # Get hostname IP
            ips.add(socket.gethostbyname(socket.gethostname()))
        except Exception:
            pass
        try:
            # Enumerate interfaces
            import subprocess as sp
            result = sp.run(
                ["hostname", "-I"],
                capture_output=True, text=True, timeout=3,
            )
            if result.returncode == 0:
                for ip in result.stdout.strip().split():
                    if ip and not ip.startswith("127."):
                        ips.add(ip)
        except Exception:
            pass
        # Fallback: try to get from socket interfaces
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None):
                addr = info[4][0]
                if ":" not in addr and not addr.startswith("127."):
                    ips.add(addr)
        except Exception:
            pass
        return list(ips) if ips else ["127.0.0.1"]

    def start(self):
        """Start discovery threads."""
        self._running = True

        # UDP broadcast + listener
        self._udp_thread = threading.Thread(
            target=self._udp_loop, daemon=True, name="DiscoveryUDP"
        )
        self._udp_thread.start()

        # ADB monitor thread
        self._adb_thread = threading.Thread(
            target=self._adb_monitor_loop, daemon=True, name="DiscoveryADB"
        )
        self._adb_thread.start()

        logger.info("Discovery started on UDP port %d", DISCOVERY_PORT)
        logger.info("Local IPs: %s", ", ".join(self._local_ips))

    def stop(self):
        self._running = False
        if self._udp_sock:
            try:
                self._udp_sock.close()
            except Exception:
                pass

    def get_adb_devices(self):
        """Get list of ADB-connected devices."""
        with self._lock:
            return list(self._adb_devices)

    def get_local_ips(self):
        """Get list of local IP addresses."""
        return list(self._local_ips)

    def _udp_loop(self):
        """UDP broadcast + listener loop."""
        try:
            self._udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            self._udp_sock.bind(("0.0.0.0", DISCOVERY_PORT))
            self._udp_sock.settimeout(1.0)  # Non-blocking for clean shutdown
        except OSError as e:
            logger.warning("UDP discovery bind failed: %s", e)
            return

        last_broadcast = 0

        while self._running:
            now = time.time()

            # Broadcast announcement periodically
            if now - last_broadcast >= BROADCAST_INTERVAL:
                self._broadcast_announcement()
                last_broadcast = now

            # Listen for incoming packets
            try:
                data, addr = self._udp_sock.recvfrom(4096)
                self._handle_discovery_packet(data, addr)
            except socket.timeout:
                continue
            except OSError:
                break

    def _broadcast_announcement(self):
        """Broadcast host presence to the LAN."""
        msg = {
            "type": "psp_announce",
            "hostname": self._hostname,
            "display_name": self._display_name,
            "ips": self._local_ips,
            "port": self._host_port,
            "version": "0.1.0",
        }
        payload = json.dumps(msg).encode("utf-8")
        try:
            self._udp_sock.sendto(payload, BROADCAST_ADDR)
        except Exception as e:
            logger.debug("Broadcast failed: %s", e)

    def _handle_discovery_packet(self, data, addr):
        """Handle incoming discovery packet from Android."""
        try:
            msg = json.loads(data.decode("utf-8"))
            msg_type = msg.get("type")

            if msg_type == "psp_discover":
                # Android is asking who's out there
                response = {
                    "type": "psp_announce",
                    "hostname": self._hostname,
                    "display_name": self._display_name,
                    "ips": self._local_ips,
                    "port": self._host_port,
                    "version": "0.1.0",
                    "respond_to": addr[0],
                }
                payload = json.dumps(response).encode("utf-8")
                # Send response directly to the requester
                self._udp_sock.sendto(payload, (addr[0], DISCOVERY_PORT))
                logger.debug("Responded to discovery from %s", addr[0])

            elif msg_type == "psp_announce":
                # Another host (or Android) announcing itself
                with self._lock:
                    # Filter out our own announcements
                    for ip in self._local_ips:
                        if ip in msg.get("ips", []):
                            return
                    # Add to LAN devices list
                    device = {
                        "hostname": msg.get("hostname", "unknown"),
                        "ips": msg.get("ips", []),
                        "port": msg.get("port", 4747),
                        "last_seen": time.time(),
                    }
                    # Update or add
                    for i, d in enumerate(self._lan_devices):
                        if set(d["ips"]) & set(device["ips"]):
                            self._lan_devices[i] = device
                            break
                    else:
                        self._lan_devices.append(device)

        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.debug("Invalid discovery packet from %s: %s", addr[0], e)

    def _adb_monitor_loop(self):
        """Monitor ADB devices (USB + wireless) periodically."""
        while self._running:
            devices = self._poll_adb_devices()
            with self._lock:
                self._adb_devices = devices
            time.sleep(ADB_POLL_INTERVAL)

    def _poll_adb_devices(self):
        """Run `adb devices -l` and parse output."""
        devices = []
        try:
            result = subprocess.run(
                ["adb", "devices", "-l"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                return devices

            lines = result.stdout.strip().splitlines()
            for line in lines:
                if line.startswith("List") or not line.strip():
                    continue
                parts = line.split()
                if len(parts) < 2:
                    continue
                serial = parts[0]
                status = parts[1]
                if status != "device":
                    continue

                # Parse extra info from -l output
                info = {"serial": serial, "status": status, "transport": "usb"}
                # Check if wireless ADB (IP:PORT format)
                if ":" in serial and serial.count(":") >= 1:
                    # Could be wireless ADB: 192.168.1.x:5555
                    # Check if it looks like an IP:port
                    try:
                        ip_part = serial.split(":")[0]
                        parts_check = ip_part.split(".")
                        if len(parts_check) == 4:
                            info["transport"] = "wireless"
                            info["ip"] = serial
                    except Exception:
                        pass

                # Try to get device model
                try:
                    model_result = subprocess.run(
                        ["adb", "-s", serial, "shell", "getprop", "ro.product.model"],
                        capture_output=True, text=True, timeout=3,
                    )
                    if model_result.returncode == 0:
                        info["model"] = model_result.stdout.strip()
                except Exception:
                    pass

                devices.append(info)

        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            logger.debug("ADB poll failed: %s", e)

        return devices


def list_devices(discovery_port=DISCOVERY_PORT, timeout=3):
    """Quick one-shot: broadcast discovery and collect responses.

    Used by the --list-devices CLI command.

    Returns:
        List of discovered devices (PCs and Android devices via ADB).
    """
    import select

    results = []

    # Send discovery request
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(timeout)
        sock.bind(("0.0.0.0", 0))  # Random port

        # Broadcast discovery request
        msg = json.dumps({"type": "psp_discover"}).encode("utf-8")
        sock.sendto(msg, BROADCAST_ADDR)

        # Listen for responses
        start = time.time()
        while time.time() - start < timeout:
            try:
                data, addr = sock.recvfrom(4096)
                msg = json.loads(data.decode("utf-8"))
                if msg.get("type") == "psp_announce":
                    results.append({
                        "hostname": msg.get("hostname", "unknown"),
                        "ips": msg.get("ips", []),
                        "port": msg.get("port", 4747),
                        "source": "lan",
                    })
            except socket.timeout:
                break
            except Exception:
                continue

        sock.close()
    except Exception as e:
        logger.debug("Discovery scan failed: %s", e)

    # Also check ADB
    try:
        adb_result = subprocess.run(
            ["adb", "devices", "-l"],
            capture_output=True, text=True, timeout=5,
        )
        for line in adb_result.stdout.strip().splitlines():
            if "device" in line and not line.startswith("List"):
                parts = line.split()
                serial = parts[0]
                results.append({
                    "serial": serial,
                    "source": "adb",
                    "model": "unknown",
                })
    except Exception:
        pass

    return results