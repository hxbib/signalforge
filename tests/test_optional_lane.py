"""Optional (hosted sandbox challenge) lane tests. The hosted agent is faked ONLY here."""
import json
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app.optional as optional  # noqa: E402
import app.optional.routes as routes  # noqa: E402
import app.watch as watch  # noqa: E402

OFFER = {"seller": "gid://seller/test-1", "seller_name": "Test Seller", "listing": "gid://variant/test-1",
         "title": "Test item", "url": "https://seller.test/item", "item_cents": 2999, "currency": "USD",
         "stock": "sold_out", "verified": True, "source": "shop_native"}
RUN = {"run_id": "run_test", "goal": {"watch_words": ""},
       "result": {"winners": [], "near_miss": {"offer": OFFER, "reason": "sold_out"}, "rejected": []}}
PROPOSAL = {"proposal": {"reason": "ok", "status": "ready", "candidate": 0, "mode": "restock", "op": "lt", "cents": 2500,
                         "cap_evidence": "under $25", "binding_evidence": "this seller", "blockers": [], "question": ""},
            "receipt": {"job": "watch_rule", "model": "fake-in-test", "id": "t", "ms": 0}}
RESTOCK = [{"t": 0, "available": False, "cents": 2499}, {"t": 60, "available": True, "cents": 2499}]
AT_CAP = [{"t": 0, "available": False, "cents": 2500}, {"t": 60, "available": True, "cents": 2500}]


@pytest.fixture(autouse=True)
def db(monkeypatch, tmp_path):
    monkeypatch.setenv("SF_DB", str(tmp_path / "w.sqlite"))
    monkeypatch.delenv("SIGNALFORGE_WATCH_FEED", raising=False)


@pytest.fixture
def doc():
    w = watch.create(RUN, "Notify me when this seller restocks under $25", compile=lambda *a: PROPOSAL, image=b"jpeg")
    assert w["rule"]["cents"] == 2500
    return w


def fake(report, env="env-test", iid="int-test"):
    text = report if isinstance(report, str) else json.dumps(report)
    return lambda brief: {"environment_id": env, "interaction_id": iid, "output_text": text, "step_types": []}


def report(**kw):
    return {"found": True, "timeline": RESTOCK, "rule_alerts": True, "request_expects_alert": False,
            "explanation": "x", "timelines_tested": 1554, **kw}


def test_brief_is_sanitized(doc):
    text = optional.brief(doc)
    assert "restocks under $25" in text and '"cap_in_us_cents": 2500' in text
    for private in ("gid://seller/test-1", "gid://variant/test-1", "Test Seller", "seller.test", "run_test"):
        assert private not in text


def test_no_counterexample_keeps_agent_reported_count(doc):
    out = optional.challenge(doc, hosted=fake({"found": False, "timeline": [], "timelines_tested": 1554}))
    assert out["status"] == "no_counterexample" and out["attempts"] == 1554 and out["replayed_locally"] is False
    assert "reported by the agent" in out["sentence"] and out["simulation"] is True
    assert out["sandbox"] == "google-hosted" and out["environment_id"] == "env-test" and out["interaction_id"] == "int-test"


def test_counterexample_only_when_replay_reproduces(doc):
    out = optional.challenge(doc, hosted=fake(report()))
    assert out["status"] == "counterexample" and out["replayed_locally"] is True
    assert out["timeline"] == RESTOCK and any(r["alerts"] for r in out["replay_rows"])


def test_claim_rejected_when_replay_disagrees(doc):
    out = optional.challenge(doc, hosted=fake(report(timeline=AT_CAP)))  # lt 2500: exactly the cap never alerts
    assert out["status"] == "no_counterexample" and out["replayed_locally"] is True
    assert "rejected" in out["sentence"] and not any(r["alerts"] for r in out["replay_rows"])


def test_silent_rule_claim_reproduced(doc):
    out = optional.challenge(doc, hosted=fake(report(timeline=AT_CAP, rule_alerts=False, request_expects_alert=True)))
    assert out["status"] == "counterexample" and "stays silent" in out["sentence"]


def test_found_without_disagreement_is_not_a_counterexample(doc):
    out = optional.challenge(doc, hosted=fake(report(request_expects_alert=True)))
    assert out["status"] == "no_counterexample"


@pytest.mark.parametrize("timeline", [None, [], [{"available": "yes", "cents": 1}], RESTOCK * 3,
                                      [{"available": True, "cents": 24.99}]])
def test_unusable_timeline_is_not_accepted(doc, timeline):
    out = optional.challenge(doc, hosted=fake(report(timeline=timeline)))
    assert out["status"] == "no_counterexample" and out["replayed_locally"] is False and out["timeline"] is None


def test_agent_fields_cannot_change_identity_or_time(doc):
    sneaky = [{"t": 0, "available": False, "cents": 2499, "seller": "other", "status": "missing"},
              {"t": 999999, "available": True, "cents": 2499, "currency": "EUR"}]
    out = optional.challenge(doc, hosted=fake(report(timeline=sneaky)))
    assert out["timeline"] == RESTOCK and out["status"] == "counterexample"


@pytest.mark.parametrize("text", ["", "I could not run code", "{not json}", '{"timeline": []}'])
def test_unparseable_answer_is_unavailable(doc, text):
    out = optional.challenge(doc, hosted=fake(text))
    assert out["status"] == "unavailable" and out["environment_id"] == "env-test" and out["attempts"] is None


def test_hosted_failure_is_unavailable_with_safe_reason(doc):
    def down(brief):
        raise optional.Unavailable("The hosted sandbox agent did not answer within 150 s.")
    out = optional.challenge(doc, hosted=down)
    assert out["status"] == "unavailable" and out["sentence"].endswith("150 s.") and out["simulation"] is True


def test_no_rule_is_unavailable_without_a_call():
    out = optional.challenge({"rule": None}, hosted=lambda brief: pytest.fail("must not call"))
    assert out["status"] == "unavailable"


def test_fenced_json_is_parsed():
    assert optional.parse('```json\n{"found": false, "timelines_tested": 7}\n```')["timelines_tested"] == 7


def test_start_marks_running_then_records_on_watch_and_run(doc, monkeypatch):
    from app import store
    runs = {"run_test": {"run_id": "run_test", "watch": dict(doc)}}
    monkeypatch.setattr(store, "get_run", lambda rid: json.loads(json.dumps(runs[rid])) if rid in runs else None)
    monkeypatch.setattr(store, "save_run", lambda run: runs.__setitem__(run["run_id"], run))
    gate, done = threading.Event(), threading.Event()

    def slow(d):
        gate.wait(5)
        try:
            return optional.challenge(d, hosted=fake({"found": False, "timeline": [], "timelines_tested": 3}))
        finally:
            done.set()
    first = routes.start(doc, slow)
    assert first["challenge"]["status"] == "running" and runs["run_test"]["watch"]["challenge"]["status"] == "running"
    assert routes.start(doc, slow) is None  # one at a time
    gate.set()
    assert done.wait(5)
    for _ in range(200):
        if routes._busy.acquire(blocking=False):
            routes._busy.release()
            break
        threading.Event().wait(0.01)
    assert watch.get(doc["watch_id"])["challenge"]["status"] == "no_counterexample"
    assert runs["run_test"]["watch"]["challenge"]["attempts"] == 3
