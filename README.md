# WiFi CSI Motion Sensing

Device-free motion detection using WiFi Channel State Information (CSI), built on a Raspberry Pi 4's onboard Broadcom BCM43455c0 radio and the [Nexmon CSI](https://github.com/seemoo-lab/nexmon_csi) firmware patch. No cameras, no dedicated sensors — motion is inferred from how it disturbs the multipath WiFi environment between the Pi and a router (TP-Link Archer XE5300).

## How it works

The Pi's WiFi radio, patched with Nexmon CSI, extracts per-subcarrier amplitude and phase data from every WiFi frame it hears. A moving body (or object) in the signal path perturbs these reflections; a static room does not. `csi_motion_detect.py` computes a rolling variance across subcarriers, compares it against a calibrated baseline, and flags motion when the deviation crosses a threshold.

See [`ARCHITECTURE.md`](./ARCHITECTURE.md) for the full system design and [`WHITEPAPER.md`](./WHITEPAPER.md) for the underlying theory, related work, and roadmap toward drone detection.

## Hardware

- Raspberry Pi 4 (Broadcom BCM43455c0 onboard WiFi — required for Nexmon CSI compatibility)
- TP-Link Archer XE5300 (or any router/AP the Pi associates with)
- Pi power + storage (SD card or USB boot)
- Optional: second USB WiFi adapter, if you want the Pi to stay network-connected while its onboard radio is in monitor/CSI mode

## Software setup

1. Flash **Raspberry Pi OS Lite (32-bit)** — match kernel version to what the Nexmon CSI install script expects.
2. Install [Nexmon CSI](https://github.com/seemoo-lab/nexmon_csi) for the Pi 4 (`bcm43455c0` target). This patches the WiFi firmware to extract CSI on a channel you specify.
3. Clone this repo and install Python dependencies:

   ```bash
   pip install -r requirements.txt
   ```

4. Tune `NUM_SUBCARRIERS` and `HEADER_LEN` in `csi_motion_detect.py` to match your specific Nexmon build's output format (check `nexmon_csi/utils/python/csiread.py` in the Nexmon repo against your captured frames if parsing looks off).

## Usage

**Live detection**, listening for CSI frames Nexmon streams over UDP:

```bash
python3 csi_motion_detect.py --udp-port 5500
```

**Offline replay**, against a captured pcap file (useful for tuning without re-running hardware):

```bash
python3 csi_motion_detect.py --pcap capture.pcap
```

Tunable flags:

| Flag | Default | Effect |
|---|---|---|
| `--window` | 20 | Rolling smoothing window (frames). Larger = smoother, slower to react. |
| `--threshold` | 3.0 | Std-devs above baseline required to flag motion. Lower = more sensitive, more false positives. |
| `--calib-frames` | 100 | Frames collected at startup (room should be still) to establish baseline. |
| `--ws-port` | none | Also broadcast every frame's result as JSON over a local WebSocket, for `radar_gui.html`. |

## Radar GUI

`radar_gui.html` is a standalone, self-contained page — open it directly in a browser (double-click, or `file://`), no server needed for itself.

- **Demo mode** (default on open): generates simulated readings so you can see the UI work with no hardware.
- **Live mode**: run the detector with `--ws-port 5600`, then enter `ws://<pi-ip>:5600` in the GUI's connection field and click **Connect**. Each detection frame streams in and renders as a radar blip.

```bash
python3 csi_motion_detect.py --udp-port 5500 --ws-port 5600
```

Note: the GUI's "angle" is a derived value (relative subcarrier energy balance), not a true angle-of-arrival measurement — a single antenna can't localize direction. It's there to give the radar something to plot spatially; treat blip position as illustrative, not as a bearing. Real AoA would need multiple antennas and phase-difference processing (a further extension, not implemented here).

## Status / Roadmap

- [x] CSI capture + variance-based motion detection (indoor, close range)
- [x] Radar-style live visualization (`radar_gui.html`, WebSocket-fed)
- [ ] Alerting (MQTT / push notification) on motion events
- [ ] Spectral (micro-Doppler) analysis to distinguish motion signatures — a step toward close-range drone detection
- [ ] Separate SDR-based RF sniffer for outdoor-range drone detection (different sensing modality — see white paper §5)

## Limitations

CSI-based sensing works within the WiFi link's multipath environment — practically, indoor ranges of 10–20m with reflective surfaces (walls, furniture) to work with. It does not extend outdoors and cannot, on its own, classify *what* moved (person vs. pet vs. object) without further signal processing.
# WIFI-CSI
