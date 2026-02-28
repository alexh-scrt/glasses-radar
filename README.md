# glasses-radar 👓📡

> Know when smart glasses are watching — before they press record.

`glasses-radar` is a command-line privacy tool that passively monitors nearby Bluetooth Low Energy (BLE) advertisements to detect smart glasses such as Meta Ray-Bans, Bose Frames, and TCL NXTWEAR. It runs entirely passively — no pairing or active connection to any device is required. When a known device signature is detected within your configured proximity threshold, you get an immediate, configurable alert.

> ⚠️ **Intended Use:** This tool is designed for privacy-conscious individuals who want to know when recording-capable smart glasses are nearby. It is intended for personal, defensive use only.

---

## Quick Start

```bash
# Install from PyPI
pip install glasses-radar

# Run a continuous scan with default settings
glasses-radar

# Scan for 60 seconds, alert when a device is within ~3m (-60 dBm)
glasses-radar --duration 60 --rssi -60

# List all fingerprints in the database
glasses-radar --list-devices
```

Requires Python 3.10+ and a system Bluetooth adapter. On Linux, you may need to run with `sudo` or grant the `CAP_NET_RAW` capability.

---

## Features

- 🔍 **Passive-only scanning** — zero pairing, zero active connections; completely invisible to target devices
- 📋 **JSON fingerprint database** — matches on manufacturer ID, service UUIDs, and device name patterns; trivially extensible
- 📊 **Rich terminal dashboard** — live signal strength bars, match confidence scores, and per-device detection history
- 🔔 **Configurable RSSI proximity radius** — tune the distance threshold (e.g. `-60 dBm` ≈ 3 m, `-80 dBm` ≈ 10 m) with per-device cooldown to suppress alert spam
- 🧩 **Community-extensible fingerprints** — add new smart glasses signatures with a single JSON entry; no code changes needed

---

## Usage Examples

### Basic continuous scan

```bash
glasses-radar
```

### Set a tight proximity threshold and enable verbose output

```bash
glasses-radar --rssi -60 --verbose
```

### Time-limited scan with JSON detection log

```bash
glasses-radar --duration 120 --log /tmp/detections.json
```

### Use a custom fingerprint database

```bash
glasses-radar --db ~/my_fingerprints.json
```

### List all known device signatures

```bash
glasses-radar --list-devices
```

```
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━┓
┃ Device                           ┃ Vendor                ┃ Enabled ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━┩
│ Meta Ray-Ban Smart Glasses Gen 2 │ Meta / EssilorLuxottica│ ✓       │
│ Bose Frames                      │ Bose Corporation       │ ✓       │
│ TCL NXTWEAR                      │ TCL                    │ ✓       │
└──────────────────────────────────┴───────────────────────┴─────────┘
```

### Detection alert (example terminal output)

```
🚨 SMART GLASSES DETECTED
──────────────────────────────────────────
 Device   : Meta Ray-Ban Smart Glasses (Gen 2)
 Vendor   : Meta / EssilorLuxottica
 Address  : AA:BB:CC:DD:EE:FF
 RSSI     : -58 dBm  ████████░░  (~2–3 m)
 Confidence: 91%     █████████░
 Matched  : manufacturer_id, service_uuid, name_pattern
 Time     : 2024-06-15 14:32:07
──────────────────────────────────────────
```

### All CLI options

```
usage: glasses-radar [-h] [--rssi RSSI] [--duration DURATION]
                     [--cooldown COOLDOWN] [--db DB] [--log LOG]
                     [--list-devices] [--verbose] [--no-sound] [--version]

options:
  -h, --help           show this help message and exit
  --rssi RSSI          RSSI threshold in dBm (default: -70). Devices with
                       weaker signal are ignored. E.g. -60 ≈ 3 m, -80 ≈ 10 m.
  --duration DURATION  Scan duration in seconds. Omit to scan indefinitely.
  --cooldown COOLDOWN  Seconds between repeat alerts for the same device
                       (default: 30).
  --db DB              Path to a custom fingerprints JSON file.
  --log LOG            Append JSON detection events to this file.
  --list-devices       Print all fingerprints in the database and exit.
  --verbose            Show all nearby BLE devices, not just matches.
  --no-sound           Disable terminal bell on detection.
  --version            Show version and exit.
```

---

## Adding a New Device Fingerprint

Open `data/fingerprints.json` and append an entry to the `fingerprints` array:

```json
{
  "id": "acme-smartglasses-v1",
  "name": "Acme Smart Glasses v1",
  "vendor": "Acme Corp",
  "notes": "First generation, released 2024.",
  "enabled": true,
  "manufacturer_ids": [0x05AC],
  "service_uuids": [
    "0000fe2c-0000-1000-8000-00805f9b34fb"
  ],
  "name_patterns": [
    "^AcmeGlass",
    "^ACMEG"
  ],
  "rssi_threshold": -75
}
```

All fields except `id`, `name`, and `enabled` are optional — any combination of `manufacturer_ids`, `service_uuids`, and `name_patterns` contributes to the match confidence score.

---

## Project Structure

```
glasses-radar/
├── pyproject.toml              # Project metadata, deps, CLI entry point
├── README.md
├── data/
│   └── fingerprints.json       # BLE fingerprint database (Meta, Bose, TCL, …)
├── glasses_radar/
│   ├── __init__.py             # Package init, version constant
│   ├── main.py                 # CLI entry point (argparse)
│   ├── scanner.py              # Async BLE scanner (Bleak)
│   ├── matcher.py              # Fingerprint matching & confidence scoring
│   ├── fingerprints.py         # JSON database loader & lookup helpers
│   ├── alerter.py              # Rich terminal alerts & JSON log output
│   └── models.py               # Dataclasses: BLEDeviceSnapshot, Fingerprint, DetectionEvent
└── tests/
    ├── test_matcher.py
    ├── test_fingerprints.py
    ├── test_alerter.py
    ├── test_scanner.py
    ├── test_models.py
    └── test_main.py
```

---

## Configuration

### CLI flags (runtime)

| Flag | Default | Description |
|---|---|---|
| `--rssi` | `-70` | RSSI threshold (dBm). Weaker devices ignored. `-60` ≈ 3 m. |
| `--duration` | ∞ | Stop scanning after N seconds. |
| `--cooldown` | `30` | Seconds before re-alerting on the same device address. |
| `--db` | bundled | Path to a custom `fingerprints.json`. |
| `--log` | none | Append newline-delimited JSON events to a file. |
| `--no-sound` | off | Suppress terminal bell (audible beep) on detection. |
| `--verbose` | off | Log all nearby BLE devices, not just fingerprint matches. |

### Per-fingerprint overrides (`data/fingerprints.json`)

| Field | Type | Description |
|---|---|---|
| `rssi_threshold` | int | Override the global RSSI threshold for this device only. |
| `enabled` | bool | Set to `false` to disable a fingerprint without deleting it. |

### Confidence weights

The matcher scores each detection across four signal types. You can tune the weights by subclassing `FingerprintMatcher` or passing a custom `ConfidenceWeights` instance:

```python
from glasses_radar.models import ConfidenceWeights
from glasses_radar.matcher import FingerprintMatcher
from glasses_radar.fingerprints import FingerprintDatabase

weights = ConfidenceWeights(
    manufacturer_id=0.40,
    service_uuid=0.35,
    name_pattern=0.20,
    rssi=0.05,
)
db = FingerprintDatabase.load()
matcher = FingerprintMatcher(db, confidence_weights=weights)
```

---

## Platform Notes

| OS | Status | Notes |
|---|---|---|
| Linux | ✅ Supported | May require `sudo` or `CAP_NET_RAW` on the Python binary. |
| macOS | ✅ Supported | Bluetooth permission prompt on first run. |
| Windows | ⚠️ Untested | Bleak supports Windows; contributions welcome. |

---

## License

MIT © glasses-radar contributors. See [LICENSE](LICENSE) for details.

---

*Built with [Jitter](https://github.com/jitter-ai) — an AI agent that ships code daily.*
