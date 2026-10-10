"""Play a scripted conversation against a running Sync server, through the real WebSocket path.

    python -m demo.play acme_thursday                 # text utterances in real time
    python -m demo.play acme_thursday --speed 3       # faster
    python -m demo.play acme_thursday --audio         # stream the rendered WAV through local speech recognition
    python -m demo.play acme_thursday --wait-recap    # also wait for the post-call record

Signs in like the desktop agent on this laptop (~/.sync/agent.json), or pass --server and --token.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import wave

import httpx
import websockets

from demo.harness import HERE, load_script
from sync.auth import load_agent_config

C = {"nudge": "\033[1;33m", "item": "\033[36m", "dim": "\033[2m", "ok": "\033[32m", "end": "\033[0m"}


def show(ev: dict) -> None:
    t = ev.get("t", 0)
    kind = ev.get("type")
    if kind == "transcript":
        who = "YOU" if ev["channel"] == "you" else (ev.get("name") or f"THEM-{ev['speaker']}")
        print(f"{C['dim']}{t:6.1f}s {who:>8}: {ev['text']}{C['end']}")
    elif kind == "nudge":
        print(f"{C['nudge']}{t:6.1f}s  ▶ [{ev['kind'].upper()} {ev['gap_type']}/{ev['tier']}] {ev['text']}"
              f"   (latency {ev['latency']}s){C['end']}")
        if ev.get("why"):
            print(f"{C['dim']}           why: {ev['why']}{C['end']}")
    elif kind == "item":
        it = ev["item"]
        extra = " | ".join(x for x in (it.get("owner") and f"owner {it['owner']}", it.get("due") and f"due {it['due']}",
                                       it.get("condition") and f"if {it['condition']}", it.get("answer") and f"answer: {it['answer']}") if x)
        print(f"{C['item']}           + {it['type']} [{it['status']}] {it['text']}{('  (' + extra + ')') if extra else ''}{C['end']}")
    elif kind == "criteria":
        done = [c["id"] for c in ev["criteria"] if c["status"] == "covered"]
        print(f"{C['dim']}           goals covered: {', '.join(done) or '-'}{C['end']}")
    elif kind in ("project.linked", "speakers", "wrapup"):
        print(f"{C['ok']}{t:6.1f}s  {kind}: {json.dumps({k: v for k, v in ev.items() if k not in ('type', 't', 'session_id')}, ensure_ascii=False)}{C['end']}")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("script")
    saved = load_agent_config()
    ap.add_argument("--server", default=saved.get("server", "http://127.0.0.1:8765"))
    ap.add_argument("--token", default=saved.get("token", ""))
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--audio", action="store_true", help="stream demo/audio/<script>.wav instead of text")
    ap.add_argument("--wait-recap", action="store_true")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    script = load_script(args.script)
    if not args.token:
        sys.exit("Not signed in: open the Sync app once to create your profile (or pass --token).")
    http = httpx.AsyncClient(base_url=args.server, timeout=60, headers={"X-Sync-Token": args.token})

    project_id = None
    if script.get("project"):
        projects = (await http.get("/projects")).json()
        project_id = next((p["id"] for p in projects if p["name"] == script["project"]), None)
        if project_id is None:
            print(f"(project '{script['project']}' not found — run `python -m demo.seed` first; continuing without it)")
    body = {"mode": "script", "project_id": project_id, "title": script.get("title", ""), "caller": script.get("caller", ""),
            "speed": 1.0 if args.audio else args.speed, "audio": args.audio}
    sess = (await http.post("/sessions", json=body)).json()
    sid = sess["id"]
    print(f"session {sid} — project {project_id}\n")
    ws_url = args.server.replace("http", "ws") + f"/ws/sessions/{sid}?token={args.token}"
    async with websockets.connect(ws_url, max_size=None) as ws:
        async def reader():
            async for raw in ws:
                show(json.loads(raw))

        rtask = asyncio.create_task(reader())
        start = time.monotonic()
        if args.audio:
            wav_path = HERE / "audio" / f"{script['name']}.wav"
            with wave.open(str(wav_path)) as w:
                assert w.getframerate() == 16000 and w.getnchannels() == 2, "expected 16 kHz stereo"
                frames_per_chunk = 1600  # 100 ms
                sent = 0
                while True:
                    data = w.readframes(frames_per_chunk)
                    if not data:
                        break
                    await ws.send(data)
                    sent += frames_per_chunk
                    await asyncio.sleep(max(0, sent / 16000 - (time.monotonic() - start)))
            await asyncio.sleep(4)
        else:
            for u in script["utterances"]:
                wait = u["t_end"] / args.speed - (time.monotonic() - start)
                if wait > 0:
                    await asyncio.sleep(wait)
                await ws.send(json.dumps({"type": "utterance", "channel": u["channel"], "text": u["text"],
                                          "speaker": u.get("speaker", ""), "t_start": u["t_start"], "t_end": u["t_end"]}))
            await asyncio.sleep(6 / args.speed)
        await ws.send(json.dumps({"type": "end"}))
        await asyncio.sleep(1)
        rtask.cancel()

    recap = (await http.get(f"/sessions/{sid}/recap")).json()
    print(f"\n{C['ok']}Draft recap:{C['end']}", json.dumps(recap["recap"], indent=2, ensure_ascii=False))
    if args.wait_recap:
        print("\nwaiting for the post-call record (deep model)…")
        t0 = time.monotonic()
        while True:
            await asyncio.sleep(5)
            recap = (await http.get(f"/sessions/{sid}/recap")).json()
            if recap["status"] == "refined" or time.monotonic() - t0 > 900:
                break
        print(f"{C['ok']}Final recap ({recap['status']}, {time.monotonic() - t0:.0f}s):{C['end']}",
              json.dumps(recap["recap"], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
