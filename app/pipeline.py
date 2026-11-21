"""One photo in, one honest answer out.

Orchestrates the lanes (models, shop catalog, merchant confirmation, ranking, watch) and records only what
really happened: every progress row, receipt and number in a run comes from a call that was actually made.
"""
import importlib
import logging
import sys
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor

from app import store
from app.config import GATE_THRESHOLD
from app.contracts import BASIS_LABEL, offer_view

LOG = logging.getLogger("signalforge.pipeline")
_runs = ThreadPoolExecutor(max_workers=8, thread_name_prefix="run")

BASIS_WORDS = {
    "barcode": "barcode on the item matches",
    "model_number": "brand and printed model number match the catalog",
    "buyer_choice": "chosen by you",
}


class Superseded(Exception):
    """A newer revision of this run exists; this worker must stop writing."""


class LaneNotReady(Exception):
    pass


class CatalogError(Exception):
    pass


def _lane(name: str):
    try:
        return importlib.import_module(name)
    except ImportError as exc:
        raise LaneNotReady(name) from exc


def _now_ms() -> int:
    return int(time.time() * 1000)


class Steps:
    """Progress rows for one worker. Saving checks the revision so a late worker never overwrites a newer run."""

    def __init__(self, run: dict):
        self.run = run
        self.revision = run["revision"]
        self._t: dict[str, float] = {}

    def save(self) -> None:
        current = store.get_run(self.run["run_id"])
        if current is None or current["revision"] != self.revision:
            raise Superseded()
        if current.get("watch") and not self.run.get("watch"):
            self.run["watch"] = current["watch"]
        store.save_run(self.run)

    def _row(self, sid: str, label: str | None = None) -> dict:
        for row in self.run["progress"]:
            if row["id"] == sid:
                return row
        row = {"id": sid, "label": label or sid, "status": "running", "ms": None, "detail": ""}
        self.run["progress"].append(row)
        return row

    def start(self, sid: str, label: str) -> None:
        self._row(sid, label).update(label=label, status="running", ms=None, detail="")
        self._t[sid] = time.monotonic()
        self.save()

    def end(self, sid: str, status: str, detail: str = "", ms: int | None = None) -> None:
        row = self._row(sid)
        if ms is None and sid in self._t:
            ms = round((time.monotonic() - self._t[sid]) * 1000)
        row.update(status=status, detail=detail, ms=ms)
        self.save()


def start(run_id: str) -> None:
    _runs.submit(_guard, run_id, None)


def resume(run_id: str, answer: dict) -> None:
    _runs.submit(_guard, run_id, answer)


def _classify(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, LaneNotReady):
        return "lane_not_ready", "This build is still wiring that step."
    if isinstance(exc, CatalogError):
        return "catalog_unavailable", str(exc)
    if type(exc).__name__ == "ModelCallError":
        args = list(getattr(exc, "args", ())) + ["", "", ""]
        job = getattr(exc, "job", None) or args[0]
        model = getattr(exc, "model", None) or args[1]
        safe = getattr(exc, "safe_message", None) or args[2] or "the call failed"
        return "model_call_failed", f"{model} could not complete the {job} step: {str(safe)[:200]}"
    return "internal", f"Unexpected {type(exc).__name__} while working on this photo."


def _guard(run_id: str, answer: dict | None) -> None:
    run = store.get_run(run_id)
    image = store.get_image(run_id)
    if run is None or image is None:
        return
    steps = Steps(run)
    try:
        if answer is None:
            _fresh(run, image, steps)
        else:
            _answered(run, image, steps, answer)
        run["_ended_ms"] = _now_ms()
        steps.save()
    except Superseded:
        return
    except Exception as exc:  # the failure is surfaced to the buyer, never papered over
        code, message = _classify(exc)
        print(f"[pipeline] run {run_id} failed: {code}: {type(exc).__name__}", file=sys.stderr)
        traceback.print_exc(limit=6, file=sys.stderr)
        for row in run["progress"]:
            if row["status"] == "running":
                row["status"] = "failed"
                row["detail"] = row["detail"] or message
        run["state"] = "unavailable"
        run["error"] = {"code": code, "message": message}
        run["_ended_ms"] = _now_ms()
        try:
            steps.save()
        except Superseded:
            pass


def _valid_gtin(value) -> str | None:
    from compare_offers import gtin
    try:
        gtin(value)
    except (ValueError, TypeError):
        return None
    return value


def _view_detail(visual: dict) -> str:
    bits = [visual.get("name") or "item"]
    ident = " ".join(x for x in (visual.get("brand"), visual.get("part_number")) if x)
    if ident:
        bits.append(ident)
    if _valid_gtin(visual.get("barcode")):
        bits.append("barcode " + visual["barcode"])
    return " · ".join(bits)


def _goal_detail(intent: dict) -> str:
    basis = "item price" if intent.get("basis") == "item" else "delivered total"
    place = {"online_us": "US online", "ship": "shipped", "pickup": "store pickup"}.get(intent.get("fulfillment"), "US online")
    words = ["compare + watch" if intent.get("operation") == "compare_and_watch" else "compare", basis, place]
    return " · ".join(words)


def _scoped_out(intent: dict) -> list[str]:
    out = []
    if intent.get("market") not in (None, "US"):
        out.append("Not compared: a market outside the US")
    if intent.get("basis") == "delivered":
        out.append("Not compared: delivered total (needs a destination, shipping and tax evidence)")
    if intent.get("fulfillment") in ("ship", "pickup"):
        where = intent.get("location") or "a specific place"
        out.append(f"Not compared: {intent['fulfillment']} for {where}. Showing US online item prices")
    for item in intent.get("unsupported") or []:
        out.append(f"Not compared: {str(item)[:80]}")
    return out


def _empty_result(rows: list[dict], status: str) -> dict:
    return {"status": status, "headline": "No verified offer yet", "price_cents": None, "currency": "USD",
            "basis_label": BASIS_LABEL, "winners": [], "near_miss": None, "rejected": [],
            "leads": [offer_view(r) for r in rows[:8]]}


def _catalog_coverage(rows: list[dict], receipts: list[dict]) -> dict:
    ms = sum(r.get("ms") or 0 for r in receipts) or None
    sellers = len({r.get("seller") for r in rows if r.get("seller")})
    return {"attempted": 1, "responded": 1, "sellers_seen": sellers, "sellers_eligible": 0,
            "checked_at": int(time.time()),
            "sources": [{"id": "shop_catalog", "name": "Shop catalog (shop.dev)", "kind": "catalog", "status": "ok",
                         "offers": len(rows), "eligible": 0, "ms": ms, "note": ""}]}


def _fresh(run: dict, image: bytes, steps: Steps) -> None:
    models = _lane("app.models")
    note = run["note"]
    run.update(state="reading", question=None, result=None, coverage=None, error=None, target=None, decision=None,
               goal=None, progress=[], receipts={"models": [], "sources": []})
    run["assumptions"] = [a for a in run["assumptions"] if not a["id"].startswith("scoped_")]
    steps.start("view", "Gemini 4 Neon read the photo")
    steps.start("gate", "Gemini Decision 1 checked the target")
    steps.start("goal", "Gemini 4 Carbon read the request")
    with ThreadPoolExecutor(max_workers=1) as pool:
        goal_job = pool.submit(models.parse_goal, image, note)
        seen = models.inspect(image, note)
        visual, decision = seen["visual"], dict(seen["decision"])
        decision.setdefault("threshold", GATE_THRESHOLD)
        receipts = {r.get("job"): r for r in seen.get("receipts", [])}
        run["receipts"]["models"] = list(seen.get("receipts", []))
        run["decision"] = {k: decision.get(k) for k in ("choice", "confidence", "pass", "threshold", "ms", "id", "model")}
        run["target"] = {"name": visual.get("name") or "", "brand": visual.get("brand") or "",
                         "mpn": visual.get("part_number") or "", "gtin": _valid_gtin(visual.get("barcode")), "upid": "",
                         "title": "", "variant_details": list(visual.get("variant_details") or []),
                         "evidence": list(visual.get("evidence") or []), "identity_source": "", "identity_basis": None,
                         "bound": False}
        steps.end("view", "done", _view_detail(visual), receipts.get("view", {}).get("ms"))
        gate_detail = f"{decision.get('choice')} · confidence {float(decision.get('confidence') or 0):.2f}"
        if seen.get("recovery"):
            gate_detail += " · after one automatic closer look"
        steps.end("gate", "done", gate_detail, receipts.get("gate", {}).get("ms") or decision.get("ms"))
        parsed = goal_job.result()
    intent = parsed["intent"]
    run["goal"] = intent
    run["receipts"]["models"].append(parsed["receipt"])
    steps.end("goal", "done", _goal_detail(intent), parsed["receipt"].get("ms"))
    scoped = _scoped_out(intent)
    for i, text in enumerate(scoped):
        run["assumptions"].append({"id": f"scoped_{i}", "label": text})
    working_image = seen.get("image") or image
    scan = None
    try:
        scan = models.scan(working_image, visual.get("focus_box") or None)
    except Exception:  # a local decoder finding nothing is not a failure of the run
        scan = None
    ctx = {"visual": visual, "decision": decision, "intent": intent, "scan": scan, "scoped": bool(scoped)}
    run["_ctx"] = ctx
    _identify(run, working_image, steps, ctx)


def _identify(run: dict, image: bytes, steps: Steps, ctx: dict, chosen: str | None = None) -> None:
    visual, decision, scan = ctx["visual"], ctx["decision"], ctx.get("scan")
    shop = _lane("app.merchants.shop")
    run["state"] = "searching"
    steps.start("identity", "Catalog identity evidence from shop.dev")
    try:
        found = shop.anchors_for(visual, image)
    except Exception as exc:
        raise CatalogError(f"The Shop catalog could not be read ({type(exc).__name__}).") from exc
    anchors, rows = list(found.get("anchors") or []), list(found.get("rows") or [])
    run["receipts"]["sources"] = list(found.get("receipts") or [])
    sellers = len({r.get("seller") for r in rows if r.get("seller")})
    steps.end("identity", "done", f"{len(anchors)} catalog products · {len(rows)} listings · {sellers} sellers")
    if not anchors:
        run["result"] = _empty_result(rows, "no_verified_offer")
        run["coverage"] = _catalog_coverage(rows, run["receipts"]["sources"])
        run["state"] = "complete"
        return
    if chosen is not None:
        anchor = next((a for a in anchors if a.get("upid") == chosen), None)
        steps.start("bind", "Exact product chosen")
        if anchor is None:
            steps.end("bind", "failed", "That product is no longer in the catalog results")
            run["result"] = _empty_result(rows, "needs_identity")
            run["coverage"] = _catalog_coverage(rows, run["receipts"]["sources"])
            run["state"] = "complete"
            return
        steps.end("bind", "done", f"{anchor.get('title')} · {BASIS_WORDS['buyer_choice']}")
        _compare(run, steps, ctx, anchor, "buyer_choice", rows)
        return
    gate_ok = bool(decision.get("pass")) or bool(scan)
    if not gate_ok or visual.get("target_count") != "one":
        text = ("I could not make out one product in this photo. What should I look for? A few words are enough."
                if decision.get("choice") == "other" else
                "I can see more than one possible item. Which one should I compare? A few words are enough.")
        run["question"] = {"question_id": "q_" + uuid.uuid4().hex[:12], "kind": "text", "text": text, "options": []}
        run["result"] = _empty_result(rows, "needs_identity")
        run["coverage"] = _catalog_coverage(rows, run["receipts"]["sources"])
        run["state"] = "needs_detail"
        return
    models = _lane("app.models")
    steps.start("bind", "Gemini 4 Carbon bound the exact product")
    bound = models.bind(image, run["note"], anchors, visual, decision, scan)
    receipt = bound.get("receipt") or {}
    if receipt:
        run["receipts"]["models"].append(receipt)
    anchor = bound.get("anchor")
    if anchor is None:
        steps.end("bind", "done", "No exact product could be confirmed from the photo alone", receipt.get("ms"))
        options = [{"id": a["upid"], "label": f"{a.get('title')} ({a.get('sellers') or 1} sellers)"} for a in anchors[:4]]
        options.append({"id": "none", "label": "None of these"})
        run["question"] = {"question_id": "q_" + uuid.uuid4().hex[:12], "kind": "choice",
                           "text": "I could not confirm the exact product from the photo alone. Is it one of these?",
                           "options": options}
        run["result"] = _empty_result(rows, "needs_identity")
        run["coverage"] = _catalog_coverage(rows, run["receipts"]["sources"])
        run["state"] = "needs_detail"
        return
    basis = bound.get("basis") or "model_number"
    steps.end("bind", "done", f"{anchor.get('title')} · {BASIS_WORDS.get(basis, basis)}", receipt.get("ms"))
    _compare(run, steps, ctx, anchor, basis, rows)


def _compare(run: dict, steps: Steps, ctx: dict, anchor: dict, basis: str, rows: list[dict]) -> None:
    compare = _lane("app.merchants.compare")
    visual, intent = ctx["visual"], ctx["intent"]
    target = {"upid": anchor.get("upid"),
              "gtin": anchor.get("gtin") or ctx.get("scan") or _valid_gtin(visual.get("barcode")),
              "brand": visual.get("brand") or anchor.get("brand") or "", "mpn": visual.get("part_number") or "",
              "name": visual.get("name") or anchor.get("title") or "",
              "variant_details": list(visual.get("variant_details") or [])}
    run["target"] = {**target, "title": anchor.get("title") or "", "evidence": list(visual.get("evidence") or []),
                     "identity_source": anchor.get("identity_source") or "Shop catalog product",
                     "identity_basis": basis, "bound": True}
    run["state"] = "verifying"
    run["question"] = None
    steps.start("offers", "Merchants checked")
    out = compare.run_comparison(target, {"basis": "item", "fulfillment": "online_us"}, rows)
    coverage = out.get("coverage") or {}
    run["result"], run["coverage"] = out.get("result"), coverage
    run["_evaluation"] = out.get("evaluation")
    for receipt in out.get("receipts") or []:
        run["receipts"]["models" if receipt.get("model") else "sources"].append(receipt)
    found_gtin = (out.get("target") or {}).get("gtin") or (out.get("evaluation") or {}).get("target_gtin")
    if found_gtin:
        run["target"]["gtin"] = found_gtin
    steps.end("offers", "done",
              f"{coverage.get('sellers_seen', 0)} sellers seen · {coverage.get('sellers_eligible', 0)} eligible")
    degraded = any(s.get("status") in ("failed", "timeout", "limited") for s in coverage.get("sources") or [])
    run["state"] = "partial" if (degraded or ctx.get("scoped")) else "complete"
    if intent.get("operation") == "compare_and_watch" and (intent.get("watch_words") or "").strip():
        steps.start("watch", "Watch request checked")
        try:
            watch = _lane("app.watch").create(run, intent["watch_words"])
        except Exception as exc:
            steps.end("watch", "failed", _classify(exc)[1])
        else:
            run["watch"] = watch
            steps.end("watch", "done", watch.get("scope_sentence") or watch.get("status") or "")


def _answered(run: dict, image: bytes, steps: Steps, answer: dict) -> None:
    question = answer["question"]
    ctx = run.get("_ctx") or {}
    if question["kind"] == "choice" and ctx:
        if answer.get("choice") == "none":
            leads = (run.get("result") or {}).get("leads") or []
            run["result"] = {**_empty_result([], "needs_identity"), "leads": leads}
            run["state"] = "complete"
            return
        _identify(run, image, steps, ctx, chosen=answer["choice"])
        return
    run["note"] = (run["note"] + " " + (answer.get("text") or "")).strip()[:500]
    _fresh(run, image, steps)
