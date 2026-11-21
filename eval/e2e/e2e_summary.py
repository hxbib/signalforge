"""Rebuild eval/results/e2e-summary.json from the recorded per-case file. Runs no arm and changes no outcome.

  .venv/bin/python eval/e2e/e2e_summary.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import e2e_score as score  # noqa: E402

RESULTS = Path(__file__).resolve().parents[2] / "eval" / "results"
HEAD_KEYS = ("generated_at", "code_sha", "code_sha_at_end", "tree_dirty", "pair_version", "pair_v2_note_seen_at_start",
             "arms_started_at", "freeze_seed", "models")

NOTES = [
    "Blind draw: 30 product-family queries fixed first, one catalog product per query picked by a RNG seeded with the code SHA, frozen before any arm ran. Every frozen case is in the denominator; none was dropped, re-drawn or re-run.",
    "Gold is the Shop catalog product id of the drawn product. The same physical item can exist under another catalog product id; an answer bound to such a duplicate is counted as a false accept here.",
    "The photos are the merchants' own catalog images. Many show no printed model number, so the full system is expected to stop and ask one question; zero follow-up answers were given, so those cases count as abstained.",
    "The baseline is handed the listing rows of the draw query (which normally contain the gold product); the full system has to find the product in the whole catalog from the photo alone.",
    "Cheapest-valid agreement is circular for the full system: the reference winner is computed by the same deterministic evaluator (app.merchants.ranking.evaluate) the system uses, over the gold product's rows. For the system it tests binding and lane wiring; for the baseline it tests constraint-following.",
    "An invalid offer is an answered offer that is not new / in stock / USD on its own row, or whose merchant storefront disagreed on price or stock when re-read at scoring time (storefront_mismatch).",
    "A system run that ended 'unavailable' with an error is counted as an error, not as an abstention.",
    "Baseline rows come from catalog search, which can list fewer sellers of a product than the full product read the reference uses; 'pricier_than_reference' can therefore also mean the cheapest seller was not among the baseline's rows.",
]

SCREEN_NOTES = [
    "Same frozen draw and photos as the two-arm run; the candidate rows are the catalog search rows of the draw query, read again at screen time (at most 60 per case). One Decision 1 call per case scores every listing; gold never enters the call.",
    "Label noise: a listing is positive only if it belongs to the gold catalog product id. The same product listed by a seller outside that catalog cluster counts as a negative here, so precision is a lower bound and the false-accept rate an upper bound.",
    "Every frozen case is in the denominator; a failed call is an error case with no judgments. Precision is null when nothing was accepted at a threshold.",
    "The screen orders and annotates listings; in the product it never grants identity on its own.",
]

RUN_NOTES = {
    "v1": ["During run v1 the runner's rate-limit detector matched a step duration whose digits contained 429; no rate-limit error occurred. The runner paused 30 s and ran one case at a time from then on (two at a time before). No case was affected or re-run."],
}


def reading(s: dict) -> list[str]:
    """Plain sentences generated from the counts (nothing typed by hand)."""
    n, sy, ba = s["n"], s["arms"]["system"], s["arms"]["baseline"]
    out = [
        f"Full system: answered {sy['answered']} of {n} photos ({sy['correct']} the gold product, {sy['false_accepts']} "
        f"another catalog product id), stopped without an answer on {sy['abstained']}, {sy['errors']} errors; "
        f"{sy['invalid_offers']} of its {sy['answered']} answered offers were invalid.",
        f"One-call baseline: answered {ba['answered']} of {n} ({ba['correct']} the gold product, {ba['false_accepts']} "
        f"another product), abstained on {ba['abstained']}, {ba['errors']} errors; {ba['invalid_offers']} of its "
        f"{ba['answered']} answered offers were invalid and {ba['cheapest_agreement']} matched the reference cheapest "
        f"valid offer.",
    ]
    if ba["correct"] > sy["correct"] and ba["false_accepts"] <= sy["false_accepts"]:
        out.append("On this sample, with no follow-up answer given, the single model call covered more photos than the "
                   "full system with no more wrong products; the full system's extra checks bought no measured "
                   "identity safety here.")
    elif sy["correct"] >= ba["correct"] and sy["false_accepts"] <= ba["false_accepts"]:
        out.append("On this sample the full system covered at least as many photos as the single model call with no "
                   "more wrong products.")
    else:
        out.append("On this sample the two arms trade coverage against wrong products; see the counts above.")
    d = s.get("diagnostic")
    if d:
        causes = ", ".join(f"{k} x{v}" for k, v in d["system_abstention_codes"].items()) or "none"
        out.append(f"Why the full system stopped (post-hoc flags, no score changed): {causes}. Of its "
                   f"{d['system_false_accepts']} false accepts, {d['system_false_accepts_same_model_number_as_gold']} "
                   f"bound a model number that the gold product's own catalog titles also carry (the same item under "
                   f"two catalog product ids).")
    sc = s.get("screen")
    if sc:
        at = {t["threshold"]: t for t in sc["thresholds"]}
        out.append(f"Listing screen (one {sc['model']} call per photo): {sc['judgments']} listing judgments over {sc['cases']} "
                   f"cases ({sc['positives']} positive, {sc['negatives']} negative, {sc['error_cases']} failed calls), ROC AUC "
                   f"{sc['roc_auc']}; at p >= 0.9 precision {at[0.9]['precision']}, recall {at[0.9]['recall']}, false-accept "
                   f"rate {at[0.9]['false_accept_rate']}; at p >= 0.5 precision {at[0.5]['precision']}, recall {at[0.5]['recall']}; "
                   f"the top-scored listing was the gold product in {sc['top1_positive_cases']} of {sc['cases_with_positive']} "
                   f"cases that had one; median {sc['median_ms_per_call']} ms per call.")
    return out


def build(cases: dict, diagnostic: dict | None, screen: dict | None = None) -> dict:
    records = cases["cases"]
    walls = sorted(r["wall_ms"] for r in records if type(r.get("wall_ms")) is int)
    s = {**{k: cases.get(k) for k in HEAD_KEYS}, "run": cases["run"], **score.summarize(records),
         "median_case_wall_ms": walls[len(walls) // 2] if walls else None}
    if diagnostic and diagnostic.get("run") == cases["run"]:
        s["diagnostic"] = {k: diagnostic[k] for k in ("post_hoc", "changes_scores", "system_abstention_codes",
                                                      "system_false_accepts", "system_false_accepts_same_model_number_as_gold")}
    if screen and screen.get("complete") and screen.get("metrics"):
        s["screen"] = {**{k: screen.get(k) for k in ("run", "generated_at", "code_sha", "tree_dirty", "model", "listing_cap",
                                                     "target_source", "label")}, **screen["metrics"], "notes": SCREEN_NOTES}
    s["reading"] = reading(s)
    s["notes"] = NOTES + RUN_NOTES.get(cases["run"], [])
    if cases.get("pair_v2_note_seen_at_start") is False:
        s["notes"].append("The arms were started before the team log carried the 'pair v2 landed' note; pair_version is what the code reported.")
    return s


def main() -> int:
    cases = json.loads((RESULTS / "e2e-v1.json").read_text())
    if not cases.get("complete"):
        print("e2e-v1.json is not complete; no summary written", file=sys.stderr)
        return 1
    path = RESULTS / "e2e-diagnostic-v1.json"
    screen_path = RESULTS / "e2e-screen.json"
    summary = build(cases, json.loads(path.read_text()) if path.exists() else None,
                    json.loads(screen_path.read_text()) if screen_path.exists() else None)
    summary["summarized_at"] = int(time.time())
    (RESULTS / "e2e-summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    print(f"e2e-summary.json rebuilt from e2e-v1.json: n={summary['n']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
