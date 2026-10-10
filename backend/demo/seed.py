"""Demo data and resets.

    python -m demo.seed            # add the demo "Acme" project and the vendor's documents to your profile
    python -m demo.seed --reset    # delete everything (all users, projects, conversations, history) for a fresh start

Run it with the server stopped. The demo data goes to the first profile created on this laptop (create yours in the
app first), or to a demo user "Meera" if there is none.
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy import select

from sync.config import settings
from sync.db import init_db, session_scope


def reset() -> None:
    for suffix in ("", "-wal", "-shm"):
        path = settings.db_path.with_name(settings.db_path.name + suffix)
        if path.exists():
            path.unlink()
    agent = settings.data_dir / "agent.json"
    if agent.exists():
        agent.unlink()
    print(f"Deleted {settings.db_path} and the desktop agent's sign-in. Sync starts fresh.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="delete all Sync data before anything else")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if args.reset:
        reset()
        return
    init_db()
    from sync.models import User

    from demo import harness

    with session_scope() as db:
        first = db.scalar(select(User).order_by(User.id))
        uid = first.id if first else None
    uid = uid or harness.ensure_user("Meera")
    harness.ingest_docs(uid)
    pid = harness.seed_acme(uid)
    print(f"Added the demo project #{pid} 'Acme · CRM integration' and the vendor's documents to user #{uid}.")


if __name__ == "__main__":
    main()
