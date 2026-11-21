"""python -m app.watch demo "<buyer words>" [--run RUN_ID] [--hand-draft MODE:OP:CENTS]

Uses a stored run (the given one, else the latest run with a verified offer). With no stored run it uses a
clearly labelled development fixture with no price and a blank image. The rule is compiled by a real
Gemini 4 Carbon call unless --hand-draft is given, which is printed as a hand-built draft in the receipts."""
import argparse
import io
import json
import sqlite3
import sys

import app.watch as watch


def _latest_run():
    from app.config import DB_PATH
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute("SELECT doc, image FROM runs ORDER BY updated_at DESC LIMIT 50").fetchall()
        conn.close()
    except sqlite3.Error:
        return None, None
    for doc, image in rows:
        run = json.loads(doc)
        if watch.candidates_of(run):
            return run, bytes(image)
    return None, None


def _fixture():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (235, 235, 235)).save(buf, "JPEG")
    offer = {"seller": "dev-seller", "seller_name": "Development fixture seller (not a real merchant)",
             "listing": "dev-variant", "title": "Development fixture listing", "url": "https://example.invalid/",
             "item_cents": None, "currency": "USD", "stock": "unknown", "verified": True, "source": "development"}
    run = {"run_id": "run_dev_fixture", "development_fixture": True, "goal": {"watch_words": ""},
           "result": {"winners": [offer], "near_miss": None, "rejected": []}}
    return run, buf.getvalue()


def _hand(spec):
    mode, op, cents = spec.split(":")
    proposal = {"reason": "ok", "status": "ready", "candidate": 0, "mode": mode, "op": op, "cents": int(cents),
                "cap_evidence": "hand-built draft", "binding_evidence": "hand-built draft: slot 0", "blockers": [],
                "question": ""}
    return lambda image, command, candidates: {"proposal": proposal, "receipt": {
        "job": "watch_rule", "model": "hand-built-draft (no model call)", "id": None, "ms": 0}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["demo"])
    ap.add_argument("command")
    ap.add_argument("--run")
    ap.add_argument("--hand-draft")
    args = ap.parse_args()
    if args.hand_draft:
        import os
        os.environ["SF_DB"] = ":memory:"  # a hand-built draft is never persisted
    if args.run:
        from app import store
        run, image = store.get_run(args.run), store.get_image(args.run)
        if run is None:
            sys.exit(f"unknown run {args.run}")
    else:
        run, image = _latest_run()
        if run is None:
            run, image = _fixture()
    try:
        doc = watch.create(run, args.command, compile=_hand(args.hand_draft) if args.hand_draft else None,
                           image=image)
    except Exception as err:
        sys.exit(f"{type(err).__name__}: {getattr(err, 'safe_message', None) or err}")
    print(json.dumps({"run_id": run.get("run_id"), "development_fixture": bool(run.get("development_fixture")),
                      "watch": doc}, indent=2))


if __name__ == "__main__":
    main()
