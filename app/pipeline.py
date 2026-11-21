"""One photo in, one honest answer out.

Orchestrates the lanes (models, shop catalog, merchant confirmation, ranking, watch) and records only what
really happened: every progress row, receipt and number in a run comes from a call that was actually made.
"""
import importlib
import logging
import os
import sys
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor

from app import store
from app.config import GATE_THRESHOLD
from app.contracts import BASIS_LABEL, WORKING, offer_view

LOG = logging.getLogger("signalforge.pipeline")
_runs = ThreadPoolExecutor(max_workers=8, thread_name_prefix="run")
_write = threading.Lock()
RUN_LIMIT_S = 90.0
MAX_OPTIONS = 5
SCREEN_LISTINGS = 60
SCREEN_LIKELY = 0.5
PRINTED_MIN = 0.75
VISUAL_MATCH_P = 0.75        # Decision 1 screen probability the best catalog product must reach
VISUAL_MATCH_MARGIN = 0.25   # and its lead over the next-best product on offer


def _visual_match_on() -> bool:
    """Off unless SF_VISUAL_MATCH=1: the shipped build keeps the strict printed-evidence behaviour (no visual_match
    basis), because the screen does not check colour or variant (coord/bugs/040)."""
    return os.environ.get("SF_VISUAL_MATCH", "0").strip() == "1"
TIMEOUT_MESSAGE = "This search took longer than 90 seconds and was stopped. Please try again."
INTERRUPTED_MESSAGE = "This search was interrupted by a server restart. Please try again."

BASIS_WORDS = {
    "barcode": "barcode on the item matches",
    "model_number": "brand and printed model number match the catalog",
    "buyer_choice": "chosen by you",
    "visual_match": "two independent model judgments agree: Decision 1 scored this catalog product {p:.2f} "
                    "from the photo and Carbon chose it",
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
        with _write:
            current = store.get_run(self.run["run_id"])
            if current is None or current["revision"] != self.revision or current.get("_closed") == self.revision:
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
        if ms is None and sid in self._t and status != "skipped":
            ms = round((time.monotonic() - self._t[sid]) * 1000)
        row.update(status=status, detail=detail, ms=ms)
        self.save()


def start(run_id: str) -> None:
    _submit(run_id, None)


def resume(run_id: str, answer: dict) -> None:
    _submit(run_id, answer)


def _submit(run_id: str, answer: dict | None) -> None:
    run = store.get_run(run_id)
    if run is None:
        return
    timer = threading.Timer(RUN_LIMIT_S, _expire, (run_id, run["revision"]))
    timer.daemon = True
    timer.start()
    _runs.submit(_guard, run_id, answer, timer)


def _close(run: dict, code: str, message: str) -> None:
    """Stop a run that can no longer finish: the real reason is shown, nothing is filled in."""
    for row in run.get("progress") or []:
        if row.get("status") == "running":
            row["status"] = "failed"
            row["detail"] = row.get("detail") or message
    run["state"] = "unavailable"
    run["question"] = None
    run["error"] = {"code": code, "message": message}
    run["_ended_ms"] = _now_ms()
    run["_closed"] = run["revision"]


def _expire(run_id: str, revision: int) -> None:
    """Wall-clock guard: a revision still working after RUN_LIMIT_S is closed; its late worker can no longer write."""
    try:
        with _write:
            run = store.get_run(run_id)
            if run is None or run["revision"] != revision or run.get("state") not in WORKING:
                return
            _close(run, "timeout", TIMEOUT_MESSAGE)
            store.save_run(run)
        print(f"[pipeline] run {run_id} stopped by the {RUN_LIMIT_S:.0f} s guard", file=sys.stderr)
    except Exception:
        traceback.print_exc(limit=4, file=sys.stderr)


def recover_interrupted() -> int:
    """At server start: runs left in a working state by a dead process are closed, never left spinning."""
    count = 0
    with _write:
        for run in store.working_runs():
            _close(run, "interrupted", INTERRUPTED_MESSAGE)
            store.save_run(run)
            count += 1
    return count


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


def _guard(run_id: str, answer: dict | None, timer: threading.Timer | None = None) -> None:
    try:
        _work(run_id, answer)
    finally:
        if timer is not None:
            timer.cancel()


def _work(run_id: str, answer: dict | None) -> None:
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
        running = [row for row in run["progress"] if row["status"] == "running"]
        named = getattr(exc, "job", None) if any(row["id"] == getattr(exc, "job", None) for row in running) else None
        for row in running:
            if named is None or row["id"] == named:
                row["status"] = "failed"
                row["detail"] = row["detail"] or message
            else:
                row["status"] = "skipped"
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


def _catalog_calls(receipts: list[dict]) -> tuple[int, int]:
    """(calls made, calls failed) to the Shop catalog, from the receipts of the calls that really happened."""
    calls = [r for r in receipts if r.get("source", "shop_catalog") == "shop_catalog"]
    return len(calls), sum(1 for r in calls if r.get("status") == "failed")


def _catalog_status(made: int, failed: int) -> tuple[str, str]:
    if not failed:
        return "ok", ""
    return ("failed" if failed >= made else "limited"), f"{failed} of {made} catalog calls failed"


def _catalog_coverage(rows: list[dict], receipts: list[dict]) -> dict:
    ms = sum(r.get("ms") or 0 for r in receipts) or None
    sellers = len({r.get("seller") for r in rows if r.get("seller")})
    status, note = _catalog_status(*_catalog_calls(receipts))
    return {"attempted": 1, "responded": 0 if status == "failed" else 1, "sellers_seen": sellers, "sellers_eligible": 0,
            "checked_at": int(time.time()),
            "sources": [{"id": "shop_catalog", "name": "Shop catalog (shop.dev)", "kind": "catalog", "status": status,
                         "offers": len(rows), "eligible": 0, "ms": ms, "note": note}]}


def _degraded(coverage: dict | None) -> bool:
    return any(s.get("status") in ("failed", "timeout", "limited") for s in (coverage or {}).get("sources") or [])


def _fresh(run: dict, image: bytes, steps: Steps) -> None:
    models = _lane("app.models")
    note = run["note"]
    run.update(state="reading", question=None, result=None, coverage=None, error=None, target=None, decision=None,
               goal=None, progress=[], receipts={"models": [], "sources": []})
    run["assumptions"] = [a for a in run["assumptions"] if not a["id"].startswith("scoped_")]
    steps.start("view", "Gemini 4 Neon reads the photo")
    steps.start("gate", "Gemini Decision 1 checks the target is clear")
    steps.start("goal", "Gemini 4 Carbon reads the request")
    with ThreadPoolExecutor(max_workers=1) as pool:
        goal_job = pool.submit(models.parse_goal, image, note)
        try:
            seen = models.inspect(image, note)
        except Exception as exc:
            job = getattr(exc, "job", None)
            failed = job if job in ("view", "gate") else "view"
            steps.end(failed, "failed", _classify(exc)[1])
            steps.end("gate" if failed == "view" else "view", "skipped")
            try:
                parsed = goal_job.result()
            except Exception as goal_exc:
                steps.end("goal", "failed", _classify(goal_exc)[1])
            else:
                run["goal"] = parsed["intent"]
                run["receipts"]["models"].append(parsed["receipt"])
                steps.end("goal", "done", _goal_detail(parsed["intent"]), parsed["receipt"].get("ms"))
            raise
        visual, decision = seen["visual"], dict(seen["decision"])
        decision.setdefault("threshold", GATE_THRESHOLD)
        receipts = {r.get("job"): r for r in seen.get("receipts", [])}
        run["receipts"]["models"] = list(seen.get("receipts", []))
        run["decision"] = {k: decision.get(k) for k in ("choice", "confidence", "pass", "threshold", "ms", "id", "model")}
        if isinstance(decision.get("probabilities"), dict):
            run["decision"]["probabilities"] = dict(decision["probabilities"])
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


def _ask_choice(run: dict, ctx: dict, anchors: list[dict], rows: list[dict], text: str) -> None:
    """One choice question. The offered anchors are kept so the buyer's pick binds exactly what was shown."""
    offered = anchors[:MAX_OPTIONS]
    options = []
    for a in offered:
        option = {"id": a["upid"], "label": f"{a.get('title')} ({a.get('sellers') or 1} sellers)"}
        if type(a.get("p")) is float:
            option["p"] = a["p"]
        options.append(option)
    options.append({"id": "none", "label": "None of these"})
    ctx["offered"] = offered
    run["question"] = {"question_id": "q_" + uuid.uuid4().hex[:12], "kind": "choice", "text": text, "options": options}
    run["result"] = _empty_result(rows, "needs_identity")
    if ctx.get("screen"):
        run["result"]["screen"] = dict(ctx["screen"])
    run["coverage"] = _catalog_coverage(rows, run["receipts"]["sources"])
    run["state"] = "needs_detail"


def _read_product(shop, upid: str) -> tuple[list[dict], dict]:
    """Fresh rows of one catalog product and the receipt of that call; a failed call is recorded, never hidden."""
    started = time.monotonic()
    try:
        doc, receipt = shop.product(upid)
        return list(shop.rows_from(doc, int(time.time()))), dict(receipt)
    except Exception as exc:
        return [], {"source": "shop_catalog", "tool": "get_product", "status": "failed", "upid": upid,
                    "error": type(exc).__name__, "read_at": int(time.time()),
                    "ms": round((time.monotonic() - started) * 1000)}


def _chosen(run: dict, steps: Steps, ctx: dict, chosen: str) -> None:
    """The buyer picked an offered product: bind that one and read its current listings straight from the catalog."""
    shop = _lane("app.merchants.shop")
    offered = [a for a in ctx.get("offered") or [] if a.get("upid")]
    anchor = next(a for a in offered if a["upid"] == chosen)
    run["state"] = "searching"
    steps.start("identity", "Shop catalog identity evidence")
    upids = [chosen] + [a["upid"] for a in offered if a["upid"] != chosen]
    with ThreadPoolExecutor(max_workers=len(upids)) as pool:
        reads = list(pool.map(lambda u: _read_product(shop, u), upids))
    run["receipts"]["sources"] = [receipt for _, receipt in reads]
    rows, seen = [], set()
    for found, _ in reads:
        for row in found:
            key = (row.get("seller"), row.get("listing"))
            if key not in seen:
                seen.add(key)
                rows.append(row)
    for row in rows:
        p = (ctx.get("screen_by_listing") or {}).get(row.get("listing"))
        if type(p) is float:
            row["screen_p"] = p
    own = [r for r in reads[0][0]]
    sellers = len({r.get("seller") for r in rows if r.get("seller")})
    if not own:
        steps.end("identity", "failed", "The chosen product could not be read from the Shop catalog just now")
        rest = [a for a in offered if a["upid"] != chosen]
        if rest:
            _ask_choice(run, ctx, rest, rows, "I could not read that product from the catalog just now. Is it one of these?")
        else:
            run["question"] = {"question_id": "q_" + uuid.uuid4().hex[:12], "kind": "text", "options": [],
                               "text": "I could not read that product from the catalog just now. "
                                       "What is it? Brand and model in a few words."}
            run["result"] = _empty_result(rows, "needs_identity")
            run["coverage"] = _catalog_coverage(rows, run["receipts"]["sources"])
            run["state"] = "needs_detail"
        return
    steps.end("identity", "done", f"{len(upids)} catalog products · {len(rows)} listings · {sellers} sellers")
    steps.start("bind", "Exact product chosen")
    steps.end("bind", "done", f"{anchor.get('title')} · {BASIS_WORDS['buyer_choice']}")
    _compare(run, steps, ctx, anchor, "buyer_choice", rows)


def _identify(run: dict, image: bytes, steps: Steps, ctx: dict, chosen: str | None = None) -> None:
    if chosen is not None and any(a.get("upid") == chosen for a in ctx.get("offered") or []):
        _chosen(run, steps, ctx, chosen)
        return
    visual, decision, scan = ctx["visual"], ctx["decision"], ctx.get("scan")
    shop = _lane("app.merchants.shop")
    run["state"] = "searching"
    steps.start("identity", "Shop catalog identity evidence")
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
        run["state"] = "partial" if _degraded(run["coverage"]) else "complete"
        return
    if chosen is not None:
        anchor = next((a for a in anchors if a.get("upid") == chosen), None)
        steps.start("bind", "Exact product chosen")
        if anchor is None:
            steps.end("bind", "failed", "That product is no longer in the catalog results")
            _ask_choice(run, ctx, anchors, rows, "That product is no longer in the catalog results. Is it one of these?")
            return
        steps.end("bind", "done", f"{anchor.get('title')} · {BASIS_WORDS['buyer_choice']}")
        _compare(run, steps, ctx, anchor, "buyer_choice", rows)
        return
    gate_ok = bool(decision.get("pass")) or bool(scan)
    if not gate_ok or visual.get("target_count") != "one":
        if decision.get("choice") == "other":
            text = "I could not make out one product in this photo. What should I look for? A few words are enough."
        elif decision.get("choice") == "multiple" or visual.get("target_count") == "many":
            text = "I can see more than one possible item. Which one should I compare? A few words are enough."
        elif not gate_ok:
            text = (f"I am not sure this photo shows one clear product (confidence "
                    f"{float(decision.get('confidence') or 0):.2f}, needs "
                    f"{float(decision.get('threshold') or GATE_THRESHOLD):.2f}). Which item should I compare? "
                    f"A few words are enough.")
        else:
            text = "I could not tell exactly which product this is. What is it? A few words are enough."
        run["question"] = {"question_id": "q_" + uuid.uuid4().hex[:12], "kind": "text", "text": text, "options": []}
        run["result"] = _empty_result(rows, "needs_identity")
        run["coverage"] = _catalog_coverage(rows, run["receipts"]["sources"])
        run["state"] = "needs_detail"
        return
    models = _lane("app.models")
    steps.start("bind", "Gemini 4 Carbon binds the exact product")
    screen_fn = getattr(models, "screen", None)
    steps.start("screen", "Gemini Decision 1 screens every listing in one pass")
    with ThreadPoolExecutor(max_workers=1) as pool:
        screen_job = None
        if screen_fn is not None:
            side = {"name": visual.get("name") or "", "brand": visual.get("brand") or "",
                    "mpn": visual.get("part_number") or "", "variant_details": list(visual.get("variant_details") or [])}
            claims = {k: v for k, v in (("mpn", visual.get("part_number")), ("brand", visual.get("brand"))) if v}
            screen_job = pool.submit(screen_fn, image, side, rows[:SCREEN_LISTINGS], claims)
        try:
            bound = models.bind(image, run["note"], anchors, visual, decision, scan)
        except Exception:
            _screened(run, steps, ctx, screen_job, rows)
            raise
        screened = _screened(run, steps, ctx, screen_job, rows)
    receipt = bound.get("receipt") or {}
    if receipt:
        run["receipts"]["models"].append(receipt)
    anchor = bound.get("anchor")
    slot = bound.get("choice_slot")
    carbon_pick = anchors[slot] if type(slot) is int and 0 <= slot < len(anchors) else None
    refused = ""
    if screened is not None:
        best: dict[str, float] = {}
        for row in rows:
            if type(row.get("screen_p")) is float and row.get("product"):
                best[row["product"]] = max(best.get(row["product"], 0.0), row["screen_p"])
        for a in anchors:
            if a.get("upid") in best:
                a["p"] = round(best[a["upid"]], 3)
        anchors.sort(key=lambda a: -a.get("p", -1.0))
        printed = (screened.get("printed") or {}).get("mpn") or {}
        if anchor is not None and (bound.get("basis") or "model_number") == "model_number" \
                and type(printed.get("p")) is float and printed["p"] < PRINTED_MIN:
            steps.end("bind", "done", f"Printed model number not confirmed on the photo "
                                      f"(Decision 1 {printed['p']:.2f}, needs {PRINTED_MIN:.2f})", receipt.get("ms"))
            _ask_choice(run, ctx, anchors, rows,
                        "I could not confirm a printed model number on the photo. Is it one of these?")
            return
        if anchor is None and carbon_pick is not None and _visual_match_on() and decision.get("pass") \
                and visual.get("target_count") == "one":
            scored = [a for a in anchors if type(a.get("p")) is float]
            p_top = scored[0]["p"] if scored else 0.0
            p_second = scored[1]["p"] if len(scored) > 1 else 0.0
            margin = round(p_top - p_second, 3)
            if scored and scored[0] is carbon_pick and p_top >= VISUAL_MATCH_P and margin >= VISUAL_MATCH_MARGIN:
                words = BASIS_WORDS["visual_match"].format(p=p_top)
                steps.end("bind", "done", f"{carbon_pick.get('title')} · {words}", receipt.get("ms"))
                _compare(run, steps, ctx, carbon_pick, "visual_match", rows,
                         {"screen_p": p_top, "screen_margin": margin})
                return
            refused = (f" · visual match not accepted: best catalog product {p_top:.2f}, next {p_second:.2f} "
                       f"(needs {VISUAL_MATCH_P:.2f} and a {VISUAL_MATCH_MARGIN:.2f} lead)"
                       + ("" if not scored or scored[0] is carbon_pick else "; Carbon chose a different product"))
    if anchor is None:
        steps.end("bind", "done", "No exact product could be confirmed from the photo alone" + refused,
                  receipt.get("ms"))
        _ask_choice(run, ctx, anchors, rows,
                    "I could not confirm the exact product from the photo alone. Is it one of these?")
        return
    basis = bound.get("basis") or "model_number"
    steps.end("bind", "done", f"{anchor.get('title')} · {BASIS_WORDS.get(basis, basis)}", receipt.get("ms"))
    _compare(run, steps, ctx, anchor, basis, rows)


def _screened(run: dict, steps: Steps, ctx: dict, job, rows: list[dict]) -> dict | None:
    """Close the `screen` row with what the one Decision 1 pass really returned; a missing or failed screen changes
    nothing else. Scores annotate the rows (ordering evidence only, never identity)."""
    if job is None:
        steps.end("screen", "failed", "The listing screen is not part of this build")
        return None
    try:
        out = job.result()
    except Exception as exc:
        steps.end("screen", "failed", _classify(exc)[1])
        return None
    receipt = out.get("receipt")
    if not receipt:
        steps.end("screen", "skipped", "Nothing to screen")
        return None
    run["receipts"]["models"].append(receipt)
    by_listing = {}
    for score in out.get("scores") or []:
        index, p = score.get("index"), score.get("p_same")
        if type(index) is int and 0 <= index < len(rows) and isinstance(p, (int, float)) and not isinstance(p, bool):
            rows[index]["screen_p"] = round(float(p), 3)
            if rows[index].get("listing"):
                by_listing[rows[index]["listing"]] = rows[index]["screen_p"]
    likely = sum(1 for p in by_listing.values() if p >= SCREEN_LIKELY)
    scored = len(out.get("scores") or [])
    ctx["screen_by_listing"] = by_listing
    ctx["screen"] = {"model": receipt.get("model") or "gemini-decision-1", "listings": scored, "ms": receipt.get("ms"),
                     "note": f"{likely} of {scored} listings likely the same item"
                             + (f"; {len(rows) - scored} not screened" if len(rows) > scored else "")}
    steps.end("screen", "done", f"{scored} listings · {likely} likely the same item", receipt.get("ms"))
    return out


def _compare(run: dict, steps: Steps, ctx: dict, anchor: dict, basis: str, rows: list[dict],
             evidence: dict | None = None) -> None:
    compare = _lane("app.merchants.compare")
    visual, intent = ctx["visual"], ctx["intent"]
    target = {"upid": anchor.get("upid"),
              "gtin": anchor.get("gtin") or ctx.get("scan") or _valid_gtin(visual.get("barcode")),
              "brand": visual.get("brand") or anchor.get("brand") or "", "mpn": visual.get("part_number") or "",
              "name": visual.get("name") or anchor.get("title") or "",
              "variant_details": list(visual.get("variant_details") or [])}
    run["target"] = {**target, "title": anchor.get("title") or "", "evidence": list(visual.get("evidence") or []),
                     "identity_source": anchor.get("identity_source") or "Shop catalog product",
                     "identity_basis": basis, "bound": True, **(evidence or {})}
    run["state"] = "verifying"
    run["question"] = None
    steps.start("offers", "Merchant offers verified")
    made, failed = _catalog_calls(run["receipts"]["sources"])
    out = compare.run_comparison(target, {"basis": "item", "fulfillment": "online_us"}, rows)
    coverage = out.get("coverage") or {}
    if failed:
        status, note = _catalog_status(made, failed)
        for source in coverage.get("sources") or []:
            if source.get("id") == "shop_catalog":
                if source.get("status") == "ok":
                    source["status"] = status
                source["note"] = "; ".join(x for x in (source.get("note"), note) if x)
    run["result"], run["coverage"] = out.get("result"), coverage
    if ctx.get("screen") and isinstance(run["result"], dict):
        run["result"]["screen"] = dict(ctx["screen"])
    run["_evaluation"] = out.get("evaluation")
    for receipt in out.get("receipts") or []:
        run["receipts"]["models" if receipt.get("model") else "sources"].append(receipt)
    found_gtin = (out.get("target") or {}).get("gtin") or (out.get("evaluation") or {}).get("target_gtin")
    if found_gtin:
        run["target"]["gtin"] = found_gtin
    steps.end("offers", "done",
              f"{coverage.get('sellers_seen', 0)} sellers seen · {coverage.get('sellers_eligible', 0)} eligible")
    final = "partial" if (_degraded(coverage) or ctx.get("scoped")) else "complete"
    if intent.get("operation") == "compare_and_watch" and (intent.get("watch_words") or "").strip():
        # the result is already saved and visible; the state turns terminal only once the watch step has answered,
        # so a polling browser also receives the watch
        steps.start("watch", "Watch request checked")
        try:
            watch = _lane("app.watch").create({**run, "state": final}, intent["watch_words"])
        except Exception as exc:
            steps.end("watch", "failed", _classify(exc)[1])
        else:
            run["watch"] = watch
            steps.end("watch", "done", watch.get("scope_sentence") or watch.get("status") or "")
    run["state"] = final


def _answered(run: dict, image: bytes, steps: Steps, answer: dict) -> None:
    question = answer["question"]
    ctx = run.get("_ctx") or {}
    if question["kind"] == "choice" and ctx:
        if answer.get("choice") == "none":
            leads = (run.get("result") or {}).get("leads") or []
            run["result"] = {**_empty_result([], "needs_identity"), "leads": leads}
            run["question"] = {"question_id": "q_" + uuid.uuid4().hex[:12], "kind": "text", "options": [],
                               "text": "What is it? Brand and model in a few words."}
            run["state"] = "needs_detail"
            return
        _identify(run, image, steps, ctx, chosen=answer["choice"])
        return
    run["note"] = (run["note"] + " " + (answer.get("text") or "")).strip()[:500]
    _fresh(run, image, steps)
