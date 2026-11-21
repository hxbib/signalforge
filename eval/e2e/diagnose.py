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
JOURNAL = ROOT / "private" / "e2e" / "journal-v1.jsonl"
OUT = RESULTS / "e2e-diagnostic-v1.json"


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


def titles_of(upid: str) -> list[str]:
    doc, rec = shop.product(upid)
    product = doc.get("product") or {}
    return [shop._clean(product.get("title"))] + [r["title"] or "" for r in shop.rows_from(doc, rec["read_at"])]


def duplicate_check(detail: dict, gold_upid: str) -> dict:
    target = detail.get("target") or {}
    part = (target.get("mpn") or "").strip()
    out = {"bound_on_model_number": bool(part), "gold_titles_carry_bound_model_number": None}
    if not part:
        return out
    try:
        out["gold_titles_carry_bound_model_number"] = any(shop.has_token(t, part) for t in titles_of(gold_upid))
    except Exception as e:
        out["error"] = type(e).__name__
    return out


def main() -> int:
    cases = json.loads((RESULTS / "e2e-v1.json").read_text())
    details = {}
    for line in JOURNAL.read_text().splitlines():
        ev = json.loads(line)
        if ev.get("event") == "detail":
            details[ev["case_id"]] = ev.get("system")
    rows, codes = [], {}
    for c in cases["cases"]:
        arm = c["arms"]["system"]
        flags = evidence_flags(details.get(c["case_id"]))
        row = {"case_id": c["case_id"], "system_outcome": arm["outcome"], **flags}
        if arm["outcome"] == "abstained":
            row["abstention_code"] = abstention_code(flags)
            codes[row["abstention_code"]] = codes.get(row["abstention_code"], 0) + 1
        if arm["outcome"] == "false_accept":
            row["duplicate_check"] = duplicate_check(details.get(c["case_id"]) or {}, c["gold_upid"])
        rows.append(row)
    false_accepts = [r for r in rows if r["system_outcome"] == "false_accept"]
    doc = {"generated_at": int(time.time()), "run": cases["run"], "post_hoc": True, "changes_scores": False,
           "n": len(rows), "system_abstention_codes": dict(sorted(codes.items())),
           "system_false_accepts": len(false_accepts),
           "system_false_accepts_same_model_number_as_gold": sum(
               bool((r.get("duplicate_check") or {}).get("gold_titles_carry_bound_model_number")) for r in false_accepts),
           "cases": rows}
    OUT.write_text(json.dumps(doc, indent=1) + "\n")
    print(json.dumps({k: v for k, v in doc.items() if k != "cases"}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
