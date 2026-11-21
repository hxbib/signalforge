"""Deterministic scoring for the end-to-end benchmark. Pure functions: no network, no model, no clock.

Gold (the drawn catalog product id) enters only here, never either arm's input.
"""
from __future__ import annotations

from statistics import median

OUTCOMES = ("correct", "false_accept", "abstained", "error")
GOAL = {"basis": "item", "fulfillment": "online_us"}


def system_outcome(run: dict | None, gold_upid: str) -> dict:
    """Outcome of the full pipeline on one photo, from its public Run JSON."""
    if not isinstance(run, dict):
        return {"outcome": "error", "reasons": ["no_run_document"], "offer": None}
    state = run.get("state")
    result = run.get("result") or {}
    winners = result.get("winners") or []
    if state == "unavailable" or run.get("error"):
        code = (run.get("error") or {}).get("code") or "unknown"
        return {"outcome": "error", "reasons": [f"unavailable:{code}"], "offer": None}
    if state not in ("complete", "partial", "needs_detail"):
        return {"outcome": "error", "reasons": [f"not_terminal:{state}"], "offer": None}
    if not winners:
        reasons = [f"state:{state}"]
        if result.get("status"):
            reasons.append(f"result:{result['status']}")
        question = run.get("question") or {}
        if question.get("kind"):
            reasons.append(f"question:{question['kind']}")
        return {"outcome": "abstained", "reasons": reasons, "offer": None}
    target = run.get("target") or {}
    offer = winners[0]
    reasons = [f"state:{state}", f"result:{result.get('status')}", f"bind:{target.get('identity_basis')}",
               f"offer_identity:{offer.get('identity_basis')}"]
    right = bool(gold_upid) and target.get("upid") == gold_upid
    return {"outcome": "correct" if right else "false_accept", "reasons": reasons, "offer": offer}


def baseline_outcome(pick: dict | None, rows: list[dict], gold_upid: str) -> dict:
    """Outcome of the single-call baseline: `pick` is {status, index, reason}, `rows` the candidates it was shown."""
    if not isinstance(pick, dict) or pick.get("status") not in ("winner", "abstain"):
        return {"outcome": "error", "reasons": ["bad_reply"], "offer": None}
    if pick["status"] == "abstain":
        return {"outcome": "abstained", "reasons": ["abstain"], "offer": None}
    index = pick.get("index")
    if type(index) is not int or not 0 <= index < len(rows):
        return {"outcome": "error", "reasons": ["bad_index"], "offer": None}
    row = rows[index]
    right = bool(gold_upid) and row.get("product") == gold_upid
    return {"outcome": "correct" if right else "false_accept", "reasons": ["winner"], "offer": row}


def offer_validity(offer: dict | None, confirm_status: str | None) -> dict | None:
    """Is the answered offer new, in stock and priced in USD, and does the merchant's storefront agree?"""
    if offer is None:
        return None
    reasons = []
    if offer.get("condition") != "new":
        reasons.append("condition_not_new")
    if offer.get("stock") != "in_stock":
        reasons.append("not_in_stock")
    if offer.get("currency") != "USD":
        reasons.append("not_usd")
    cents = offer.get("item_cents")
    if type(cents) is not int or cents <= 0:
        reasons.append("price_missing")
    if confirm_status == "mismatch":
        reasons.append("storefront_mismatch")
    return {"valid": not reasons, "reasons": reasons, "confirm": confirm_status or "not_checked"}


def reference(evaluation: dict | None) -> dict:
    """Reference winner for the gold product from app.merchants.ranking.evaluate (None when it could not be read)."""
    if not isinstance(evaluation, dict):
        return {"status": "unavailable", "price_cents": None, "eligible": 0}
    return {"status": evaluation.get("status"), "price_cents": evaluation.get("price_cents"),
            "eligible": len(evaluation.get("eligible") or [])}


def cheapest_code(outcome: dict, ref: dict) -> str:
    """How the arm's chosen price relates to the reference cheapest valid offer of the gold product."""
    if outcome["outcome"] != "correct":
        return "not_applicable"
    if ref.get("status") == "unavailable":
        return "reference_unavailable"
    if ref.get("price_cents") is None:
        return "no_reference_offer"
    cents = (outcome.get("offer") or {}).get("item_cents")
    if type(cents) is not int:
        return "price_missing"
    if cents == ref["price_cents"]:
        return "agree"
    return "pricier_than_reference" if cents > ref["price_cents"] else "cheaper_than_reference"


def arm_record(outcome: dict, validity: dict | None, cheapest: str, ms: int | None) -> dict:
    """What is published per arm per case: codes and timings only (no prices, no merchant text)."""
    return {"outcome": outcome["outcome"], "reasons": list(outcome["reasons"]), "offer": validity,
            "cheapest": cheapest, "ms": ms}


def _count(values) -> dict:
    out: dict[str, int] = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return dict(sorted(out.items()))


def summarize_arm(records: list[dict]) -> dict:
    times = [r["ms"] for r in records if type(r.get("ms")) is int]
    answered = [r for r in records if r.get("offer")]
    invalid = [r for r in answered if not r["offer"]["valid"]]
    return {
        "correct": sum(r["outcome"] == "correct" for r in records),
        "false_accepts": sum(r["outcome"] == "false_accept" for r in records),
        "abstained": sum(r["outcome"] == "abstained" for r in records),
        "errors": sum(r["outcome"] == "error" for r in records),
        "answered": len(answered),
        "invalid_offers": len(invalid),
        "invalid_reasons": _count(code for r in invalid for code in r["offer"]["reasons"]),
        "storefront_checks": _count(r["offer"]["confirm"] for r in answered),
        "cheapest_agreement": sum(r.get("cheapest") == "agree" for r in records),
        "cheapest_codes": _count(r.get("cheapest") for r in records),
        "reason_codes": _count(code for r in records if r["outcome"] != "correct" for code in r["reasons"]),
        "median_ms": int(median(times)) if times else None,
    }


def summarize(cases: list[dict]) -> dict:
    """Every case stays in the denominator: n is the number of frozen cases, whatever happened to them."""
    names = [arm for arm in ("system", "baseline") if cases and arm in cases[0]["arms"]]
    arms = {arm: summarize_arm([c["arms"][arm] for c in cases]) for arm in names}
    pairs = _count(f"system_{c['arms']['system']['outcome']}__baseline_{c['arms']['baseline']['outcome']}"
                   for c in cases) if len(names) == 2 else {}
    return {"n": len(cases), "arms": arms, "paired": pairs,
            "reference_offer_cases": sum((c.get("reference") or {}).get("status") == "winner" for c in cases),
            "gold_in_baseline_candidates": sum(bool(c.get("gold_in_candidates")) for c in cases)}
