"""Golden-gap eval: replay scripted conversations with planted gaps and measure what Sync catches.

    python -m demo.eval_gaps                    # all scripts, signals only (fast, deterministic)
    python -m demo.eval_gaps --llm --speed 1    # include the live model (real-time)
    python -m demo.eval_gaps acme_thursday --mode normal

Uses a throwaway database so your real data is never touched.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("scripts", nargs="*", help="script names (default: all with planted gaps)")
    ap.add_argument("--speed", type=float, default=8.0)
    ap.add_argument("--mode", default="active", choices=["quiet", "normal", "active"])
    ap.add_argument("--llm", action="store_true", help="enable the live model (T1)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    tmp = tempfile.mkdtemp(prefix="sync-eval-")
    os.environ["SYNC_LLM_ENABLED"] = "true" if args.llm else "false"
    sys.stdout.reconfigure(encoding="utf-8")

    from sync.config import settings

    settings.llm_enabled = args.llm
    from sync.db import init_db

    init_db(f"sqlite:///{Path(tmp) / 'eval.db'}")
    from demo import harness

    names = args.scripts or sorted(p.stem for p in harness.SCRIPTS.glob("*.json") if '"gaps"' in p.read_text(encoding="utf-8"))
    uid = harness.ensure_user("Meera", args.mode)
    harness.ingest_docs(uid)
    totals = {"gaps": 0, "hits": 0, "nudges": 0, "matched": 0}

    async def run_all():
        for name in names:
            script = harness.load_script(name)
            pid = harness.seed_acme(uid) if name.startswith("acme_thursday") else None

            def show(ev):
                if args.verbose and ev.get("type") == "nudge":
                    print(f"   t={ev['t']:6.1f}s  [{ev['gap_type']:>3} {ev['tier']}] {ev['text']}")

            res = await harness.replay(script, user_id=uid, speed=args.speed, project_id=pid, mode=args.mode, on_event=show)
            sc = harness.score(script, res, llm=args.llm)
            totals["gaps"] += sc["gaps"]
            totals["hits"] += sc["hits"]
            totals["nudges"] += sc["nudges"]
            totals["matched"] += round(sc["precision"] * sc["nudges"])
            print(f"{name:22s} recall {sc['hits']}/{sc['gaps']} ({sc['card_hits']} via wrap-up card)  "
                  f"precision {sc['precision']:.2f}  nudges {sc['nudges']} ({sc['per_10min']}/10min)  "
                  f"avg latency {sc['latency_avg']}s")
            if args.verbose:
                for e in res["events"]:
                    if e.get("type") == "wrapup":
                        print(f"   t={e['t']:6.1f}s  [WRAP-UP] open: {[c['id'] for c in e['open_criteria']]} | "
                              f"unanswered: {e['unanswered']} | no date: {e['missing_dates']} | parked: {e['parked']}")
            for text in sc["forbidden"]:
                print(f"   UNWANTED nudge (off-topic for this call): {text}")
            for g in sc["missed"]:
                print(f"   missed {g['gap']} after #{g['after']}: {g['note']}")

    asyncio.run(run_all())
    r = totals["hits"] / max(1, totals["gaps"])
    p = totals["matched"] / max(1, totals["nudges"])
    print(f"\nTOTAL recall {totals['hits']}/{totals['gaps']} = {r:.0%}   precision {p:.0%}   ({args.mode} mode, "
          f"{'with' if args.llm else 'without'} live model)")


if __name__ == "__main__":
    main()
