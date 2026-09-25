# Device-Free Motion Sensing with Consumer WiFi Channel State Information

**A practitioner's white paper on CSI-based sensing, its indoor motion-detection application, and a feasibility assessment for drone detection**

---

## Abstract

Commodity WiFi hardware, without modification to routers or access points, can be repurposed as a passive motion sensor by extracting Channel State Information (CSI) from ordinary 802.11 traffic. This paper describes a working implementation on a Raspberry Pi 4 using the Nexmon CSI firmware patch, covers the physical principle that makes this possible, and assesses — with appropriate skepticism — how far the same technique can be pushed toward detecting small aerial objects such as drones. The conclusion is that CSI sensing is well-suited to indoor, close-range motion detection but is the wrong tool for outdoor drone detection; a companion RF-spectrum (SDR-based) approach is proposed for that use case.

## 1. Introduction

WiFi signals propagate not as a single direct path but as a superposition of many paths — direct line-of-sight plus reflections off walls, furniture, and any object in the environment (multipath propagation). Consumer WiFi radios must characterize this multipath channel to demodulate OFDM signals correctly, and in doing so compute exactly the data needed for sensing: per-subcarrier amplitude and phase, known as Channel State Information.

Standard WiFi firmware discards CSI after using it internally for equalization. Research projects — most notably the Nexmon CSI extractor for Broadcom chips — patch the firmware to expose this data to userspace, turning any Broadcom-equipped device (including the Raspberry Pi 4's onboard radio) into a CSI-capable sensor with no additional hardware.

This is the same underlying principle behind Comcast's recently deployed "WiFi Motion" feature on Xfinity gateways, and behind a substantial academic literature on device-free WiFi sensing (human activity recognition, fall detection, breathing-rate estimation, and gesture recognition) dating back over a decade.

## 2. Physical principle

When an object moves through a WiFi link's coverage area, it changes the multipath environment from one moment to the next: reflections lengthen or shorten, new paths appear or disappear, and the phase relationships between subcarriers shift. A static environment produces a stable CSI signature; a body in motion produces a time-varying one.

Critically, this is a **near-field, in-environment** phenomenon — it depends on the moving object being close enough to meaningfully perturb the reflective paths between transmitter and receiver, which for typical indoor WiFi power levels and antenna gain means single-digit to low-double-digit meters, and works best with walls and surfaces present to generate the reflections in the first place.

## 3. Implementation approach

The system described here (full architecture in the accompanying `ARCHITECTURE.md`) consists of:

1. **Sensing hardware**: Raspberry Pi 4 (Broadcom BCM43455c0 chip), no separate sensor required.
2. **Firmware layer**: Nexmon CSI, exposing per-frame, per-subcarrier CSI over UDP or pcap.
3. **Detection logic**: a rolling-variance detector — a deliberately simple baseline. Per-frame variance across subcarriers is smoothed over a short window and compared against a calibrated still-room baseline in standard-deviation units.

This is intentionally the simplest viable detector, not the most capable one. It establishes a working pipeline (capture → parse → detect → alert) that later stages — spectral analysis, ML classification — can be dropped into without re-architecting the system.

### 3.1 Why variance thresholding first

Variance thresholding requires no training data, no labeled examples, and no model — only a still-room calibration period. It is the WiFi-sensing equivalent of a PIR motion sensor: binary, fast, and good enough to validate that the pipeline works end to end before investing in more sophisticated signal processing.

### 3.2 Known limitations of this baseline

- Cannot distinguish *type* of motion (person vs. pet vs. object vs. HVAC airflow in extreme cases).
- Sensitive to environmental changes unrelated to the target (furniture moved, a door left open) — baseline drift requires recalibration.
- Single-Pi deployments give binary presence, not location; multi-node CSI fusion is required for anything like localization.

## 4. Related work

Device-free WiFi sensing has an active research base. Representative directions:

- **Human activity recognition**: classifying gait, falls, and gestures from CSI amplitude/phase time series, typically using spectral (Doppler/STFT) features rather than raw variance.
- **Vital-sign sensing**: breathing- and heartbeat-rate estimation from sub-millimeter chest-wall motion, exploiting CSI phase sensitivity.
- **Commercial deployment**: Comcast's Xfinity WiFi Motion (2026) is a productized version of the same principle at consumer scale, using multiple existing gateway/client links rather than a single dedicated sensor.
- **Drone/UAV detection via CSI**: a smaller body of academic work explores using CSI micro-Doppler signatures (from rotor-induced vibration) to detect drones at short range, distinguishing rotor signatures from human motion via frequency-domain features rather than amplitude variance alone.

## 5. Feasibility assessment: extending toward drone detection

Two genuinely different problems get conflated under "drone detection," and they call for different sensing modalities.

### 5.1 Close-range, indoor (CSI-compatible)

A drone hovering or flying within a few meters of the sensing link — indoors, or very near a window/opening — will perturb multipath reflections just as a person does, and its rotors introduce a higher-frequency, more periodic micro-Doppler signature than human gait. This is within reach of the existing pipeline, with two additions:

- Replace variance thresholding with **spectral analysis** (short-time Fourier transform or similar) of the CSI time series, to isolate the rotor-frequency band from the lower-frequency signature of human motion.
- Build or acquire labeled reference data (drone hover vs. person walking vs. empty room) to set classification thresholds, since rotor signatures are consistent enough across drone models to generalize reasonably well, per the academic literature cited above.

This is a natural, incremental extension of the current system — same hardware, same capture pipeline, more sophisticated detection stage.

### 5.2 Outdoor, standoff range (CSI-incompatible)

Detecting a drone at tens to hundreds of meters outdoors — the "is something flying near my property" use case — is **not** a good fit for CSI sensing. Two independent reasons:

- **Signal range and multipath**: WiFi CSI sensing relies on rich multipath (walls, furniture) to generate the reflections it measures. Open outdoor space has little of this, and consumer WiFi transmit power falls off well before drone standoff distances of interest.
- **Physics mismatch**: outdoor drone detection is better served by sensing modalities built for that regime — RF spectrum monitoring for the drone's control/video downlink (characteristic frequency-hopping patterns in the 2.4/5.8GHz bands, detectable with an SDR such as an RTL-SDR or HackRF) or acoustic detection (rotor noise has a distinct, detectable frequency signature even on modest hardware).

**Recommendation**: treat outdoor drone detection as a separate subsystem — an SDR-based RF sniffer — sharing the Pi as a compute platform but using an entirely different sensing front end, rather than attempting to stretch CSI sensing beyond its effective range.

## 6. Roadmap

| Phase | Capability | Status |
|---|---|---|
| 1 | Indoor motion detection (variance thresholding) | Implemented |
| 2 | Alerting integration (MQTT/webhook) | Planned |
| 3 | Spectral analysis for motion-type discrimination | Planned |
| 4 | Close-range drone signature classification (indoor/near-field) | Proposed |
| 5 | SDR-based outdoor RF drone detection (separate subsystem) | Proposed, separate project |

## 7. Conclusion

Consumer WiFi hardware, with no modification beyond an open-source firmware patch, is a capable indoor motion sensor — the same physical principle now being productized at scale by ISPs. The approach scales naturally toward close-range drone signature detection through added spectral analysis, but has a hard physical ceiling that makes it unsuitable for outdoor, standoff drone detection, where RF-spectrum or acoustic sensing are the appropriate tools. The system architecture described here is deliberately structured so that the capture pipeline is reusable across these extensions, while detection logic — the part expected to evolve — remains cleanly separable.

## References

- Nexmon CSI Extractor, Seemoo Lab, TU Darmstadt — https://github.com/seemoo-lab/nexmon_csi
- Comcast Xfinity "WiFi Motion" feature announcement, 2026
- General literature on WiFi-based device-free sensing: human activity recognition, vital-sign estimation, and micro-Doppler-based object classification via CSI.
