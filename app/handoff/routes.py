"""Phone hand-off routes: a short-lived single-use token bound to the laptop's session."""
import io
import os
import secrets
import threading
import time
from urllib.parse import urlsplit

import segno
from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response

from app import config, pipeline, store
from app.contracts import new_run
from live_smoke import clean_image

router = APIRouter()

TTL_SECONDS = 300
MAX_TOKENS = 500
_lock = threading.Lock()
_tokens: dict[str, dict] = {}


def _err(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


def _same_origin(request: Request) -> bool:
    origin = request.headers.get("origin")
    if not origin:
        return True
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    public = urlsplit(os.environ.get("SF_PUBLIC_URL") or "").netloc
    return urlsplit(origin).netloc in {host, public} - {""}


def _base(request: Request) -> str:
    public = (os.environ.get("SF_PUBLIC_URL") or "").strip().rstrip("/")
    if public:
        return public
    scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{scheme}://{host}"


def _prune(now: float) -> None:
    for key in [k for k, v in _tokens.items() if v["expires"] + TTL_SECONDS < now]:
        del _tokens[key]


def _live(token: str) -> dict | None:
    """The entry while it can still take a photo. Caller holds the lock."""
    entry = _tokens.get(token)
    if entry is None or entry["run_id"] or entry["expires"] <= time.time():
        return None
    return entry


@router.post("/api/handoff")
def create_handoff(request: Request):
    if not _same_origin(request):
        return _err(400, "bad_origin", "This request did not come from the app.")
    now = time.time()
    token = secrets.token_urlsafe(18)
    url = f"{_base(request)}/handoff/{token}"
    with _lock:
        _prune(now)
        if len(_tokens) >= MAX_TOKENS:
            return _err(429, "busy", "Too many codes are open. Try again in a few seconds.")
        _tokens[token] = {"session": request.state.session, "expires": now + TTL_SECONDS, "run_id": None, "url": url}
    return {"token": token, "url": url, "qr_url": f"/api/handoff/{token}/qr.svg", "expires_in": TTL_SECONDS}


@router.get("/api/handoff/{token}/qr.svg")
def handoff_qr(token: str):
    with _lock:
        entry = _live(token)
        url = entry["url"] if entry else None
    if url is None:
        return _err(404, "not_found", "No such code.")
    out = io.BytesIO()
    segno.make(url, error="m", micro=False).save(out, kind="svg", border=4, dark="#000", light="#fff", omitsize=True)
    return Response(content=out.getvalue(), media_type="image/svg+xml", headers={"Cache-Control": "no-store"})


@router.get("/handoff/{token}")
def handoff_page(token: str):
    return FileResponse(config.WEB_DIR / "handoff.html", media_type="text/html", headers={"Cache-Control": "no-store"})


@router.get("/api/handoff/{token}")
def handoff_status(token: str, request: Request):
    with _lock:
        entry = _tokens.get(token)
        if entry is None or entry["session"] != request.state.session:
            return _err(404, "not_found", "No such code.")
        if entry["run_id"]:
            return {"status": "received", "run_id": entry["run_id"]}
        status = "waiting" if entry["expires"] > time.time() else "expired"
    return {"status": status, "run_id": None}


@router.get("/api/handoff/{token}/open")
def handoff_open(token: str):
    """The phone page asks only whether the code can still take a photo."""
    with _lock:
        return {"open": _live(token) is not None}


@router.post("/api/handoff/{token}/photo")
async def handoff_photo(token: str, request: Request, image: UploadFile = File(...)):
    if not _same_origin(request):
        return _err(400, "bad_origin", "This request did not come from the app.")
    with _lock:
        entry = _live(token)
        session = entry["session"] if entry else None
    if session is None:
        return _err(404, "expired", "This link has expired — make a new code on the other screen")
    if store.active_runs() >= config.MAX_ACTIVE_RUNS or store.active_runs(session) >= config.MAX_ACTIVE_RUNS_PER_SESSION:
        return _err(429, "busy", "Too many searches are running. Try again in a few seconds.")
    raw = await image.read(config.MAX_UPLOAD_BYTES + 1)
    if len(raw) > config.MAX_UPLOAD_BYTES:
        return _err(413, "too_large", "Choose an image up to 5 MB.")
    try:
        cleaned = clean_image(raw)
    except Exception:
        return _err(400, "unsupported_image", "Use one JPEG, PNG or WebP photo.")
    run = new_run("")
    with _lock:
        entry = _live(token)
        if entry is None:
            return _err(404, "expired", "This link has expired — make a new code on the other screen")
        entry["run_id"] = run["run_id"]
    store.create_run(run, session, cleaned)
    pipeline.start(run["run_id"])
    return {"ok": True}
