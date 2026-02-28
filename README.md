# glasses-radar

> Passive BLE scanner that detects smart glasses radio fingerprints

`glasses-radar` is a command-line security tool that passively monitors nearby Bluetooth Low Energy (BLE) advertisements to detect the radio fingerprint signatures of smart glasses such as Meta Ray-Bans and similar devices. It runs entirely passively — no pairing or active connection to any device is required.

## ⚠️ Intended Use

This tool is designed for **privacy-conscious individuals** who want to be alerted when smart glasses capable of recording audio or video are nearby. It is intended for **personal, defensive use only**. Do not use this tool to harass, stalk, or surveil individuals.

---

## Features

- 🔍 **Passive BLE scanning** — no pairing, no active connections
- 📋 **JSON fingerprint database** — easy to extend with new device signatures
- 📊 **Rich terminal dashboard** — real-time display with signal strength bars
- 🔔 **Configurable RSSI proximity thresholds** — set your own distance alert radius
- ⏱️ **Per-device alert cooldown** — no alert spam
- 🏷️ **Match confidence scoring** — see how confident the detection is

---

## Supported Devices

| Device | Manufacturer | Status |
|---|---|---|
| Meta Ray-Ban Smart Glasses (Gen 2) | Meta / EssilorLuxottica | ✅ Supported |
| Ray-Ban Stories (Gen 1) | Meta / EssilorLuxottica | ✅ Supported |
| Bose Frames Alto | Bose Corporation | ✅ Supported |
| Bose Frames Rondo | Bose Corporation | ✅ Supported |
| Bose Frames Tenor / Soprano | Bose Corporation | ✅ Supported |
| TCL NXTWEAR S | TCL Communication | ✅ Supported |
| TCL NXTWEAR G | TCL Communication | ✅ Supported |
| Amazon Echo Frames | Amazon | ✅ Supported |
| Vuzix Blade | Vuzix Corporation | ✅ Supported |
| Snap Spectacles (v3) | Snap Inc. | ✅ Supported |
| INMO Air | INMO | ✅ Supported |

---

## Installation

### Requirements

- Python 3.10+
- Linux or macOS
- Bluetooth adapter with BLE support
- On Linux: `bluez` stack and appropriate permissions

### From PyPI (once published)

```bash
pip install glasses-radar
```

### From source

```bash
git clone https://github.com/example/glasses-radar.git
cd glasses-radar
pip install -e .
```

### Linux Bluetooth permissions

On Linux you may need to grant your user Bluetooth scan permissions, or run with `sudo`:

```bash
# Option 1: run with sudo
sudo glasses-radar

# Option 2: grant capabilities to the Python interpreter
sudo setcap cap_net_raw,cap_net_admin+eip $(readlink -f $(which python3))
```

---

## Usage

```
usage: glasses-radar [-h] [-d DURATION] [-r RSSI] [-v] [--db DB] [--list-devices]
                     [--cooldown COOLDOWN] [--no-sound] [--log LOG]

Passive BLE scanner for smart glasses detection

options:
  -h, --help            show this help message and exit
  -d, --duration DURATION
                        Scan duration in seconds (0 = run forever) [default: 0]
  -r, --rssi RSSI       RSSI threshold in dBm for proximity alerts [default: -70]
  -v, --verbose         Enable verbose output showing all BLE devices
  --db DB               Path to custom fingerprints JSON database
  --list-devices        List all known fingerprinted devices and exit
  --cooldown COOLDOWN   Alert cooldown per device in seconds [default: 30]
  --no-sound            Disable terminal bell on detection
  --log LOG             Write detection events to a JSON log file
```

### Examples

```bash
# Scan indefinitely with default settings
glasses-radar

# Scan for 60 seconds with tighter proximity threshold (~2-3 metres)
glasses-radar --duration 60 --rssi -60

# Verbose mode — shows all BLE devices, not just matches
glasses-radar --verbose

# Use a custom fingerprint database
glasses-radar --db ~/my_fingerprints.json

# List all supported devices
glasses-radar --list-devices

# Log detections to a file for later review
glasses-radar --log /tmp/detections.json
```

---

## Understanding RSSI Thresholds

RSSI (Received Signal Strength Indicator) is measured in dBm (decibels relative to 1 milliwatt). The values are always **negative** — the closer to zero, the stronger the signal.

| RSSI Range | Approximate Distance | Suggested Use |
|---|---|---|
| -40 to -50 dBm | ~1 metre (same room) | Very close detection only |
| -55 to -65 dBm | ~2-5 metres | Typical indoor range |
| -70 to -75 dBm | ~5-10 metres | Default; good balance |
| -80 to -90 dBm | >10 metres | Long range; more false positives |

Set the RSSI threshold with `--rssi`. Devices with a measured RSSI **above** (closer to zero than) the threshold will trigger an alert.

---

## Fingerprint Database

Fingerprints are stored in `data/fingerprints.json`. Each entry follows this schema:

```json
{
  "id": "unique-device-id",
  "name": "Human-Readable Device Name",
  "vendor": "Manufacturer Name",
  "notes": "Optional notes about this fingerprint",
  "enabled": true,
  "manufacturer_ids": [1234],
  "service_uuids": [
    "0000180a-0000-1000-8000-00805f9b34fb"
  ],
  "name_patterns": [
    "DeviceName",
    "AltName"
  ],
  "rssi_threshold": -70,
  "confidence_weights": {
    "manufacturer_id": 50,
    "service_uuid": 30,
    "name_pattern": 40
  },
  "match_threshold": 40
}
```

### Field Descriptions

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string | ✅ | Unique identifier for this fingerprint |
| `name` | string | ✅ | Human-readable device name |
| `vendor` | string | ✅ | Manufacturer or vendor name |
| `notes` | string | ❌ | Optional notes about the signature |
| `enabled` | boolean | ✅ | Whether this fingerprint is active |
| `manufacturer_ids` | int[] | ✅ | BLE manufacturer ID values (16-bit) |
| `service_uuids` | string[] | ✅ | Advertised BLE service UUIDs (full 128-bit format) |
| `name_patterns` | string[] | ✅ | Substrings to match in the device local name |
| `rssi_threshold` | int | ✅ | Device-specific RSSI threshold override |
| `confidence_weights` | object | ✅ | Points awarded for each type of match |
| `match_threshold` | int | ✅ | Minimum confidence score to raise an alert |

### Adding a New Device

1. Open `data/fingerprints.json`
2. Add a new object to the `fingerprints` array following the schema above
3. Run `glasses-radar --list-devices` to verify it loaded correctly

To find the manufacturer ID and service UUIDs for a device:
- On Linux: use `btmgmt` or `hcitool lescan` with `btmon`
- On macOS: use `LightBlue` or `nRF Connect` apps
- On Android/iOS: use `nRF Connect for Mobile`

---

## Output Format

When a device is detected, `glasses-radar` displays a detection card:

```
╔════════════════════════════════════════════════════════════════╗
║  🚨 SMART GLASSES DETECTED                                    ║
╠════════════════════════════════════════════════════════════════╣
║  Device:      Meta Ray-Ban Smart Glasses (Gen 2)              ║
║  Address:     AA:BB:CC:DD:EE:FF                               ║
║  RSSI:        -62 dBm  ████████░░  Strong                     ║
║  Confidence:  80%                                             ║
║  Matched:     manufacturer_id, name_pattern                   ║
║  Time:        2024-01-15 14:32:01                             ║
╚════════════════════════════════════════════════════════════════╝
```

### JSON Log Format

When `--log` is specified, each detection event is appended as a JSON line:

```json
{"timestamp": "2024-01-15T14:32:01.234567", "address": "AA:BB:CC:DD:EE:FF", "name": "RayBan-1234", "fingerprint_id": "meta-ray-ban-gen2", "fingerprint_name": "Meta Ray-Ban Smart Glasses (Gen 2)", "rssi": -62, "confidence": 80, "matched_fields": ["manufacturer_id", "name_pattern"]}
```

---

## Privacy & Legal

- This tool only **receives** BLE advertisement packets that are publicly broadcast by devices.
- It does **not** connect to, pair with, or interact with any device.
- BLE advertisements are broadcast in the clear and are receivable by any standard Bluetooth adapter.
- Laws regarding passive radio monitoring vary by jurisdiction. Ensure compliance with your local laws before use.

---

## Contributing

Contributions are welcome! If you have identified the BLE fingerprint of a smart glasses device not yet in the database, please open a pull request adding it to `data/fingerprints.json`.

---

## License

MIT License. See [LICENSE](LICENSE) for details.
