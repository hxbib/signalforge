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
    out += ["", "Cases not answered correctly by the full system:"]
    missed = [c for c in cases["cases"] if c["arms"]["system"]["outcome"] != "correct"]
    for c in missed:
        s, b = c["arms"]["system"], c["arms"]["baseline"]
        out.append(f"- {c['case_id']} ({c['query']}, {c['sellers']} sellers): system {s['outcome']} "
                   f"[{', '.join(s['reasons'])}]; baseline {b['outcome']}")
    if not missed:
        out.append("- none")
    if diagnostic:
        d = diagnostic
        out += ["", "Post-hoc failure analysis (e2e-diagnostic-v1.json; changes no score):",
                f"- full-system abstentions by cause: {_codes(d['system_abstention_codes'])}",
                f"- full-system false accepts where the gold product's own catalog titles carry the model number the "
                f"system bound on (same item under two catalog product ids): "
                f"{d['system_false_accepts_same_model_number_as_gold']}/{d['system_false_accepts']}"]
    sc = summary.get("screen")
    if sc:
        out += ["", f"Listing screen calibration ({sc['run']}, one {sc['model']} call per case, code {str(sc['code_sha'])[:12]}"
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
        out += [f"- {note}" for note in sc["notes"]]
    if summary.get("reading"):
        out += ["", "Reading (generated from the counts):"] + [f"- {x}" for x in summary["reading"]]
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
