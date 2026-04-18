#!/usr/bin/env python3
"""G522 Centurion 0x50 — direktes HID-Test-Script.

Liest Notifications (Connect/Disconnect, Battery, Mute, LED) und kann aktiv die
Battery abfragen sowie die Mic-LED steuern (experimentell).

Usage:
  python3 g522_test.py [--device /dev/hidrawX] [--query-battery] [--listen]
  python3 g522_test.py --set-led on|off
"""

import argparse
import struct
import sys
import time

DEVICE = "/dev/hidraw8"

# Centurion Rahmenformat (0x50 / G522):
# [0x50] [device_addr=0x23] [cpl_length] [flags=0x00] [payload...]
# Nach dem Unwrapping:
# 0x11 0xFF [sub_id] [address] [data...]
CENTURION_REPORT_ID = 0x50
DEVICE_ADDR = 0x23
FRAME_SIZE = 64
HIDPP_LONG = 0x11


def unwrap(raw: bytes) -> bytes | None:
    """Centurion 0x50-Frame → HID++ Long Message."""
    if len(raw) < 5 or raw[0] != CENTURION_REPORT_ID or raw[1] != DEVICE_ADDR:
        return None
    cpl_length = raw[2]
    # flags = raw[3]  # always 0x00
    inner = raw[4 : 3 + cpl_length]   # cpl_length - 1 bytes (skip flags)
    msg = bytes([HIDPP_LONG, 0xFF]) + inner
    # Pad to 20 bytes
    if len(msg) < 20:
        msg = msg + b"\x00" * (20 - len(msg))
    return msg


def build_query(payload: bytes) -> bytes:
    """Baut einen Centurion 0x50 Output-Frame für ein gegebenes Payload."""
    cpl_length = len(payload) + 1   # +1 für den flags-Byte
    frame = bytes([CENTURION_REPORT_ID, DEVICE_ADDR, cpl_length, 0x00]) + payload
    return frame + b"\x00" * (FRAME_SIZE - len(frame))


def decode_notification(data: bytes) -> str:
    """Gibt eine lesbare Beschreibung der Notification zurück."""
    if len(data) < 3:
        return f"zu kurz: {data.hex()}"

    sub_id  = data[0]
    address = data[1]

    # Connection / Disconnect
    if sub_id == 0x05 and address == 0x10:
        status = "VERBUNDEN" if data[2] == 0x01 else "GETRENNT"
        return f"[VERBINDUNG] {status}  (raw: {data[:4].hex(' ')})"

    # Proxy-Notifications (sub_id=0x03, address=0x10): Battery, Mute, LED
    if sub_id == 0x03 and address == 0x10 and len(data) >= 8:
        inner_dev  = data[5]
        inner_feat = data[6]

        # Battery (inner_dev=0x05)
        if inner_dev == 0x05 and len(data) >= 10:
            bat_pct    = data[8]
            charge_raw = data[9]
            charge_str = {0x00: "entlädt", 0x01: "lädt", 0x02: "voll"}.get(charge_raw, f"0x{charge_raw:02x}")
            feat_str   = {0x00: "Lade-Event", 0x0d: "Battery-Response", 0x0f: "Initial-Battery"}.get(inner_feat, f"feat=0x{inner_feat:02x}")
            return f"[BATTERIE] {bat_pct}%  Status: {charge_str}  ({feat_str})  raw: {data[:10].hex(' ')}"

        # Mute / LED (inner_dev=0x15)
        if inner_dev == 0x15:
            state = data[7]
            if inner_feat == 0x00:
                status = "GEMUTET" if state == 0x01 else "ENTMUTET"
                return f"[MUTE] {status}  raw: {data[:8].hex(' ')}"
            if inner_feat == 0x10:
                status = "AN" if state == 0x01 else "AUS"
                return f"[MIC-LED] {status}  raw: {data[:8].hex(' ')}"

    return f"[UNBEKANNT] sub=0x{sub_id:02x} addr=0x{address:02x}  {data[:12].hex(' ')}"


def query_battery(fd) -> None:
    """Sendet Battery-Query und wartet auf Antwort."""
    # Outer: dev=0x03, feat=0x1d; Inner: dev=0x05, feat=0x0d (battery, keine Args)
    # Ergibt: 50 23 08 00 03 1d 00 03 00 05 0d [zeros...]
    inner_payload = bytes([0x03, 0x1d, 0x00, 0x03, 0x00, 0x05, 0x0d])
    frame = build_query(inner_payload)
    print(f"→ Sende Battery-Query: {frame[:12].hex(' ')} ...")

    import os
    os.write(fd, frame)

    print("  Warte auf Antwort (500ms)...")
    deadline = time.monotonic() + 0.5
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        # nicht-blockierendes read mit select
        import select
        r, _, _ = select.select([fd], [], [], remaining)
        if not r:
            break
        raw = os.read(fd, FRAME_SIZE)
        if not raw:
            continue
        msg = unwrap(raw)
        if msg is None:
            print(f"  (kein Centurion-Frame: {raw[:8].hex(' ')})")
            continue
        notification = msg[2:]
        desc = decode_notification(notification)
        print(f"← {desc}")
        # Wenn es die Battery-Response ist, fertig
        if (notification[0] == 0x03 and notification[1] == 0x10
                and len(notification) >= 10
                and notification[5] == 0x05 and notification[6] == 0x0d):
            return
    print("  Timeout — keine Battery-Response erhalten.")


def listen(fd, duration: float = 30.0) -> None:
    """Lauscht auf alle Centurion-Notifications."""
    import os, select
    print(f"Lausche {duration:.0f}s auf Notifications von /dev/hidraw* ...")
    print("(Schalte das Headset an/aus, stecke Ladekabel ein/aus)\n")
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        r, _, _ = select.select([fd], [], [], min(remaining, 1.0))
        if not r:
            continue
        raw = os.read(fd, FRAME_SIZE)
        if not raw:
            continue
        ts = time.strftime("%H:%M:%S")
        msg = unwrap(raw)
        if msg is None:
            # Nicht-Centurion (z.B. Audio-HID)
            print(f"[{ts}] (non-centurion: {raw[:8].hex(' ')})")
            continue
        notification = msg[2:]
        desc = decode_notification(notification)
        print(f"[{ts}] {desc}")


def _send_and_read(fd, inner_payload: bytes, label: str, timeout: float = 0.5) -> list[bytes]:
    """Sendet einen Centurion-Frame und sammelt alle Antworten."""
    import os, select
    frame = build_query(inner_payload)
    print(f"→ {label}: {frame[:len(inner_payload)+4].hex(' ')} ...")
    os.write(fd, frame)
    print(f"  Warte auf Antwort ({timeout*1000:.0f}ms)...")
    responses = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        r, _, _ = select.select([fd], [], [], remaining)
        if not r:
            break
        raw = os.read(fd, FRAME_SIZE)
        if not raw:
            continue
        msg = unwrap(raw)
        if msg is None:
            print(f"  (non-centurion: {raw[:8].hex(' ')})")
            continue
        notification = msg[2:]
        desc = decode_notification(notification)
        print(f"← {desc}  raw: {notification[:10].hex(' ')}")
        responses.append(notification)
    if not responses:
        print("  Timeout — keine Antwort.")
    return responses


def set_led(fd, state: bool) -> None:
    """Versucht die Mic-LED zu setzen — probiert mehrere Funktions-Varianten."""
    state_byte = 0x01 if state else 0x00
    label = "AN" if state else "AUS"

    # Versuch 1: inner_feat=0x10 (get/notify → wahrscheinlich read-only, aber testen)
    print(f"\n[Versuch 1] inner_feat=0x10 (bekannter Notify-Kanal)")
    _send_and_read(fd, bytes([0x03, 0x1d, 0x00, 0x03, 0x00, 0x15, 0x10, state_byte]),
                   f"LED-{label} feat=0x10")

    # Versuch 2: inner_feat=0x20 (nächste Funktion = typischerweise "set" in HID++)
    print(f"\n[Versuch 2] inner_feat=0x20 (vermutliche Set-Funktion)")
    _send_and_read(fd, bytes([0x03, 0x1d, 0x00, 0x03, 0x00, 0x15, 0x20, state_byte]),
                   f"LED-{label} feat=0x20")

    # Versuch 3: inner_feat=0x00 mit state (Mute-Feature, vielleicht LED-Control dabei)
    print(f"\n[Versuch 3] inner_feat=0x00 (Mute-Feature mit state-Byte)")
    _send_and_read(fd, bytes([0x03, 0x1d, 0x00, 0x03, 0x00, 0x15, 0x00, state_byte]),
                   f"LED-{label} feat=0x00")


def main():
    parser = argparse.ArgumentParser(description="G522 Centurion Test-Tool")
    parser.add_argument("--device", default=DEVICE, help=f"HID-Device (default: {DEVICE})")
    parser.add_argument("--query-battery", action="store_true", help="Battery einmalig abfragen")
    parser.add_argument("--set-led", choices=["on", "off"], help="Mic-LED setzen (experimentell)")
    parser.add_argument("--listen", action="store_true", help="Notifications lauschen (30s)")
    parser.add_argument("--duration", type=float, default=30.0, help="Lausch-Dauer in Sekunden")
    args = parser.parse_args()

    if not args.query_battery and not args.listen and not args.set_led:
        args.listen = True   # Default: lauschen

    import os
    try:
        fd = os.open(args.device, os.O_RDWR)
    except PermissionError:
        print(f"Fehler: Keine Rechte für {args.device}. Als root ausführen oder udev-Regel prüfen.")
        sys.exit(1)
    except FileNotFoundError:
        print(f"Fehler: {args.device} nicht gefunden.")
        sys.exit(1)

    print(f"Geöffnet: {args.device}\n")

    try:
        if args.query_battery:
            query_battery(fd)
        if args.set_led:
            set_led(fd, args.set_led == "on")
        if args.listen:
            listen(fd, args.duration)
    finally:
        os.close(fd)


if __name__ == "__main__":
    main()
