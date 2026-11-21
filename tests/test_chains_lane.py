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


PUBLIC = [(2, 1, 6, "", ("93.184.216.34", 443))]


def dns(monkeypatch, answers):
    seen = []

    def fake(host, port, *a, **k):
        seen.append(host)
        if isinstance(answers, Exception):
            raise answers
        return [(2, 1, 6, "", (ip, port)) for ip in answers]
    monkeypatch.setattr(chains, "_getaddrinfo", fake)
    return seen


def test_real_fetch_cache_and_threads(monkeypatch):
    """_fetch itself: one request per URL across threads, cached, pinned to the validated address."""
    chains._cache.clear()
    dns(monkeypatch, ["93.184.216.34"])
    n = []

    def opener(host, ip, path):
        n.append((host, ip, path))
        return 200, json.dumps(doc()).encode()
    monkeypatch.setattr(chains, "_open", opener)
    ts = [threading.Thread(target=chains.confirm, args=(dict(BASE), 0)) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert n == [(HOST, "93.184.216.34", "/products/demo-set.js")]
    assert chains.confirm(dict(BASE), 0)["status"] == "confirmed"
    chains._cache.clear()


def test_oversize_and_redirect(monkeypatch):
    chains._cache.clear()
    dns(monkeypatch, ["93.184.216.34"])
    monkeypatch.setattr(chains, "_open", lambda h, ip, p: (200, b"x" * (chains.MAX_BYTES + 5)))
    assert chains.confirm(dict(BASE), 0)["status"] == "unavailable"
    chains._cache.clear()
    monkeypatch.setattr(chains, "_open", lambda h, ip, p: (301, b""))
    out = chains.confirm(dict(BASE), 0)
    assert out["status"] == "unavailable" and "redirect blocked" in out["note"]
    chains._cache.clear()


@pytest.mark.parametrize("url", [
    "https://169.254.169.254/products/x?variant=111", "https://127.0.0.1/products/x?variant=111",
    "https://[::1]/products/x?variant=111", "https://10.0.0.5/products/x?variant=111",
    "https://metadata.google.internal/products/x?variant=111", "https://printer.local/products/x?variant=111",
    "https://localhost/products/x?variant=111", "https://2130706433/products/x?variant=111",
    "https://store.example.com:443@evil/products/x?variant=111"])
def test_internal_hosts_refused_before_any_io(monkeypatch, url):
    chains._cache.clear()
    seen = dns(monkeypatch, ["93.184.216.34"])
    monkeypatch.setattr(chains, "_open", lambda *a: pytest.fail("must not connect"))
    out = chains.confirm(dict(BASE, url=url), 0)
    assert out["status"] == "not_permitted" and seen == []


@pytest.mark.parametrize("answers", [["127.0.0.1"], ["10.1.2.3"], ["169.254.169.254"], ["192.168.0.1"],
                                     ["93.184.216.34", "172.16.0.9"], ["::1"], ["fe80::1"], ["::ffff:127.0.0.1"],
                                     ["0.0.0.0"], ["224.0.0.1"], ["100.64.0.1"], ["240.0.0.1"], []])
def test_dns_to_non_public_refused(monkeypatch, answers):
    chains._cache.clear()
    dns(monkeypatch, answers)
    monkeypatch.setattr(chains, "_open", lambda *a: pytest.fail("must not connect"))
    out = chains.confirm(dict(BASE), 0)
    assert out["status"] == "not_permitted" and out["quote"] is None and "refused" in out["note"]
    assert chains.identity(dict(BASE))["status"] == "not_permitted"
    chains._cache.clear()


def test_dns_failure_refused(monkeypatch):
    chains._cache.clear()
    dns(monkeypatch, OSError("nxdomain"))
    assert chains.confirm(dict(BASE), 0)["status"] == "not_permitted"
    chains._cache.clear()


def test_public_ip_helper():
    assert chains._public_ip("93.184.216.34") and chains._public_ip("2606:4700::1111")
    assert not any(chains._public_ip(x) for x in ["127.0.0.1", "::ffff:10.0.0.1", "fc00::1", "nonsense"])


def test_pinned_connection_revalidates_peer(monkeypatch):
    class Sock:
        closed = False
        def getpeername(self): return ("10.0.0.7", 443)
        def close(self): Sock.closed = True
    monkeypatch.setattr(chains.socket, "create_connection", lambda addr, timeout: Sock())
    c = chains._PinnedHTTPS(HOST, "93.184.216.34", 1)
    with pytest.raises(chains.Refused):
        c.connect()
    assert Sock.closed


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


def test_url_host_must_match_catalog_seller_host(fake):
    calls = fake(doc())
    out = chains.confirm(dict(BASE, seller_domain="other-shop.example.org"), 0)
    assert out["status"] == "not_permitted" and calls == []
    assert chains.confirm(dict(BASE, seller_domain="example.com"), 0)["status"] == "confirmed"
