# G522 Centurion 0x50 — Protocol Reverse Engineering Notes

Reverse-engineered via USB HID sniffing (Wireshark + USBPcap on Windows with G Hub running).

## Hardware

| What | Value |
|------|-------|
| Dongle PID | `0x0B18` |
| HID device | `/dev/hidrawX` (Control-Interface, nicht Audio) |
| Centurion Report ID | `0x50` |
| G522 device address | `0x23` (outer frame), `0x05` (inner proxy) |

## Frame Format

### Host → Dongle (Output)

```
[0x50] [device_addr=0x23] [cpl_length] [flags=0x00] [payload...]
padding to 64 bytes
```

`cpl_length` = `len(payload) + 1` (zählt das flags-Byte mit).

### Dongle → Host (Input / Notifications)

Gleicher Frame-Wrapper, dann unwrappen zu HID++ Long:

```
[0x50] [0x23] [cpl_length] [0x00] [inner_payload...]
→ unwrapped: [0x11] [0xFF] [inner_payload...]
```

Der innere Payload beginnt dann mit `sub_id` / `address` wie normales HID++ 2.0.

## Proxy / Relay-Mechanismus

Der Dongle fungiert als Proxy zwischen Host und Headset:

- **Outer dev** `0x03`, **feat** `0x1d` → Host schickt Query an Dongle
- **Outer dev** `0x03`, **feat** `0x10` → Antworten/Notifications kommen rein

In den inner bytes steckt das eigentliche Ziel:
- `inner_dev = 0x05` = G522 Headset selbst

## Known Notifications

### Connection Status (`sub_id=0x05, address=0x10`)

```
05 10 01 00  → Headset connected (eingeschaltet / in range)
05 10 00 00  → Headset disconnected (ausgeschaltet / außer Reichweite)
```

Erscheint zuverlässig beim Ein- und Ausschalten.

Kurz vor diesen Events kommt oft ein `03 00 ...`-Paket (internes Dongle-Event, ignorieren).

### Battery Notification (`sub_id=0x03, address=0x10`)

Spontane Notifications bei Ladestandsänderungen (Kabel ein/aus):

```
03 10 [??] [??] [??] [inner_dev=0x05] [inner_feat] [func] [bat_pct] [charge_raw]
```

| `inner_feat` | Bedeutung |
|---|---|
| `0x00` | Lade-Event (spontane Notification bei Kabelwechsel) |
| `0x0d` | Battery-Response (Antwort auf aktiven Query) |
| `0x0f` | Initial-Battery (beim Verbinden gesendet) |

| `charge_raw` | Status |
|---|---|
| `0x00` | Entlädt (kein Kabel) |
| `0x01` | Lädt aktiv |
| `0x02` | Trickle/Charge-Complete (\*) |

(\*) `0x02` erscheint kurz (~1s) nach `0x01` auch bei nicht-vollem Akku — vermutlich Firmware-Quirk.
Für praktische Zwecke: `0x01` und `0x02` = Kabel eingesteckt.

## Active Battery Query

Query an Dongle senden:

```
inner_payload = bytes([0x03, 0x1d, 0x00, 0x03, 0x00, 0x05, 0x0d])
frame = [0x50, 0x23, len(inner_payload)+1, 0x00] + inner_payload + padding
```

Vollständiger 64-Byte-Frame: `50 23 08 00 03 1d 00 03 00 05 0d 00 00 ...`

Antwort (Battery-Response, `inner_feat=0x0d`):

```
03 10 00 06 00 05 0d [func] [bat_pct] [charge_raw]
```

`bat_pct` ist Dezimal-Prozentwert (z.B. `0x14` = 20%, `0x50` = 80%).

## Test-Script

`tools/g522_test.py` — direktes HID-Lesen ohne Solaar:

```bash
# Battery einmalig abfragen:
python3 tools/g522_test.py --device /dev/hidrawX --query-battery

# Notifications lauschen (Connect/Disconnect, Laden):
python3 tools/g522_test.py --device /dev/hidrawX --listen --duration 60
```

Braucht Read+Write-Zugriff auf das hidraw-Device (root oder udev-Regel).

## Sniff-Captures

Wireshark-Captures unter `/mnt/tower-nvme/solarr_wireshark/`:

| Datei | Inhalt |
|---|---|
| `headset_aus_wird_eingeschaltet.pcapng` | Voll-Handshake, Battery-Query bei Connect |
| `headset_ein_ladekabel_eingesteckt_ladekabel_ausgesteckt.pcapng` | Lade-Notifications |
| `headset_ein_wird_ausgeschaltet.pcapng` | Disconnect-Notification |
| `headset_ein_leiser_lauter.pcapng` | Lautstärke = reines USB Audio, keine Centurion-Pakete |
