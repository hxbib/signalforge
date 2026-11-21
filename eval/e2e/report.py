"""Print the two-arm table from eval/results/e2e-*.json. Every number comes from those files.

  .venv/bin/python eval/e2e/report.py            # table + where each arm failed
  .venv/bin/python eval/e2e/report.py --markdown # the same as a Markdown block
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

RESULTS = Path(__file__).resolve().parents[2] / "eval" / "results"
ARMS = (("system", "SignalForge full system"), ("baseline", "Baseline: one Gemini 4 Carbon call"))
COLUMNS = (("correct", "correct"), ("false_accepts", "false accepts"), ("abstained", "abstained"), ("errors", "errors"),
           ("invalid_offers", "invalid offers"), ("cheapest_agreement", "cheapest agrees"), ("median_ms", "median ms"))


def load() -> tuple[dict, dict, dict]:
    return tuple(json.loads((RESULTS / name).read_text())
                 for name in ("e2e-freeze.json", "e2e-v1.json", "e2e-summary.json"))


def _num(value) -> str:
    return "-" if value is None else f"{value:.3f}"


def _codes(counts: dict) -> str:
    return ", ".join(f"{k} x{v}" for k, v in counts.items()) or "none"


ARM_LABEL = {"system": "full system", "baseline": "one Carbon call"}


def runs_table(summary: dict) -> list[str]:
    """v1 (A), v2 (A, in-sample), B (held-out) side by side, per arm."""
    runs = summary.get("runs") or []
    if len(runs) < 2:
        return []
    head = ["run", "arm", "correct", "false accepts", "same item, other id", "abstained", "errors", "invalid offers",
            "cheapest agrees", "median ms"]
    rows = []
    for r in runs:
        split = (r.get("diagnostic") or {}).get("false_accepts") or {}
        tag = f"{r['name']} (draw {r.get('draw')}{', IN-SAMPLE' if r.get('in_sample') else ''}{', held-out' if r.get('held_out') else ''})"
        for arm in ("system", "baseline"):
            a, n = r["arms"][arm], r["n"]
            same = split.get(arm, {}).get("same_item_other_catalog_id")
            label = ARM_LABEL[arm] + (" [from v1]" if arm == "baseline" and r.get("baseline_source") else "")
            rows.append([tag, label, f"{a['correct']}/{n}", f"{a['false_accepts']}/{n}",
                         "not analysed" if same is None else f"{same} of {a['false_accepts']}", f"{a['abstained']}/{n}",
                         f"{a['errors']}/{n}", f"{a['invalid_offers']}/{a['answered']} answered",
                         f"{a['cheapest_agreement']}/{n}", "-" if a["median_ms"] is None else str(a["median_ms"])])
    widths = [max(len(x[i]) for x in [head] + rows) for i in range(len(head))]
    out = ["", "All runs side by side (false accepts are strict: any other catalog product id; 'same item, other id' is the "
               "post-hoc subset with mechanical same-item evidence and changes no score):"]
    out += ["  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip() for r in [head] + rows]
    for r in runs:
        out.append(f"- {r['name']}: {r['label']}; code {str(r.get('code_sha'))[:12]}"
                   f"{' + uncommitted changes' if r.get('tree_dirty') else ''}, listing check {r.get('pair_version')}, "
                   f"SF_VISUAL_MATCH {'on' if r.get('visual_match') else 'off'}"
                   + (f"; baseline: {r['baseline_source']}" if r.get("baseline_source") else ""))
        codes = (r.get("diagnostic") or {}).get("system_abstention_codes")
        if codes is not None:
            out.append(f"  full-system abstentions by cause: {_codes(codes)}")
        gap = (r.get("diagnostic") or {}).get("visual_match_gap")
        if gap:
            out.append(f"  why visual_match did not fire ({gap['abstentions_with_screen_scores']} abstentions with screen "
                       f"scores): {_codes(gap['codes'])}; gold was the top-scored option in {gap['gold_is_top_option']}, "
                       f"among the options in {gap['gold_among_options']}")
    out += [f"- {x}" for x in summary.get("comparison") or []]
    return out


def screen_lines(sc: dict) -> list[str]:
    out = ["", f"Listing screen calibration ({sc['run']}, one {sc['model']} call per case, code {str(sc['code_sha'])[:12]}"
               f"{' + uncommitted changes' if sc.get('tree_dirty') else ''}):",
           f"cases {sc['cases']}, calls {sc['calls']}, failed {sc['error_cases']}, listing judgments {sc['judgments']} "
           f"({sc['positives']} positive / {sc['negatives']} negative; {sc['not_scored']} listings over the cap not scored), "
           f"median {sc['median_ms_per_call']} ms per call",
           f"ROC AUC {sc['roc_auc']}, Brier {sc['brier']}, top-scored listing is the gold product in "
           f"{sc['top1_positive_cases']}/{sc['cases_with_positive']} cases that had a positive "
           f"({sc['cases_without_positive']} had none)", "",
           "threshold  accepted  precision  recall  false-accept rate"]
    for t in sc["thresholds"]:
        out.append(f"p >= {t['threshold']:<4}  {t['tp'] + t['fp']:<8}  {_num(t['precision']):<9}  {_num(t['recall']):<6}  "
                   f"{_num(t['false_accept_rate'])} ({t['fp']}/{t['fp'] + t['tn']})")
    out += ["", "reliability bin  count  mean predicted  observed positive rate"]
    for b in sc["reliability"]:
        out.append(f"{b['bin']:<15}  {b['count']:<5}  {_num(b['mean_predicted']):<14}  {_num(b['observed_positive_rate'])}")
    return out


def lines(freeze: dict, cases: dict, summary: dict, markdown: bool = False, diagnostic: dict | None = None) -> list[str]:
    n = summary["n"]
    out = [f"End-to-end blind benchmark, run {cases['run']}: {n} cases drawn with seed {freeze['seed'][:12]} "
           f"(frozen at {freeze['created_at']}), arms run on code {summary['code_sha'][:12]}"
           f"{' + uncommitted changes' if summary.get('tree_dirty') else ''}, listing check version "
           f"{summary.get('pair_version') or 'not recorded'}, generated at {summary['generated_at']}.", ""]
    head = ["arm"] + [label for _, label in COLUMNS]
    rows = []
    for key, label in ARMS:
        a = summary["arms"][key]
        cells = [label]
        for field, _ in COLUMNS:
            v = a[field]
            if field == "median_ms":
                cells.append("-" if v is None else str(v))
            elif field == "invalid_offers":
                cells.append(f"{v}/{a['answered']} answered")
            else:
                cells.append(f"{v}/{n}")
        rows.append(cells)
    if markdown:
        out.append("| " + " | ".join(head) + " |")
        out.append("|" + "|".join("---" for _ in head) + "|")
        out += ["| " + " | ".join(r) + " |" for r in rows]
    else:
        widths = [max(len(x[i]) for x in [head] + rows) for i in range(len(head))]
        for r in [head] + rows:
            out.append("  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip())
    out += ["", f"Gold product had a valid reference offer in {summary['reference_offer_cases']}/{n} cases; "
                f"it was among the baseline's candidate rows in {summary['gold_in_baseline_candidates']}/{n}.",
            "Paired outcomes: " + _codes(summary["paired"]), "", "Where each arm did not answer correctly (reason codes):"]
    for key, label in ARMS:
        a = summary["arms"][key]
        out.append(f"- {label}: {_codes(a['reason_codes'])}")
        out.append(f"  invalid offers: {_codes(a['invalid_reasons'])}; storefront re-check of answered offers: "
                   f"{_codes(a['storefront_checks'])}; cheapest-valid codes: {_codes(a['cheapest_codes'])}")
    out += ["", f"Run {cases['run']} detail. Cases not answered correctly by the full system:"]
    missed = [c for c in cases["cases"] if c["arms"]["system"]["outcome"] != "correct"]
    for c in missed:
        s, b = c["arms"]["system"], c["arms"]["baseline"]
        out.append(f"- {c['case_id']} ({c['query']}, {c['sellers']} sellers): system {s['outcome']} "
                   f"[{', '.join(s['reasons'])}]; baseline {b['outcome']}")
    if not missed:
        out.append("- none")
    if diagnostic:
        d = diagnostic
        out += ["", "Post-hoc failure analysis of run v1 (e2e-diagnostic-v1.json; changes no score):",
                f"- full-system abstentions by cause: {_codes(d['system_abstention_codes'])}",
                f"- full-system false accepts where the gold product's own catalog titles carry the model number the "
                f"system bound on (same item under two catalog product ids): "
                f"{d['system_false_accepts_same_model_number_as_gold']}/{d['system_false_accepts']}"]
    out += runs_table(summary)
    screens = summary.get("screens") or ({"a": summary["screen"]} if summary.get("screen") else {})
    for key in ("a", "b"):
        if key in screens:
            out += screen_lines(screens[key])
    if screens:
        out += [f"- {note}" for note in next(iter(screens.values()))["notes"]]
    if summary.get("reading"):
        out += ["", "Reading of run v1 (generated from the counts):"] + [f"- {x}" for x in summary["reading"]]
    out += ["", "Notes:"] + [f"- {note}" for note in summary["notes"]]
    return out


def main(argv: list[str]) -> int:
    freeze, cases, summary = load()
    path = RESULTS / "e2e-diagnostic-v1.json"
    diagnostic = json.loads(path.read_text()) if path.exists() else None
    print("\n".join(lines(freeze, cases, summary, markdown="--markdown" in argv, diagnostic=diagnostic)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
