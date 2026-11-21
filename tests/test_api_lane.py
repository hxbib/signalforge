"""HTTP tests. The lanes behind the pipeline are fakes from tests/test_pipeline_lane.py; no network, no model calls."""
import io
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app  # noqa: E402,F401
from app import config, main, pipeline, store  # noqa: E402
from app.contracts import new_run  # noqa: E402
from tests.test_pipeline_lane import UPID, Fakes, ModelCallError  # noqa: E402

TERMINAL = ("complete", "partial", "needs_detail", "unavailable")


def photo(fmt="JPEG") -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (64, 48), (200, 30, 30)).save(out, fmt)
    return out.getvalue()


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


@pytest.fixture
def client(fakes):
    return TestClient(main.app)


def upload(client, note="", data=None, name="p.jpg", kind="image/jpeg"):
    return client.post("/api/search", files={"image": (name, data if data is not None else photo(), kind)},
                       data={"note": note})


def finished(client, run_id, revision=None):
    deadline = time.time() + 10
    while time.time() < deadline:
        r = client.get(f"/api/search/{run_id}")
        assert r.status_code == 200
        doc = r.json()
        if doc["state"] in TERMINAL and (revision is None or doc["revision"] == revision):
            return doc
        time.sleep(0.02)
    raise AssertionError("run did not finish")


def test_upload_runs_to_a_result(client):
    r = upload(client)
    assert r.status_code == 202
    body = r.json()
    assert body["revision"] == 1 and body["state"] == "reading" and body["run_id"].startswith("run_")
    doc = finished(client, body["run_id"])
    assert doc["state"] == "complete" and doc["result"]["status"] == "single_offer"
    assert not [k for k in doc if k.startswith("_")]
    assert doc["image_url"] == f"/api/search/{body['run_id']}/image"
    image = client.get(doc["image_url"])
    assert image.status_code == 200 and image.headers["content-type"] == "image/jpeg"
    proof = client.get(f"/api/search/{body['run_id']}/proof").json()
    assert set(proof) == {"receipts", "decision", "evaluation", "coverage"}


@pytest.mark.parametrize("fmt", ["PNG", "WEBP"])
def test_png_and_webp_are_accepted(client, fmt):
    r = upload(client, data=photo(fmt), name="p", kind="application/octet-stream")
    assert r.status_code == 202
    assert finished(client, r.json()["run_id"])["state"] == "complete"


def test_note_is_kept_verbatim_and_bounded(client):
    run_id = upload(client, note="  under $25 please  " + "x" * 600).json()["run_id"]
    doc = finished(client, run_id)
    assert doc["note"].startswith("under $25 please") and len(doc["note"]) == 500


def test_non_image_upload_is_400(client, fakes):
    r = upload(client, data=b"this is not an image", name="note.txt", kind="text/plain")
    assert r.status_code == 400 and r.json()["error"]["code"] == "unsupported_image"
    assert fakes.calls == []


def test_gif_is_400(client):
    out = io.BytesIO()
    Image.new("RGB", (8, 8)).save(out, "GIF")
    assert upload(client, data=out.getvalue(), name="p.gif", kind="image/gif").status_code == 400


def test_missing_image_field_is_rejected(client):
    assert client.post("/api/search", data={"note": "x"}).status_code in (400, 422)


def test_upload_over_5_mb_is_413(client, fakes):
    r = upload(client, data=b"\xff" * (config.MAX_UPLOAD_BYTES + 1))
    assert r.status_code == 413 and r.json()["error"]["code"] == "too_large"
    assert fakes.calls == []


def test_foreign_session_gets_404_everywhere(client, fakes):
    fakes.bind_slot = None
    run_id = upload(client).json()["run_id"]
    doc = finished(client, run_id)
    stranger = TestClient(main.app)
    assert stranger.get(f"/api/search/{run_id}").status_code == 404
    assert stranger.get(f"/api/search/{run_id}/image").status_code == 404
    assert stranger.get(f"/api/search/{run_id}/proof").status_code == 404
    r = stranger.post(f"/api/search/{run_id}/answer",
                      json={"revision": doc["revision"], "question_id": doc["question"]["question_id"], "choice": UPID})
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    assert client.get(f"/api/search/{run_id}").json()["state"] == "needs_detail"


def test_unknown_run_is_404(client):
    assert client.get("/api/search/run_does_not_exist").status_code == 404
    assert client.post("/api/search/run_does_not_exist/answer", json={}).status_code == 404


def test_cross_origin_post_is_refused(client):
    r = client.post("/api/search", files={"image": ("p.jpg", photo(), "image/jpeg")},
                    headers={"origin": "https://elsewhere.example"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_origin"


def test_choice_answer_stale_revision_409_then_accepted_once(client, fakes):
    fakes.bind_slot = None
    run_id = upload(client).json()["run_id"]
    doc = finished(client, run_id)
    qid = doc["question"]["question_id"]
    url = f"/api/search/{run_id}/answer"
    stale = client.post(url, json={"revision": doc["revision"] - 1, "question_id": qid, "choice": UPID})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "stale"
    assert client.post(url, json={"revision": doc["revision"], "question_id": "q_other", "choice": UPID}).status_code == 409
    bad = client.post(url, json={"revision": doc["revision"], "question_id": qid, "choice": "not-offered"})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "bad_choice"
    assert client.post(url, content=b"not json", headers={"content-type": "application/json"}).status_code == 400
    ok = client.post(url, json={"revision": doc["revision"], "question_id": qid, "choice": UPID})
    assert ok.status_code == 202 and ok.json()["revision"] == doc["revision"] + 1
    again = client.post(url, json={"revision": doc["revision"], "question_id": qid, "choice": UPID})
    assert again.status_code == 409
    done = finished(client, run_id, revision=doc["revision"] + 1)
    assert done["state"] == "complete" and done["target"]["identity_basis"] == "buyer_choice"
    assert client.post(url, json={"revision": done["revision"], "question_id": qid, "choice": UPID}).status_code == 409


def test_none_of_these_answer_ends_without_a_price(client, fakes):
    fakes.bind_slot = None
    run_id = upload(client).json()["run_id"]
    doc = finished(client, run_id)
    r = client.post(f"/api/search/{run_id}/answer",
                    json={"revision": doc["revision"], "question_id": doc["question"]["question_id"], "choice": "none"})
    assert r.status_code == 202
    done = finished(client, run_id, revision=2)
    assert done["state"] == "needs_detail" and done["result"]["status"] == "needs_identity"
    assert done["result"]["winners"] == [] and done["result"]["price_cents"] is None
    assert done["question"]["kind"] == "text" and done["question"]["question_id"] != doc["question"]["question_id"]


def test_text_answer_reruns_and_empty_text_is_400(client, fakes):
    fakes.visual["target_count"] = "many"
    fakes.decision.update({"pass": False, "choice": "multiple"})
    run_id = upload(client).json()["run_id"]
    doc = finished(client, run_id)
    assert doc["question"]["kind"] == "text"
    url = f"/api/search/{run_id}/answer"
    payload = {"revision": doc["revision"], "question_id": doc["question"]["question_id"]}
    empty = client.post(url, json={**payload, "text": "   "})
    assert empty.status_code == 400 and empty.json()["error"]["code"] == "empty_answer"
    fakes.visual["target_count"] = "one"
    fakes.decision.update({"pass": True, "choice": "single"})
    assert client.post(url, json={**payload, "text": "the blue box"}).status_code == 202
    done = finished(client, run_id, revision=2)
    assert done["state"] == "complete" and done["note"] == "the blue box"


def test_model_failure_surfaces_as_unavailable_with_the_safe_message(client, fakes):
    fakes.inspect_error = ModelCallError("view", "gemini-4-neon", "HTTP 503 from the model service")
    run_id = upload(client).json()["run_id"]
    doc = finished(client, run_id)
    assert doc["state"] == "unavailable" and doc["result"] is None and doc["coverage"] is None
    assert doc["error"] == {"code": "model_call_failed",
                            "message": "gemini-4-neon could not complete the view step: HTTP 503 from the model service"}
    statuses = {r["id"]: r["status"] for r in doc["progress"]}
    assert statuses["view"] == "failed" and statuses["gate"] == "skipped"


def test_startup_marks_interrupted_runs_unavailable(fakes):
    run = new_run("")
    run["state"] = "verifying"
    run["progress"] = [{"id": "offers", "label": "Merchant offers verified", "status": "running", "ms": None, "detail": ""}]
    store.create_run(run, "x" * 32, b"img")
    with TestClient(main.app) as client:
        client.cookies.set("sf_session", "x" * 32)
        doc = client.get(f"/api/search/{run['run_id']}").json()
        first = doc["elapsed_ms"]
        assert doc["state"] == "unavailable" and doc["result"] is None
        assert doc["error"] == {"code": "interrupted",
                                "message": "This search was interrupted by a server restart. Please try again."}
        assert doc["progress"][0]["status"] == "failed"
        time.sleep(0.05)
        assert client.get(f"/api/search/{run['run_id']}").json()["elapsed_ms"] == first


def test_busy_session_gets_429(client, fakes, monkeypatch):
    monkeypatch.setattr(config, "MAX_ACTIVE_RUNS_PER_SESSION", 0)
    r = upload(client)
    assert r.status_code == 429 and r.json()["error"]["code"] == "busy"


def test_health_reports_without_secrets(client):
    body = client.get("/api/health").json()
    assert body["ok"] is True and set(body) == {"ok", "key_present", "commit", "models", "lanes"}
    assert isinstance(body["key_present"], bool)


def test_elapsed_time_excludes_the_wait_for_the_answer(client, fakes):
    fakes.bind_slot = None
    run_id = upload(client).json()["run_id"]
    doc = finished(client, run_id)
    time.sleep(0.6)
    assert client.get(f"/api/search/{run_id}").json()["elapsed_ms"] == doc["elapsed_ms"]
    client.post(f"/api/search/{run_id}/answer",
                json={"revision": doc["revision"], "question_id": doc["question"]["question_id"], "choice": UPID})
    done = finished(client, run_id, revision=2)
    assert doc["elapsed_ms"] <= done["elapsed_ms"] < doc["elapsed_ms"] + 500
