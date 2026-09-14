# Spigen Audio CTRL

A native Arch Linux desktop controller for the **Spigen SA-HP P10** headphones. It uses GTK 4 and talks directly to BlueZ over D-Bus—no Wine, Electron, or Windows runtime is involved.

## Features

- Six ANC profiles: Deep, Adaptive, Commuting, Anti-Wind, Transparency, and Off
- Low-latency Gaming Mode, including live state synchronization
- 10-band hardware DSP EQ with ±8 dB controls, an editable response graph, Harman target overlay, presets, and custom preset slots
- Remapping for MFB single/double/triple click and volume-button single/long press
- BLE discovery by device name, GATT service discovery, notifications, clean disconnect, and automatic reconnect
- Battery level from both BlueZ's standard Battery API and the headphone RCSP response
- Device name, firmware, gaming mode, and onboard key-map parsing

All settings are sent to the headphone hardware with the same RCSP packets as the Windows application. EQ and button assignments are stored by the headphones, not applied as a Linux audio effect.

## Screenshots

| Noise control | Equalizer | Button remapping |
|---|---|---|
| ![Noise control](docs/screenshots/noise-control.png) | ![Equalizer](docs/screenshots/equalizer.png) | ![Button remapping](docs/screenshots/button-remap.png) |

## Arch Linux requirements

Install the runtime dependencies and enable Bluetooth:

```bash
sudo pacman -S --needed bluez bluez-utils gtk4 python python-cairo python-dbus python-gobject
sudo systemctl enable --now bluetooth.service
```

Pair the headphones once through your desktop Bluetooth settings or with `bluetoothctl`. The app can discover and connect to an already-known or nearby P10 device.

## Run from the repository

```bash
./spigen-audio-ctrl
```

No Python virtual environment or downloaded PyPI package is required on Arch; all dependencies come from the official repositories.

## Install

```bash
make test
sudo make install
```

Then launch **Spigen Audio CTRL** from the desktop application menu or run `spigen-audio-ctrl`.

For an Arch package, push/tag the source as `v2.0.0`, then run:

```bash
makepkg -si
```

The included `PKGBUILD` builds from that release tag.

## Troubleshooting

- **No Bluetooth adapter found:** verify `systemctl status bluetooth` and `rfkill list bluetooth`.
- **Headphones not found:** turn them on, disconnect them from another controlling phone, and pair once with `bluetoothctl`.
- **GATT services did not resolve:** reconnect the headphones, or remove and pair them again. BlueZ must expose the vendor BLE service in addition to the audio profile.
- **D-Bus access denied:** run the app as your normal desktop user, not with `sudo`; ensure your login session has normal BlueZ/Polkit access.
- Protocol and transport activity is printed to stdout when launched from a terminal.

## Development and tests

```bash
python3 -m pytest -q
python3 -m py_compile spigen_audio_ctrl/*.py
```

The protocol suite checks every ANC packet, gaming mode, key remapping, the complete EQ binary layout, battery parsing, and hardware-state TLVs. A real P10 is only required for end-to-end GATT verification.

## Architecture

| Path | Role |
|---|---|
| `spigen_audio_ctrl/app.py` | Native GTK 4 UI and feature state |
| `spigen_audio_ctrl/bluetooth.py` | BlueZ discovery, connection, GATT I/O, notifications, reconnect |
| `spigen_audio_ctrl/protocol.py` | Platform-independent RCSP packet encoding and parsing |
| `tests/test_protocol.py` | Hardware-protocol regression tests |
| `packaging/io.github.spigen.AudioCTRL.desktop` | Desktop-menu entry |
| `Makefile`, `PKGBUILD` | Local and Arch Linux installation |

## Protocol

The SA-HP P10 uses proprietary RCSP frames over a vendor BLE GATT service:

```text
FE DC BA [flags] [opcode] [len_hi] [len_lo] [sequence] [payload...] EF
```

- Write characteristic: UUID `AE01` / `0001`
- Notify characteristic: UUID `AE02` / `0002`
- Standard battery service: UUID `180F`, characteristic `2A19`

## Disclaimer and license

This is an independent, unofficial project and is not affiliated with Spigen Global Co., Ltd.

[MIT](LICENSE) — Copyright (c) 2026 Safan Sulfikar
# Spigen-Audio-CTRL-Linux
