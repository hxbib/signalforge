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


def summarize_pairs(doc):
    cases = doc["cases"]
    arms = {a: arm_counts([c for c in cases if c["arm"] == a]) for a in ARMS}
    strata = defaultdict(lambda: {a: [] for a in ARMS})
    for c in cases:
        strata[f'{c["category"]}/{c["gold"]}'][c["arm"]].append(c)
    by_stratum = [{"stratum": k, **{a: {"n": len(v[a]), "correct": sum(x["correct"] for x in v[a]),
                                        "abstained": sum(x["verdict"] == "abstain" for x in v[a]),
                                        "errors": sum(bool(x["error"]) for x in v[a]),
                                        "false_accepts": sum(x["gold"] == "different" and x["verdict"] == "match" for x in v[a])}
                                    for a in ARMS}} for k, v in sorted(strata.items())]
    ok = {(c["id"], c["arm"]): c["correct"] for c in cases}
    paired = {"both": 0, "baseline_only": 0, "full_only": 0, "neither": 0}
    for i in sorted({c["id"] for c in cases}):
        b, f = ok[(i, "baseline")], ok[(i, "full")]
        paired["both" if b and f else "baseline_only" if b else "full_only" if f else "neither"] += 1
    models = sorted({r["model"] for c in cases for r in c.get("receipts", []) if r.get("model")})
    return {"name": doc["name"], "version": doc["version"], "code_sha": doc["code_sha"],
            "freeze_code_sha": doc.get("freeze_code_sha"), "started_at": doc.get("started_at"),
            "finished_at": doc.get("finished_at"), "n": len({c["id"] for c in cases}), "model_ids": models,
            "arms": arms, "paired": paired, "by_stratum": by_stratum}


def build(results=RESULTS):
    results = Path(results)
    files = sorted(p for p in results.glob("pairs-*.json"))
    versions = [summarize_pairs(json.loads(p.read_text())) for p in files]
    if not versions:
        return None
    latest = versions[-1]
    gap = results / "baskets-gap.json"
    summary = {"generated_at": int(time.time()), "generated_from": [p.name for p in files] + ([gap.name] if gap.exists() else []),
               "experiment": "A: WDC v2 product-pair identity (independent public gold)",
               "code_sha": latest["code_sha"], "model_ids": latest["model_ids"], "n": latest["n"],
               "arms": latest["arms"], "paired": latest["paired"], "by_stratum": latest["by_stratum"],
               "latest_version": latest["version"], "versions": versions,
               "experiment_b": json.loads(gap.read_text()) if gap.exists() else None}
    freeze = results / "freeze.json"
    if freeze.exists():
        f = json.loads(freeze.read_text())
        summary["freeze"] = {k: f[k] for k in ("frozen_at", "frozen_at_iso", "code_sha", "seed", "corpus", "corpus_sha256",
                                               "inputs_sha256", "gold_sha256", "n")}
    return summary


def main(results=RESULTS):
    summary = build(results)
    if summary is None:
        raise SystemExit("no pairs-*.json results in " + str(results))
    (Path(results) / "summary.json").write_text(json.dumps(summary, indent=1))
    print("wrote", Path(results) / "summary.json")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else RESULTS)
