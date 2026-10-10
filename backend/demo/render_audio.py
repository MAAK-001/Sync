"""Render a demo script to a 16 kHz stereo WAV (left = you, right = them) for the audio demo/eval.

    python -m demo.render_audio acme_thursday              # free offline Windows voices (default)
    python -m demo.render_audio acme_thursday --elevenlabs # realistic voices (uses free credits; fictional text only)

ElevenLabs is only ever given these fictional scripts. A credit guard keeps usage under SYNC_ELEVENLABS_MAX_CREDITS,
and every line is cached so nothing is generated twice.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

from sync.config import settings

from demo.harness import HERE, load_script

SR = 16000
CACHE = HERE / "audio" / "cache"
USAGE = HERE / "audio" / ".elevenlabs_usage.json"
# Voice per speaker. ElevenLabs ids are public library voices; change them to taste.
ELEVEN_VOICES = {"you": "21m00Tcm4TlvDq8ikWAM", "A": "pNInz6obpgDQGcFmaJgB", "B": "EXAVITQu4vr4xnJ8nN5k",
                 "C": "ErXwobaYiN019PkySvjV"}


def sapi_voices() -> list[str]:
    ps = "Add-Type -AssemblyName System.Speech; (New-Object System.Speech.Synthesis.SpeechSynthesizer).GetInstalledVoices() | % { $_.VoiceInfo.Name }"
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, check=True)
    return [v.strip() for v in out.stdout.splitlines() if v.strip()]


def sapi(text: str, voice: str) -> np.ndarray:
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "u.wav"
        safe = text.replace("'", "''")
        ps = (
            "Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            f"$s.SelectVoice('{voice}'); $s.Rate = 1; "
            "$f = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono); "
            f"$s.SetOutputToWaveFile('{path}', $f); $s.Speak('{safe}'); $s.Dispose()"
        )
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True, capture_output=True)
        with wave.open(str(path)) as w:
            return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


def eleven(text: str, voice_id: str) -> np.ndarray:
    import httpx

    usage = json.loads(USAGE.read_text()) if USAGE.exists() else {"credits": 0}
    cost = len(text)  # conservative: 1 credit per character
    if usage["credits"] + cost > settings.elevenlabs_max_credits:
        raise SystemExit(f"ElevenLabs credit guard: {usage['credits']} used, limit {settings.elevenlabs_max_credits}")
    r = httpx.post(f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}", params={"output_format": "pcm_16000"},
                   headers={"xi-api-key": settings.elevenlabs_api_key},
                   json={"text": text, "model_id": "eleven_flash_v2_5"}, timeout=60)
    r.raise_for_status()
    usage["credits"] += cost
    USAGE.write_text(json.dumps(usage))
    return np.frombuffer(r.content, dtype=np.int16)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("script")
    ap.add_argument("--elevenlabs", action="store_true")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    script = load_script(args.script)
    CACHE.mkdir(parents=True, exist_ok=True)
    if args.elevenlabs and not settings.elevenlabs_api_key:
        raise SystemExit("Set SYNC_ELEVENLABS_API_KEY in backend/.env first.")
    voices = [] if args.elevenlabs else sapi_voices()
    if not args.elevenlabs and not voices:
        raise SystemExit("No Windows voices found.")

    def voice_for(u: dict) -> str:
        key = "you" if u["channel"] == "you" else u.get("speaker", "A")
        if args.elevenlabs:
            return ELEVEN_VOICES.get(key, ELEVEN_VOICES["A"])
        order = ["you", "A", "B", "C"]
        return voices[order.index(key) % len(voices)] if key in order else voices[0]

    # Natural turn-taking: each line starts after the previous one ends (plus the script's pause).
    clips, t = [], 1.0
    for u in script["utterances"]:
        voice = voice_for(u)
        key = hashlib.sha1(f"{'el' if args.elevenlabs else 'sapi'}|{voice}|{u['text']}".encode()).hexdigest()[:16]
        cached = CACHE / f"{key}.npy"
        pcm = np.load(cached) if cached.exists() else (eleven if args.elevenlabs else sapi)(u["text"], voice)
        np.save(cached, pcm)
        seg = pcm.astype(np.float32) / 32768.0
        clips.append((t, 0 if u["channel"] == "you" else 1, seg, u["text"]))
        u["t_start"], u["t_end"] = round(t, 2), round(t + len(seg) / SR, 2)
        t += len(seg) / SR + u.get("pause", 0.7)
    total = int((t + 2) * SR)
    track = np.zeros((total, 2), dtype=np.float32)
    for start_s, ch, seg, text in clips:
        start = int(start_s * SR)
        track[start:start + len(seg), ch] += seg
        print(f"{start_s:6.1f}s {'YOU ' if ch == 0 else 'THEM'} ({len(seg) / SR:.1f}s) {text[:60]}")
    timing = HERE / "audio" / f"{script['name']}.timing.json"
    timing.write_text(json.dumps([{"t_start": u["t_start"], "t_end": u["t_end"]} for u in script["utterances"]]))
    out = HERE / "audio" / f"{script['name']}.wav"
    with wave.open(str(out), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(track, -1, 1) * 32767).astype(np.int16).tobytes())
    print(f"\nwrote {out} ({total / SR:.0f}s)")


if __name__ == "__main__":
    main()
