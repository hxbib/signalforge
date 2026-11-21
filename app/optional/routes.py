"""Optional lane endpoint: POST /api/watch/{watch_id}/challenge (CONTRACT section 7)."""
import json
import threading

from fastapi import APIRouter, Request

import app.optional as optional
import app.watch as watch
from app.watch.routes import _error, _watch_for

router = APIRouter()

_busy = threading.Lock()
RUNNING = {"status": "running", "sandbox": "google-hosted", "environment_id": None, "interaction_id": None,
           "attempts": None, "replayed_locally": False,
           "sentence": "A Gemini Managed Agent in a Google-hosted sandbox is trying to break this rule.",
           "timeline": None, "ms": None, "simulation": True}


def record(watch_id: str, result: dict) -> dict | None:
    """Write watch["challenge"] onto the stored watch and onto the run that carries it."""
    from app import store
    with watch._lock:
        ledger = watch._open()
        try:
            with ledger.write():
                doc = watch._load(ledger, watch_id)
                if doc is None:
                    return None
                doc["challenge"] = result
                ledger.db.execute("UPDATE app_watch SET doc=? WHERE id=?", (json.dumps(doc), watch_id))
        finally:
            ledger.close()
    run = store.get_run(doc.get("run_id") or "")
    if run is not None and (run.get("watch") or {}).get("watch_id") == watch_id:
        run["watch"]["challenge"] = result
        store.save_run(run)
    return doc


def _work(doc, challenge):
    try:
        try:
            result = challenge(doc)
        except Exception:  # never leave a watch stuck on "running"
            result = {**RUNNING, "status": "unavailable", "sentence": "The challenge did not complete."}
        record(doc["watch_id"], result)
    finally:
        _busy.release()


def start(doc: dict, challenge=None) -> dict | None:
    """Start one background challenge. None when another one is already running."""
    if not _busy.acquire(blocking=False):
        return None
    try:
        stored = record(doc["watch_id"], dict(RUNNING))
        threading.Thread(target=_work, args=(stored or doc, challenge or optional.challenge), daemon=True).start()
    except BaseException:
        _busy.release()
        raise
    return stored


@router.post("/api/watch/{watch_id}/challenge")
def challenge_watch(watch_id: str, request: Request):
    doc, denied = _watch_for(request, watch_id)
    if denied:
        return denied
    if not doc.get("rule"):
        return _error(400, "no_rule", "This watch has no rule to challenge yet.")
    started = start(doc)
    if started is not None:
        return started
    current = watch.get(watch_id) or doc
    if (current.get("challenge") or {}).get("status") == "running":
        return current
    return _error(409, "challenge_busy", "Another rule challenge is running. Try again in a minute.")
