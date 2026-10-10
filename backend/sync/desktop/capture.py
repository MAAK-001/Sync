"""Capture both sides of a call on Windows: system output (loopback = the other side) + microphone (you).

Produces 100 ms stereo PCM16 frames at 16 kHz: left = you (mic), right = them (loopback).
WASAPI loopback delivers nothing while the PC is silent, so missing audio is padded with silence by wall clock.
"""
from __future__ import annotations

import queue
import threading
import time

import numpy as np

SR = 16000
FRAME = SR // 10  # 100 ms


def _resample(x: np.ndarray, src: int) -> np.ndarray:
    if src == SR or len(x) == 0:
        return x
    if src % SR == 0:  # 48 kHz -> 16 kHz: average groups (cheap anti-aliasing for speech)
        k = src // SR
        n = len(x) // k * k
        return x[:n].reshape(-1, k).mean(axis=1)
    n_out = int(len(x) * SR / src)
    return np.interp(np.linspace(0, len(x) - 1, n_out), np.arange(len(x)), x).astype(np.float32)


class _Stream:
    def __init__(self, pa, device: dict):
        import pyaudiowpatch as pyaudio

        self.rate = int(device["defaultSampleRate"])
        self.channels = max(1, min(2, int(device["maxInputChannels"]) or 1))
        self.buf: queue.Queue[np.ndarray] = queue.Queue()
        self.rest = np.zeros(0, dtype=np.float32)  # only touched by the mixer thread
        self.stream = pa.open(format=pyaudio.paInt16, channels=self.channels, rate=self.rate, input=True,
                              input_device_index=device["index"], frames_per_buffer=int(self.rate / 50),
                              stream_callback=self._cb)

    def _cb(self, data, _frames, _time, _status):
        import pyaudiowpatch as pyaudio

        x = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
        if self.channels == 2:
            x = x.reshape(-1, 2).mean(axis=1)
        self.buf.put(_resample(x, self.rate))
        return (None, pyaudio.paContinue)

    def take(self, n: int) -> np.ndarray:
        chunks, have = [self.rest], len(self.rest)
        while have < n:
            try:
                c = self.buf.get_nowait()
            except queue.Empty:
                break
            chunks.append(c)
            have += len(c)
        x = np.concatenate(chunks)
        x, self.rest = x[:n], x[n:]  # keep the remainder for the next frame
        return np.pad(x, (0, n - len(x)))

    def close(self) -> None:
        self.stream.stop_stream()
        self.stream.close()


class CallCapture:
    """Start/stop capture; ``frames`` yields interleaved stereo PCM16 bytes every 100 ms."""

    def __init__(self) -> None:
        import pyaudiowpatch as pyaudio

        self.pa = pyaudio.PyAudio()
        wasapi = self.pa.get_host_api_info_by_type(pyaudio.paWASAPI)
        speakers = self.pa.get_device_info_by_index(wasapi["defaultOutputDevice"])
        loop_dev = next((d for d in self.pa.get_loopback_device_info_generator() if speakers["name"] in d["name"]), None)
        if loop_dev is None:
            raise RuntimeError("no WASAPI loopback device for the default speakers")
        mic_dev = self.pa.get_device_info_by_index(wasapi["defaultInputDevice"])
        self.mic = _Stream(self.pa, mic_dev)
        self.loop = _Stream(self.pa, loop_dev)
        self.out: queue.Queue[bytes] = queue.Queue()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._mix, daemon=True)
        self._thread.start()

    def _mix(self) -> None:
        next_t = time.monotonic()
        while not self._stop.is_set():
            next_t += 0.1
            time.sleep(max(0.0, next_t - time.monotonic()))
            you, them = self.mic.take(FRAME), self.loop.take(FRAME)
            stereo = np.empty(FRAME * 2, dtype=np.int16)
            stereo[0::2] = np.clip(you * 32767, -32768, 32767).astype(np.int16)
            stereo[1::2] = np.clip(them * 32767, -32768, 32767).astype(np.int16)
            self.out.put(stereo.tobytes())

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1)
        self.mic.close()
        self.loop.close()
        self.pa.terminate()
