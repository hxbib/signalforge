"""Watch endpoints (CONTRACT section 6)."""
from urllib.parse import urlsplit

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

import app.watch as watch

router = APIRouter()


def _error(status, code, message):
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


def _denied(request, run_id):
    """Same-origin and session ownership, enforced whenever the app's session middleware is present."""
    from app import store
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).netloc != (request.headers.get("x-forwarded-host")
                                                  or request.headers.get("host") or ""):
        return _error(400, "bad_origin", "This request did not come from the app.")
    session = getattr(request.state, "session", None)
    if session is not None and store.owner(run_id) != session:
        return _error(404, "not_found", "Unknown search.")
    return None


def _watch_for(request, watch_id):
    doc = watch.get(watch_id)
    if doc is None:
        return None, _error(404, "not_found", "Unknown watch.")
    denied = _denied(request, doc.get("run_id") or "")
    return (None, _error(404, "not_found", "Unknown watch.")) if denied else (doc, None)


def _replies(body):
    replies = {k: v for k, v in (body.get("replies") or {}).items() if type(v) is bool}
    answer = body.get("answer") or {}
    if answer.get("question_id") and answer.get("choice") in ("yes", "no"):
        replies[answer["question_id"]] = answer["choice"] == "yes"
    return replies


@router.post("/api/search/{run_id}/watch")
def create_watch(run_id: str, request: Request, body: dict | None = Body(default=None)):
    from app import store
    body = body or {}
    run = store.get_run(run_id)
    if run is None:
        return _error(404, "not_found", "Unknown search.")
    denied = _denied(request, run_id)
    if denied:
        return denied
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
def pause_watch(watch_id: str, request: Request):
    _, denied = _watch_for(request, watch_id)
    if denied:
        return denied
    try:
        return watch.pause(watch_id)
    except watch.WatchError as err:
        return _error(err.status, err.code, err.message)


@router.post("/api/watch/{watch_id}/whatif")
def what_if(watch_id: str, request: Request, body: dict | None = Body(default=None)):
    doc, denied = _watch_for(request, watch_id)
    if denied:
        return denied
    if not doc.get("rule"):
        return _error(400, "no_rule", "This watch has no rule to simulate yet.")
    timeline = (body or {}).get("timeline")
    if not isinstance(timeline, list) or not 0 < len(timeline) <= 50:
        return _error(400, "bad_timeline", "Send a timeline of 1 to 50 points.")
    try:
        return {"simulation": True, "rows": watch.what_if(doc, timeline)}
    except watch.WatchError as err:
        return _error(err.status, err.code, err.message)
