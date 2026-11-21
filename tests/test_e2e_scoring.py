"""Scoring of the end-to-end benchmark, with fakes only (no network, no model). Amounts are arbitrary test values."""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(f"e2e_{name}", ROOT / "eval" / "e2e" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


score = _load("e2e_score")
report = _load("report")
diagnose = _load("diagnose")
runner_source = (ROOT / "eval" / "e2e" / "run.py").read_text()

GOLD, OTHER = "gid://shopify/p/gold", "gid://shopify/p/other"


def offer(**over):
    row = {"seller": "s1", "listing": "l1", "product": GOLD, "condition": "new", "stock": "in_stock",
           "currency": "USD", "item_cents": 1000, "identity_basis": "catalog_product"}
    row.update(over)
    return row


def run_doc(state="complete", upid=GOLD, winners=None, status="winner", error=None, question=None):
    return {"state": state, "target": {"upid": upid, "identity_basis": "model_number"}, "error": error,
            "question": question, "result": {"status": status, "winners": [offer()] if winners is None else winners}}


def test_system_right_product_with_winner_is_correct():
    out = score.system_outcome(run_doc(), GOLD)
    assert out["outcome"] == "correct" and out["offer"]["listing"] == "l1"
    assert "bind:model_number" in out["reasons"]


def test_system_other_product_is_false_accept():
    assert score.system_outcome(run_doc(upid=OTHER), GOLD)["outcome"] == "false_accept"
    assert score.system_outcome(run_doc(upid=""), "")["outcome"] == "false_accept"


def test_system_no_answer_is_abstained_with_reason_codes():
    asked = run_doc(state="needs_detail", upid="", winners=[], status="needs_identity", question={"kind": "choice"})
    out = score.system_outcome(asked, GOLD)
    assert out == {"outcome": "abstained", "offer": None,
                   "reasons": ["state:needs_detail", "result:needs_identity", "question:choice"]}
    none = score.system_outcome(run_doc(winners=[], status="no_verified_offer"), GOLD)
    assert none["outcome"] == "abstained" and "result:no_verified_offer" in none["reasons"]


def test_system_failures_are_errors_not_abstentions():
    failed = run_doc(state="unavailable", winners=[], error={"code": "model_call_failed", "message": "x"})
    assert score.system_outcome(failed, GOLD) == {"outcome": "error", "reasons": ["unavailable:model_call_failed"], "offer": None}
    assert score.system_outcome(None, GOLD)["outcome"] == "error"
    assert score.system_outcome(run_doc(state="verifying"), GOLD)["reasons"] == ["not_terminal:verifying"]


def test_baseline_outcomes():
    rows = [offer(product=OTHER, listing="l0"), offer()]
    assert score.baseline_outcome({"status": "winner", "index": 1, "reason": ""}, rows, GOLD)["outcome"] == "correct"
    assert score.baseline_outcome({"status": "winner", "index": 0, "reason": ""}, rows, GOLD)["outcome"] == "false_accept"
    assert score.baseline_outcome({"status": "abstain", "index": None, "reason": ""}, rows, GOLD)["outcome"] == "abstained"
    for bad in (2, -1, None, True, "1"):
        assert score.baseline_outcome({"status": "winner", "index": bad, "reason": ""}, rows, GOLD)["reasons"] == ["bad_index"]
    assert score.baseline_outcome(None, rows, GOLD)["outcome"] == "error"
    assert score.baseline_outcome({"status": "maybe"}, rows, GOLD)["outcome"] == "error"


def test_offer_validity_reason_codes():
    assert score.offer_validity(None, None) is None
    assert score.offer_validity(offer(), "confirmed") == {"valid": True, "reasons": [], "confirm": "confirmed"}
    assert score.offer_validity(offer(), "unavailable")["valid"] is True
    assert score.offer_validity(offer(), None)["confirm"] == "not_checked"
    bad = score.offer_validity(offer(condition="secondhand", stock="sold_out", currency="CAD", item_cents=None), "mismatch")
    assert bad["valid"] is False
    assert bad["reasons"] == ["condition_not_new", "not_in_stock", "not_usd", "price_missing", "storefront_mismatch"]
    assert score.offer_validity(offer(item_cents=True), "confirmed")["reasons"] == ["price_missing"]


def test_cheapest_codes():
    ref = score.reference({"status": "winner", "price_cents": 1000, "eligible": [{}, {}]})
    assert ref == {"status": "winner", "price_cents": 1000, "eligible": 2}
    right = {"outcome": "correct", "reasons": [], "offer": offer()}
    assert score.cheapest_code(right, ref) == "agree"
    assert score.cheapest_code({**right, "offer": offer(item_cents=1200)}, ref) == "pricier_than_reference"
    assert score.cheapest_code({**right, "offer": offer(item_cents=900)}, ref) == "cheaper_than_reference"
    assert score.cheapest_code({**right, "offer": offer(item_cents=None)}, ref) == "price_missing"
    assert score.cheapest_code({"outcome": "false_accept", "reasons": [], "offer": offer()}, ref) == "not_applicable"
    assert score.cheapest_code({"outcome": "abstained", "reasons": [], "offer": None}, ref) == "not_applicable"
    assert score.cheapest_code(right, score.reference({"status": "abstain", "price_cents": None, "eligible": []})) == "no_reference_offer"
    assert score.cheapest_code(right, score.reference(None)) == "reference_unavailable"


def _case(case_id, sys_arm, base_arm, ref_status="winner", gold_in=True):
    return {"case_id": case_id, "query": "q", "gold_upid": GOLD, "sellers": 2, "gold_in_candidates": gold_in,
            "reference": {"status": ref_status, "eligible": 1}, "arms": {"system": sys_arm, "baseline": base_arm}}


def _arm(outcome, reasons=(), valid=None, confirm="confirmed", invalid=(), cheapest="not_applicable", ms=1000):
    off = None if valid is None else {"valid": valid, "reasons": list(invalid), "confirm": confirm}
    return {"outcome": outcome, "reasons": list(reasons), "offer": off, "cheapest": cheapest, "ms": ms}


def test_arm_record_publishes_codes_only():
    out = {"outcome": "correct", "reasons": ["winner"], "offer": offer()}
    rec = score.arm_record(out, score.offer_validity(out["offer"], "confirmed"), "agree", 1234)
    assert rec == {"outcome": "correct", "reasons": ["winner"], "cheapest": "agree", "ms": 1234,
                   "offer": {"valid": True, "reasons": [], "confirm": "confirmed"}}
    assert "item_cents" not in str(rec)


def test_summary_keeps_every_case_in_the_denominator():
    cases = [
        _case("a", _arm("correct", valid=True, cheapest="agree", ms=3000), _arm("correct", valid=True, cheapest="pricier_than_reference", ms=500)),
        _case("b", _arm("abstained", ["state:needs_detail"], ms=2000),
              _arm("false_accept", ["winner"], valid=False, confirm="mismatch", invalid=["not_in_stock", "storefront_mismatch"], ms=700)),
        _case("c", _arm("error", ["exception:ValueError"], ms=None), _arm("abstained", ["abstain"], ms=900), ref_status="abstain", gold_in=False),
        _case("d", _arm("false_accept", ["state:complete"], valid=True, confirm="unavailable", ms=1000), _arm("error", ["bad_index"], ms=None)),
    ]
    s = score.summarize(cases)
    assert s["n"] == 4 and s["reference_offer_cases"] == 3 and s["gold_in_baseline_candidates"] == 3
    sy, ba = s["arms"]["system"], s["arms"]["baseline"]
    assert (sy["correct"], sy["false_accepts"], sy["abstained"], sy["errors"]) == (1, 1, 1, 1)
    assert (ba["correct"], ba["false_accepts"], ba["abstained"], ba["errors"]) == (1, 1, 1, 1)
    for arm in (sy, ba):
        assert arm["correct"] + arm["false_accepts"] + arm["abstained"] + arm["errors"] == s["n"]
    assert sy["answered"] == 2 and sy["invalid_offers"] == 0 and sy["storefront_checks"] == {"confirmed": 1, "unavailable": 1}
    assert ba["invalid_offers"] == 1 and ba["invalid_reasons"] == {"not_in_stock": 1, "storefront_mismatch": 1}
    assert sy["cheapest_agreement"] == 1 and ba["cheapest_agreement"] == 0
    assert sy["median_ms"] == 2000 and ba["median_ms"] == 700
    assert sy["reason_codes"] == {"exception:ValueError": 1, "state:complete": 1, "state:needs_detail": 1}
    assert s["paired"]["system_correct__baseline_correct"] == 1 and sum(s["paired"].values()) == 4


def test_empty_arm_has_no_median():
    assert score.summarize_arm([_arm("error", ["x"], ms=None)])["median_ms"] is None


def test_report_table_is_built_from_the_json_only():
    cases = [_case("a", _arm("correct", valid=True, cheapest="agree", ms=3000), _arm("abstained", ["abstain"], ms=500)),
             _case("b", _arm("abstained", ["state:needs_detail"], ms=2000), _arm("false_accept", ["winner"], valid=True, ms=700))]
    summary = {"generated_at": 2, "code_sha": "c" * 40, "tree_dirty": True, "pair_version": "v2", **score.summarize(cases),
               "notes": ["note one"]}
    text = "\n".join(report.lines({"seed": "s" * 40, "created_at": 1}, {"run": "v1", "cases": cases}, summary))
    assert "SignalForge full system             1/2      0/2" in text
    assert "listing check version v2" in text and "+ uncommitted changes" in text
    assert "- b (q, 2 sellers): system abstained [state:needs_detail]; baseline false_accept" in text
    assert "- note one" in text
    md = report.lines({"seed": "s" * 40, "created_at": 1}, {"run": "v1", "cases": cases}, summary, markdown=True)
    assert any(line.startswith("| SignalForge full system | 1/2 | 0/2 | 1/2 | 0/2 | 0/1 answered | 1/2 | 2500 |") for line in md)


def test_diagnostic_flags_and_abstention_codes():
    detail = {"target": {"brand": "Acme", "mpn": "", "gtin": None}, "decision": {"pass": True, "choice": "single"},
              "question": {"kind": "choice"}}
    flags = diagnose.evidence_flags(detail)
    assert flags == {"brand_read": True, "model_number_read": False, "barcode_read": False, "gate_pass": True,
                     "gate_choice": "single", "question_kind": "choice"}
    assert diagnose.abstention_code(flags) == "no_model_number_or_barcode_in_photo"
    assert diagnose.abstention_code({**flags, "model_number_read": True}) == "model_number_read_but_not_bound"
    assert diagnose.abstention_code({**flags, "gate_pass": False}) == "gate_did_not_pass"
    assert diagnose.abstention_code(diagnose.evidence_flags(None)) == "gate_did_not_pass"


def test_duplicate_check_uses_exact_model_tokens(monkeypatch):
    titles = {GOLD: ["Acme Widget AB-1234 blue", "Widget"], OTHER: ["Other  Title"], "same": ["acme widget ab-1234 BLUE"]}
    monkeypatch.setattr(diagnose, "titles_of", lambda upid: titles[upid])
    hit = diagnose.duplicate_check({"target": {"mpn": "AB-1234"}}, GOLD, OTHER)
    assert hit == {"bound_on_model_number": True, "gold_titles_carry_bound_model_number": True,
                   "identical_catalog_title": False, "same_item_evidence": "model_number"}
    miss = diagnose.duplicate_check({"target": {"mpn": "AB-123"}}, GOLD, OTHER)
    assert miss["gold_titles_carry_bound_model_number"] is False and miss["same_item_evidence"] is None
    by_title = diagnose.duplicate_check({}, GOLD, "same")
    assert by_title["bound_on_model_number"] is False and by_title["same_item_evidence"] == "identical_catalog_title"
    failed = diagnose.duplicate_check({}, GOLD, "missing")
    assert failed["error"] == "KeyError" and failed["same_item_evidence"] is None
    split = diagnose.false_accept_split([hit, miss, by_title])
    assert split == {"strict": 3, "same_item_other_catalog_id": 2, "no_same_item_evidence": 1}


def test_runs_side_by_side_mark_in_sample_and_copied_baseline():
    summarize = _load("e2e_summary")
    v1 = {"run": "v1", "complete": True, "code_sha": "a" * 40, "pair_version": "v2", "cases": [
        {**_case("a", _arm("abstained", ["state:needs_detail"]), _arm("correct", valid=True, cheapest="agree")), "wall_ms": 10},
        {**_case("b", _arm("abstained", ["state:needs_detail"]), _arm("abstained", ["abstain"])), "wall_ms": 20}]}
    v2_cases = [{**_case("a", _arm("correct", valid=True, cheapest="agree"), None), "wall_ms": 30},
                {**_case("b", _arm("false_accept", ["bind:visual_match"], valid=True), None), "wall_ms": 40}]
    for c in v2_cases:
        del c["arms"]["baseline"]
    v2 = {"run": "v2", "complete": True, "code_sha": "b" * 40, "visual_match": True, "cases": v2_cases}
    diag = {"run": "v2", "post_hoc": True, "changes_scores": False, "system_abstention_codes": {},
            "false_accepts": {"system": {"strict": 1, "same_item_other_catalog_id": 1, "no_same_item_evidence": 0}}}
    held = {"run": "b", "complete": True, "code_sha": "c" * 40, "visual_match": True, "cases": [
        {**_case("x", _arm("correct", valid=True, cheapest="agree"), _arm("correct", valid=True)), "wall_ms": 5}]}
    s = summarize.build(v1, None, None, {"v2": (v2, diag), "b": (held, None)})
    assert [r["name"] for r in s["runs"]] == ["v1", "v2", "b"] and s["headline_run"] == "b"
    r1, r2, rb = s["runs"]
    assert s["arms"] == r1["arms"] and r1["visual_match"] is False and r1["in_sample"] is False
    assert r2["in_sample"] is True and r2["visual_match"] is True and r2["baseline_source"] == "e2e-v1.json (not re-run)"
    assert r2["arms"]["baseline"] == r1["arms"]["baseline"]
    assert (r2["arms"]["system"]["correct"], r2["arms"]["system"]["false_accepts"]) == (1, 1) and r2["n"] == 2
    assert r2["diagnostic"]["false_accepts"]["system"]["same_item_other_catalog_id"] == 1
    assert rb["held_out"] is True and rb["baseline_source"] is None and rb["arms"]["baseline"]["correct"] == 1
    assert "IN-SAMPLE" in s["comparison"][0] and s["comparison"][1].startswith("Held-out draw B (the number to quote)")
    unfinished = summarize.build(v1, None, None, {"v2": ({**v2, "complete": False}, None)})
    assert [r["name"] for r in unfinished["runs"]] == ["v1"] and unfinished["headline_run"] == "v1"
    text = "\n".join(report.lines({"seed": "s" * 40, "created_at": 1}, v1, s))
    assert "v2 (draw A, IN-SAMPLE) full system 1/2 1/2 1 of 1 0/2" in " ".join(text.split())
    assert "one Carbon call [from v1]" in text and "b (draw B, held-out)" in text and "SF_VISUAL_MATCH on" in text


def test_rate_limit_detector_ignores_timing_digits():
    import re
    pattern = re.search(r'_LIMIT = re.compile\(r"(.+)"\)', runner_source).group(1)
    limit = re.compile(pattern)
    assert limit.search("ClientError 429: quota") and limit.search("RESOURCE_EXHAUSTED")
    assert not limit.search("bind done in 4292 ms") and not limit.search("1429")


def test_report_prints_the_diagnostic_block():
    cases = [_case("a", _arm("abstained", ["state:needs_detail"]), _arm("abstained", ["abstain"]))]
    summary = {"generated_at": 2, "code_sha": "c" * 40, **score.summarize(cases), "notes": []}
    diag = {"system_abstention_codes": {"no_model_number_or_barcode_in_photo": 1}, "system_false_accepts": 0,
            "system_false_accepts_same_model_number_as_gold": 0}
    text = "\n".join(report.lines({"seed": "s" * 40, "created_at": 1}, {"run": "v1", "cases": cases}, summary, diagnostic=diag))
    assert "abstentions by cause: no_model_number_or_barcode_in_photo x1" in text and ": 0/0" in text


def test_summary_rebuild_adds_generated_reading_and_keeps_counts():
    summarize = _load("e2e_summary")
    records = [
        {**_case("a", _arm("abstained", ["state:needs_detail"]), _arm("correct", valid=True, cheapest="agree")), "wall_ms": 3000},
        {**_case("b", _arm("false_accept", ["state:partial"], valid=True), _arm("correct", valid=False, invalid=["storefront_mismatch"])), "wall_ms": 5000},
    ]
    cases = {"run": "v1", "complete": True, "code_sha": "c" * 40, "pair_version": "v2", "cases": records}
    diag = {"run": "v1", "post_hoc": True, "changes_scores": False, "system_abstention_codes": {"gate_did_not_pass": 1},
            "system_false_accepts": 1, "system_false_accepts_same_model_number_as_gold": 0}
    s = summarize.build(cases, diag)
    assert {k: s[k] for k in ("n", "arms", "paired")} == {k: score.summarize(records)[k] for k in ("n", "arms", "paired")}
    assert s["code_sha"] == "c" * 40 and s["pair_version"] == "v2" and s["median_case_wall_ms"] == 5000
    assert s["reading"][0].startswith("Full system: answered 1 of 2 photos (0 the gold product, 1 another catalog product id)")
    assert "1 of its 2 answered offers were invalid and 1 matched the reference" in s["reading"][1]
    assert "bought no measured identity safety" in s["reading"][2]
    assert "gate_did_not_pass x1" in s["reading"][3] and s["diagnostic"]["changes_scores"] is False
    assert any("429" in note for note in s["notes"])
    assert "diagnostic" not in summarize.build(cases, {**diag, "run": "v9"})
    better = [_case("a", _arm("correct", valid=True, cheapest="agree"), _arm("abstained", ["abstain"]))]
    assert "at least as many photos" in summarize.build({"run": "x", "cases": better}, None)["reading"][2]


calibration = _load("e2e_calibration")


def _judged(pairs, status="ok", ms=100, **over):
    case = {"status": status, "ms": ms, "not_scored": 0, "called": True,
            "judgments": [{"index": i, "p_same": p, "confidence": 0.5, "positive": y} for i, (p, y) in enumerate(pairs)]}
    case.update(over)
    return case


def test_threshold_counts_and_rates():
    pairs = [(0.95, True), (0.9, False), (0.6, True), (0.4, False), (0.1, False)]
    hi = calibration.at_threshold(pairs, 0.9)
    assert (hi["tp"], hi["fp"], hi["fn"], hi["tn"]) == (1, 1, 1, 2)
    assert hi["precision"] == 0.5 and hi["recall"] == 0.5 and hi["false_accept_rate"] == round(1 / 3, 4)
    mid = calibration.at_threshold(pairs, 0.5)
    assert mid["precision"] == round(2 / 3, 4) and mid["recall"] == 1.0
    none = calibration.at_threshold([(0.1, False)], 0.9)
    assert none["precision"] is None and none["recall"] is None and none["false_accept_rate"] == 0.0


def test_roc_auc_is_the_rank_statistic_with_ties():
    assert calibration.roc_auc([(0.9, True), (0.8, True), (0.2, False), (0.1, False)]) == 1.0
    assert calibration.roc_auc([(0.1, True), (0.9, False)]) == 0.0
    assert calibration.roc_auc([(0.5, True), (0.5, False)]) == 0.5
    assert calibration.roc_auc([(0.9, True), (0.5, True), (0.5, False), (0.1, False)]) == 0.875
    assert calibration.roc_auc([(0.9, True)]) is None and calibration.roc_auc([]) is None


def test_reliability_bins_cover_zero_to_one_inclusive():
    table = calibration.reliability([(0.0, False), (0.19, False), (0.2, True), (0.85, True), (1.0, True), (0.9, False)])
    assert [b["count"] for b in table] == [2, 1, 0, 0, 3]
    assert table[0]["observed_positive_rate"] == 0.0 and table[1]["mean_predicted"] == 0.2
    assert table[2]["mean_predicted"] is None and table[2]["observed_positive_rate"] is None
    assert table[4]["mean_predicted"] == round((0.85 + 1.0 + 0.9) / 3, 4) and table[4]["observed_positive_rate"] == round(2 / 3, 4)
    assert calibration.brier([(1.0, True), (0.0, False), (0.5, True)]) == round(0.25 / 3, 4)


def test_screen_metrics_keep_failed_calls_in_the_denominator():
    cases = [_judged([(0.95, True), (0.3, False)], ms=100), _judged([(0.8, False), (0.7, True)], ms=300),
             _judged([(0.2, False)], ms=200, not_scored=4), _judged([], status="error", ms=None)]
    m = calibration.metrics(cases)
    assert (m["cases"], m["calls"], m["error_cases"], m["judgments"], m["positives"], m["negatives"]) == (4, 4, 1, 5, 2, 3)
    assert m["median_ms_per_call"] == 200 and m["not_scored"] == 4 and m["cases_without_positive"] == 1
    assert (m["top1_positive_cases"], m["cases_with_positive"]) == (1, 2)
    assert m["false_accept_rate_at_0.9"] == 0.0 and [t["threshold"] for t in m["thresholds"]] == [0.5, 0.75, 0.9]
    assert calibration.metrics([{**_judged([], status="error", ms=None), "called": False}])["calls"] == 0


def test_screen_block_reaches_summary_and_report():
    summarize = _load("e2e_summary")
    records = [{**_case("a", _arm("abstained", ["state:needs_detail"]), _arm("correct", valid=True, cheapest="agree")), "wall_ms": 1}]
    screen_cases = [_judged([(0.95, True), (0.3, False)])]
    screen = {"run": "screen-v1", "complete": True, "code_sha": "d" * 40, "model": "gemini-decision-1",
              "metrics": calibration.metrics(screen_cases), "cases": screen_cases}
    s = summarize.build({"run": "v1", "code_sha": "c" * 40, "cases": records}, None, screen)
    assert s["screen"]["roc_auc"] == 1.0 and s["screen"]["judgments"] == 2 and "judgments" not in str(s["screen"].get("cases"))
    assert any("lower bound" in note for note in s["screen"]["notes"])
    assert any(x.startswith("Listing screen (one gemini-decision-1 call per photo): 2 listing judgments over 1 cases")
               for x in s["reading"]) and s["reading"][-1] == summarize.PHOTO_CAVEAT
    assert "screen" not in summarize.build({"run": "v1", "cases": records}, None, {**screen, "complete": False})
    text = "\n".join(report.lines({"seed": "s" * 40, "created_at": 1}, {"run": "v1", "cases": records}, s))
    assert "ROC AUC 1.0, Brier" in text and "p >= 0.9   1         1.000      1.000   0.000 (0/1)" in text
    assert "0.8-1.0          1      0.950           1.000" in text


def test_visual_match_gap_codes():
    q = {"options": [{"id": GOLD, "p": 0.8}, {"id": OTHER, "p": 0.7}, {"id": "none", "label": "None of these"}]}
    assert diagnose.visual_match_gap(q, GOLD, 0.75, 0.25) == {
        "code": "screen_margin_below_threshold", "gold_is_top_option": True, "gold_among_options": True}
    low = {"options": [{"id": OTHER, "p": 0.6}, {"id": GOLD, "p": 0.2}]}
    assert diagnose.visual_match_gap(low, GOLD, 0.75, 0.25) == {
        "code": "screen_top_below_threshold", "gold_is_top_option": False, "gold_among_options": True}
    clear = {"options": [{"id": OTHER, "p": 0.9}, {"id": "x", "p": 0.3}]}
    out = diagnose.visual_match_gap(clear, GOLD, 0.75, 0.25)
    assert out["code"] == "second_model_pick_differs_or_missing" and out["gold_among_options"] is False
    assert diagnose.visual_match_gap({"options": [{"id": "a"}]}, GOLD, 0.75, 0.25) is None
    assert diagnose.visual_match_gap(None, GOLD, 0.75, 0.25) is None
    counts = diagnose.gap_counts([diagnose.visual_match_gap(q, GOLD, 0.75, 0.25), diagnose.visual_match_gap(low, GOLD, 0.75, 0.25), out])
    assert counts["abstentions_with_screen_scores"] == 3 and counts["gold_is_top_option"] == 1 and counts["gold_among_options"] == 2
    assert counts["codes"] == {"screen_margin_below_threshold": 1, "screen_top_below_threshold": 1,
                               "second_model_pick_differs_or_missing": 1}


def test_no_lead_rule_is_post_hoc_and_reads_recorded_runs_only():
    refused = "No exact product could be confirmed from the photo alone · visual match not accepted: best catalog product 0.81, next 0.78 (needs 0.75 and a 0.25 lead)"
    def system(detail, top=GOLD, p=0.81):
        return {"progress": [{"id": "bind", "detail": detail}],
                "question": {"options": [{"id": top, "p": p}, {"id": "x", "p": 0.78}, {"id": "none"}]}}
    asked = {"outcome": "abstained", "reasons": ["state:needs_detail"]}
    assert diagnose.no_lead_case(system(refused), asked, GOLD, 0.75) == {"accepted": True, "deployed_rule_accepted": False, "gold": True}
    assert diagnose.no_lead_case(system(refused, top=OTHER), asked, GOLD, 0.75)["gold"] is False
    assert diagnose.no_lead_case(system(refused + "; Carbon chose a different product"), asked, GOLD, 0.75)["accepted"] is False
    assert diagnose.no_lead_case(system(refused, p=0.6), asked, GOLD, 0.75)["accepted"] is False
    assert diagnose.no_lead_case(system("No exact product could be confirmed from the photo alone"), asked, GOLD, 0.75) is None
    fired = diagnose.no_lead_case({}, {"outcome": "correct", "reasons": ["bind:visual_match"]}, GOLD, 0.75)
    assert fired == {"accepted": True, "deployed_rule_accepted": True, "gold": True}
    counts = diagnose.no_lead_counts([fired, diagnose.no_lead_case(system(refused, top=OTHER), asked, GOLD, 0.75), None,
                                      diagnose.no_lead_case(system(refused, p=0.6), asked, GOLD, 0.75)], 4, 0.75)
    assert counts["deployed"] is False and counts["is_score"] is False
    assert (counts["cases"], counts["reached_visual_match_decision"], counts["accepted"], counts["accepted_by_deployed_rule"],
            counts["accepted_gold"], counts["accepted_other_catalog_id"]) == (4, 3, 2, 1, 1, 1)


def test_one_tap_arm_is_a_third_row_with_its_own_stats():
    summarize = _load("e2e_summary")
    v1 = {"run": "v1", "complete": True, "code_sha": "a" * 40, "generated_at": 1,
          "cases": [{**_case("a", _arm("abstained", ["state:needs_detail"]), _arm("correct", valid=True)), "wall_ms": 1}]}
    held = {"run": "b", "complete": True, "visual_match": True, "cases": [
        {**_case("x", _arm("abstained", ["state:needs_detail"]), _arm("correct", valid=True)), "wall_ms": 5},
        {**_case("y", _arm("abstained", ["state:needs_detail"]), _arm("abstained", ["abstain"])), "wall_ms": 5}]}
    tap = {"run": "b-tap", "complete": True, "visual_match": True, "code_sha": "e" * 40, "cases": [
        {"case_id": "x", "arms": {"system_one_tap": _arm("correct", valid=True, cheapest="agree", ms=9000)},
         "tap": {"question": "choice", "gold_offered": True, "gold_first": True, "answered": "gold", "first_leg_outcome": "abstained"}},
        {"case_id": "y", "arms": {"system_one_tap": _arm("abstained", ["state:needs_detail"], ms=4000)},
         "tap": {"question": "choice", "gold_offered": False, "gold_first": False, "answered": "none", "first_leg_outcome": "abstained"}}]}
    s = summarize.build(v1, None, None, {"b": (held, None)}, None, {"b": (tap, None)})
    b = s["runs"][1]
    assert list(b["arms"]) == ["system", "system_one_tap", "baseline"]
    assert (b["arms"]["system_one_tap"]["correct"], b["arms"]["system_one_tap"]["abstained"], b["arms"]["system"]["correct"]) == (1, 1, 0)
    t = b["one_tap"]
    assert (t["choice_questions"], t["gold_offered"], t["gold_first"], t["answered_gold"], t["answered_none"]) == (2, 1, 1, 1, 1)
    assert "gold label" in t["note"] and any("The baseline arm gets no such help" in x for x in b["reading"])
    assert s["photo_caveat"] == summarize.PHOTO_CAVEAT and summarize.PHOTO_CAVEAT in s["notes"]
    flat = " ".join("\n".join(report.lines({"seed": "s" * 40, "created_at": 1}, v1, s)).split())
    assert "b (draw B, held-out) full system + one correct tap 1/2 0/2" in flat and "gold the FIRST option in 1" in flat
    unfinished = summarize.build(v1, None, None, {"b": (held, None)}, None, {"b": ({**tap, "complete": False}, None)})
    assert "system_one_tap" not in unfinished["runs"][1]["arms"] and "one_tap" not in unfinished["runs"][1]
