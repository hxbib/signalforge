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
RUN_META = {
    "v1": {"label": "Draw A, first run: strict printed-evidence identity (the visual_match basis did not exist yet)",
           "draw": "A", "in_sample": False, "held_out": False},
    "v2": {"label": "Draw A again after the visual_match change, system arm only. IN-SAMPLE: the change and its 0.75 "
                    "threshold were read off this draw's own results and calibration table",
           "draw": "A", "in_sample": True, "held_out": False},
    "b": {"label": "Held-out draw B: new products, frozen before any call, both arms",
          "draw": "B", "in_sample": False, "held_out": True},
}
DIAGNOSTIC_KEYS = ("post_hoc", "changes_scores", "system_abstention_codes", "system_false_accepts",
                   "system_false_accepts_same_model_number_as_gold", "false_accepts", "visual_match_gap", "no_lead_rule", "no_lead_rule_note")
PHOTO_CAVEAT = (
    "What the photos are: every case photo is the catalog's own product image of the drawn product. That is the "
    "easiest input for a model that matches a picture to a listing (the candidate listing was made from that very "
    "image) and the hardest for a rule that wants printed evidence (a studio shot rarely shows a model number or a "
    "barcode). The benchmark therefore shows how each arm behaves on clean merchant imagery with no typed hint: how "
    "often it answers, how often the answer is another product, and whether the answered offer survives the checks. "
    "It does not show how either arm performs on a shopper's own photo of an item in hand, where the picture is not "
    "the listing's own and packaging text may be readable; neither arm was measured on such photos here.")
ONE_TAP_NOTE = (
    "system_one_tap: the system run plus at most one answer to a choice question, given by a simulated buyer who "
    "knows what they are holding (the gold option if it is offered, otherwise 'None of these'). The tap uses the gold "
    "label, so this arm measures the system given one correct human confirmation; the baseline arm gets no such help. "
    "It is a separate fresh run of the system, so its first leg can differ from the zero-tap system row, and it "
    "carries its own code SHA and SF_VISUAL_MATCH setting: the one-tap run on draw B had visual_match on, the one on "
    "draw A ran after the shipped default became off.")

NOTES = [
    "Blind draw: 30 product-family queries fixed first, one catalog product per query picked by a RNG seeded with the code SHA, frozen before any arm ran. Every frozen case is in the denominator; none was dropped, re-drawn or re-run.",
    "Runs: v1 = draw A, first measurement. After v1 and the screen calibration were seen, ONE change was made to the system (a visual_match identity basis). v2 re-measures the system arm on draw A and is IN-SAMPLE (the change and its threshold came from draw A; the v1 baseline is shown next to it, not re-run). B is a new draw of the same queries (seed = sha256 of e2e-v1.json, every draw-A product excluded), frozen before any call: the held-out measurement.",
    "Gold is the Shop catalog product id of the drawn product. The same physical item can exist under another catalog product id; an answer bound to such a duplicate is counted as a false accept here.",
    "The photos are the merchants' own catalog images. Many show no printed model number, so the full system is expected to stop and ask one question; zero follow-up answers were given, so those cases count as abstained.",
    "The baseline is handed the listing rows of the draw query (which normally contain the gold product); the full system has to find the product in the whole catalog from the photo alone.",
    PHOTO_CAVEAT,
    ONE_TAP_NOTE,
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
    if d and "system_false_accepts" in d:
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


def screen_block(screen: dict | None) -> dict | None:
    if not (screen and screen.get("complete") and screen.get("metrics")):
        return None
    return {**{k: screen.get(k) for k in ("run", "generated_at", "code_sha", "tree_dirty", "model", "listing_cap",
                                          "target_source", "label")}, **screen["metrics"], "notes": SCREEN_NOTES}


def tap_stats(doc: dict) -> dict:
    taps = [c.get("tap") or {} for c in doc["cases"]]
    return {"cases": len(taps), "choice_questions": sum(t.get("question") == "choice" for t in taps),
            "text_questions": sum(t.get("question") == "text" for t in taps),
            "answered_without_question": sum(t.get("question") is None and t.get("first_leg_outcome") in ("correct", "false_accept") for t in taps),
            "gold_offered": sum(bool(t.get("gold_offered")) for t in taps),
            "gold_first": sum(bool(t.get("gold_first")) for t in taps),
            "answered_gold": sum(t.get("answered") == "gold" for t in taps),
            "answered_none": sum(t.get("answered") == "none" for t in taps),
            "correct_via_tap": sum(c["arms"]["system_one_tap"]["outcome"] == "correct"
                                   and "bind:buyer_choice" in c["arms"]["system_one_tap"]["reasons"] for c in doc["cases"]),
            "correct_without_tap": sum(c["arms"]["system_one_tap"]["outcome"] == "correct"
                                       and "bind:buyer_choice" not in c["arms"]["system_one_tap"]["reasons"] for c in doc["cases"]),
            "gold_tap_without_verified_offer": sum((c.get("tap") or {}).get("answered") == "gold"
                                                   and c["arms"]["system_one_tap"]["outcome"] == "abstained" for c in doc["cases"]),
            "source": f"e2e-{doc['run']}.json", "code_sha": doc.get("code_sha"), "tree_dirty": doc.get("tree_dirty"),
            "visual_match": bool(doc.get("visual_match")), "generated_at": doc.get("generated_at"), "note": ONE_TAP_NOTE}


def run_block(doc: dict, diagnostic: dict | None, v1: dict | None = None, tap: tuple | None = None) -> dict:
    """One run for the side-by-side table. A run without its own baseline arm shows the v1 baseline, marked as such."""
    name, records, source = doc["run"], doc["cases"], None
    if records and "baseline" not in records[0]["arms"] and v1:
        base = {c["case_id"]: c["arms"]["baseline"] for c in v1["cases"]}
        records = [{**c, "arms": {**c["arms"], "baseline": base[c["case_id"]]},
                    "gold_in_candidates": None} for c in records]
        source = "e2e-v1.json (not re-run)"
    tap_doc, tap_diag = tap or (None, None)
    if tap_doc and tap_doc.get("complete"):
        tapped = {c["case_id"]: c["arms"]["system_one_tap"] for c in tap_doc["cases"]}
        records = [{**c, "arms": {**c["arms"], "system_one_tap": tapped[c["case_id"]]}} for c in records]
    walls = sorted(r["wall_ms"] for r in doc["cases"] if type(r.get("wall_ms")) is int)
    block = {"name": name, **RUN_META.get(name, {"label": name}),
             **{k: doc.get(k) for k in ("generated_at", "code_sha", "code_sha_at_end", "tree_dirty", "pair_version")},
             "visual_match": bool(doc.get("visual_match")), "visual_match_thresholds": doc.get("visual_match_thresholds"),
             "baseline_source": source, **score.summarize(records),
             "median_case_wall_ms": walls[len(walls) // 2] if walls else None}
    if source:
        block.pop("gold_in_baseline_candidates", None)
    if diagnostic and diagnostic.get("run") == name:
        block["diagnostic"] = {k: diagnostic[k] for k in DIAGNOSTIC_KEYS if k in diagnostic}
    if tap_doc and tap_doc.get("complete"):
        block["one_tap"] = tap_stats(tap_doc)
        if tap_diag and "diagnostic" in block and "false_accepts" in block["diagnostic"]:
            block["diagnostic"]["false_accepts"].update(
                {k: v for k, v in (tap_diag.get("false_accepts") or {}).items() if k == "system_one_tap"})
        if tap_diag and tap_diag.get("no_lead_rule") and "diagnostic" in block and not block["diagnostic"].get("no_lead_rule"):
            block["diagnostic"]["no_lead_rule"] = {**tap_diag["no_lead_rule"],
                                                   "source": f"zero-tap first legs of e2e-{tap_doc['run']}.json; "
                                                             + tap_diag["no_lead_rule"]["source"]}
    block["reading"] = reading(block)[:2] + tap_reading(block) + split_reading(block) + no_lead_reading(block)
    return block


def tap_reading(block: dict) -> list[str]:
    t, a = block.get("one_tap"), block["arms"].get("system_one_tap")
    if not t or not a:
        return []
    n = block["n"]
    setting = "on" if t["visual_match"] else "off (the shipped default)"
    return [f"With one correct tap (system_one_tap, the tap uses the gold label; this arm ran on code "
            f"{str(t.get('code_sha'))[:12]} with SF_VISUAL_MATCH {setting}): {a['correct']} correct "
            f"({t['correct_via_tap']} through the tap, {t['correct_without_tap']} without one), "
            f"{a['false_accepts']} false accepts, {a['abstained']} abstained, {a['errors']} errors of {n}; "
            f"{a['invalid_offers']} of its {a['answered']} answered offers were invalid and {a['cheapest_agreement']} matched "
            f"the reference cheapest valid offer. The system asked a choice question in {t['choice_questions']} of {n} "
            f"cases; the gold product was among the options in {t['gold_offered']} and was the first option (ordered "
            f"by the Decision 1 screen) in {t['gold_first']}; {t['gold_tap_without_verified_offer']} gold taps ended with "
            f"no verified offer. The baseline arm gets no such help."]


def no_lead_reading(block: dict) -> list[str]:
    r = (block.get("diagnostic") or {}).get("no_lead_rule")
    if not r:
        note = (block.get("diagnostic") or {}).get("no_lead_rule_note")
        return [f"Documented next step (a rule without the lead requirement), NOT deployed and NOT a score: {note}."] if note else []
    return [f"Documented next step, NOT deployed and NOT a score (computed from the recorded runs, nothing re-run): a "
            f"rule without the lead requirement ({r['rule']}) would have accepted {r['accepted']} of {r['cases']} cases "
            f"({r['accepted_by_deployed_rule']} of them also accepted by the deployed rule): {r['accepted_gold']} the "
            f"gold product, {r['accepted_other_catalog_id']} another catalog product id."]


def gap_reading(block: dict) -> list[str]:
    gap = (block.get("diagnostic") or {}).get("visual_match_gap")
    if not gap:
        return []
    codes = ", ".join(f"{k} x{v}" for k, v in gap["codes"].items()) or "none"
    return [f"Why visual_match did not fire on the {gap['abstentions_with_screen_scores']} abstentions that reached the "
            f"screen (post-hoc, from the probabilities on the question's options): {codes}. The gold product was the "
            f"top-scored option in {gap['gold_is_top_option']} of them and among the options in {gap['gold_among_options']}."]


def split_reading(block: dict) -> list[str]:
    split = (block.get("diagnostic") or {}).get("false_accepts")
    if not split:
        return gap_reading(block)
    return gap_reading(block) + [f"False accepts by catalog product id, {arm}: {v['strict']} strict, of which {v['same_item_other_catalog_id']} "
            f"carry mechanical evidence of being the same item under another catalog product id (same model number "
            f"or identical catalog title) and {v['no_same_item_evidence']} carry none."
            for arm, v in split.items()]


def comparison(runs: list[dict]) -> list[str]:
    """Generated sentences across runs (system arm), nothing typed by hand."""
    by = {r["name"]: r for r in runs}
    out = []
    def line(r):
        a = r["arms"]["system"]
        return (f"{a['correct']} correct, {a['false_accepts']} false accepts, {a['abstained']} abstained, "
                f"{a['errors']} errors of {r['n']}")
    if "v1" in by and "v2" in by:
        out.append(f"Full system on draw A: v1 (visual_match off) {line(by['v1'])}; v2 (visual_match on, IN-SAMPLE) "
                   f"{line(by['v2'])}. v2 is not an unbiased estimate: the change was designed from this draw.")
    if "b" in by:
        b = by["b"]
        base = b["arms"]["baseline"]
        out.append(f"Held-out draw B (the number to quote): full system {line(b)}; one-call baseline {base['correct']} "
                   f"correct, {base['false_accepts']} false accepts, {base['abstained']} abstained, {base['errors']} "
                   f"errors of {b['n']}.")
    for r in runs:
        out += [f"Run {r['name']}: {x}" for x in tap_reading(r) + no_lead_reading(r)]
    return out


def build(cases: dict, diagnostic: dict | None, screen: dict | None = None, later: dict | None = None,
          screen_b: dict | None = None, taps: dict | None = None) -> dict:
    records = cases["cases"]
    walls = sorted(r["wall_ms"] for r in records if type(r.get("wall_ms")) is int)
    s = {**{k: cases.get(k) for k in HEAD_KEYS}, "run": cases["run"], **score.summarize(records),
         "median_case_wall_ms": walls[len(walls) // 2] if walls else None}
    if diagnostic and diagnostic.get("run") == cases["run"]:
        s["diagnostic"] = {k: diagnostic[k] for k in DIAGNOSTIC_KEYS if k in diagnostic}
    if screen_block(screen):
        s["screen"] = screen_block(screen)
    s["reading"] = reading(s)
    runs = [run_block({**cases, "visual_match": False}, diagnostic)]
    for name in ("v2", "b"):
        doc, diag = (later or {}).get(name) or (None, None)
        if doc and doc.get("complete"):
            runs.append(run_block(doc, diag, cases, (taps or {}).get(name)))
    s["runs"] = runs
    s["headline_run"] = "b" if any(r["name"] == "b" for r in runs) else "v1"
    s["comparison"] = comparison(runs)
    s["photo_caveat"] = PHOTO_CAVEAT
    s["reading"].append(PHOTO_CAVEAT)
    s["screens"] = {k: v for k, v in (("a", screen_block(screen)), ("b", screen_block(screen_b))) if v}
    s["notes"] = NOTES + RUN_NOTES.get(cases["run"], [])
    if cases.get("pair_v2_note_seen_at_start") is False:
        s["notes"].append("The arms were started before the team log carried the 'pair v2 landed' note; pair_version is what the code reported.")
    return s


def main() -> int:
    cases = json.loads((RESULTS / "e2e-v1.json").read_text())
    if not cases.get("complete"):
        print("e2e-v1.json is not complete; no summary written", file=sys.stderr)
        return 1
    def read(name):
        target = RESULTS / name
        return json.loads(target.read_text()) if target.exists() else None

    later = {name: (read(f"e2e-{name}.json"), read(f"e2e-diagnostic-{name}.json")) for name in ("v2", "b")}
    taps = {"v2": (read("e2e-a-tap.json"), read("e2e-diagnostic-a-tap.json")),
            "b": (read("e2e-b-tap.json"), read("e2e-diagnostic-b-tap.json"))}
    summary = build(cases, read("e2e-diagnostic-v1.json"), read("e2e-screen.json"), later, read("e2e-screen-b.json"), taps)
    summary["summarized_at"] = int(time.time())
    (RESULTS / "e2e-summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    print(f"e2e-summary.json rebuilt: runs {[r['name'] for r in summary['runs']]}, screens {list(summary['screens'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
