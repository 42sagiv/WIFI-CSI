#!/usr/bin/env python3
"""
CSI-based motion detection for Nexmon CSI on Raspberry Pi 4 (BCM43455c0).

Reads CSI frames extracted by nexmon_csi, computes a rolling variance
across subcarrier amplitudes, and flags motion when it crosses a
threshold. Start here, tune from there.

Dependencies:
    pip install numpy scapy websockets

Nexmon CSI dumps UDP packets (default port 5500) containing the CSI
payload, OR you can point this at a pcap file captured with tcpdump.
This script supports both a live UDP listener and offline pcap replay.

Optionally, pass --ws-port to also broadcast every frame's detection
result as JSON over a local WebSocket server, for consumption by
radar_gui.html (or any other client).
"""

import argparse
import asyncio
import json
import socket
import struct
import threading
import time
from collections import deque

import numpy as np

# --- CSI frame parsing -------------------------------------------------
# Nexmon CSI extractor prepends a small header before the raw CSI data.
# Format (matches the reference nexmon_csi udp_collect.py / C struct):
#   4 bytes  : magic / source_mac trailer (varies by build, adjust below)
#   2 bytes  : chip / bandwidth info (skip if using default 20MHz config)
#   N*4 bytes: CSI values, packed as interleaved int16 (real, imag) pairs
#
# The exact header layout depends on your nexmon_csi build (make.sh
# config). Check nexmon_csi/utils/python/csiread.py in the repo for the
# authoritative struct — this is a simplified version for a standard
# 20MHz / 64-subcarrier capture. Adjust NUM_SUBCARRIERS and HEADER_LEN
# to match your build's output if parsing looks garbled.

NUM_SUBCARRIERS = 64
HEADER_LEN = 18  # bytes before CSI payload begins in nexmon's udp frame


def parse_csi_frame(raw_bytes):
    """Extract per-subcarrier amplitude array from one CSI UDP payload."""
    if len(raw_bytes) < HEADER_LEN + NUM_SUBCARRIERS * 4:
        return None

    payload = raw_bytes[HEADER_LEN:HEADER_LEN + NUM_SUBCARRIERS * 4]
    # Each subcarrier is int16 real + int16 imag, little-endian
    values = struct.unpack(f"<{NUM_SUBCARRIERS * 2}h", payload)
    iq = np.array(values, dtype=np.float32).reshape(-1, 2)
    real, imag = iq[:, 0], iq[:, 1]
    amplitude = np.sqrt(real**2 + imag**2)
    return amplitude


# --- Motion detector -----------------------------------------------------

class MotionDetector:
    def __init__(self, window_size=20, threshold_multiplier=3.0, calib_frames=100):
        self.window = deque(maxlen=window_size)
        self.threshold_multiplier = threshold_multiplier
        self.calib_frames = calib_frames
        self.baseline_mean = None
        self.baseline_std = None
        self._calib_buffer = []

    def calibrate(self, amplitude_vector):
        """Call repeatedly at startup with no motion in the environment."""
        self._calib_buffer.append(amplitude_vector)
        if len(self._calib_buffer) >= self.calib_frames:
            stacked = np.stack(self._calib_buffer)
            # variance across subcarriers per frame, then stats over frames
            per_frame_var = np.var(stacked, axis=1)
            self.baseline_mean = np.mean(per_frame_var)
            self.baseline_std = np.std(per_frame_var) + 1e-6
            return True
        return False

    def update(self, amplitude_vector):
        """Feed one CSI frame's amplitude vector; returns (is_motion, score)."""
        frame_var = np.var(amplitude_vector)
        self.window.append(frame_var)

        if self.baseline_mean is None:
            return False, 0.0

        smoothed = np.mean(self.window)
        score = (smoothed - self.baseline_mean) / self.baseline_std
        is_motion = score > self.threshold_multiplier
        return is_motion, score


# --- WebSocket broadcast (optional, for radar_gui.html) --------------------
#
# Runs a tiny WebSocket server on a background thread. Every processed
# frame (motion or not) is pushed to all connected clients as JSON:
#   {"ts": <unix time>, "score": <float>, "motion": <bool>, "angle": <0-360>}
#
# "angle" is NOT a real angle-of-arrival measurement — a single omnidirectional
# antenna can't determine direction. It's a deterministic pseudo-angle derived
# from which subcarriers show the largest deviation, purely so the radar GUI
# has something to plot spatially rather than a single fixed point. Treat it
# as illustrative, not as localization. Real AoA would need multiple antennas
# and phase-difference processing, which is a further extension, not this.

class WebSocketBroadcaster:
    def __init__(self, port):
        self.port = port
        self.clients = set()
        self.loop = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._ready = threading.Event()

    def start(self):
        self._thread.start()
        self._ready.wait(timeout=5)

    def _run(self):
        import websockets

        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        async def handler(ws):
            self.clients.add(ws)
            try:
                async for _ in ws:
                    pass  # this server only pushes, doesn't expect input
            finally:
                self.clients.discard(ws)

        async def main():
            async with websockets.serve(handler, "0.0.0.0", self.port):
                print(f"WebSocket broadcast on ws://0.0.0.0:{self.port}")
                self._ready.set()
                await asyncio.Future()  # run forever

        self.loop.run_until_complete(main())

    def publish(self, message: dict):
        if self.loop is None or not self.clients:
            return
        payload = json.dumps(message)
        asyncio.run_coroutine_threadsafe(self._broadcast(payload), self.loop)

    async def _broadcast(self, payload):
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)


def _pseudo_angle(amplitude_vector):
    """Deterministic 0-360 value from which subcarrier band deviates most.
    Illustrative only — see note above. Not a real direction estimate."""
    half = len(amplitude_vector) // 2
    low_energy = float(np.sum(amplitude_vector[:half]))
    high_energy = float(np.sum(amplitude_vector[half:]))
    balance = (high_energy - low_energy) / (high_energy + low_energy + 1e-6)
    return (balance + 1.0) * 180.0  # maps [-1, 1] -> [0, 360]


# --- Live UDP mode ---------------------------------------------------------

def run_udp(port, window_size, threshold, calib_frames, ws_port=None):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", port))
    print(f"Listening for CSI UDP frames on port {port}...")

    broadcaster = None
    if ws_port:
        broadcaster = WebSocketBroadcaster(ws_port)
        broadcaster.start()

    detector = MotionDetector(window_size, threshold, calib_frames)
    calibrated = False

    while True:
        data, _addr = sock.recvfrom(4096)
        amp = parse_csi_frame(data)
        if amp is None:
            continue

        if not calibrated:
            calibrated = detector.calibrate(amp)
            if calibrated:
                print("Calibration complete. Baseline established.")
            continue

        is_motion, score = detector.update(amp)
        ts = time.strftime("%H:%M:%S")
        if is_motion:
            print(f"[{ts}] MOTION DETECTED  (score={score:.2f})")
        else:
            print(f"[{ts}] idle             (score={score:.2f})", end="\r")

        if broadcaster:
            broadcaster.publish({
                "ts": time.time(),
                "score": float(score),
                "motion": bool(is_motion),
                "angle": _pseudo_angle(amp),
            })


# --- Offline pcap mode ------------------------------------------------------

def run_pcap(path, window_size, threshold, calib_frames):
    from scapy.all import PcapReader, UDP, Raw

    detector = MotionDetector(window_size, threshold, calib_frames)
    calibrated = False

    with PcapReader(path) as reader:
        for pkt in reader:
            if not pkt.haslayer(UDP) or not pkt.haslayer(Raw):
                continue
            amp = parse_csi_frame(bytes(pkt[Raw].load))
            if amp is None:
                continue

            if not calibrated:
                calibrated = detector.calibrate(amp)
                continue

            is_motion, score = detector.update(amp)
            if is_motion:
                print(f"MOTION DETECTED  (score={score:.2f})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="CSI motion detector")
    ap.add_argument("--udp-port", type=int, default=5500,
                     help="Listen for live CSI UDP frames on this port (default nexmon_csi port)")
    ap.add_argument("--pcap", type=str, default=None,
                     help="Instead of live UDP, replay a pcap file")
    ap.add_argument("--window", type=int, default=20,
                     help="Rolling window size (frames) for smoothing")
    ap.add_argument("--threshold", type=float, default=3.0,
                     help="Std-devs above baseline to flag as motion")
    ap.add_argument("--calib-frames", type=int, default=100,
                     help="Number of still-environment frames to calibrate on")
    ap.add_argument("--ws-port", type=int, default=None,
                     help="Also broadcast detection results over WebSocket on this "
                          "port, for radar_gui.html or another live client")
    args = ap.parse_args()

    if args.pcap:
        run_pcap(args.pcap, args.window, args.threshold, args.calib_frames)
    else:
        run_udp(args.udp_port, args.window, args.threshold, args.calib_frames, args.ws_port)
