"""Build eval/results/summary.json from the raw per-case result files in eval/results/. No hand-typed numbers."""
import json
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

RESULTS = Path(__file__).resolve().parent / "results"
ARMS = ("baseline", "full")


def _lat(values):
    if not values:
        return None
    s = sorted(values)
    return {"median_ms": round(statistics.median(s)), "p90_ms": s[min(len(s) - 1, int(round(0.9 * (len(s) - 1))))],
            "mean_ms": round(statistics.mean(s)), "max_ms": s[-1]}


def arm_counts(cases):
    n = len(cases)
    errors = sum(1 for c in cases if c["error"] or c["verdict"] is None)
    abst = sum(1 for c in cases if c["verdict"] == "abstain")
    correct = sum(1 for c in cases if c["correct"])
    answered = sum(1 for c in cases if c["verdict"] in ("match", "different"))
    return {"n": n, "correct": correct, "wrong": answered - correct, "abstained": abst, "errors": errors,
            "answered": answered,
            "false_accepts": sum(1 for c in cases if c["gold"] == "different" and c["verdict"] == "match"),
            "actual_negatives": sum(1 for c in cases if c["gold"] == "different"),
            "false_rejects": sum(1 for c in cases if c["gold"] == "match" and c["verdict"] == "different"),
            "actual_positives": sum(1 for c in cases if c["gold"] == "match"),
            "precision_on_answered": round(correct / answered, 4) if answered else None,
            "latency": _lat([c["ms"] for c in cases if not c["error"]]),
            "rate_limit_retries": sum(c.get("rate_limit_retries", 0) for c in cases)}


def _paired(cases_b, cases_f):
    ok_b = {c["id"]: c["correct"] for c in cases_b}
    ok_f = {c["id"]: c["correct"] for c in cases_f}
    paired = {"both": 0, "baseline_only": 0, "full_only": 0, "neither": 0}
    for i in sorted(ok_f):
        b, f = ok_b[i], ok_f[i]
        paired["both" if b and f else "baseline_only" if b else "full_only" if f else "neither"] += 1
    return paired


def _strata(cases):
    strata = defaultdict(lambda: {a: [] for a in ARMS})
    for c in cases:
        strata[f'{c["category"]}/{c["gold"]}'][c["arm"]].append(c)
    return [{"stratum": k, **{a: {"n": len(v[a]), "correct": sum(x["correct"] for x in v[a]),
                                  "abstained": sum(x["verdict"] == "abstain" for x in v[a]),
                                  "errors": sum(bool(x["error"]) for x in v[a]),
                                  "false_accepts": sum(x["gold"] == "different" and x["verdict"] == "match" for x in v[a])}
                              for a in ARMS}} for k, v in sorted(strata.items())]


def _reading(label, arms):
    b, f = arms["baseline"], arms["full"]
    rel = "equals" if f["correct"] == b["correct"] else "trails" if f["correct"] < b["correct"] else "beats"
    return (f'{label}: the system listing check {rel} a single Carbon call on correctness '
            f'({f["correct"]}/{f["n"]} vs {b["correct"]}/{b["n"]}); false accepts {f["false_accepts"]}/{f["actual_negatives"]} vs '
            f'{b["false_accepts"]}/{b["actual_negatives"]}; abstentions {f["abstained"]} vs {b["abstained"]}.')


def summarize_pairs(doc, baseline_fallback=None):
    cases = list(doc["cases"])
    run_arms = doc.get("arms_run") or sorted({c["arm"] for c in cases})
    baseline_source = doc["name"] + ".json"
    if "baseline" not in run_arms and baseline_fallback is not None:
        cases += [c for c in baseline_fallback["cases"] if c["arm"] == "baseline"]
        baseline_source = baseline_fallback["name"] + ".json (not re-run)"
    arms = {a: arm_counts([c for c in cases if c["arm"] == a]) for a in ARMS if any(c["arm"] == a for c in cases)}
    paired = _paired([c for c in cases if c["arm"] == "baseline"], [c for c in cases if c["arm"] == "full"]) \
        if len(arms) == 2 else None
    models = sorted({r["model"] for c in doc["cases"] for r in c.get("receipts", []) if r.get("model")})
    label = doc.get("label") or f'{doc["version"]} · draw {doc.get("draw", "A")}'
    return {"name": doc["name"], "label": label, "version": doc["version"], "draw": doc.get("draw", "A"),
            "in_sample": doc.get("draw", "A") == "A" and doc["version"] != "v1",
            "held_out": doc.get("draw", "A") == "B",
            "system_arm": doc.get("system_arm", "app.models.pair v1-neon-carbon-agreement"),
            "arms_run": run_arms, "baseline_source": baseline_source,
            "code_sha": doc["code_sha"], "freeze_code_sha": doc.get("freeze_code_sha"),
            "started_at": doc.get("started_at"), "finished_at": doc.get("finished_at"),
            "n": len({c["id"] for c in doc["cases"]}), "model_ids": models,
            "arms": arms, "paired": paired, "by_stratum": _strata(cases),
            "reading": _reading(label, arms) if len(arms) == 2 else None}


def _order(doc):
    return (doc.get("draw", "A"), doc["version"])


def build(results=RESULTS):
    results = Path(results)
    docs = sorted((json.loads(p.read_text()) for p in results.glob("pairs-*.json")), key=_order)
    if not docs:
        return None
    v1a = next((d for d in docs if d.get("draw", "A") == "A" and d["version"] == "v1"), None)
    versions = [summarize_pairs(d, v1a if d.get("draw", "A") == "A" else None) for d in docs]
    headline = next((v for v in versions if v["held_out"]), versions[0])
    gap, e2e = results / "baskets-gap.json", results / "e2e-summary.json"
    extra = [p for p in (gap, e2e) if p.exists()]
    summary = {"generated_at": int(time.time()),
               "generated_from": [d["name"] + ".json" for d in docs] + [p.name for p in extra],
               "experiment": "WDC v2 product-pair identity (independent public gold): is this listing the exact same product?",
               "headline_run": headline["name"], "code_sha": versions[-1]["code_sha"],
               "model_ids": sorted({m for v in versions for m in v["model_ids"]}),
               "n": headline["n"], "arms": headline["arms"], "paired": headline["paired"], "by_stratum": headline["by_stratum"],
               "latest_version": versions[-1]["version"], "versions": versions,
               "reading": [v["reading"] for v in versions if v["reading"]],
               "latency_note": "ms = the final successful attempt only; HTTP 429 back-off sleeps are excluded (bug 007), wall_ms kept per case",
               "experiment_b": json.loads(gap.read_text()) if gap.exists() else None,
               "e2e": json.loads(e2e.read_text()) if e2e.exists() else None}
    for key, fname in (("freeze", "freeze.json"), ("freeze_b", "freeze-b.json")):
        f = results / fname
        if f.exists():
            f = json.loads(f.read_text())
            summary[key] = {k: f.get(k) for k in ("frozen_at", "frozen_at_iso", "code_sha", "seed", "seed_rule", "corpus",
                                                  "corpus_sha256", "inputs_sha256", "gold_sha256", "n", "disjoint_from")}
    return summary


def main(results=RESULTS):
    summary = build(results)
    if summary is None:
        raise SystemExit("no pairs-*.json results in " + str(results))
    (Path(results) / "summary.json").write_text(json.dumps(summary, indent=1))
    print("wrote", Path(results) / "summary.json")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else RESULTS)
