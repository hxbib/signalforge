"""python -m app.watch demo "<buyer words>" [--run RUN_ID] [--runs-db PATH]

Creates a watch on a real stored run (the given one, else the latest run with a verified offer), with the rule
compiled by a real Gemini 4 Carbon call. With no such run it exits; it never invents one."""
import argparse
import json
import sqlite3
import sys

import app.watch as watch


def _latest_run(path=None, run_id=None):
    from app.config import DB_PATH
    try:
        conn = sqlite3.connect(f"file:{path or DB_PATH}?mode=ro", uri=True)
        rows = conn.execute("SELECT doc, image FROM runs WHERE ?='' OR run_id=? ORDER BY updated_at DESC LIMIT 50",
                            (run_id or "", run_id or "")).fetchall()
        conn.close()
    except sqlite3.Error:
        return None, None
    for doc, image in rows:
        run = json.loads(doc)
        if watch.candidates_of(run):
            return run, bytes(image)
    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["demo"])
    ap.add_argument("command")
    ap.add_argument("--run")
    ap.add_argument("--runs-db", help="read the run (read-only) from this SQLite file instead of SF_DB")
    args = ap.parse_args()
    run, image = _latest_run(args.runs_db, args.run)
    if run is None:
        sys.exit("no stored run with a verified offer — run a real search first")
    try:
        doc = watch.create(run, args.command, image=image)
    except Exception as err:
        sys.exit(f"{type(err).__name__}: {getattr(err, 'safe_message', None) or err}")
    print(json.dumps({"run_id": run.get("run_id"), "watch": doc}, indent=2))


if __name__ == "__main__":
    main()
