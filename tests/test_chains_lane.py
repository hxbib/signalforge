"""RETAIL lane: shape fakes only (no real merchant prices). The network read is replaced by a fake."""
import json
import threading

import pytest

import app  # noqa: F401
from app.merchants import chains

HOST = "store.example.com"
URL = f"https://{HOST}/products/demo-set?variant=111&_gsid=x"
BASE = {"seller": "gid://shopify/Shop/1", "seller_name": "Demo", "seller_domain": HOST,
        "listing": "gid://shopify/ProductVariant/111", "product": "gid://shopify/p/abc",
        "url": URL, "item_cents": 1000, "currency": "USD", "stock": "in_stock", "source": "shop_native"}


def doc(**v):
    var = {"id": 111, "title": "Default Title", "price": 1000, "available": True,
           "barcode": "0673419404235", "sku": "SKU-1"}
    var.update(v)
    return {"handle": "demo-set", "title": "Demo Set", "vendor": "DemoCo", "tags": [],
            "variants": [{"id": 222, "price": 1, "available": False}, var]}


@pytest.fixture
def fake(monkeypatch):
    calls = []

    def install(result):
        def _fetch(host, handle):
            calls.append((host, handle))
            r = {"ok": True, "doc": result, "error": "", "read_at": 1700000000, "ms": 5, "hash": "h"}
            if isinstance(result, str):
                r.update(ok=False, doc=None, error=result, hash=None)
            return r
        monkeypatch.setattr(chains, "_fetch", _fetch)
        return calls
    return install


def test_confirmed_shape(fake):
    calls = fake(doc())
    out = chains.confirm(dict(BASE), 1700000000)
    assert out["status"] == "confirmed" and calls == [(HOST, "/products/demo-set")]
    q = out["quote"]
    assert (q["seller"], q["listing"], q["product"]) == (BASE["seller"], BASE["listing"], BASE["product"])
    assert q["source"] == "merchant_api" and q["item_cents"] == 1000 and q["currency"] == "USD"
    assert q["stock"] == "in_stock" and q["condition"] == "new" and q["unconditional"] is True
    assert q["gtin"] == "0673419404235" and q["sku"] == "SKU-1"
    assert set(out["receipt"]) == {"source", "url_host", "read_at", "ms", "response_hash"}
    assert out["receipt"]["url_host"] == HOST


def test_price_mismatch_keeps_both_and_no_currency(fake):
    fake(doc(price=900))
    out = chains.confirm(dict(BASE), 0)
    assert out["status"] == "mismatch" and "1000" in out["note"] and "900" in out["note"]
    assert out["quote"]["currency"] is None


def test_stock_mismatch(fake):
    fake(doc(available=False))
    out = chains.confirm(dict(BASE), 0)
    assert out["status"] == "mismatch" and "sold_out" in out["note"]


def test_missing_variant_unavailable(fake):
    fake(doc(id=999))
    out = chains.confirm(dict(BASE), 0)
    assert out["status"] == "unavailable" and out["quote"] is None


@pytest.mark.parametrize("err", ["HTTP 404", "timeout", "non-JSON response", "HTTP 403"])
def test_errors_unavailable(fake, err):
    fake(err)
    out = chains.confirm(dict(BASE), 0)
    assert out["status"] == "unavailable" and out["quote"] is None and err in out["note"]
    assert chains.identity(dict(BASE))["status"] == "unavailable"


@pytest.mark.parametrize("url", ["http://store.example.com/products/x?variant=111",
                                 "https://store.example.com/collections/x",
                                 "https://user:pw@store.example.com/products/x?variant=111",
                                 "https://store.example.com:8443/products/x?variant=111",
                                 "ftp://store.example.com/products/x", "", None])
def test_not_permitted(fake, url):
    calls = fake(doc())
    out = chains.confirm(dict(BASE, url=url), 0)
    assert out["status"] == "not_permitted" and calls == []


def test_condition_and_selling_plan(fake):
    fake(doc(title="Certified Pre-Owned", requires_selling_plan=True))
    q = chains.confirm(dict(BASE), 0)["quote"]
    assert q["condition"] in ("used", "certified") and q["unconditional"] is False
    fake(dict(doc(), tags=["Refurbished"]))
    assert chains.confirm(dict(BASE), 0)["quote"]["condition"] == "refurbished"


def test_identity_invalid_barcode(fake):
    fake(doc(barcode="12345"))
    out = chains.identity(dict(BASE))
    assert out["status"] == "ok" and out["gtin"] is None and out["sku"] == "SKU-1"
    assert out["vendor"] == "DemoCo" and out["title"] == "Demo Set"


def test_never_raises(monkeypatch):
    monkeypatch.setattr(chains, "_fetch", lambda h, p: 1 / 0)
    assert chains.confirm(dict(BASE), 0)["status"] == "unavailable"
    assert chains.identity(dict(BASE))["status"] == "unavailable"
    assert chains.confirm(None, 0)["status"] in ("not_permitted", "unavailable")


def test_real_fetch_cache_and_threads(monkeypatch):
    """_fetch itself: one request per URL across threads, cached, bounded."""
    chains._cache.clear()
    n = []

    class Resp:
        def __init__(self, body): self.body = body
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def geturl(self): return f"https://{HOST}/products/demo-set.js"
        def read(self, k): return self.body[:k]

    def opener(req, timeout):
        n.append(req.full_url)
        assert req.get_header("User-agent") == chains.UA and timeout == chains.TIMEOUT_S
        return Resp(json.dumps(doc()).encode())
    monkeypatch.setattr(chains._opener, "open", opener)
    ts = [threading.Thread(target=chains.confirm, args=(dict(BASE), 0)) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert n == [f"https://{HOST}/products/demo-set.js"]
    assert chains.confirm(dict(BASE), 0)["status"] == "confirmed"
    chains._cache.clear()


def test_oversize_and_redirect(monkeypatch):
    chains._cache.clear()

    class Resp:
        def __init__(self, body, url): self.body, self.url = body, url
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def geturl(self): return self.url
        def read(self, k): return self.body[:k]
    monkeypatch.setattr(chains._opener, "open",
                        lambda req, timeout: Resp(b"x" * (chains.MAX_BYTES + 5), req.full_url))
    assert chains.confirm(dict(BASE), 0)["status"] == "unavailable"
    chains._cache.clear()
    monkeypatch.setattr(chains._opener, "open",
                        lambda req, timeout: Resp(json.dumps(doc()).encode(), "https://evil.example.net/x.js"))
    out = chains.confirm(dict(BASE), 0)
    assert out["status"] == "unavailable" and "another host" in out["note"]
    chains._cache.clear()


def test_registry_shape():
    ids = {r["id"] for r in chains.REGISTRY}
    assert {"shop_catalog", "merchant_storefront_ajax", "merchant_jsonld", "serpapi_google_shopping",
            "bestbuy", "walmart", "target", "amazon", "ebay"} <= ids
    for r in chains.REGISTRY:
        assert {"id", "name", "kind", "terms_ref", "connected", "capabilities"} <= set(r)
        assert set(r["capabilities"]) == {"display", "repeated_reads", "retention", "evaluation"}
        if not r["connected"]:
            assert r.get("reason")


def test_locale_path(fake):
    calls = fake(doc())
    out = chains.confirm(dict(BASE, url=f"https://{HOST}/en-us/products/demo-set?variant=111"), 0)
    assert out["status"] == "confirmed" and calls == [(HOST, "/en-us/products/demo-set")]
