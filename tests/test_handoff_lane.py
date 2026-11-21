"""Phone hand-off tests. pipeline.start is a fake here; no network, no model calls."""
import io
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app  # noqa: E402,F401
from app import config, main, pipeline, store  # noqa: E402
from app.handoff import routes  # noqa: E402


def photo() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (64, 48), (200, 30, 30)).save(out, "JPEG")
    return out.getvalue()


@pytest.fixture
def started(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "runs.sqlite")
    monkeypatch.setattr(store, "_ready", False)
    monkeypatch.setenv("SF_PUBLIC_URL", "https://demo.example")
    calls = []
    monkeypatch.setattr(pipeline, "start", calls.append)
    routes._tokens.clear()
    yield calls
    routes._tokens.clear()


def make(laptop):
    res = laptop.post("/api/handoff")
    assert res.status_code == 200
    return res.json()


def send(phone, token, data=None):
    return phone.post(f"/api/handoff/{token}/photo", files={"image": ("p.jpg", data or photo(), "image/jpeg")})


def test_lane_is_reported():
    assert main.LANES.get("app.handoff.routes") is True


def test_lifecycle_run_is_owned_by_the_laptop(started):
    laptop, phone = TestClient(main.app), TestClient(main.app)
    body = make(laptop)
    token = body["token"]
    assert body["url"] == f"https://demo.example/handoff/{token}"
    assert body["qr_url"] == f"/api/handoff/{token}/qr.svg" and body["expires_in"] == 300
    qr = laptop.get(body["qr_url"])
    assert qr.status_code == 200 and qr.headers["content-type"].startswith("image/svg+xml")
    assert qr.headers["cache-control"] == "no-store" and "<svg" in qr.text
    page = phone.get(f"/handoff/{token}")
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    assert phone.get(f"/api/handoff/{token}/open").json() == {"open": True}
    assert laptop.get(f"/api/handoff/{token}").json() == {"status": "waiting", "run_id": None}
    assert send(phone, token).json() == {"ok": True}
    status = laptop.get(f"/api/handoff/{token}").json()
    assert status["status"] == "received" and status["run_id"]
    run_id = status["run_id"]
    assert started == [run_id]
    assert store.owner(run_id) == laptop.cookies.get("sf_session")
    assert store.owner(run_id) != phone.cookies.get("sf_session")
    assert laptop.get(f"/api/search/{run_id}").status_code == 200
    assert phone.get(f"/api/search/{run_id}").status_code == 404


def test_wrong_session_cannot_read_status(started):
    laptop, other = TestClient(main.app), TestClient(main.app)
    token = make(laptop)["token"]
    assert other.get(f"/api/handoff/{token}").status_code == 404
    assert laptop.get("/api/handoff/nope").status_code == 404


def test_single_use(started):
    laptop, phone = TestClient(main.app), TestClient(main.app)
    token = make(laptop)["token"]
    assert send(phone, token).status_code == 200
    again = send(phone, token)
    assert again.status_code == 404 and again.json()["error"]["code"] == "expired"
    assert len(started) == 1
    assert phone.get(f"/api/handoff/{token}/open").json() == {"open": False}


def test_expiry(started):
    laptop, phone = TestClient(main.app), TestClient(main.app)
    token = make(laptop)["token"]
    routes._tokens[token]["expires"] = 0
    assert laptop.get(f"/api/handoff/{token}").json() == {"status": "expired", "run_id": None}
    assert send(phone, token).status_code == 404
    assert laptop.get(f"/api/handoff/{token}/qr.svg").status_code == 404
    assert phone.get(f"/handoff/{token}").status_code == 200
    assert started == []


def test_bad_file_and_oversize(started):
    laptop, phone = TestClient(main.app), TestClient(main.app)
    token = make(laptop)["token"]
    bad = send(phone, token, b"not an image")
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "unsupported_image"
    big = send(phone, token, b"\0" * (config.MAX_UPLOAD_BYTES + 1))
    assert big.status_code == 413 and big.json()["error"]["code"] == "too_large"
    assert started == []
    assert send(phone, token).status_code == 200


def test_base_falls_back_to_forwarded_headers(started, monkeypatch):
    monkeypatch.delenv("SF_PUBLIC_URL")
    laptop = TestClient(main.app)
    res = laptop.post("/api/handoff", headers={"x-forwarded-proto": "https", "x-forwarded-host": "sf.example"})
    assert res.json()["url"].startswith("https://sf.example/handoff/")
