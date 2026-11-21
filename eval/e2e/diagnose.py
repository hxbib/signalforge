"""Post-hoc failure analysis of the recorded end-to-end run. Changes no score.

Reads the private run journal (what the system saw and asked, per case) and writes flags and counts only to
eval/results/e2e-diagnostic-v1.json. For a system false accept it re-reads the two catalog products involved and
records whether the model number the system bound on is also carried by the gold product's own catalog titles
(the same item listed under two catalog product ids) - a mechanical check, not a hand adjudication.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.merchants import shop  # noqa: E402

RESULTS = ROOT / "eval" / "results"
PRIVATE = ROOT / "private" / "e2e"


def evidence_flags(detail: dict | None) -> dict:
    """What the photo gave the system, as booleans (no text)."""
    detail = detail or {}
    target, decision, question = detail.get("target") or {}, detail.get("decision") or {}, detail.get("question") or {}
    return {"brand_read": bool((target.get("brand") or "").strip()),
            "model_number_read": bool((target.get("mpn") or "").strip()),
            "barcode_read": bool(target.get("gtin")),
            "gate_pass": decision.get("pass"), "gate_choice": decision.get("choice"),
            "question_kind": question.get("kind")}


def abstention_code(flags: dict) -> str:
    if flags["gate_pass"] is not True:
        return "gate_did_not_pass"
    if not flags["model_number_read"] and not flags["barcode_read"]:
        return "no_model_number_or_barcode_in_photo"
    return "model_number_read_but_not_bound"


def visual_match_gap(question: dict | None, gold_upid: str, p_min: float, margin_min: float) -> dict | None:
    """Why the visual_match basis did not fire on a case that ended at the choice question: read from the screen
    probabilities the question's options carry (ordered by probability). None when no option carries one."""
    scored = [(o.get("id"), o["p"]) for o in (question or {}).get("options") or [] if type(o.get("p")) is float]
    if not scored:
        return None
    top, second = scored[0][1], scored[1][1] if len(scored) > 1 else 0.0
    if top < p_min:
        code = "screen_top_below_threshold"
    elif round(top - second, 3) < margin_min:
        code = "screen_margin_below_threshold"
    else:
        code = "second_model_pick_differs_or_missing"
    return {"code": code, "gold_is_top_option": scored[0][0] == gold_upid,
            "gold_among_options": any(upid == gold_upid for upid, _ in scored)}


REFUSED, DIFFERENT = "visual match not accepted", "Carbon chose a different product"


def no_lead_case(system: dict, arm: dict, gold_upid: str, p_min: float) -> dict | None:
    """What a rule WITHOUT the lead requirement would have done on one recorded run (nothing is re-run): accept the
    top-scored catalog product when its screen probability reaches p_min and the second model picked that same
    product. Read from the recorded bind step and the probabilities on the question's options. None when the run
    never reached the visual-match decision."""
    if "bind:visual_match" in arm.get("reasons", []):
        return {"accepted": True, "deployed_rule_accepted": True, "gold": arm["outcome"] == "correct"}
    bind = next((row.get("detail") or "" for row in system.get("progress") or [] if row.get("id") == "bind"), "")
    scored = [(o.get("id"), o["p"]) for o in (system.get("question") or {}).get("options") or [] if type(o.get("p")) is float]
    if REFUSED not in bind or not scored:
        return None
    accepted = scored[0][1] >= p_min and DIFFERENT not in bind
    return {"accepted": accepted, "deployed_rule_accepted": False, "gold": scored[0][0] == gold_upid}


def no_lead_counts(rows: list[dict | None], n: int, p_min: float) -> dict:
    reached = [r for r in rows if r]
    accepted = [r for r in reached if r["accepted"]]
    return {"deployed": False, "is_score": False,
            "rule": f"top catalog product's screen probability >= {p_min} and the second model picked the same product; no lead requirement",
            "source": "recorded runs only: the bind step's recorded outcome and the screen probabilities on the question's options",
            "cases": n, "reached_visual_match_decision": len(reached), "accepted": len(accepted),
            "accepted_by_deployed_rule": sum(r["deployed_rule_accepted"] for r in accepted),
            "accepted_gold": sum(r["gold"] for r in accepted),
            "accepted_other_catalog_id": sum(not r["gold"] for r in accepted)}


def gap_counts(gaps: list[dict]) -> dict:
    codes: dict[str, int] = {}
    for g in gaps:
        codes[g["code"]] = codes.get(g["code"], 0) + 1
    return {"abstentions_with_screen_scores": len(gaps), "codes": dict(sorted(codes.items())),
            "gold_is_top_option": sum(g["gold_is_top_option"] for g in gaps),
            "gold_among_options": sum(g["gold_among_options"] for g in gaps)}


def titles_of(upid: str) -> list[str]:
    doc, rec = shop.product(upid)
    product = doc.get("product") or {}
    return [shop._clean(product.get("title"))] + [r["title"] or "" for r in shop.rows_from(doc, rec["read_at"])]


def _norm(title: str) -> str:
    return " ".join((title or "").split()).casefold()


def duplicate_check(detail: dict, gold_upid: str, answered_upid: str | None = None) -> dict:
    """Mechanical evidence that an answer bound to another catalog product id is the same item as gold:
    the model number the system bound on is carried by the gold product's own titles, or the two catalog products
    have the identical catalog title. No evidence does not prove a different item; nothing is adjudicated by hand."""
    target = detail.get("target") or {}
    part = (target.get("mpn") or "").strip()
    out = {"bound_on_model_number": bool(part), "gold_titles_carry_bound_model_number": None,
           "identical_catalog_title": None, "same_item_evidence": None}
    try:
        gold_titles = titles_of(gold_upid)
        if part:
            out["gold_titles_carry_bound_model_number"] = any(shop.has_token(t, part) for t in gold_titles)
        if answered_upid:
            answered = titles_of(answered_upid)
            out["identical_catalog_title"] = bool(_norm(gold_titles[0])) and _norm(gold_titles[0]) == _norm(answered[0])
    except Exception as e:
        out["error"] = type(e).__name__
    if out["gold_titles_carry_bound_model_number"]:
        out["same_item_evidence"] = "model_number"
    elif out["identical_catalog_title"]:
        out["same_item_evidence"] = "identical_catalog_title"
    return out


def false_accept_split(checks: list[dict]) -> dict:
    same = sum(1 for c in checks if c.get("same_item_evidence"))
    return {"strict": len(checks), "same_item_other_catalog_id": same, "no_same_item_evidence": len(checks) - same}


def main(argv: list[str] | None = None) -> int:
    argv = argv or []
    name = argv[argv.index("--run") + 1] if "--run" in argv else "v1"
    cases = json.loads((RESULTS / f"e2e-{name}.json").read_text())
    details = {}
    journal_text = (PRIVATE / f"journal-{name}.jsonl").read_text()
    for line in journal_text.splitlines():
        ev = json.loads(line)
        if ev.get("event") == "detail":
            details[ev["case_id"]] = ev
    arm_key = "system" if "system" in cases["cases"][0]["arms"] else "system_one_tap"
    rows, codes, checks, gaps, no_lead = [], {}, {arm_key: [], "baseline": []}, [], []
    limits = cases.get("visual_match_thresholds") or {}
    for c in cases["cases"]:
        detail = details.get(c["case_id"]) or {}
        system = detail.get("system") or {}
        arm = c["arms"][arm_key]
        first = detail.get("first_leg")   # one-tap runs: the zero-tap leg before the simulated answer
        if cases.get("visual_match") and (arm_key == "system" or first):
            no_lead.append(no_lead_case(first or system, first or arm, c["gold_upid"], limits.get("p") or 0.75))
        flags = evidence_flags(system)
        row = {"case_id": c["case_id"], "system_outcome": arm["outcome"], **flags}
        if arm["outcome"] == "abstained":
            row["abstention_code"] = abstention_code(flags)
            codes[row["abstention_code"]] = codes.get(row["abstention_code"], 0) + 1
            gap = visual_match_gap(system.get("question"), c["gold_upid"], limits.get("p") or 0.75, limits.get("margin") or 0.25)
            if gap and cases.get("visual_match"):
                row["visual_match_gap"] = gap
                gaps.append(gap)
        if arm["outcome"] == "false_accept":
            row["duplicate_check"] = duplicate_check(system, c["gold_upid"], (system.get("target") or {}).get("upid"))
            checks[arm_key].append(row["duplicate_check"])
        if (c["arms"].get("baseline") or {}).get("outcome") == "false_accept":
            row["baseline_duplicate_check"] = duplicate_check({}, c["gold_upid"], detail.get("baseline_product"))
            checks["baseline"].append(row["baseline_duplicate_check"])
        rows.append(row)
    doc = {"generated_at": int(time.time()), "run": cases["run"], "post_hoc": True, "changes_scores": False,
           "n": len(rows), "system_abstention_codes": dict(sorted(codes.items())),
           "system_false_accepts": len(checks[arm_key]),
           "system_false_accepts_same_model_number_as_gold": sum(
               bool(c.get("gold_titles_carry_bound_model_number")) for c in checks[arm_key]),
           "no_lead_rule": no_lead_counts(no_lead, len(rows), limits.get("p") or 0.75)
           if no_lead and DIFFERENT in journal_text else None,
           "no_lead_rule_note": None if not no_lead or DIFFERENT in journal_text else
           "not computed: this run's recording never shows the second model's pick differing, so it cannot tell whether "
           "the pick agreed with the top-scored product",
           "false_accepts": {arm: false_accept_split(checks[arm]) for arm in c["arms"]},
           "visual_match_gap": gap_counts(gaps) if cases.get("visual_match") else None,
           "cases": rows}
    (RESULTS / f"e2e-diagnostic-{name}.json").write_text(json.dumps(doc, indent=1) + "\n")
    print(json.dumps({k: v for k, v in doc.items() if k != "cases"}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
