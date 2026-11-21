"""Run both arms of Experiment A on the frozen WDC packets, then score against the private gold.

  .venv/bin/python eval/run_pairs.py v1

baseline = ONE Gemini 4 Carbon call (reference deal_models.pair_verdict(packet, False): PAIR prompt, Pair schema).
system   = app.models.pair — the production two-model listing check used for identity tier "model_number":
           Gemini 4 Neon extraction + Gemini 4 Carbon verdict, agreement + verbatim-quote verification, else abstain.
Both arms go through app.models.io (the verified SDK call shape). Models only ever see the packet (title,
description, brand, spec table); gold is read only after every prediction is written. At most 2 model calls
in flight; an HTTP 429 (no answer produced) waits 30 s and is retried at most twice and counted. Every packet
stays in the denominator: an error is recorded, never dropped or replaced.
"""
import json
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))
import app  # noqa: E402,F401  (reference/ on sys.path)
from bench import digest, put, read, score  # noqa: E402

BENCH = ROOT / "private" / "wdc" / "bench"
RESULTS = ROOT / "eval" / "results"
MAX_CALLS = 2
_print = threading.Lock()


def error_class(exc):
    text = (getattr(exc, "safe_message", "") or str(exc)).lower()
    if "429" in text or "resource_exhausted" in text or "rate" in text and "limit" in text:
        return "rate_limited"
    if "timeout" in text or "timed out" in text or "deadline" in text:
        return "timeout"
    if "validat" in text or "json" in text:
        return "invalid_output"
    if "api_key" in text or "not configured" in text:
        return "setup"
    return type(exc).__name__


def baseline_arm(packet):
    from deal_models import PAIR, Pair
    from gemini_io import CARBON
    from app.models.io import structured, text_part
    value, receipt = structured("pair_baseline", CARBON, PAIR, Pair, text_part({"left": packet["left"], "right": packet["right"]}))
    return value.verdict, [receipt]


def system_arm(packet):
    from app.models import pair
    out = pair(packet["left"], packet["right"])
    return out["verdict"], out["receipts"]


ARMS = {"baseline": baseline_arm, "full": system_arm}


def run_one(row, arm):
    start = time.monotonic()
    retries, answer, receipts, err = 0, None, [], None
    while True:
        try:
            answer, receipts = ARMS[arm](row["packet"])
            err = None
            break
        except Exception as exc:  # recorded, never swallowed
            err = error_class(exc)
            if err == "rate_limited" and retries < 2:
                retries += 1
                time.sleep(30)
                continue
            answer = None
            break
    rec = {"id": row["id"], "sha": digest(row), "answer": answer, "error": err,
           "ms": round((time.monotonic() - start) * 1000), "rate_limit_retries": retries,
           "receipts": [{k: r.get(k) for k in ("job", "model", "id", "ms")} for r in receipts]}
    with _print:
        print(f"{row['id']} {arm:8s} {answer or '-':9s} err={err} {rec['ms']}ms", flush=True)
    return rec


def main(version):
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    freeze = json.loads((RESULTS / "freeze.json").read_text())
    rows = read(BENCH / "in" / "pairs.jsonl")
    assert [r["id"] for r in rows] == [c["id"] for c in freeze["cases"]], "inputs differ from the freeze record"
    assert all(digest(r) == c["packet_sha"] for r, c in zip(rows, freeze["cases"])), "packet digest mismatch"
    out_json = RESULTS / f"pairs-{version}.json"
    if out_json.exists():
        raise SystemExit(f"{out_json} exists; results are never overwritten")
    from app.models.io import clients
    clients()  # preflight: no key configured -> raise before any case runs, nothing written
    started = int(time.time())
    jobs = [(r, arm) for r in rows for arm in ("baseline", "full")]
    with ThreadPoolExecutor(max_workers=MAX_CALLS) as pool:
        preds = list(pool.map(lambda j: run_one(*j), jobs))
    finished = int(time.time())
    by = {(p["id"], arm): p for p, (_, arm) in zip(preds, jobs)}
    runs = BENCH / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    base_rows = [by[(r["id"], "baseline")] for r in rows]
    full_rows = [by[(r["id"], "full")] for r in rows]
    put(runs / f"pair-base-{version}.jsonl", base_rows)
    put(runs / f"pair-full-{version}.jsonl", full_rows)
    # --- gold enters only here, after every prediction is on disk ---
    gold = read(BENCH / "gold" / "pairs.jsonl")
    bench_score = score(rows, gold, base_rows, full_rows)
    labels = {g["id"]: g["expected"] for g in gold}
    strata = {c["id"]: c for c in freeze["cases"]}
    cases = []
    for r in rows:
        for arm in ("baseline", "full"):
            p = by[(r["id"], arm)]
            cases.append({"id": r["id"], "wdc_pair_id": strata[r["id"]]["wdc_pair_id"],
                          "category": strata[r["id"]]["category"], "arm": arm, "verdict": p["answer"],
                          "gold": labels[r["id"]], "correct": p["answer"] == labels[r["id"]],
                          "abstained": p["answer"] == "abstain", "error": p["error"], "ms": p["ms"],
                          "rate_limit_retries": p["rate_limit_retries"], "receipts": p["receipts"]})
    doc = {"experiment": "A", "name": f"pairs-{version}", "version": version, "code_sha": sha,
           "freeze_code_sha": freeze["code_sha"], "inputs_sha256": freeze["inputs_sha256"],
           "started_at": started, "finished_at": finished, "bench_score": bench_score, "cases": cases}
    with out_json.open("x", encoding="utf8") as f:
        json.dump(doc, f, indent=1)
    print(json.dumps(bench_score, indent=1))
    subprocess.run([sys.executable, str(ROOT / "eval" / "summarize.py")], check=True)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "v1")
