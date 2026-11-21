"""Watch endpoints (CONTRACT section 6)."""
from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

import app.watch as watch

router = APIRouter()


def _error(status, code, message):
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


def _replies(body):
    replies = {k: v for k, v in (body.get("replies") or {}).items() if type(v) is bool}
    answer = body.get("answer") or {}
    if answer.get("question_id") and answer.get("choice") in ("yes", "no"):
        replies[answer["question_id"]] = answer["choice"] == "yes"
    return replies


@router.post("/api/search/{run_id}/watch")
def create_watch(run_id: str, body: dict | None = Body(default=None)):
    from app import store
    body = body or {}
    run = store.get_run(run_id)
    if run is None:
        return _error(404, "not_found", "Unknown search.")
    command = body.get("command")
    if not isinstance(command, str) or not command.strip():
        command = ((run.get("goal") or {}).get("watch_words") or "")
    try:
        doc = watch.create(run, command, _replies(body))
    except watch.WatchError as err:
        return _error(err.status, err.code, err.message)
    except Exception as err:  # a failed model call is surfaced, never replaced by a default
        if type(err).__name__ == "ModelCallError":
            return _error(502, "model_failed", getattr(err, "safe_message", None) or "The watch rule model call failed.")
        raise
    run = store.get_run(run_id) or run
    run["watch"] = doc
    store.save_run(run)
    return doc


@router.post("/api/watch/{watch_id}/pause")
def pause_watch(watch_id: str):
    try:
        return watch.pause(watch_id)
    except watch.WatchError as err:
        return _error(err.status, err.code, err.message)


@router.post("/api/watch/{watch_id}/whatif")
def what_if(watch_id: str, body: dict | None = Body(default=None)):
    doc = watch.get(watch_id)
    if doc is None:
        return _error(404, "not_found", "Unknown watch.")
    if not doc.get("rule"):
        return _error(400, "no_rule", "This watch has no rule to simulate yet.")
    timeline = (body or {}).get("timeline")
    if not isinstance(timeline, list) or not 0 < len(timeline) <= 50:
        return _error(400, "bad_timeline", "Send a timeline of 1 to 50 points.")
    try:
        return {"simulation": True, "rows": watch.what_if(doc, timeline)}
    except watch.WatchError as err:
        return _error(err.status, err.code, err.message)
