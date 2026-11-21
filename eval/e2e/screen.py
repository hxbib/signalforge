"""Screen calibration on the frozen draw: is Gemini Decision 1's p_same for a listing a usable probability?

For every frozen case: the candidate rows of the draw query (catalog search, at most 60), ONE models.screen call
with the case photo and a target built from the photo alone (what Gemini 4 Neon read during the system-arm run),
and a label per listing taken from the draw itself: positive iff the row belongs to the gold catalog product.
Gold never enters the call. Run once; a case is never repeated (an interrupted or failed call is an error row).

  .venv/bin/python eval/e2e/screen.py            # draw A
  .venv/bin/python eval/e2e/screen.py --draw b   # held-out draw B (after `run.py --run b`)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("SF_STORE_INTERACTIONS", "delete")

import app.models as models  # noqa: E402
from app.merchants import shop  # noqa: E402
from live_smoke import clean_image  # noqa: E402

import e2e_calibration as calibration  # noqa: E402
import e2e_summary  # noqa: E402

RESULTS = ROOT / "eval" / "results"
PRIVATE = ROOT / "private" / "e2e"
OUT = RESULTS / "e2e-screen.json"
FREEZE = RESULTS / "e2e-freeze.json"
PHOTOS = PRIVATE
ARM_JOURNAL = PRIVATE / "journal-v1.jsonl"
JOURNAL = PRIVATE / "journal-screen.jsonl"
NAME = "screen-v1"


def select_b() -> None:
    """Held-out draw B: its own freeze, photos, system-arm journal (for the photo-only target) and output."""
    global OUT, FREEZE, PHOTOS, ARM_JOURNAL, JOURNAL, NAME
    OUT, FREEZE, PHOTOS = RESULTS / "e2e-screen-b.json", RESULTS / "e2e-freeze-b.json", PRIVATE / "b"
    ARM_JOURNAL, JOURNAL, NAME = PRIVATE / "journal-b.jsonl", PRIVATE / "journal-screen-b.jsonl", "screen-b"
CAP = 60
_lock = threading.Lock()


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def _log(event: dict) -> None:
    with _lock, JOURNAL.open("a") as f:
        f.write(json.dumps(event) + "\n")


def photo_targets() -> dict[str, dict]:
    """What Neon read from each photo during the system-arm run: photo-derived fields only (no catalog id or title)."""
    out = {}
    if ARM_JOURNAL.exists():
        for line in ARM_JOURNAL.read_text().splitlines():
            ev = json.loads(line)
            if ev.get("event") == "detail":
                target = (ev.get("system") or {}).get("target") or {}
                out[ev["case_id"]] = {"name": target.get("name") or "", "brand": target.get("brand") or "",
                                      "mpn": target.get("mpn") or "",
                                      "variant_details": list(target.get("variant_details") or [])}
    return out


def error_case(case: dict, code: str, called: bool, wall_ms: int | None = None, listings: int = 0) -> dict:
    return {"case_id": case["case_id"], "query": case["query"], "gold_upid": case["gold_upid"], "status": "error",
            "error_code": code, "called": called, "ms": None, "wall_ms": wall_ms, "listings": listings, "scored": 0,
            "not_scored": 0, "positives": 0, "judgments": []}


def run_case(case: dict, target: dict) -> dict:
    image = clean_image((PHOTOS / f"{case['case_id']}.img").read_bytes())
    t = time.monotonic()
    try:
        doc, rec = shop.search(case["query"], limit=10)
        found = shop.rows_from(doc, rec["read_at"])
    except Exception as e:
        return error_case(case, f"catalog:{type(e).__name__}", called=False)
    rows = found[:CAP]
    try:
        out = models.screen(image, target, rows)
    except Exception as e:
        return error_case(case, f"screen:{type(e).__name__}", called=True,
                          wall_ms=round((time.monotonic() - t) * 1000), listings=len(found))
    judgments = [{"index": s["index"], "listing": rows[s["index"]].get("listing"), "p_same": s["p_same"],
                  "confidence": s["confidence"], "positive": rows[s["index"]].get("product") == case["gold_upid"]}
                 for s in out.get("scores") or []]
    receipt = out.get("receipt") or {}
    return {"case_id": case["case_id"], "query": case["query"], "gold_upid": case["gold_upid"],
            "status": "ok" if receipt else "error", "error_code": None if receipt else "no_call_made",
            "called": bool(receipt), "ms": receipt.get("ms"), "wall_ms": round((time.monotonic() - t) * 1000),
            "listings": len(found), "scored": len(judgments), "not_scored": len(found) - len(judgments),
            "positives": sum(j["positive"] for j in judgments), "target_from_photo": any(bool(v) for v in target.values()),
            "judgments": judgments}


def interim(records: list[dict]) -> str:
    m = calibration.metrics(records)
    at = {t["threshold"]: t for t in m["thresholds"]}
    return (f"screen after {m['cases']} cases: {m['judgments']} judgments ({m['positives']} positive), errors {m['error_cases']}, "
            f"AUC {m['roc_auc']}, p>=0.5 precision {at[0.5]['precision']} recall {at[0.5]['recall']}, "
            f"p>=0.9 precision {at[0.9]['precision']} recall {at[0.9]['recall']} false-accept rate {at[0.9]['false_accept_rate']}, "
            f"median {m['median_ms_per_call']} ms")


def write(freeze: dict, records: list[dict], head: dict, complete: bool) -> None:
    order = {c["case_id"]: i for i, c in enumerate(freeze["cases"])}
    records = sorted(records, key=lambda r: order[r["case_id"]])
    OUT.write_text(json.dumps({**head, "generated_at": int(time.time()), "complete": complete,
                               "metrics": calibration.metrics(records) if complete else None, "cases": records}, indent=1) + "\n")


def main(argv: list[str] | None = None) -> int:
    if "b" in [a.lower() for a in (argv or [])]:
        select_b()
    freeze = json.loads(FREEZE.read_text())
    records, started = {}, set()
    if JOURNAL.exists():
        for line in JOURNAL.read_text().splitlines():
            ev = json.loads(line)
            if ev["event"] == "start":
                started.add(ev["case_id"])
            else:
                records[ev["record"]["case_id"]] = ev["record"]
    for case in freeze["cases"]:
        if case["case_id"] in started and case["case_id"] not in records:
            records[case["case_id"]] = error_case(case, "runner_stopped_mid_case", called=True)
            _log({"event": "done", "record": records[case["case_id"]]})
    head = {"run": NAME, "code_sha": _git("rev-parse", "HEAD"),
            "tree_dirty": bool(_git("status", "--porcelain", "--untracked-files=no")), "freeze_seed": freeze["seed"],
            "model": "gemini-decision-1", "listing_cap": CAP, "started_at": int(time.time()),
            "target_source": "photo only: name, brand, model number and variant details read by Gemini 4 Neon in the system-arm run",
            "label": "positive iff the listing belongs to the drawn (gold) catalog product id"}
    targets = photo_targets()
    todo = [c for c in freeze["cases"] if c["case_id"] not in records]

    def work(case):
        _log({"event": "start", "case_id": case["case_id"]})
        try:
            rec = run_case(case, targets.get(case["case_id"]) or {})
        except Exception as e:
            rec = error_case(case, f"exception:{type(e).__name__}", called=False)
        _log({"event": "done", "record": rec})
        with _lock:
            records[rec["case_id"]] = rec
            print(f"{rec['case_id']} {rec['status']} {rec.get('error_code') or ''} listings {rec['listings']} scored {rec['scored']} "
                  f"positives {rec['positives']} ms {rec['ms']}", flush=True)
            print(interim(list(records.values())), flush=True)
            write(freeze, list(records.values()), head, complete=False)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(work, todo))
    complete = len(records) == len(freeze["cases"])
    write(freeze, list(records.values()), head, complete)
    if complete:
        e2e_summary.main()
    print(f"wrote {OUT.name}: {len(records)} of {len(freeze['cases'])} cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
