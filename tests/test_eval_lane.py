"""Offline tests for the proof lane. Synthetic result files live only in temp dirs; nothing touches eval/results/."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "eval"))
import app  # noqa: E402,F401
import summarize  # noqa: E402
import report  # noqa: E402
import run_pairs  # noqa: E402
from bench import digest, read  # noqa: E402


def _case(i, arm, verdict, gold, cat="cameras", err=None, ms=1000):
    return {"id": f"pair-{i:02d}", "wdc_pair_id": f"{i}#x", "category": cat, "arm": arm, "verdict": verdict, "gold": gold,
            "correct": verdict == gold, "abstained": verdict == "abstain", "error": err, "ms": ms, "rate_limit_retries": 0,
            "receipts": [{"job": "j", "model": "gemini-4-carbon", "id": "t", "ms": ms}]}


def _doc(tmp, cases, version="v1"):
    doc = {"experiment": "A", "name": f"pairs-{version}", "version": version, "code_sha": "a" * 40,
           "freeze_code_sha": "a" * 40, "inputs_sha256": "0" * 64, "started_at": 1, "finished_at": 2, "cases": cases}
    (tmp / f"pairs-{version}.json").write_text(json.dumps(doc))


CASES = [_case(1, "baseline", "match", "different"), _case(1, "full", "abstain", "different"),
         _case(2, "baseline", "match", "match", cat="computers"), _case(2, "full", "match", "match", cat="computers"),
         _case(3, "baseline", None, "different", err="timeout"), _case(3, "full", "different", "different")]


def test_summary_counts_every_case(tmp_path):
    _doc(tmp_path, CASES)
    s = summarize.build(tmp_path)
    b, f = s["arms"]["baseline"], s["arms"]["full"]
    assert (b["n"], b["correct"], b["wrong"], b["abstained"], b["errors"], b["false_accepts"]) == (3, 1, 1, 0, 1, 1)
    assert (f["n"], f["correct"], f["wrong"], f["abstained"], f["errors"], f["false_accepts"]) == (3, 2, 0, 1, 0, 0)
    assert b["correct"] + b["wrong"] + b["abstained"] + b["errors"] == b["n"]
    assert s["paired"] == {"both": 1, "baseline_only": 0, "full_only": 1, "neither": 1}
    assert {x["stratum"] for x in s["by_stratum"]} == {"cameras/different", "computers/match"}
    assert s["model_ids"] == ["gemini-4-carbon"]


def test_report_numbers_come_from_files(tmp_path):
    _doc(tmp_path, CASES)
    md = report.render(summarize.build(tmp_path))
    assert "| 1/3 |" in md and "| 2/3 |" in md and "pairs-v1.json" in md


def test_v1_and_v2_side_by_side(tmp_path):
    _doc(tmp_path, CASES, "v1")
    _doc(tmp_path, CASES, "v2")
    s = summarize.build(tmp_path)
    assert [v["version"] for v in s["versions"]] == ["v1", "v2"] and s["latest_version"] == "v2"


def test_router_404_then_data(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import app.evidence.routes as routes
    monkeypatch.setattr(routes, "RESULTS", tmp_path)
    monkeypatch.setattr(routes, "load", lambda results=tmp_path, _l=routes.load: _l(results))
    api = FastAPI()
    api.include_router(routes.router)
    client = TestClient(api)
    r = client.get("/api/proof/eval")
    assert r.status_code == 404 and r.json() == {"error": {"code": "no_eval", "message": "No evaluation run recorded yet"}}
    _doc(tmp_path, CASES)
    summarize.main(tmp_path)
    r = client.get("/api/proof/eval")
    assert r.status_code == 200
    body = r.json()
    assert "pairs-v1.json" in body["generated_from"] and len(body["cases"]) == 6
    assert body["summary"]["arms"]["full"]["correct"] == 2


def test_error_classes():
    class E(Exception):
        def __init__(self, m):
            self.safe_message = m
    assert run_pairs.error_class(E("ClientError 429: RESOURCE_EXHAUSTED")) == "rate_limited"
    assert run_pairs.error_class(E("ReadTimeout: timed out")) == "timeout"
    assert run_pairs.error_class(E("ValidationError: bad json")) == "invalid_output"


def test_failed_call_is_recorded_not_dropped(monkeypatch):
    def boom(packet):
        raise RuntimeError("server error")
    monkeypatch.setitem(run_pairs.ARMS, "full", boom)
    row = {"id": "pair-01", "task": "pair", "packet": {"left": {"title": "a"}, "right": {"title": "b"}}}
    rec = run_pairs.run_one(row, "full")
    assert rec["answer"] is None and rec["error"] == "RuntimeError" and rec["sha"] == digest(row)


def test_frozen_draw_matches_inputs_if_present():
    freeze = ROOT / "eval" / "results" / "freeze.json"
    inputs = ROOT / "private" / "wdc" / "bench" / "in" / "pairs.jsonl"
    if not (freeze.exists() and inputs.exists()):
        pytest.skip("no frozen draw on this machine")
    f = json.loads(freeze.read_text())
    rows = read(inputs)
    assert len(rows) == f["n"] == 40
    assert [digest(r) for r in rows] == [c["packet_sha"] for c in f["cases"]]
    # no gold, id, cluster or label fields reach a model input
    for r in rows:
        for side in ("left", "right"):
            assert set(r["packet"][side]) <= {"title", "description", "brand", "specTableContent"}


def test_v2_in_sample_reuses_v1_baseline_and_heldout_is_labelled(tmp_path):
    _doc(tmp_path, CASES, "v1")
    v2 = {"experiment": "A", "name": "pairs-v2", "version": "v2", "draw": "A", "arms_run": ["full"], "code_sha": "b" * 40,
          "cases": [c for c in CASES if c["arm"] == "full"]}
    (tmp_path / "pairs-v2.json").write_text(json.dumps(v2))
    b = {"experiment": "A", "name": "pairs-b-v2", "version": "v2", "draw": "B", "arms_run": ["baseline", "full"],
         "code_sha": "c" * 40, "cases": [dict(c, id="b-" + c["id"]) for c in CASES]}
    (tmp_path / "pairs-b-v2.json").write_text(json.dumps(b))
    (tmp_path / "e2e-summary.json").write_text(json.dumps({"n": 1}))
    s = summarize.build(tmp_path)
    names = [v["name"] for v in s["versions"]]
    assert names == ["pairs-v1", "pairs-v2", "pairs-b-v2"]
    v2s = s["versions"][1]
    assert v2s["in_sample"] and not v2s["held_out"] and v2s["baseline_source"].startswith("pairs-v1.json")
    assert v2s["arms"]["baseline"]["correct"] == 1 and v2s["paired"] is not None
    assert s["headline_run"] == "pairs-b-v2" and s["versions"][2]["held_out"]
    assert s["e2e"] == {"n": 1} and "e2e-summary.json" in s["generated_from"]
    assert len(s["reading"]) == 3


def test_heldout_draw_disjoint_if_present():
    fa, fb = ROOT / "eval" / "results" / "freeze.json", ROOT / "eval" / "results" / "freeze-b.json"
    if not (fa.exists() and fb.exists()):
        pytest.skip("no draw B on this machine")
    a = {c["wdc_pair_id"] for c in json.loads(fa.read_text())["cases"]}
    b = json.loads(fb.read_text())
    assert b["n"] == 40 and not (a & {c["wdc_pair_id"] for c in b["cases"]})


def test_rate_limit_sleep_excluded_from_ms(monkeypatch):
    calls = {"n": 0}

    class RL(Exception):
        safe_message = "ClientError 429 RESOURCE_EXHAUSTED"

    def flaky(packet):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RL()
        return "match", []
    monkeypatch.setitem(run_pairs.ARMS, "full", flaky)
    monkeypatch.setattr(run_pairs.time, "sleep", lambda s: None)
    rec = run_pairs.run_one({"id": "pair-01", "task": "pair", "packet": {"left": {"title": "a"}, "right": {"title": "b"}}}, "full")
    assert rec["answer"] == "match" and rec["rate_limit_retries"] == 1 and rec["ms"] <= rec["wall_ms"]
