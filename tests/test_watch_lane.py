"""WATCH lane tests. Fakes (compile results, runs, feeds) exist only here."""
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app.watch as watch  # noqa: E402
from ledger import Ledger  # noqa: E402
from core import Rule, Seen  # noqa: E402

CAP = 2500
OFFER = {"seller": "gid://seller/test-1", "seller_name": "Test Seller", "listing": "gid://variant/test-1",
         "title": "Test item", "url": "https://seller.test/item", "item_cents": 2999, "currency": "USD",
         "stock": "sold_out", "verified": True, "source": "shop_native"}
RUN = {"run_id": "run_test", "goal": {"watch_words": ""},
       "result": {"winners": [], "near_miss": {"offer": OFFER, "reason": "sold_out"}, "rejected": []}}


def proposal(mode="restock", op="lt", cents=CAP, status="ready", reason="ok", question=""):
    ready = status == "ready"
    return {"proposal": {"reason": reason, "status": status, "candidate": 0 if ready else None,
                         "mode": mode if ready else None, "op": op if ready else None,
                         "cents": cents if ready else None, "cap_evidence": "under $25" if ready else "",
                         "binding_evidence": "this seller" if ready else "", "blockers": [], "question": question},
            "receipt": {"job": "watch_rule", "model": "fake-in-test", "id": "t", "ms": 0}}


@pytest.fixture(autouse=True)
def db(monkeypatch, tmp_path):
    monkeypatch.setenv("SF_DB", str(tmp_path / "w.sqlite"))
    monkeypatch.delenv("SIGNALFORGE_WATCH_FEED", raising=False)
    return tmp_path


def make(command, fake=None, replies=None, run=RUN):
    return watch.create(run, command, replies, compile=lambda img, cmd, cands: fake or proposal(), image=b"jpeg")


def test_explicit_phrase_ready_zero_questions_and_proof():
    w = make("Notify me when this seller restocks under $25")
    assert w["status"] == "preview_only" and w["question"] is None
    assert w["origin"] == "explicit_request" and all(o["origin"] == "explicit_request" for o in w["origins"])
    assert len(w["proof_hash"]) == 64 and w["proof"]["passed"] == w["proof"]["checks"]
    assert w["rule"] == {"seller": OFFER["seller"], "variant": OFFER["listing"], "mode": "restock", "op": "lt", "cents": CAP}
    assert w["scope_sentence"] == "Watching Test Seller for a restock below $25.00"
    assert w["playground"] and all(r["simulation"] is True for r in w["playground"])


def test_no_feed_is_preview_only():
    w = make("Notify me when this seller restocks under $25")
    assert w["status"] == "preview_only"
    assert w["preview_reason"] == watch.PREVIEW_REASON


def test_vague_words_one_question():
    w = make("when it comes back cheap", proposal(status="clarify", reason="missing_budget", question="How cheap?"))
    assert w["status"] == "needs_detail" and w["question"]["text"] == watch.ASK_CAP and w["proof_hash"] is None
    # even if the model invented a cap, the verifier refuses an unstated one
    w = make("when it comes back cheap", run=dict(RUN, run_id="run_other"))
    assert w["status"] == "needs_detail" and w["question"]["kind"] == "text" and w["rule"]["cents"] == CAP


def test_ambiguous_mode_one_witness_question_then_buyer_answer():
    w = make("Notify me when this is under $25")
    assert w["status"] == "needs_detail" and w["question"]["kind"] == "choice"
    assert {o["id"] for o in w["question"]["options"]} == {"yes", "no"}
    w2 = make("Notify me when this is under $25", replies={w["question"]["question_id"]: True})
    assert w2["status"] == "preview_only" and w2["origin"] == "buyer" and w2["proof_hash"]


def test_no_words_asks_what_to_watch():
    w = make("")
    assert w["status"] == "needs_detail" and w["question"]["text"] == "What should I watch for?"


def test_pause_idempotent_and_get():
    w = make("Notify me when this seller restocks under $25")
    p1, p2 = watch.pause(w["watch_id"]), watch.pause(w["watch_id"])
    assert p1["status"] == p2["status"] == "paused" and p1 == p2
    assert watch.get(w["watch_id"])["status"] == "paused"


def test_create_is_idempotent():
    a = make("Notify me when this seller restocks under $25")
    b = make("Notify me when this seller restocks under $25")
    assert a == b


def test_authorized_feed_activates_and_ledger_dedupes(monkeypatch, db):
    feed = db / "feed.json"
    feed.write_text(json.dumps({"feed_id": "dev-feed", "observations": [
        {"seller": OFFER["seller"], "variant": OFFER["listing"], "available": False, "cents": 2400,
         "currency": "USD", "t": 1000, "status": "ok"}]}))
    monkeypatch.setenv("SIGNALFORGE_WATCH_FEED", str(feed))
    w = make("Notify me when this seller restocks under $25")
    assert w["status"] == "active" and w["feed_id"] == "dev-feed" and w["preview_reason"] is None
    led = Ledger(os.environ["SF_DB"])
    rule = Rule(**w["rule"])
    led.ingest(w["watch_id"], "a", Seen(rule.seller, rule.variant, False, 2400, "USD", 1000), {})
    led.ingest(w["watch_id"], "b", Seen(rule.seller, rule.variant, True, 2400, "USD", 1060), {})
    led.close()
    led = Ledger(os.environ["SF_DB"])  # restart
    assert led.ingest(w["watch_id"], "b", Seen(rule.seller, rule.variant, True, 2400, "USD", 1060), {}) == "duplicate"
    assert led.db.execute("SELECT count(*) FROM alert").fetchone()[0] == 1
    led.close()
    assert watch.pause(w["watch_id"])["status"] == "paused"
    led = Ledger(os.environ["SF_DB"])
    with pytest.raises(ValueError):
        led.ingest(w["watch_id"], "c", Seen(rule.seller, rule.variant, False, 2400, "USD", 1120), {})
    led.close()


def test_feed_for_other_variant_stays_preview(monkeypatch, db):
    feed = db / "feed.json"
    feed.write_text(json.dumps({"feed_id": "f", "observations": [
        {"seller": OFFER["seller"], "variant": "other", "available": True, "cents": 1, "currency": "USD", "t": 1}]}))
    monkeypatch.setenv("SIGNALFORGE_WATCH_FEED", str(feed))
    assert make("Notify me when this seller restocks under $25")["status"] == "preview_only"



def wid(mode="restock", op="lt"):
    return {"rule": {"seller": "s", "variant": "v", "mode": mode, "op": op, "cents": CAP}}


def test_strict_vs_inclusive_at_exact_cap():
    tl = [{"t": 0, "available": False, "cents": CAP}, {"t": 60, "available": True, "cents": CAP}]
    assert [r["alerts"] for r in watch.what_if(wid(op="lt"), tl)] == [False, False]
    assert [r["alerts"] for r in watch.what_if(wid(op="lte"), tl)] == [False, True]


def test_restock_vs_becomes_eligible_price_falls_in_stock():
    tl = [{"t": 0, "available": True, "cents": CAP + 500}, {"t": 60, "available": True, "cents": CAP - 1}]
    assert watch.what_if(wid("restock"), tl)[-1]["alerts"] is False
    assert watch.what_if(wid("becomes_eligible"), tl)[-1]["alerts"] is True


def test_simulation_never_fabricates():
    ret = {"t": 60, "available": True, "cents": CAP - 1}
    base = {"t": 0, "available": False, "cents": CAP - 1}
    cases = {
        "baseline": [dict(ret, t=0)],
        "stale": [base, dict(ret, t=300)],
        "variant": [base, dict(ret, variant="other")],
        "cad": [base, dict(ret, currency="CAD")],
        "error": [base, dict(ret, status="source_error")],
        "order": [base, dict(ret, t=0, attempt="late")],
    }
    for name, tl in cases.items():
        rows = watch.what_if(wid(), tl)
        assert not any(r["alerts"] for r in rows), name
        assert all(r["simulation"] is True for r in rows)
    dup = watch.what_if(wid(), [base, ret, ret])
    assert [r["alerts"] for r in dup] == [False, True, False] and dup[-1]["reason"] == "duplicate"


def test_default_playground_expectations():
    w = make("Notify me when this seller restocks under $25")
    got = {r["label"]: r["alerts"] for r in w["playground"]}
    assert got["Price drops below the cap while already in stock"] is False
    assert got["Comes back in stock at exactly the cap"] is False
    assert got["Comes back in stock just under the cap"] is True
    assert got["Restocks above the cap"] is False
    assert got["Feed gap longer than 150 s, then a restock"] is False
    assert got["A different variant comes back in stock"] is False
    assert got["Restock priced in CAD"] is False
    assert got["First reading is already in stock under the cap"] is False
    assert got["The same restock reading is delivered twice"] is True


def test_routes(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import app.store as store
    import app.watch.routes as routes
    runs = {"run_test": json.loads(json.dumps(RUN))}
    monkeypatch.setattr(store, "get_run", lambda rid: json.loads(json.dumps(runs[rid])) if rid in runs else None)
    monkeypatch.setattr(store, "save_run", lambda run: runs.__setitem__(run["run_id"], run))
    monkeypatch.setattr(store, "get_image", lambda rid: b"jpeg")
    monkeypatch.setattr(watch, "_compile", lambda img, cmd, cands: proposal())
    api = FastAPI()
    api.include_router(routes.router)
    c = TestClient(api)
    r = c.post("/api/search/run_test/watch", json={})
    assert r.status_code == 200 and r.json()["question"]["text"] == "What should I watch for?"
    r = c.post("/api/search/run_test/watch", json={"command": "Notify me when this seller restocks under $25"})
    w = r.json()
    assert w["status"] == "preview_only" and runs["run_test"]["watch"]["watch_id"] == w["watch_id"]
    r = c.post(f"/api/watch/{w['watch_id']}/whatif", json={"timeline": [
        {"t": 0, "available": False, "cents": 2400}, {"t": 60, "available": True, "cents": 2400}]})
    assert r.json()["simulation"] is True and [x["alerts"] for x in r.json()["rows"]] == [False, True]
    assert c.post(f"/api/watch/{w['watch_id']}/pause").json()["status"] == "paused"
    assert c.post(f"/api/watch/{w['watch_id']}/pause").json()["status"] == "paused"
    assert c.post("/api/watch/w_nope/pause").status_code == 404
    assert c.post("/api/search/run_nope/watch", json={}).status_code == 404


def test_any_store_never_silently_single_store():
    w = make("Tell me when any store restocks this under $25")
    assert w["status"] == "needs_detail" and w["question"]["text"] == watch.ASK_ONE_STORE and w["rule"] is None


def test_negation_or_unsupported_gets_editable_draft_not_guess():
    w = make("Don't notify me when it restocks under $25")
    assert w["status"] == "needs_detail" and w["question"]["text"] == watch.ASK_RESTATE
    assert w["proof_hash"] is None and w["draft"]["editable"] is True
    w = make("Notify me when it restocks under $25 with free 2-day shipping",
             proposal(status="unsupported", reason="unsupported_condition", question="I can't watch shipping speed."))
    assert w["status"] == "needs_detail" and w["question"]["text"] == "I can't watch shipping speed."
    assert w["draft"]["status"] == "unsupported" and w["proof_hash"] is None
