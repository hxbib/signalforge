"""Live SDK boundary for the model jobs (google-genai 3.3.0). No mock mode.

Call shapes were checked against the installed SDK:
  stable.interactions.create(model=, input=[{"type":"image","mime_type":,"data":<base64 str>}, {"type":"text","text":}],
      system_instruction=<str>, store=<bool>, generation_config={"thinking_level": ...},
      response_format={"type":"text","mime_type":"application/json","schema":<JSON Schema>})
      -> .id, .output_text, .status
  Measured live: with store=False the service answers with id == "" (there is no interaction to refer to); only a
  stored interaction has an id. interactions.delete(id=...) removes a stored one (a later get returns 404).
  RETENTION below selects stateless / delete-after-read / kept.
http_options timeout is in milliseconds; the per-call `timeout=` argument is in seconds.
"""
import base64
import json
import math
import os
import threading
import time

import app  # noqa: F401  (puts reference/ on sys.path)
from gemini_io import CARBON, DECISION, NEON

THRESHOLD = 0.75
STABLE_TIMEOUT_MS = int(os.environ.get("SF_MODEL_TIMEOUT_MS", "45000"))
BETA_TIMEOUT_MS = int(os.environ.get("SF_DECISION_TIMEOUT_MS", "15000"))
# Shopper media stays transient unless the operator decides otherwise (SF_STORE_INTERACTIONS):
#   "0" (default)  stateless call; the service issues no id, so the receipt carries "id": None
#   "delete"       stored for the duration of the call, deleted right after the output is read; the receipt carries
#                  the id the service issued and "retained": False (or "retained": True + "delete_error" if the
#                  delete did not go through)
#   "1"            stored and kept at the model service; the receipt carries the id and "retained": True
RETENTION = {"0": "none", "1": "keep", "delete": "delete"}.get(os.environ.get("SF_STORE_INTERACTIONS", "0"), "none")
# Allowed levels only (never "minimal"); a waiting shopper gets the fast level by default.
THINKING = {NEON: os.environ.get("SF_NEON_THINKING", "low"), CARBON: os.environ.get("SF_CARBON_THINKING", "low")}

_lock = threading.Lock()
_clients = None


class ModelCallError(Exception):
    """A real model call failed. Carries (job, model, safe_message); never swallowed, never defaulted."""

    def __init__(self, job, model, safe_message):
        super().__init__(f"{job} [{model}]: {safe_message}")
        self.job, self.model, self.safe_message = job, model, safe_message


def _scrub(text):
    text = " ".join(str(text).split())
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        secret = os.environ.get(name)
        if secret:
            text = text.replace(secret, "[key]")
    return text[:400]


def safe_message(exc):
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    detail = getattr(exc, "message", None) or str(exc)
    return _scrub(f"{type(exc).__name__}{' ' + str(code) if code else ''}: {detail}")


def _load_env():
    """app.config loads .env into os.environ; until that module exists read the file directly."""
    try:
        import app.config  # noqa: F401
    except ModuleNotFoundError:
        pass
    path = app.ROOT / ".env"
    if os.environ.get("GEMINI_API_KEY") or not path.is_file():
        return
    for line in path.read_text().splitlines():
        name, sep, value = line.strip().partition("=")
        if sep and name.strip() and not name.lstrip().startswith("#"):
            os.environ.setdefault(name.removeprefix("export ").strip(), value.strip().strip("'\""))


def clients():
    global _clients
    with _lock:
        if _clients is None:
            _load_env()
            if not os.environ.get("GEMINI_API_KEY"):
                raise ModelCallError("setup", "-", "GEMINI_API_KEY is not configured on the server")
            from google import genai
            _clients = (genai.Client(http_options={"api_version": "v1", "timeout": STABLE_TIMEOUT_MS}),
                        genai.Client(http_options={"api_version": "v1beta", "timeout": BETA_TIMEOUT_MS}))
        return _clients


def parts(image, note, extra=None, mime="image/jpeg"):
    if mime not in ("image/jpeg", "image/png", "image/webp") or not 0 < len(image) <= 2_500_000:
        raise ValueError("image type or size invalid")
    return [{"type": "image", "mime_type": mime, "data": base64.b64encode(image).decode()},
            {"type": "text", "text": json.dumps({"buyer_note": note, "data": extra or {}}, ensure_ascii=False)}]


def text_part(value):
    return [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}]


def wire_schema(schema):
    """Pydantic JSON Schema -> the subset the structured-output endpoint enforces ($ref inlined, no titles/defaults)."""
    doc = schema.model_json_schema()
    defs = doc.pop("$defs", {})

    def walk(node):
        if isinstance(node, list):
            return [walk(x) for x in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            return walk(defs[node["$ref"].rsplit("/", 1)[-1]])
        out = {}
        for key, value in node.items():
            if key in ("title", "default"):
                continue
            out[key] = {k: walk(v) for k, v in value.items()} if key == "properties" else walk(value)
        if out.get("type") == "object" and "properties" in out:
            # every field is requested explicitly so the reply always validates against the strict Pydantic shape
            out["required"] = list(out["properties"])
        return out

    return walk(doc)


def structured(job, model, instruction, schema, data):
    """One native structured-output Interactions call, validated by Pydantic. Returns (value, receipt)."""
    start = time.monotonic()
    try:
        stable, _ = clients()
        result = stable.interactions.create(
            model=model, input=data, system_instruction=instruction, store=RETENTION != "none",
            generation_config={"thinking_level": THINKING.get(model, "low")},
            response_format={"type": "text", "mime_type": "application/json", "schema": wire_schema(schema)})
        issued = getattr(result, "id", None) or None
        kept = _release(stable, issued) if RETENTION != "none" else {}
        text = getattr(result, "output_text", None)
        if not text:
            raise ValueError(f"empty model output (status {getattr(result, 'status', 'unknown')})")
        value = schema.model_validate_json(text)
        if RETENTION != "none" and not issued:
            raise ValueError("stored interaction returned no id")
    except ModelCallError:
        raise
    except Exception as exc:
        raise ModelCallError(job, model, safe_message(exc)) from exc
    return value, {"job": job, "model": model, "id": issued, "ms": round((time.monotonic() - start) * 1000), **kept}


def _release(stable, issued):
    """Retention facts for the receipt. In delete mode the stored interaction is removed before anything else."""
    if RETENTION == "keep" or not issued:
        return {"retained": bool(issued)}
    error = None
    for _ in range(2):
        try:
            stable.interactions.delete(id=issued)
            return {"retained": False}
        except Exception as exc:
            error = safe_message(exc)
    return {"retained": True, "delete_error": error}


QUESTION = {"target": {
    "type": "choice",
    "instructions": "Is there one distinguishable referent for the buyer's request in the visual input?",
    "criteria": {"single": "One item is in focus or explicitly indicated.",
                 "multiple": "Several plausible items and no way to choose.",
                 "other": "Image is unrelated, obscured or unreadable."}}}


def gate(data, job="gate"):
    """Gemini Decision 1 bounded choice. The Decisions response has no id; any id the service returns is passed on."""
    start = time.monotonic()
    try:
        _, beta = clients()
        result = beta.decisions.create(model=DECISION, input=data, questions=QUESTION)
        answer = (result.answers or {})["target"]
        choice, confidence = answer.choice, float(answer.confidence)
        if choice not in QUESTION["target"]["criteria"]:
            raise ValueError("decision returned an unknown choice")
        if not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise ValueError("invalid decision confidence")
    except ModelCallError:
        raise
    except Exception as exc:
        raise ModelCallError(job, DECISION, safe_message(exc)) from exc
    return {"pass": choice == "single" and confidence >= THRESHOLD, "choice": choice, "confidence": confidence,
            "threshold": THRESHOLD, "id": getattr(result, "id", None), "model": DECISION,
            "ms": round((time.monotonic() - start) * 1000)}
