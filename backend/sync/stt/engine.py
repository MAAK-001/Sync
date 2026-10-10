"""Local speech recognition with sherpa-onnx: silero VAD per channel + an offline recognizer per segment.

Audio arrives as 16 kHz PCM16. Stereo frames are interleaved [you, them] (mic, system audio);
mono frames are a single room microphone (in-person mode).
Recognition runs on one worker thread, so the event loop (and the live engine) never blocks.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from ..config import settings
from ..textparse import fix_asr

log = logging.getLogger("sync.stt")
SR = 16000

# name -> (folder under models/stt, kind)
CATALOG: dict[str, tuple[str, str]] = {
    "moonshine-base": ("sherpa-onnx-moonshine-base-en-quantized-2026-02-27", "moonshine"),
    "moonshine-tiny": ("sherpa-onnx-moonshine-tiny-en-quantized-2026-02-27", "moonshine"),
    "parakeet-110m": ("sherpa-onnx-nemo-parakeet_tdt_ctc_110m-en-36000-int8", "nemo_ctc"),
    "parakeet-0.6b": ("sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8", "transducer"),
}

_recognizer = None
_rec_lock = threading.Lock()
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="stt")


def stt_dir() -> Path:
    return settings.models_dir / "stt"


def _pick(folder: Path, *patterns: str) -> str:
    for pat in patterns:
        hits = sorted(folder.glob(pat))
        if hits:
            return str(hits[0])
    raise FileNotFoundError(f"no file matching {patterns} in {folder}")


def build_recognizer(name: str | None = None, threads: int | None = None):
    import sherpa_onnx

    name = name or settings.stt_model
    folder_name, kind = CATALOG[name]
    folder = stt_dir() / folder_name
    threads = threads or settings.stt_threads
    tokens = _pick(folder, "tokens.txt", "*tokens*.txt")
    if kind == "moonshine":
        # 2026 Moonshine exports: encoder + merged decoder; older exports: preprocess/encode/uncached/cached.
        try:
            return sherpa_onnx.OfflineRecognizer.from_moonshine_v2(
                encoder=_pick(folder, "*encoder*.ort", "*encoder*.onnx"),
                decoder=_pick(folder, "*decoder*.ort", "*decoder*.onnx"),
                tokens=tokens, num_threads=threads)
        except (AttributeError, FileNotFoundError, TypeError):
            return sherpa_onnx.OfflineRecognizer.from_moonshine(
                preprocessor=_pick(folder, "preprocess*.onnx"), encoder=_pick(folder, "encode*.onnx"),
                uncached_decoder=_pick(folder, "uncached_decode*.onnx"), cached_decoder=_pick(folder, "cached_decode*.onnx"),
                tokens=tokens, num_threads=threads)
    if kind == "nemo_ctc":
        return sherpa_onnx.OfflineRecognizer.from_nemo_ctc(model=_pick(folder, "model*.onnx"), tokens=tokens,
                                                           num_threads=threads)
    return sherpa_onnx.OfflineRecognizer.from_transducer(
        encoder=_pick(folder, "encoder*.onnx"), decoder=_pick(folder, "decoder*.onnx"),
        joiner=_pick(folder, "joiner*.onnx"), tokens=tokens, num_threads=threads, model_type="nemo_transducer")


def recognizer():
    global _recognizer
    with _rec_lock:
        if _recognizer is None:
            t0 = time.perf_counter()
            _recognizer = build_recognizer()
            log.info("STT model %s loaded in %.1fs", settings.stt_model, time.perf_counter() - t0)
    return _recognizer


def transcribe(samples: np.ndarray) -> str:
    rec = recognizer()
    stream = rec.create_stream()
    stream.accept_waveform(SR, samples)
    rec.decode_stream(stream)
    text = fix_asr(stream.result.text.strip())
    # Some models emit all-caps or lower-case; normalise the first letter for readability.
    return text[:1].upper() + text[1:] if text else ""


_speaker_model = None


def speaker_embedding(samples: np.ndarray) -> np.ndarray | None:
    """Voice embedding of one phrase (CAM++), used only within a call to tell remote speakers apart."""
    global _speaker_model
    path = stt_dir() / "campplus_en.onnx"
    if not path.exists() or len(samples) < SR:  # under 1 s is too short to tell voices apart
        return None
    import sherpa_onnx

    with _rec_lock:
        if _speaker_model is None:
            _speaker_model = sherpa_onnx.SpeakerEmbeddingExtractor(
                sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(path), num_threads=1))
    stream = _speaker_model.create_stream()
    stream.accept_waveform(SR, samples)
    stream.input_finished()
    v = np.array(_speaker_model.compute(stream), dtype=np.float32)
    return v / max(float(np.linalg.norm(v)), 1e-9)


class SpeakerTracker:
    """Online clustering of remote voices into labels A, B, C… (kept in memory for this call only).

    Same voice scores ~0.8-0.95 with CAM++, different voices ~0.1-0.35. Only phrases of 1.5 s or more may start a new
    speaker; shorter ones join the closest known voice (or the previous speaker), which keeps clusters clean."""

    JOIN = 0.5
    NEW_MIN_SECONDS = 1.5

    def __init__(self, max_speakers: int | None = None) -> None:
        self.max_speakers = max_speakers  # 1 on a one-to-one phone call: there is only one remote voice
        self.centroids: list[np.ndarray] = []
        self.last = "A"

    def assign(self, emb: np.ndarray | None, seconds: float = 2.0) -> str:
        if self.max_speakers == 1 or emb is None:
            return self.last
        if self.centroids:
            sims = [float(c @ emb) for c in self.centroids]
            best = int(np.argmax(sims))
            if sims[best] >= self.JOIN or seconds < self.NEW_MIN_SECONDS:
                if sims[best] >= self.JOIN:
                    c = self.centroids[best] * 4 + emb  # slow-moving centroid
                    self.centroids[best] = c / np.linalg.norm(c)
                self.last = chr(ord("A") + best)
                return self.last
        if seconds < self.NEW_MIN_SECONDS and self.centroids:
            return self.last
        self.centroids.append(emb)
        self.last = chr(ord("A") + len(self.centroids) - 1)
        return self.last


def transcribe_segment(samples: np.ndarray, with_speaker: bool) -> tuple[str, np.ndarray | None]:
    """One job per phrase (text + voice embedding), so phrases are never reordered."""
    text = transcribe(samples)
    return text, (speaker_embedding(samples) if with_speaker and text else None)


def available() -> dict:
    folder_name, kind = CATALOG.get(settings.stt_model, ("?", "?"))
    return {"model": settings.stt_model, "installed": (stt_dir() / folder_name).exists(),
            "vad": (stt_dir() / "silero_vad.onnx").exists()}


def _vad():
    import sherpa_onnx

    cfg = sherpa_onnx.VadModelConfig()
    cfg.silero_vad.model = str(stt_dir() / "silero_vad.onnx")
    cfg.silero_vad.threshold = 0.5
    cfg.silero_vad.min_silence_duration = 0.45  # end of a phrase
    cfg.silero_vad.min_speech_duration = 0.25
    cfg.silero_vad.max_speech_duration = 12.0  # long monologues are cut so text keeps flowing
    cfg.sample_rate = SR
    return sherpa_onnx.VoiceActivityDetector(cfg, buffer_size_in_seconds=60), cfg.silero_vad.window_size


class _Channel:
    def __init__(self, name: str, pipeline: AudioPipeline):
        self.name = name
        self.pipeline = pipeline
        self.vad, self.window = _vad()
        self.buf = np.zeros(0, dtype=np.float32)
        self.offset = 0  # samples consumed
        self.speaking = False

    def feed(self, samples: np.ndarray) -> None:
        self.buf = np.concatenate([self.buf, samples])
        while len(self.buf) >= self.window:
            chunk, self.buf = self.buf[: self.window], self.buf[self.window:]
            self.vad.accept_waveform(chunk)
            self.offset += self.window
            speaking = self.vad.is_speech_detected()
            if speaking != self.speaking:
                self.speaking = speaking
                self.pipeline.on_speech(self.name, speaking)
            self.drain()

    def drain(self) -> None:
        while not self.vad.empty():
            front = self.vad.front
            samples = np.array(front.samples, dtype=np.float32)  # copy before pop() frees the segment
            start = front.start / SR
            self.vad.pop()
            self.pipeline.on_segment(self.name, samples, start, start + len(samples) / SR)


class AudioPipeline:
    """Feeds a LiveSession from raw audio bytes (from the desktop agent or a browser)."""

    def __init__(self, live, channels: int = 2):
        self.live = live
        self.channels = channels
        self.loop: asyncio.AbstractEventLoop | None = None
        self.chans: dict[str, _Channel] = {}
        self.pending: set[asyncio.Future] = set()
        # A Phone Link call has exactly one remote voice (and narrow-band audio confuses voice matching).
        self.speakers = SpeakerTracker(max_speakers=1 if getattr(live, "app", "") == "phone link" else None)
        self.t_offset: float | None = None

    async def start(self) -> None:
        self.loop = asyncio.get_running_loop()
        await self.loop.run_in_executor(_executor, recognizer)  # load once, off the event loop
        names = ["you", "them"] if self.channels == 2 else ["them"]
        self.chans = {n: _Channel(n, self) for n in names}

    def feed(self, data: bytes) -> None:
        if self.t_offset is None:  # audio time 0 = this moment on the session clock
            self.t_offset = self.live.now()
        pcm = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
        if self.channels == 2:
            pcm = pcm[: len(pcm) // 2 * 2].reshape(-1, 2)
            self.chans["you"].feed(pcm[:, 0].copy())
            self.chans["them"].feed(pcm[:, 1].copy())
        else:
            self.chans["them"].feed(pcm)

    def on_speech(self, channel: str, active: bool) -> None:
        self.live.speech(channel, active)

    def on_segment(self, channel: str, samples: np.ndarray, t_start: float, t_end: float) -> None:
        if len(samples) < SR * 0.3:
            return
        fut = asyncio.ensure_future(self._recognize(channel, samples, t_start, t_end))
        self.pending.add(fut)
        fut.add_done_callback(self.pending.discard)

    async def _recognize(self, channel: str, samples: np.ndarray, t_start: float, t_end: float) -> None:
        t0 = time.perf_counter()
        try:
            text, emb = await self.loop.run_in_executor(_executor, transcribe_segment, samples,
                                                        channel == "them" and self.speakers.max_speakers != 1)
        except Exception:  # noqa: BLE001  (one bad segment must not silence the rest of the call)
            log.exception("speech recognition failed on a %.1fs %s segment", t_end - t_start, channel)
            return
        speaker = self.speakers.assign(emb, len(samples) / SR) if channel == "them" and text else ""
        log.debug("STT %s/%s %.1fs audio -> %.2fs: %s", channel, speaker, t_end - t_start, time.perf_counter() - t0, text)
        if text and len(text.strip(" .,?!")) > 1:
            off = self.t_offset or 0.0
            await self.live.add_utterance(channel, text, speaker=speaker, t_start=off + t_start, t_end=off + t_end)

    async def stop(self) -> None:
        """Flush the last phrase on each channel and finish everything still being transcribed."""
        for ch in self.chans.values():
            ch.vad.flush()
            ch.drain()
        if self.pending:
            await asyncio.wait(self.pending, timeout=120)
