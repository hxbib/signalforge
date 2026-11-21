"""Shared constants and view helpers for the Run JSON described in docs/CONTRACT.md."""
import time
import uuid

WORKING = ("reading", "searching", "verifying")
TERMINAL = ("complete", "partial", "needs_detail", "unavailable")
ASSUMPTIONS = [
    {"id": "condition", "label": "New"},
    {"id": "market", "label": "US online sellers"},
    {"id": "basis", "label": "Item price before shipping & tax"},
    {"id": "offer", "label": "No membership or coupon"},
]
BASIS_LABEL = "Item price · USD · before shipping & tax"


def new_run(note: str) -> dict:
    now = int(time.time())
    run_id = "run_" + uuid.uuid4().hex
    return {
        "run_id": run_id, "revision": 1, "state": "reading", "created_at": now, "updated_at": now, "elapsed_ms": 0,
        "image_url": f"/api/search/{run_id}/image", "note": note, "assumptions": [dict(a) for a in ASSUMPTIONS],
        "progress": [], "target": None, "decision": None, "goal": None, "question": None, "result": None,
        "coverage": None, "receipts": {"models": [], "sources": []}, "watch": None, "error": None,
        "_started_ms": int(time.time() * 1000), "_ended_ms": None, "_ctx": {},
    }


def public(run: dict) -> dict:
    """The document the browser sees: private bookkeeping keys (leading underscore) are removed."""
    out = {k: v for k, v in run.items() if not k.startswith("_")}
    started = run.get("_started_ms") or run["created_at"] * 1000
    ended = run.get("_ended_ms") or int(time.time() * 1000)
    out["elapsed_ms"] = max(0, ended - started)
    return out


def offer_view(row: dict, verified: bool = False) -> dict:
    cents = row.get("item_cents")
    return {
        "seller": row.get("seller"), "seller_name": row.get("seller_name") or row.get("seller_domain") or "Unknown merchant",
        "listing": row.get("listing"), "title": row.get("title") or "", "url": row.get("url"),
        "image_url": row.get("image_url"), "item_cents": cents if type(cents) is int else None,
        "currency": row.get("currency"), "condition": row.get("condition") or "unknown",
        "stock": row.get("stock") or "unknown", "fulfillment": row.get("fulfillment") or "",
        "source": row.get("source"), "read_at": row.get("read_at"), "verified": bool(verified),
        "confirmed_by": list(row.get("confirmed_by") or []), "identity_basis": row.get("identity_basis"),
        "gtin": row.get("gtin"),
    }
