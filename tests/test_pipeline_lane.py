"""Pipeline tests. The lanes are replaced by fakes that exist only in this file; no network, no model calls."""
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app  # noqa: E402,F401
from app import pipeline, store  # noqa: E402
from app.contracts import new_run, public  # noqa: E402

UPID = "gid://shopify/p/test-1"
ROW = {"seller": "gid://seller/1", "seller_name": "Test Seller", "listing": "gid://variant/1", "product": UPID,
       "title": "Test Brand X100", "url": "https://seller.test/x100", "source": "shop_native", "item_cents": 1234,
       "currency": "USD", "condition": "new", "stock": "in_stock"}
ANCHOR = {"upid": UPID, "title": "Test Brand X100", "brand": "Test Brand", "mpn_tokens": ["X100"], "gtin": None,
          "sellers": 1, "identity_source": "Shop catalog product"}
VISUAL = {"name": "Test item", "brand": "Test Brand", "part_number": "X100", "barcode": "", "variant_details": [],
          "target_count": "one", "focus_box": [], "evidence": ["X100 printed on the box"]}
OFFER = {"seller": ROW["seller"], "seller_name": "Test Seller", "listing": ROW["listing"], "title": ROW["title"],
         "url": ROW["url"], "item_cents": 1234, "currency": "USD", "stock": "in_stock", "verified": True}


class ModelCallError(Exception):
    def __init__(self, job, model, safe_message):
        super().__init__(f"{job} [{model}]: {safe_message}")
        self.job, self.model, self.safe_message = job, model, safe_message


class Fakes:
    """Injected lanes. Each attribute can be changed by a test before the run starts."""

    def __init__(self):
        self.visual = dict(VISUAL)
        self.decision = {"pass": True, "choice": "single", "confidence": 0.93, "threshold": 0.75, "id": None,
                         "model": "fake-gate", "ms": 3}
        self.intent = {"operation": "compare", "market": "US", "basis": "item", "fulfillment": "online_us",
                       "location": "", "watch_words": "", "unsupported": []}
        self.anchors = [dict(ANCHOR)]
        self.rows = [dict(ROW)]
        self.catalog_receipts = [{"source": "shop_catalog", "tool": "search_catalog", "status": "ok", "ms": 5}]
        self.bind_slot = 0
        self.source_status = "ok"
        self.inspect_error = self.goal_error = self.catalog_error = self.compare_error = self.watch_error = None
        self.inspect_gate = None
        self.product_rows = {UPID: [dict(ROW)]}
        self.screen_p = 0.91
        self.printed_p = 0.96
        self.screen_error = None
        self.screen_args = None
        self.product_errors = {}
        self.calls = []
        self.notes = []

    def inspect(self, image, note):
        self.calls.append("inspect")
        self.notes.append(note)
        if self.inspect_gate is not None:
            self.inspect_gate.wait(5)
        if self.inspect_error:
            raise self.inspect_error
        return {"image": image, "visual": dict(self.visual), "decision": dict(self.decision), "recovery": None,
                "receipts": [{"job": "view", "model": "fake-view", "id": "v", "ms": 7},
                             {"job": "gate", "model": "fake-gate", "id": None, "ms": 3}]}

    def parse_goal(self, image, note):
        self.calls.append("goal")
        if self.goal_error:
            raise self.goal_error
        return {"intent": dict(self.intent), "receipt": {"job": "goal", "model": "fake-goal", "id": "g", "ms": 4}}

    def scan(self, image, focus_box=None):
        return None

    def bind(self, image, note, anchors, visual, decision, verified_scan=None):
        self.calls.append("bind")
        receipt = {"job": "bind", "model": "fake-bind", "id": "b", "ms": 6}
        if self.bind_slot is None:
            return {"slot": None, "anchor": None, "basis": None, "question": "which?", "receipt": receipt}
        return {"slot": self.bind_slot, "anchor": anchors[self.bind_slot], "basis": "model_number", "question": "",
                "receipt": receipt}

    def screen(self, image, target, listings, claims=None):
        self.calls.append("screen")
        self.screen_args = (target, listings, claims)
        if self.screen_error:
            raise self.screen_error
        ps = self.screen_p if isinstance(self.screen_p, list) else [self.screen_p] * len(listings)
        return {"scores": [{"index": i, "p_same": ps[i], "confidence": 0.8} for i in range(len(listings))],
                "printed": {"mpn": {"p": self.printed_p, "confidence": 0.9} if (claims or {}).get("mpn") else None,
                            "brand": {"p": 0.99, "confidence": 0.9} if (claims or {}).get("brand") else None},
                "receipt": {"job": "screen", "model": "gemini-decision-1", "id": None, "ms": 9,
                            "questions": len(listings) + len(claims or {})}}

    def anchors_for(self, visual, image=None):
        self.calls.append("catalog")
        if self.catalog_error:
            raise self.catalog_error
        return {"anchors": [dict(a) for a in self.anchors], "rows": [dict(r) for r in self.rows],
                "receipts": [dict(r) for r in self.catalog_receipts]}

    def product(self, upid):
        self.calls.append("product:" + upid)
        if upid in self.product_errors:
            raise self.product_errors[upid]
        return ({"upid": upid, "rows": [dict(r) for r in self.product_rows.get(upid, [])]},
                {"source": "shop_catalog", "tool": "get_product", "status": "ok", "upid": upid, "ms": 4})

    def rows_from(self, doc, read_at):
        return [dict(r, read_at=read_at) for r in doc["rows"]]

    def run_comparison(self, target, goal, rows, **kwargs):
        self.calls.append("compare")
        self.compared_rows = rows
        if self.compare_error:
            raise self.compare_error
        return {"result": {"status": "single_offer", "headline": "One verified offer", "price_cents": 1234,
                           "currency": "USD", "basis_label": "Item price", "winners": [dict(OFFER)], "near_miss": None,
                           "rejected": [], "leads": []},
                "coverage": {"attempted": 1, "responded": 1, "sellers_seen": 1, "sellers_eligible": 1, "checked_at": 1,
                             "sources": [{"id": "shop_catalog", "name": "Shop catalog (shop.dev)", "kind": "catalog",
                                          "status": self.source_status, "offers": 1, "eligible": 1, "ms": 5, "note": ""}]},
                "receipts": [{"source": "merchant_storefront", "url_host": "seller.test", "read_at": 1, "ms": 2}],
                "evaluation": {"eligible": 1}}

    def create(self, run, command, replies=None):
        self.calls.append("watch")
        if self.watch_error:
            raise self.watch_error
        return {"watch_id": "w_test", "status": "preview_only", "scope_sentence": "Watching Test Seller", "command": command}

    def lane(self, name):
        table = {"app.models": ("inspect", "parse_goal", "scan", "bind", "screen"), "app.merchants.shop": ("anchors_for", "product", "rows_from"),
                 "app.merchants.compare": ("run_comparison",), "app.watch": ("create",)}
        return SimpleNamespace(**{k: getattr(self, k) for k in table[name]})


@pytest.fixture
def fakes(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "runs.sqlite")
    monkeypatch.setattr(store, "_ready", False)
    f = Fakes()
    monkeypatch.setattr(pipeline, "_lane", f.lane)
    yield f
    deadline = time.time() + 5
    while store.working_runs() and time.time() < deadline:  # let background workers finish before the next test
        time.sleep(0.02)


def make_run(note=""):
    run = new_run(note)
    store.create_run(run, "session-a", b"jpeg-bytes")
    return run["run_id"]


def answer(run_id, **fields):
    """What the answer route does before it hands the run back to the pipeline."""
    run = store.get_run(run_id)
    question = run["question"]
    run["revision"] += 1
    run.update(question=None, state="searching", error=None)
    run["_ended_ms"] = None
    store.save_run(run)
    pipeline._work(run_id, {"question": question, **fields})
    return store.get_run(run_id)


def rows_of(run):
    return {r["id"]: r for r in run["progress"]}


def test_clear_photo_runs_every_step_and_ends_complete(fakes):
    run_id = make_run()
    pipeline._work(run_id, None)
    run = store.get_run(run_id)
    assert run["state"] == "complete" and run["error"] is None and run["question"] is None
    assert [r["id"] for r in run["progress"]] == ["view", "gate", "goal", "identity", "bind", "screen", "offers"]
    assert all(r["status"] == "done" for r in run["progress"])
    assert run["result"]["status"] == "single_offer" and run["result"]["winners"][0]["seller_name"] == "Test Seller"
    assert run["target"]["bound"] is True and run["target"]["identity_basis"] == "model_number"
    assert run["target"]["upid"] == UPID
    assert {r["job"] for r in run["receipts"]["models"]} == {"view", "gate", "goal", "bind", "screen"}
    assert run["_ended_ms"] and fakes.calls.count("compare") == 1


def test_labels_are_neutral_present_tense(fakes):
    run_id = make_run()
    pipeline._work(run_id, None)
    labels = {r["id"]: r["label"] for r in store.get_run(run_id)["progress"]}
    assert labels == {"view": "Gemini 4 Neon reads the photo", "gate": "Gemini Decision 1 checks the target is clear",
                      "goal": "Gemini 4 Carbon reads the request", "identity": "Shop catalog identity evidence",
                      "bind": "Gemini 4 Carbon binds the exact product",
                      "screen": "Gemini Decision 1 screens every listing in one pass",
                      "offers": "Merchant offers verified"}


def test_public_document_hides_private_keys(fakes):
    run_id = make_run()
    pipeline._work(run_id, None)
    doc = public(store.get_run(run_id))
    assert not [k for k in doc if k.startswith("_")] and doc["elapsed_ms"] >= 0


@pytest.mark.parametrize("status", ["failed", "timeout", "limited"])
def test_degraded_source_ends_partial(fakes, status):
    fakes.source_status = status
    run_id = make_run()
    pipeline._work(run_id, None)
    assert store.get_run(run_id)["state"] == "partial"


def test_failed_catalog_call_is_surfaced_and_run_is_partial(fakes):
    fakes.catalog_receipts.append({"source": "shop_catalog", "tool": "get_product", "status": "failed", "ms": 12000})
    run_id = make_run()
    pipeline._work(run_id, None)
    run = store.get_run(run_id)
    source = run["coverage"]["sources"][0]
    assert run["state"] == "partial"
    assert source["status"] == "limited" and source["note"] == "1 of 2 catalog calls failed"


def test_catalog_coverage_counts_failed_calls():
    ok = {"source": "shop_catalog", "status": "ok", "ms": 500}
    bad = {"source": "shop_catalog", "status": "failed", "ms": 12000}
    assert pipeline._catalog_coverage([], [ok])["sources"][0]["status"] == "ok"
    mixed = pipeline._catalog_coverage([], [ok, bad])
    assert mixed["sources"][0]["status"] == "limited" and mixed["sources"][0]["note"] == "1 of 2 catalog calls failed"
    dead = pipeline._catalog_coverage([], [bad, bad])
    assert dead["sources"][0]["status"] == "failed" and dead["responded"] == 0


def test_scoped_out_request_ends_partial_with_reason(fakes):
    fakes.intent["basis"] = "delivered"
    run_id = make_run("delivered price")
    pipeline._work(run_id, None)
    run = store.get_run(run_id)
    assert run["state"] == "partial"
    assert any(a["id"].startswith("scoped_") and a["label"].startswith("Not compared") for a in run["assumptions"])


def test_no_catalog_product_ends_without_a_price(fakes):
    fakes.anchors = []
    run_id = make_run()
    pipeline._work(run_id, None)
    run = store.get_run(run_id)
    assert run["state"] == "complete" and run["result"]["status"] == "no_verified_offer"
    assert run["result"]["winners"] == [] and run["result"]["price_cents"] is None
    assert "compare" not in fakes.calls and "bind" not in fakes.calls


def test_unbound_photo_asks_one_choice_question_then_buyer_choice_compares(fakes):
    fakes.bind_slot = None
    run_id = make_run()
    pipeline._work(run_id, None)
    run = store.get_run(run_id)
    assert run["state"] == "needs_detail" and run["question"]["kind"] == "choice"
    assert [o["id"] for o in run["question"]["options"]] == [UPID, "none"]
    assert run["result"]["status"] == "needs_identity" and run["result"]["winners"] == []
    assert run["result"]["price_cents"] is None and "compare" not in fakes.calls
    run = answer(run_id, choice=UPID)
    assert run["state"] == "complete" and run["revision"] == 2 and run["question"] is None
    assert run["target"]["identity_basis"] == "buyer_choice" and run["result"]["status"] == "single_offer"
    assert fakes.calls.count("inspect") == 1 and fakes.calls.count("bind") == 1
    assert fakes.calls.count("catalog") == 1 and "product:" + UPID in fakes.calls


def test_chosen_option_binds_what_was_offered_even_if_a_new_search_would_not_find_it(fakes):
    other = {**ANCHOR, "upid": "gid://shopify/p/other", "title": "Other product"}
    fakes.anchors = [dict(ANCHOR), other]
    fakes.bind_slot = None
    run_id = make_run()
    pipeline._work(run_id, None)
    fakes.anchors = [other]                      # a second search would no longer return the offered product
    run = answer(run_id, choice=UPID)
    assert run["state"] == "complete" and run["target"]["upid"] == UPID and run["target"]["title"] == "Test Brand X100"
    assert fakes.calls.count("catalog") == 1     # no new search
    assert [r["listing"] for r in fakes.compared_rows] == [ROW["listing"]]
    assert all(r["status"] == "done" for r in run["progress"])
    assert {r["tool"] for r in run["receipts"]["sources"] if r.get("source") == "shop_catalog"} == {"get_product"}


def test_chosen_product_that_cannot_be_read_gets_a_new_question_not_a_dead_end(fakes):
    other = {**ANCHOR, "upid": "gid://shopify/p/other", "title": "Other product"}
    fakes.anchors = [dict(ANCHOR), other]
    fakes.bind_slot = None
    run_id = make_run()
    pipeline._work(run_id, None)
    fakes.product_errors[UPID] = TimeoutError("catalog timeout")
    run = answer(run_id, choice=UPID)
    assert run["state"] == "needs_detail" and run["question"]["kind"] == "choice"
    assert [o["id"] for o in run["question"]["options"]] == ["gid://shopify/p/other", "none"]
    assert run["result"]["winners"] == [] and "compare" not in fakes.calls
    assert rows_of(run)["identity"]["status"] == "failed"
    assert any(r.get("status") == "failed" for r in run["receipts"]["sources"])
    assert run["coverage"]["sources"][0]["status"] in ("limited", "failed")
    fakes.product_rows["gid://shopify/p/other"] = []
    run = answer(run_id, choice="gid://shopify/p/other")
    assert run["state"] == "needs_detail" and run["question"]["kind"] == "text" and run["revision"] == 3


def test_none_of_these_is_an_honest_no_result_with_a_way_forward(fakes):
    fakes.bind_slot = None
    run_id = make_run()
    pipeline._work(run_id, None)
    leads = store.get_run(run_id)["result"]["leads"]
    run = answer(run_id, choice="none")
    assert run["state"] == "needs_detail" and run["result"]["status"] == "needs_identity"
    assert run["result"]["winners"] == [] and run["result"]["price_cents"] is None
    assert run["result"]["leads"] == leads and "compare" not in fakes.calls
    assert run["question"]["kind"] == "text" and run["question"]["options"] == []
    assert run["question"]["text"] == "What is it? Brand and model in a few words."
    fakes.bind_slot = 0
    run = answer(run_id, text="Test Brand X100")
    assert run["state"] == "complete" and run["revision"] == 3 and run["note"] == "Test Brand X100"
    assert run["result"]["status"] == "single_offer" and fakes.calls.count("inspect") == 2


@pytest.mark.parametrize("choice,count,start", [
    ("other", "one", "I could not make out one product"),
    ("multiple", "many", "I can see more than one possible item"),
    ("single", "many", "I can see more than one possible item"),
    ("single", "one", "I am not sure this photo shows one clear product (confidence 0.67, needs 0.75). Which item"),
])
def test_text_question_matches_the_real_reason(fakes, choice, count, start):
    fakes.visual["target_count"] = count
    fakes.decision.update({"pass": False, "choice": choice, "confidence": 0.67})
    run_id = make_run()
    pipeline._work(run_id, None)
    run = store.get_run(run_id)
    assert run["state"] == "needs_detail" and run["question"]["kind"] == "text"
    assert run["question"]["text"].startswith(start)


def test_chosen_product_with_no_listings_left_asks_again(fakes):
    fakes.bind_slot = None
    run_id = make_run()
    pipeline._work(run_id, None)
    fakes.product_rows[UPID] = []
    run = answer(run_id, choice=UPID)
    assert run["state"] == "needs_detail" and run["question"]["kind"] == "text"
    assert run["result"]["status"] == "needs_identity" and "compare" not in fakes.calls


def test_several_items_ask_a_text_question_and_the_answer_reruns_with_the_words(fakes):
    fakes.visual["target_count"] = "many"
    fakes.decision.update({"pass": False, "choice": "multiple"})
    run_id = make_run()
    pipeline._work(run_id, None)
    run = store.get_run(run_id)
    assert run["state"] == "needs_detail" and run["question"]["kind"] == "text"
    assert "bind" not in fakes.calls and "compare" not in fakes.calls
    fakes.visual["target_count"] = "one"
    fakes.decision.update({"pass": True, "choice": "single"})
    run = answer(run_id, text="the blue box")
    assert run["state"] == "complete" and run["note"] == "the blue box" and fakes.notes[-1] == "the blue box"
    assert fakes.calls.count("inspect") == 2 and run["revision"] == 2


def test_superseded_revision_never_overwrites(fakes):
    fakes.inspect_gate = threading.Event()
    run_id = make_run()
    worker = threading.Thread(target=pipeline._work, args=(run_id, None))
    worker.start()
    deadline = time.time() + 5
    while "inspect" not in fakes.calls and time.time() < deadline:
        time.sleep(0.01)
    newer = store.get_run(run_id)
    newer["revision"] = 2
    newer["state"] = "needs_detail"
    newer["note"] = "written by the newer revision"
    store.save_run(newer)
    fakes.inspect_gate.set()
    worker.join(5)
    assert not worker.is_alive()
    run = store.get_run(run_id)
    assert run["revision"] == 2 and run["state"] == "needs_detail" and run["note"] == "written by the newer revision"
    assert run["result"] is None and "compare" not in fakes.calls


def test_view_failure_is_unavailable_with_the_safe_message_and_no_result(fakes):
    fakes.inspect_error = ModelCallError("view", "gemini-4-neon", "HTTP 503 from the model service")
    run_id = make_run()
    pipeline._work(run_id, None)
    run = store.get_run(run_id)
    assert run["state"] == "unavailable" and run["result"] is None and run["coverage"] is None
    assert run["error"] == {"code": "model_call_failed",
                            "message": "gemini-4-neon could not complete the view step: HTTP 503 from the model service"}
    rows = rows_of(run)
    assert rows["view"]["status"] == "failed" and rows["view"]["detail"] == run["error"]["message"]
    assert rows["gate"]["status"] == "skipped" and rows["gate"]["ms"] is None
    assert rows["goal"]["status"] == "done"
    assert "catalog" not in fakes.calls and run["_ended_ms"]


def test_gate_failure_marks_gate_failed_and_view_skipped(fakes):
    fakes.inspect_error = ModelCallError("gate", "gemini-decision-1", "timed out")
    run_id = make_run()
    pipeline._work(run_id, None)
    rows = rows_of(store.get_run(run_id))
    assert rows["gate"]["status"] == "failed" and rows["view"]["status"] == "skipped"
    assert rows["view"]["detail"] == ""


def test_goal_failure_is_unavailable(fakes):
    fakes.goal_error = ModelCallError("goal", "gemini-4-carbon", "HTTP 500")
    run_id = make_run()
    pipeline._work(run_id, None)
    run = store.get_run(run_id)
    rows = rows_of(run)
    assert run["state"] == "unavailable" and run["result"] is None
    assert rows["goal"]["status"] == "failed" and rows["view"]["status"] == "done" and rows["gate"]["status"] == "done"


def test_catalog_failure_is_unavailable(fakes):
    fakes.catalog_error = TimeoutError("secret detail that must not leak")
    run_id = make_run()
    pipeline._work(run_id, None)
    run = store.get_run(run_id)
    assert run["state"] == "unavailable" and run["error"]["code"] == "catalog_unavailable"
    assert "secret" not in run["error"]["message"] and run["result"] is None
    assert rows_of(run)["identity"]["status"] == "failed"


def test_unexpected_failure_never_leaks_text_or_fabricates(fakes):
    fakes.compare_error = RuntimeError("stack detail /home/secret")
    run_id = make_run()
    pipeline._work(run_id, None)
    run = store.get_run(run_id)
    assert run["state"] == "unavailable" and run["error"]["code"] == "internal"
    assert "secret" not in run["error"]["message"] and run["result"] is None
    assert rows_of(run)["offers"]["status"] == "failed"


def test_missing_lane_is_reported(fakes, monkeypatch):
    def lane(name):
        if name == "app.merchants.compare":
            raise pipeline.LaneNotReady(name)
        return fakes.lane(name)
    monkeypatch.setattr(pipeline, "_lane", lane)
    run_id = make_run()
    pipeline._work(run_id, None)
    run = store.get_run(run_id)
    assert run["state"] == "unavailable" and run["error"]["code"] == "lane_not_ready" and run["result"] is None


def test_explicit_watch_request_creates_the_watch_after_the_result(fakes):
    fakes.intent.update({"operation": "compare_and_watch", "watch_words": "notify me when this restocks under $25"})
    run_id = make_run("notify me when this restocks under $25")
    pipeline._work(run_id, None)
    run = store.get_run(run_id)
    assert run["state"] == "complete" and run["watch"]["watch_id"] == "w_test"
    assert rows_of(run)["watch"]["status"] == "done" and fakes.calls[-1] == "watch"
    assert run["result"]["status"] == "single_offer"


def test_watch_failure_keeps_the_result(fakes):
    fakes.intent.update({"operation": "compare_and_watch", "watch_words": "notify me"})
    fakes.watch_error = ModelCallError("watch_rule", "gemini-4-carbon", "HTTP 500")
    run_id = make_run("notify me")
    pipeline._work(run_id, None)
    run = store.get_run(run_id)
    assert run["state"] == "complete" and run["watch"] is None and run["result"]["status"] == "single_offer"
    assert rows_of(run)["watch"]["status"] == "failed"


def test_no_watch_step_without_an_explicit_request(fakes):
    run_id = make_run()
    pipeline._work(run_id, None)
    run = store.get_run(run_id)
    assert "watch" not in rows_of(run) and "watch" not in fakes.calls and run["watch"] is None


def test_restart_recovery_closes_working_runs_only(fakes):
    stuck = []
    for state in ("reading", "searching", "verifying"):
        run = new_run("")
        run["state"] = state
        run["progress"] = [{"id": "view", "label": "x", "status": "done", "ms": 5, "detail": "seen"},
                           {"id": "offers", "label": "y", "status": "running", "ms": None, "detail": ""}]
        store.create_run(run, "s", b"img")
        stuck.append(run["run_id"])
    done_id = make_run()
    pipeline._work(done_id, None)
    waiting = new_run("")
    waiting["state"] = "needs_detail"
    store.create_run(waiting, "s", b"img")
    assert pipeline.recover_interrupted() == 3
    for run_id in stuck:
        run = store.get_run(run_id)
        assert run["state"] == "unavailable" and run["result"] is None and run["_ended_ms"]
        assert run["error"] == {"code": "interrupted",
                                "message": "This search was interrupted by a server restart. Please try again."}
        assert [r["status"] for r in run["progress"]] == ["done", "failed"]
        assert run["progress"][0]["detail"] == "seen"
    assert store.get_run(done_id)["state"] == "complete"
    assert store.get_run(waiting["run_id"])["state"] == "needs_detail"
    assert pipeline.recover_interrupted() == 0


def test_wall_clock_guard_closes_a_hanging_run_and_the_late_worker_cannot_write(fakes, monkeypatch):
    monkeypatch.setattr(pipeline, "RUN_LIMIT_S", 0.2)
    fakes.inspect_gate = threading.Event()
    run_id = make_run()
    pipeline.start(run_id)
    deadline = time.time() + 5
    while store.get_run(run_id)["state"] != "unavailable" and time.time() < deadline:
        time.sleep(0.02)
    run = store.get_run(run_id)
    assert run["state"] == "unavailable" and run["error"]["code"] == "timeout"
    assert "longer than 90 seconds" in run["error"]["message"] and run["result"] is None and run["_ended_ms"]
    assert all(r["status"] == "failed" for r in run["progress"])
    fakes.inspect_gate.set()
    deadline = time.time() + 5
    while "goal" not in fakes.calls and time.time() < deadline:
        time.sleep(0.02)
    time.sleep(0.3)
    late = store.get_run(run_id)
    assert late["state"] == "unavailable" and late["result"] is None and "compare" not in fakes.calls


def test_guard_leaves_a_finished_run_alone(fakes, monkeypatch):
    monkeypatch.setattr(pipeline, "RUN_LIMIT_S", 0.3)
    run_id = make_run()
    pipeline.start(run_id)
    deadline = time.time() + 5
    while store.get_run(run_id)["state"] != "complete" and time.time() < deadline:
        time.sleep(0.02)
    time.sleep(0.5)
    run = store.get_run(run_id)
    assert run["state"] == "complete" and run["error"] is None
    pipeline._expire(run_id, run["revision"])
    assert store.get_run(run_id)["state"] == "complete"


def test_screen_scores_every_listing_and_annotates_rows_and_result(fakes):
    fakes.rows = [dict(ROW), {**ROW, "listing": "gid://variant/2", "seller": "gid://seller/2"}]
    fakes.screen_p = [0.91, 0.12]
    run_id = make_run()
    pipeline._work(run_id, None)
    run = store.get_run(run_id)
    target, listings, claims = fakes.screen_args
    assert claims == {"mpn": "X100", "brand": "Test Brand"} and len(listings) == 2 and target["mpn"] == "X100"
    assert [r.get("screen_p") for r in fakes.compared_rows] == [0.91, 0.12]
    row = rows_of(run)["screen"]
    assert row["status"] == "done" and row["detail"] == "2 listings · 1 likely the same item" and row["ms"] == 9
    assert run["result"]["screen"] == {"model": "gemini-decision-1", "listings": 2, "ms": 9,
                                       "note": "1 of 2 listings likely the same item"}
    assert run["state"] == "complete" and run["target"]["identity_basis"] == "model_number"


def test_screen_is_capped_at_60_listings(fakes):
    fakes.rows = [{**ROW, "listing": f"gid://variant/{i}", "seller": f"gid://seller/{i}"} for i in range(70)]
    run_id = make_run()
    pipeline._work(run_id, None)
    assert len(fakes.screen_args[1]) == 60
    assert sum(1 for r in fakes.compared_rows if "screen_p" in r) == 60
    assert store.get_run(run_id)["result"]["screen"]["note"].endswith("10 not screened")


def test_failed_screen_changes_nothing_else(fakes):
    fakes.screen_error = ModelCallError("screen", "gemini-decision-1", "HTTP 500")
    run_id = make_run()
    pipeline._work(run_id, None)
    run = store.get_run(run_id)
    row = rows_of(run)["screen"]
    assert row["status"] == "failed" and row["detail"] == "gemini-decision-1 could not complete the screen step: HTTP 500"
    assert run["state"] == "complete" and run["result"]["status"] == "single_offer" and "screen" not in run["result"]
    assert run["target"]["identity_basis"] == "model_number" and "screen_p" not in fakes.compared_rows[0]


def test_missing_screen_function_is_reported_and_the_run_proceeds(fakes, monkeypatch):
    def lane(name):
        ns = fakes.lane(name)
        if name == "app.models":
            del ns.screen
        return ns
    monkeypatch.setattr(pipeline, "_lane", lane)
    run_id = make_run()
    pipeline._work(run_id, None)
    run = store.get_run(run_id)
    assert rows_of(run)["screen"]["status"] == "failed" and run["state"] == "complete"


def test_unprinted_model_number_is_not_accepted_as_a_bind(fakes):
    fakes.printed_p = 0.31
    run_id = make_run()
    pipeline._work(run_id, None)
    run = store.get_run(run_id)
    assert run["state"] == "needs_detail" and run["question"]["kind"] == "choice" and "compare" not in fakes.calls
    assert run["result"]["winners"] == [] and run["target"]["bound"] is False
    assert "0.31" in rows_of(run)["bind"]["detail"]
    run = answer(run_id, choice=UPID)
    assert run["state"] == "complete" and run["target"]["identity_basis"] == "buyer_choice"
    assert fakes.compared_rows[0]["screen_p"] == 0.91 and run["result"]["screen"]["listings"] == 1


def test_printed_guard_boundary_and_failed_screen_keep_the_bind(fakes):
    fakes.printed_p = 0.75
    run_id = make_run()
    pipeline._work(run_id, None)
    assert store.get_run(run_id)["target"]["identity_basis"] == "model_number"
    fakes.printed_p = 0.1
    fakes.screen_error = ModelCallError("screen", "gemini-decision-1", "timed out")
    run_id = make_run()
    pipeline._work(run_id, None)
    assert store.get_run(run_id)["target"]["identity_basis"] == "model_number"


def test_choice_options_are_ordered_by_screen_probability_and_capped_at_five(fakes):
    fakes.bind_slot = None
    fakes.anchors = [{**ANCHOR, "upid": f"gid://shopify/p/{i}", "title": f"Product {i}"} for i in range(7)]
    fakes.rows = [{**ROW, "product": f"gid://shopify/p/{i}", "listing": f"gid://variant/{i}"} for i in range(7)]
    fakes.screen_p = [0.1, 0.2, 0.9, 0.3, 0.4, 0.5, 0.6]
    run_id = make_run()
    pipeline._work(run_id, None)
    run = store.get_run(run_id)
    options = run["question"]["options"]
    assert [o["id"] for o in options] == [f"gid://shopify/p/{i}" for i in (2, 6, 5, 4, 3)] + ["none"]
    assert [o.get("p") for o in options] == [0.9, 0.6, 0.5, 0.4, 0.3, None]
    assert [lead.get("screen_p") for lead in run["result"]["leads"]][:3] == [0.1, 0.2, 0.9]
    assert run["result"]["screen"]["listings"] == 7


def test_gate_probabilities_are_recorded_when_given(fakes):
    fakes.decision["probabilities"] = {"single": 0.93, "multiple": 0.05, "other": 0.02}
    run_id = make_run()
    pipeline._work(run_id, None)
    assert store.get_run(run_id)["decision"]["probabilities"] == {"single": 0.93, "multiple": 0.05, "other": 0.02}
    fakes.decision.pop("probabilities")
    run_id = make_run()
    pipeline._work(run_id, None)
    assert "probabilities" not in store.get_run(run_id)["decision"]


def test_bind_failure_still_records_what_the_screen_did(fakes, monkeypatch):
    def bind(*args, **kwargs):
        raise ModelCallError("bind", "gemini-4-carbon", "HTTP 500")
    monkeypatch.setattr(fakes, "bind", bind)
    run_id = make_run()
    pipeline._work(run_id, None)
    run = store.get_run(run_id)
    rows = rows_of(run)
    assert run["state"] == "unavailable" and run["result"] is None
    assert rows["bind"]["status"] == "failed" and rows["screen"]["status"] == "done"
