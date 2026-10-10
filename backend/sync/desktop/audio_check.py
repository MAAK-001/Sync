"""Which part of Sync's audio capture makes Phone Link move a call back to the phone?

    python -m sync.desktop.audio_check

Quit the Sync desktop agent first (tray icon → Quit). Then run this, start a Phone Link call on the laptop and leave it
running for about a minute. The check opens nothing for 10 s (a baseline), then only the speaker loopback (the other
side), then only the microphone (you), and reports whether the call stayed on the laptop each time.
"""
from __future__ import annotations

import time

from ..config import settings
from .detect import active_calls

STEP = 15  # seconds per step


def _on_laptop() -> bool:
    return any(c.app == "phone link" for c in active_calls())


def _hold(label: str, opened) -> bool:
    """Keep ``opened`` (a list of streams) for STEP seconds; True if the call stayed on the laptop all along."""
    print(f"  {label}: ", end="", flush=True)
    stayed = True
    for _ in range(STEP):
        time.sleep(1)
        on = _on_laptop()
        stayed &= on
        print("." if on else "x", end="", flush=True)
    for s in opened:
        s.stop_stream()
        s.close()
    print("  call stayed on the laptop" if stayed else "  CALL LEFT THE LAPTOP")
    return stayed


def main() -> None:
    import pyaudiowpatch as pyaudio

    print("Start a Phone Link call on this laptop now (waiting up to 2 minutes)…")
    for _ in range(240):
        if _on_laptop():
            break
        time.sleep(0.5)
    else:
        print("No Phone Link call found. Is the call's audio on the laptop (not the phone)?")
        return
    print("Call found. Keep talking normally; don't touch the call for the next minute.\n")
    pa = pyaudio.PyAudio()
    wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
    speakers = pa.get_device_info_by_index(wasapi["defaultOutputDevice"])
    loop_dev = next(d for d in pa.get_loopback_device_info_generator() if speakers["name"] in d["name"])
    mic_dev = pa.get_device_info_by_index(wasapi["defaultInputDevice"])

    def open_(dev):
        ch = max(1, min(2, int(dev["maxInputChannels"]) or 1))
        rate = int(dev["defaultSampleRate"])
        return pa.open(format=pyaudio.paInt16, channels=ch, rate=rate, input=True, input_device_index=dev["index"],
                       frames_per_buffer=rate // 50, stream_callback=lambda *a: (None, pyaudio.paContinue))

    results = {"nothing open (baseline)": _hold("nothing open (baseline)", [])}
    if results["nothing open (baseline)"]:
        results[f"speaker loopback ({loop_dev['name']})"] = _hold("speaker loopback only", [open_(loop_dev)])
    if all(results.values()):
        results[f"microphone ({mic_dev['name']})"] = _hold("microphone only", [open_(mic_dev)])
    pa.terminate()
    report = "\n".join(f"{k}: {'stayed on laptop' if v else 'MOVED TO PHONE'}" for k, v in results.items())
    print("\nResult\n" + report)
    if not all(results.values()):
        print("\n(After a step that moved the call, the later steps can't run: move the call back to the laptop and "
              "run the check again if you want them.)")
    (settings.data_dir / "audio_check.log").write_text(time.strftime("%Y-%m-%d %H:%M\n") + report + "\n", encoding="utf-8")
    print(f"\nSaved to {settings.data_dir / 'audio_check.log'}")


if __name__ == "__main__":
    main()
