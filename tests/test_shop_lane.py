"""Shape tests for the shop.dev catalog lane. Fakes exist only here; no network is used.
Amounts below are arbitrary test integers, not merchant prices."""
import pytest

import app  # noqa: F401
from app.merchants import shop


def variant(vid, seller_id, seller_name, title, *, amount=100, currency="USD", available=True,
            condition=("new",), url_host="store.example", **extra):
    v = {"id": f"gid://shopify/ProductVariant/{vid}", "title": title, "handle": f"h{vid}",
         "url": f"https://{url_host}/products/h{vid}?variant={vid}&_gsid=x&utm_source=shopify",
         "price": {"amount": amount, "currency": currency},
         "seller": {"id": f"gid://shopify/Shop/{seller_id}", "name": seller_name,
                    "url": f"https://{url_host}", "domain": f"{seller_id}.myshopify.com"},
         "media": [{"type": "image", "url": f"https://cdn.example/{vid}.jpg"}], "source": "shopify"}
    if condition is not None:
        v["condition"] = list(condition)
    if available is not None:
        v["availability"] = {"available": available}
    v.update(extra)
    return v


def prod(upid, title, variants):
    return {"id": f"gid://shopify/p/{upid}", "title": title, "variants": variants}


META = {"read_at": 1_800_000_000, "effective_at": 1_800_000_000, "response_hash": "h", "tool": "x"}


class FakeCall:
    def __init__(self, search_doc, image_doc=None, products=None, fail=()):
        self.search_doc, self.image_doc, self.products, self.fail = search_doc, image_doc, products or {}, set(fail)
        self.calls = []

    def __call__(self, tool, catalog):
        self.calls.append((tool, catalog))
        if tool == "search_catalog":
            doc = self.image_doc if "like" in catalog and self.image_doc is not None else self.search_doc
            return doc, dict(META, tool=tool)
        if catalog["id"] in self.fail:
            raise shop.SourceError("Shop tool error")
        return {"product": self.products[catalog["id"]]}, dict(META, tool=tool)


def test_rows_from_maps_every_variant():
    doc = {"products": [prod("A", "Acme Kit 10343", [
        variant(1, 11, "One", "Acme Kit 10343"),
        variant(2, 22, "Two", "Acme <b>10343</b>", currency="EUR", available=False, condition=("secondhand",)),
        variant(3, 33, "Three", "Acme 10343", available=None, condition=None, requires={"selling_plan": True}),
    ])], "messages": [{"type": "warning", "code": "x", "content": "disclose"}]}
    rows = shop.rows_from(doc, 123)
    assert len(rows) == 3
    a, b, c = rows
    assert a["seller"] == "gid://shopify/Shop/11" and a["listing"] == "gid://shopify/ProductVariant/1"
    assert a["product"] == "gid://shopify/p/A" and a["seller_domain"] == "store.example"
    assert a["url"] == doc["products"][0]["variants"][0]["url"]  # untouched
    assert (a["source"], a["read_at"], a["gtin"], a["fulfillment"]) == ("shop_native", 123, None, "online_us")
    assert (a["condition"], a["stock"], a["unconditional"], a["currency"]) == ("new", "in_stock", True, "USD")
    assert a["item_cents"] == 100 and a["image_url"] == "https://cdn.example/1.jpg"
    assert (b["condition"], b["stock"], b["currency"]) == ("secondhand", "sold_out", "EUR")  # never converted
    assert b["title"] == "Acme 10343"
    assert (c["condition"], c["stock"], c["unconditional"]) == ("unknown", "unknown", False)
    assert a["warnings"][0]["content"] == "disclose"


def test_rows_from_get_product_doc():
    doc = {"product": prod("A", "Acme", [variant(1, 11, "One", "Acme")])}
    assert [r["product"] for r in shop.rows_from(doc, 1)] == ["gid://shopify/p/A"]


def test_condition_only_new_when_exactly_new():
    assert shop._condition(["new"]) == "new"
    assert shop._condition(["new", "refurbished"]) == "unknown"
    assert shop._condition([]) == "unknown"


def test_token_matching():
    assert shop.has_token("LEGO - Botanicals - Mini Orchid [10343]", "10343")
    assert not shop.has_token("LEGO 103430", "10343")
    assert shop.has_token("Sony WH-1000XM5 Headphones", "WH1000XM5")
    assert shop._mpn_like(shop._tokens("Anker 735 Charger 65W x2")) == ["65W", "735"]


VISUAL = {"name": "Acme Kit", "brand": "Acme", "part_number": "10343", "barcode": "", "variant_details": [],
          "query": "acme kit"}


def search_doc():
    return {"products": [
        prod("Z", "Other thing 999", [variant(9, 99, "Nine", "Other thing 999")]),
        prod("A", "Acme Kit 10343", [variant(1, 11, "One", "Acme Kit 10343")]),
        prod("B", "Acme Kit 10343 (Used)", [variant(5, 55, "Five", "Acme Kit 10343", condition=("secondhand",))]),
    ], "messages": [], "pagination": {"has_next_page": False}}


def test_anchors_for_shapes_order_and_dedupe(monkeypatch):
    cluster = prod("A", "Acme Kit 10343", [variant(1, 11, "One", "Acme Kit 10343"),
                                          variant(2, 22, "Two", "Acme - Kit [10343]"),
                                          variant(3, 33, "Three", "Kit only")])
    fake = FakeCall(search_doc(), image_doc={"products": [prod("C", "Acme 10343 photo", [variant(7, 77, "Seven", "x")])]},
                    products={"gid://shopify/p/A": cluster,
                              "gid://shopify/p/B": prod("B", "Acme Kit 10343 (Used)", [variant(5, 55, "Five", "Acme 10343")]),
                              "gid://shopify/p/C": prod("C", "Acme 10343 photo", [variant(7, 77, "Seven", "x")]),
                              "gid://shopify/p/Z": prod("Z", "Other thing 999", [variant(9, 99, "Nine", "Other")])})
    monkeypatch.setattr(shop._ref, "call", fake)
    res = shop.anchors_for(VISUAL, image=b"\xff\xd8jpeg")
    tools = [t for t, _ in fake.calls]
    assert len(tools) <= shop.MAX_CALLS and tools.count("get_product") <= 4
    q = [c for t, c in fake.calls if t == "search_catalog" and "like" not in c][0]
    assert q["query"] == "Acme 10343 Kit" and q["pagination"]["limit"] == 10
    a0 = res["anchors"][0]
    assert a0["upid"] == "gid://shopify/p/A" and a0["exact_variant_by_mpn"] is True
    assert a0["sellers"] == 3 and "10343" in a0["mpn_tokens"] and a0["gtin"] is None
    assert a0["brand"] == "Acme" and a0["identity_source"] == "Shop catalog product"
    assert a0["required_variant_details"] == [] and len(a0["supporting"]) == 3
    by = {a["upid"]: a for a in res["anchors"]}
    assert by["gid://shopify/p/B"]["exact_variant_by_mpn"] is False  # one seller only
    assert by["gid://shopify/p/C"]["found_by"] == ["image"]
    assert res["anchors"][-1]["upid"] == "gid://shopify/p/Z"  # no token match goes last
    keys = [(r["seller"], r["listing"]) for r in res["rows"]]
    assert len(keys) == len(set(keys)) == 6
    assert all(r["status"] == "ok" and r["source"] == "shop_catalog" for r in res["receipts"])


def test_failed_product_call_is_surfaced(monkeypatch):
    fake = FakeCall(search_doc(), products={}, fail={"gid://shopify/p/A", "gid://shopify/p/B", "gid://shopify/p/Z"})
    monkeypatch.setattr(shop._ref, "call", fake)
    res = shop.anchors_for(VISUAL)
    failed = [r for r in res["receipts"] if r["status"] == "failed"]
    assert len(failed) == 3 and all(r["tool"] == "get_product" for r in failed)
    assert res["anchors"][0]["exact_variant_by_mpn"] is False  # only one seller seen


def test_all_searches_failing_raises(monkeypatch):
    def boom(tool, catalog):
        raise shop.SourceError("Shop tool error")
    monkeypatch.setattr(shop._ref, "call", boom)
    with pytest.raises(shop.ShopError) as e:
        shop.anchors_for(VISUAL)
    assert e.value.receipts[0]["status"] == "failed"


def test_query_falls_back_to_visual_query():
    assert shop._query({"brand": "", "part_number": "", "name": "", "query": "acme kit"}) == "acme kit"


def test_search_clamps_limit(monkeypatch):
    fake = FakeCall({"products": []})
    monkeypatch.setattr(shop._ref, "call", fake)
    shop.search("x", limit=500)
    assert fake.calls[0][1]["pagination"]["limit"] == shop.MAX_LIMIT


def test_anchor_brand_requires_whole_tokens(monkeypatch):
    # bug 006: "Sony" must not become the anchor brand of "Ultrasonyx Bluetooth Speaker SPK-200"
    assert not shop.brand_in_title("Ultrasonyx Bluetooth Speaker SPK-200", "Sony")
    assert shop.brand_in_title("LEGO® Botanicals 10343", "LEGO")
    assert shop.brand_in_title("Z-Man Games Pandemic", "Z-Man Games")
    assert not shop.brand_in_title("Z-Man Pandemic", "Z-Man Games")
    assert not shop.brand_in_title("Anything", "")
    doc = {"products": [prod("S", "Ultrasonyx Bluetooth Speaker SPK-200",
                             [variant(1, 11, "One", "Ultrasonyx Bluetooth Speaker SPK-200")])]}
    fake = FakeCall(doc, products={"gid://shopify/p/S": doc["products"][0]})
    monkeypatch.setattr(shop._ref, "call", fake)
    res = shop.anchors_for({"name": "Speaker", "brand": "Sony", "part_number": "SPK-200", "query": ""})
    assert res["anchors"][0]["brand"] == ""
