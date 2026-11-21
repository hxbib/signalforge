"""HTTP surface: one upload route, one polling route, the lane routers and the static browser app."""
import importlib
import logging
import secrets
import subprocess
from urllib.parse import urlsplit

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from app import ROOT, config, pipeline, store
from app.contracts import new_run, public
from live_smoke import clean_image

LOG = logging.getLogger("signalforge")
app = FastAPI(title="SignalForge", docs_url=None, redoc_url=None, openapi_url=None)


@app.on_event("startup")
def _recover_interrupted_runs() -> None:
    count = pipeline.recover_interrupted()
    if count:
        LOG.warning("%d interrupted searches closed at startup", count)


def _commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              timeout=3).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


COMMIT = _commit()


def err(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


@app.middleware("http")
async def session_cookie(request: Request, call_next):
    token = request.cookies.get("sf_session") or ""
    fresh = len(token) < 20
    if fresh:
        token = secrets.token_urlsafe(24)
    request.state.session = token
    response = await call_next(request)
    if fresh:
        https = request.headers.get("x-forwarded-proto") == "https" or request.url.scheme == "https"
        response.set_cookie("sf_session", token, httponly=True, samesite="lax", max_age=86400, secure=https)
    if request.url.path == "/" or request.url.path.startswith(("/static", "/proof")):
        response.headers["Cache-Control"] = "no-cache"
    return response


def _same_origin(request: Request) -> bool:
    origin = request.headers.get("origin")
    if not origin:
        return True
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    return urlsplit(origin).netloc == host


def _owned(request: Request, run_id: str) -> dict | None:
    if store.owner(run_id) != request.state.session:
        return None
    return store.get_run(run_id)


@app.post("/api/search", status_code=202)
async def create_search(request: Request, image: UploadFile = File(...), note: str = Form("")):
    if not _same_origin(request):
        return err(400, "bad_origin", "This request did not come from the app.")
    session = request.state.session
    if store.active_runs() >= config.MAX_ACTIVE_RUNS or store.active_runs(session) >= config.MAX_ACTIVE_RUNS_PER_SESSION:
        return err(429, "busy", "Too many searches are running. Try again in a few seconds.")
    raw = await image.read(config.MAX_UPLOAD_BYTES + 1)
    if len(raw) > config.MAX_UPLOAD_BYTES:
        return err(413, "too_large", "Choose an image up to 5 MB.")
    try:
        cleaned = clean_image(raw)
    except Exception:
        return err(400, "unsupported_image", "Use one JPEG, PNG or WebP photo.")
    run = new_run((note or "").strip()[:500])
    store.create_run(run, session, cleaned)
    pipeline.start(run["run_id"])
    return {"run_id": run["run_id"], "revision": run["revision"], "state": run["state"]}


@app.get("/api/search/{run_id}")
def read_search(run_id: str, request: Request):
    run = _owned(request, run_id)
    if run is None:
        return err(404, "not_found", "No such search.")
    return public(run)


@app.get("/api/search/{run_id}/image")
def read_image(run_id: str, request: Request):
    if _owned(request, run_id) is None:
        return err(404, "not_found", "No such search.")
    return Response(content=store.get_image(run_id), media_type="image/jpeg", headers={"Cache-Control": "private, max-age=600"})


@app.post("/api/search/{run_id}/answer", status_code=202)
async def answer_search(run_id: str, request: Request):
    if not _same_origin(request):
        return err(400, "bad_origin", "This request did not come from the app.")
    run = _owned(request, run_id)
    if run is None:
        return err(404, "not_found", "No such search.")
    try:
        body = await request.json()
    except ValueError:
        return err(400, "bad_request", "Send a JSON body.")
    question = run.get("question")
    if (not question or run["state"] != "needs_detail" or body.get("revision") != run["revision"]
            or body.get("question_id") != question["question_id"]):
        return err(409, "stale", "That question is no longer open.")
    if question["kind"] == "choice":
        choice = body.get("choice")
        if choice not in {o["id"] for o in question["options"]}:
            return err(400, "bad_choice", "Pick one of the offered choices.")
        answer = {"question": question, "choice": choice}
    else:
        text = str(body.get("text") or "").strip()[:200]
        if not text:
            return err(400, "empty_answer", "Type a few words.")
        answer = {"question": question, "text": text}
    run["revision"] += 1
    run.update(question=None, state="searching", error=None)
    run["_ended_ms"] = None
    store.save_run(run)
    pipeline.resume(run_id, answer)
    return {"run_id": run_id, "revision": run["revision"], "state": run["state"]}


@app.get("/api/search/{run_id}/proof")
def read_proof(run_id: str, request: Request):
    run = _owned(request, run_id)
    if run is None:
        return err(404, "not_found", "No such search.")
    return {"receipts": run["receipts"], "decision": run["decision"], "evaluation": run.get("_evaluation"),
            "coverage": run["coverage"]}


LANES = {}
for _name in ("app.models", "app.merchants.shop", "app.merchants.chains", "app.merchants.compare", "app.watch"):
    try:
        importlib.import_module(_name)
        LANES[_name] = True
    except Exception as exc:  # a lane that is not importable is reported, never faked
        LANES[_name] = False
        LOG.warning("lane %s not loaded: %s", _name, type(exc).__name__)
for _name in ("app.watch.routes", "app.evidence.routes"):
    try:
        app.include_router(importlib.import_module(_name).router)
        LANES[_name] = True
    except Exception as exc:
        LANES[_name] = False
        LOG.warning("router %s not loaded: %s", _name, type(exc).__name__)


@app.get("/api/health")
def health():
    return {"ok": True, "key_present": config.key_present(), "commit": COMMIT, "models": config.MODEL_IDS, "lanes": LANES}


def _page(name: str):
    path = config.WEB_DIR / name
    if not path.is_file():
        return err(503, "ui_not_built", "The browser app is not built yet.")
    return FileResponse(path, media_type="text/html")


@app.get("/")
def index():
    return _page("index.html")


@app.get("/proof")
def proof_page():
    return _page("proof.html")


app.mount("/static", StaticFiles(directory=config.WEB_DIR), name="static")
