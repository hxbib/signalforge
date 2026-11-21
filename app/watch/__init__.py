"""Explicit, tested, reversible in-app watch. Wraps reference autowatch / proof / ledger / core.

A watch only becomes active when an authorized merchant feed for the exact seller + variant is
configured server-side (SIGNALFORGE_WATCH_FEED). Otherwise it is an honest rule preview. Nothing here
polls a merchant or shop.dev."""
from collections import namedtuple
from dataclasses import asdict
import json
import os
import re
from pathlib import Path
import sqlite3
import threading
import time

import app  # noqa: F401  (puts reference/ on sys.path)
from autowatch import CAP, NEGATION, advance
from core import MAX_GAP, Rule, Seen, digest
from gemini_io import Proposal, bound_rule
from ledger import Ledger, approval_digest

PREVIEW_REASON = ("No authorized merchant feed with repeated-read rights is connected, so this is a rule preview. "
                  "Nothing is polled.")
ASK_WHAT = "What should I watch for?"
ASK_CAP = "What is the highest item price, in USD, that should trigger the alert?"
ASK_RESTATE = ("I could not turn every condition in your words into one rule. Restate it, for example: "
               "\"Notify me when this seller restocks under $25\".")
ASK_ONE_STORE = ("I can only watch one store at a time, so I can't watch every store. Name the store, or say "
                 "\"this seller\" to watch the store shown.")
ANY_STORE = re.compile(r"\b(?:any|every|all|other)\s+(?:stores?|sellers?|shops?|retailers?|merchants?|sites?)\b|\banywhere\b", re.I)
MAX_FEED_BYTES = 1_000_000

Binding = namedtuple("Binding", "seller variant")

WHY = {
    "baseline": "The first reading only sets the baseline, so it cannot alert.",
    "stock_returned": "It was unavailable at the previous reading and is now in stock within the cap.",
    "already_in_stock": "It was already in stock at the previous reading, so this is not a restock.",
    "entered_offer": "This is the first reading where it is both in stock and within the cap.",
    "already_eligible": "It already qualified at the previous reading, so nothing new happened.",
    "outside_price_limit": "It is in stock, but the price does not satisfy the cap.",
    "currently_unavailable": "It is not in stock at this reading.",
    "continuity_lost": f"The gap since the previous usable reading exceeds {MAX_GAP} s or was unusable, "
                       "so no change can be proven.",
    "source_unknown": "The feed reported an error or missing data, so nothing can be concluded.",
    "identity_mismatch": "This reading is for a different seller or variant than the watched one.",
    "currency_unverified": "The price is not in USD, so it cannot be compared with the cap.",
    "incomplete_observation": "The reading lacks a stock state or a price.",
    "duplicate": "The same reading arrived again; it is counted once.",
    "out_of_order": "This reading is older than one already processed, so it is ignored.",
    "conflicting_duplicate": "A different reading reused an earlier reading id, so it is rejected.",
    "invalid_time": "The reading has an invalid timestamp.",
}

_lock = threading.Lock()


class WatchError(Exception):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


# ---------- persistence ----------

def _db_path():
    path = Path(os.environ.get("SF_DB") or app.ROOT / "data" / "signalforge.sqlite")
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


def _open():
    ledger = Ledger(_db_path())
    ledger.db.execute("CREATE TABLE IF NOT EXISTS app_watch(id TEXT PRIMARY KEY, run_id TEXT NOT NULL, "
                      "status TEXT NOT NULL, doc TEXT NOT NULL, created_at INTEGER NOT NULL)")
    return ledger


def _load(ledger, watch_id):
    row = ledger.db.execute("SELECT doc FROM app_watch WHERE id=?", (watch_id,)).fetchone()
    return json.loads(row[0]) if row else None


def get(watch_id: str) -> dict | None:
    ledger = _open()
    try:
        return _load(ledger, watch_id)
    finally:
        ledger.close()


def pause(watch_id: str) -> dict:
    with _lock:
        ledger = _open()
        try:
            with ledger.write():
                doc = _load(ledger, watch_id)
                if doc is None:
                    raise WatchError("not_found", "Unknown watch.", 404)
                if doc["status"] != "paused":
                    doc["paused_from"] = doc["status"]
                    doc["status"] = "paused"
                    ledger.db.execute("UPDATE app_watch SET status='paused', doc=? WHERE id=?",
                                      (json.dumps(doc), watch_id))
                ledger.db.execute("UPDATE watch SET active=0 WHERE id=?", (watch_id,))
            return doc
        finally:
            ledger.close()


# ---------- source permission ----------

def _feed_binding(seller, variant):
    """The server-side authorized feed for this exact seller + variant, or None."""
    path = os.environ.get("SIGNALFORGE_WATCH_FEED", "").strip()
    if not path:
        return None
    try:
        with Path(path).open("rb") as f:
            raw = f.read(MAX_FEED_BYTES + 1)
        if len(raw) > MAX_FEED_BYTES:
            return None
        doc = json.loads(raw)
        feed_id = doc["feed_id"]
        matches = [o for o in doc["observations"] if (o.get("seller"), o.get("variant")) == (seller, variant)]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    if not isinstance(feed_id, str) or not feed_id or len(matches) != 1:
        return None
    return {"source": "approved_feed", "feed_id": feed_id, "seller": seller, "variant": variant}


# ---------- candidates and wording ----------

def _money(cents):
    return f"${cents // 100}.{cents % 100:02d}"


def candidates_of(run: dict) -> list[dict]:
    result = (run or {}).get("result") or {}
    offers = list(result.get("winners") or [])
    if result.get("near_miss"):
        offers.append(result["near_miss"].get("offer"))
    offers += [r.get("offer") for r in result.get("rejected") or []]
    seen, out = set(), []
    for o in offers:
        if not o or not o.get("verified") or not o.get("seller") or not o.get("listing"):
            continue
        key = (o["seller"], o["listing"])
        if key in seen:
            continue
        seen.add(key)
        out.append({"slot": len(out), "seller": o["seller"], "seller_name": o.get("seller_name") or o["seller"],
                    "listing": o["listing"], "title": o.get("title") or "", "item_cents": o.get("item_cents"),
                    "currency": o.get("currency"), "stock": o.get("stock"), "url": o.get("url")})
    return out


def scope_sentence(rule: Rule, name: str) -> str:
    bound = "below" if rule.op == "lt" else "at or below"
    if rule.mode == "restock":
        return f"Watching {name} for a restock {bound} {_money(rule.cents)}"
    return f"Watching {name} for the first time it is in stock {bound} {_money(rule.cents)}"


def _state(seen: Seen) -> str:
    stock = "in stock" if seen.available else "sold out"
    return f"{stock} at {_money(seen.cents)}"


def _witness_question(world, name):
    text = (f"Should this alert you: {name} goes from {_state(world.before)} "
            f"to {_state(world.after)} a minute later?")
    return {"question_id": world.key, "text": text, "kind": "choice",
            "options": [{"id": "yes", "label": "Yes, alert me"}, {"id": "no", "label": "No"}],
            "world": world.payload()}


def _text_question(text, qid):
    return {"question_id": qid, "text": text, "kind": "text", "options": []}


# ---------- simulation (same evaluator as real observations: Ledger.ingest -> core.decide) ----------

def _rule_of(watch):
    r = watch.get("rule") or {}
    return Rule(r["seller"], r["variant"], r["mode"], r["op"], r["cents"])


def what_if(watch: dict, timeline: list[dict]) -> list[dict]:
    """Replay a hypothetical timeline through an in-memory Ledger. Every row is a simulation."""
    rule = _rule_of(watch)
    sim = Ledger(":memory:")
    try:
        sim.db.execute("INSERT INTO watch(id,rule,binding,bundle,proof) VALUES('sim',?,?,?,?)",
                       (json.dumps(asdict(rule)), json.dumps({"source": "simulation"}), "simulation", "{}"))
        rows = []
        for i, p in enumerate(timeline or []):
            if not isinstance(p, dict):
                raise WatchError("bad_timeline", "Each timeline point must be an object.")
            t = p.get("t", i * 60)
            cents = p.get("cents")
            seen = Seen(p.get("seller", rule.seller), p.get("variant", rule.variant), p.get("available"),
                        cents, p.get("currency", "USD"), t, p.get("status", "ok"))
            attempt = str(p.get("attempt") or f"source-{t}")
            before = sim.db.execute("SELECT count(*) FROM alert").fetchone()[0]
            try:
                reason = sim.ingest("sim", attempt, seen, {"simulation": True})
            except ValueError as err:
                reason = "conflicting_duplicate" if "duplicate" in str(err) else "invalid_time"
            except (sqlite3.IntegrityError, TypeError):
                reason = "out_of_order" if type(t) is int else "invalid_time"
            alerts = sim.db.execute("SELECT count(*) FROM alert").fetchone()[0] > before
            avail = {True: "in stock", False: "sold out"}.get(seen.available, "stock unknown")
            price = _money(cents) if type(cents) is int and cents >= 0 else "no price"
            label = f"t={t}s · {avail} · {price}"
            if seen.currency != "USD":
                label += f" {seen.currency}"
            if (seen.seller, seen.variant) != (rule.seller, rule.variant):
                label += " · other variant"
            if seen.status != "ok":
                label += f" · {seen.status}"
            rows.append({"label": label, "alerts": alerts, "why": WHY.get(reason, reason), "reason": reason,
                         "simulation": True})
        return rows
    finally:
        sim.close()


def _scenarios(rule: Rule):
    k = rule.cents
    up = k + max(100, k // 10)
    other = rule.variant + "-other"
    return [
        ("Price drops below the cap while already in stock", [(0, True, up), (60, True, k - 1)]),
        ("Comes back in stock at exactly the cap", [(0, False, k), (60, True, k)]),
        ("Comes back in stock just under the cap", [(0, False, k - 1), (60, True, k - 1)]),
        ("Restocks above the cap", [(0, False, up), (60, True, up)]),
        (f"Feed gap longer than {MAX_GAP} s, then a restock", [(0, False, k - 1), (MAX_GAP + 50, True, k - 1)]),
        ("First reading is already in stock under the cap", [(0, True, k - 1)]),
        ("A different variant comes back in stock", [(0, False, k - 1), (60, True, k - 1, {"variant": other})]),
        ("The same restock reading is delivered twice", [(0, False, k - 1), (60, True, k - 1), (60, True, k - 1)]),
        ("Restock priced in CAD", [(0, False, k - 1), (60, True, k - 1, {"currency": "CAD"})]),
    ]


def playground(watch: dict) -> list[dict]:
    rule = _rule_of(watch)
    out = []
    for label, steps in _scenarios(rule):
        timeline = [{"t": s[0], "available": s[1], "cents": s[2], **(s[3] if len(s) > 3 else {})} for s in steps]
        rows = what_if(watch, timeline)
        fired = [r for r in rows if r["alerts"]]
        decisive = fired[0] if fired else rows[-1]
        why = decisive["why"]
        if fired and len(rows) > len(fired) and rows[-1]["reason"] == "duplicate":
            why += " " + WHY["duplicate"]
        out.append({"label": label, "alerts": bool(fired), "why": why, "simulation": True,
                    "timeline": timeline})
    return out


# ---------- create ----------

def _compile(image, command, candidates):
    from app.models import compile_watch
    return compile_watch(image, command, candidates)


def _image_for(run_id):
    from app import store
    return store.get_image(run_id)


def _base(watch_id, run, command, status):
    return {"watch_id": watch_id, "run_id": run.get("run_id"), "status": status, "command": command,
            "scope_sentence": "", "rule": None, "origin": None, "proof_hash": None,
            "pause_url": f"/api/watch/{watch_id}/pause", "preview_reason": None, "question": None,
            "playground": [], "simulation_note": "Playground rows are hypothetical timelines, not observations.",
            "draft": None, "receipts": [], "created_at": int(time.time())}


def _persist(ledger, doc):
    ledger.db.execute("INSERT OR IGNORE INTO app_watch(id,run_id,status,doc,created_at) VALUES(?,?,?,?,?)",
                      (doc["watch_id"], doc["run_id"] or "", doc["status"], json.dumps(doc), doc["created_at"]))


def create(run: dict, command: str, replies: dict | None = None, *, compile=None, image=None) -> dict:
    """run = finished Run JSON; command = buyer's verbatim words; replies = {witness_id: bool} from real taps."""
    replies = {k: v for k, v in (replies or {}).items() if type(v) is bool}
    command = (command or "").strip()
    run_id = run.get("run_id") or ""
    watch_id = "w_" + digest({"run": run_id, "command": command, "replies": sorted(replies.items())})[:20]
    existing = get(watch_id)
    if existing is not None:
        return existing
    doc = _base(watch_id, run, command, "needs_detail")
    if not command:
        doc["question"] = _text_question(ASK_WHAT, "q_watch_words")
        return _store(doc)
    if ANY_STORE.search(command):
        doc["question"] = _text_question(ASK_ONE_STORE, "q_watch_scope")
        return _store(doc)
    candidates = candidates_of(run)
    if not candidates:
        doc["question"] = _text_question("There is no verified offer to watch yet. Which store should I watch?",
                                         "q_watch_seller")
        return _store(doc)
    if image is None:
        image = _image_for(run_id)
    if not image:
        raise WatchError("no_image", "The photo for this search is no longer available.", 404)
    compiled = (compile or _compile)(image, command, candidates)
    doc["receipts"] = [compiled.get("receipt")]
    proposal = Proposal.model_validate(compiled["proposal"])
    doc["draft"] = {"candidate": proposal.candidate, "mode": proposal.mode, "op": proposal.op,
                    "cents": proposal.cents, "status": proposal.status, "reason": proposal.reason,
                    "cap_evidence": proposal.cap_evidence, "binding_evidence": proposal.binding_evidence,
                    "blockers": proposal.blockers, "editable": True}
    if proposal.status != "ready":
        ask = (ASK_CAP if proposal.reason == "missing_budget" or len(CAP.findall(command)) != 1
               else proposal.question.strip() or ASK_RESTATE)
        doc["question"] = _text_question(ask, f"q_watch_{proposal.reason}")
        return _store(doc)
    try:
        draft = bound_rule(proposal, [Binding(c["seller"], c["listing"]) for c in candidates])
    except ValueError:
        doc["question"] = _text_question("Which store's listing should I watch?", "q_watch_seller")
        return _store(doc)
    cand = candidates[proposal.candidate]
    name = cand["seller_name"]
    doc["rule"] = asdict(draft)
    doc["seller_name"], doc["title"], doc["url"] = name, cand["title"], cand["url"]
    try:
        result = advance(draft, command, replies)
    except ValueError:
        doc["question"] = _text_question("That answer no longer matches this rule. What should I watch for?",
                                         "q_watch_stale")
        return _store(doc)
    if not result["ready"]:
        if "question" in result:
            doc["question"] = _witness_question(result["question"], name)
        elif len(CAP.findall(command)) != 1:
            doc["question"] = _text_question(ASK_CAP, "q_watch_cap")
        else:
            doc["question"] = _text_question(ASK_RESTATE, "q_watch_restate")
        doc["scope_sentence"] = "Draft: " + scope_sentence(draft, name)
        doc["playground"] = playground(doc)
        return _store(doc)
    rule = result["rule"]
    origins = result["origins"]
    doc.update(rule=asdict(rule), scope_sentence=scope_sentence(rule, name), proof_hash=result["proof"]["proof_hash"],
               origin="buyer" if any(o["origin"] == "buyer" for o in origins) else "explicit_request",
               origins=origins, proof={"suite": result["proof"]["suite"], "rule_hash": result["proof"]["rule_hash"],
                                       "checks": len(result["proof"]["rows"]),
                                       "passed": sum(r["pass"] for r in result["proof"]["rows"])})
    doc["playground"] = playground(doc)
    binding = _feed_binding(rule.seller, rule.variant)
    if binding is None:
        doc["status"], doc["preview_reason"] = "preview_only", PREVIEW_REASON
        return _store(doc)
    doc["status"], doc["feed_id"] = "active", binding["feed_id"]
    capture = digest({"run": run_id, "command": command})
    bundle = approval_digest(binding, capture, result["proof"])
    return _store(doc, activation=(draft, result["answers"], rule, binding, capture, bundle))


def _store(doc, activation=None):
    with _lock:
        ledger = _open()
        try:
            if activation:
                draft, answers, rule, binding, capture, bundle = activation
                ledger.activate(doc["watch_id"], draft, answers, rule, binding, capture, bundle)
            with ledger.write():
                _persist(ledger, doc)
            return _load(ledger, doc["watch_id"])
        finally:
            ledger.close()
