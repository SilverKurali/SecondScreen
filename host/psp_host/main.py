"""PSP Host entry point."""

import logging

from .config import parse_args
from .capture import get_encoder_info, list_x11_outputs
from .server import run_server


def main():
    """Main entry point."""
    args = parse_args()

    # Graphical mode: launch GTK4 control panel
    if getattr(args, "gui", False):
        from .gui import run_gui
        raise SystemExit(run_gui())

    # Self-test mode: verify bundled runtime integrity (used by CI packaging)
    if getattr(args, "selftest", False):
        from .selftest import run_selftest
        raise SystemExit(run_selftest())

    # Setup logging
    level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # List outputs mode
    if args.list_outputs:
        outputs = list_x11_outputs()
        print("Available X11 outputs:")
        for o in outputs:
            print(f"  {o}")
        print()
        print("Available encoders:")
        for name, codec, element, available in get_encoder_info():
            status = "✓" if available else "✗"
            print(f"  {status} {name} ({element}) → {codec}")
        return

    # List devices mode
    if args.list_devices:
        from .discovery import list_devices
        print("🔍 Scanning for PSP hosts and ADB devices ...")
        print()
        devices = list_devices(timeout=3)
        if not devices:
            print("  No devices found.")
            print()
            print("  Tips:")
            print("  - For LAN: make sure PSP Host is running on the target PC")
            print("  - For ADB: connect device via USB or 'adb connect <ip>:5555'")
            return

        lan_devices = [d for d in devices if d.get("source") == "lan"]
        adb_devices = [d for d in devices if d.get("source") == "adb"]

        if lan_devices:
            print(f"📡 LAN hosts ({len(lan_devices)}):")
            for d in lan_devices:
                ips = ", ".join(d.get("ips", []))
                print(f"  {d['hostname']} ({ips}:{d['port']})")
            print()

        if adb_devices:
            print(f"📱 ADB devices ({len(adb_devices)}):")
            for d in adb_devices:
                transport = "无线" if ":" in d.get("serial", "") else "USB"
                print(f"  {d['serial']} ({transport})")
            print()

        return

    # Print summary
    print("PSP Host v0.5.1")
    print(f"  Stream: {args.width}x{args.height} @ {args.fps} fps")
    print(f"  Bitrate: {args.bitrate_kbps} kbps")
    print(f"  Port: {args.port}")
    print(f"  ADB mode: {args.adb_mode}")
    if args.output:
        print(f"  Output: {args.output}")
    if args.region:
        print(f"  Region: {args.region}")
    if not args.no_input:
        print("  Input: enabled (touch → mouse)")
    print()

    run_server(args)


if __name__ == "__main__":
    main()
