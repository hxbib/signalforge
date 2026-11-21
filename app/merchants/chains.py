"""Direct merchant evidence: the merchant's own Shopify storefront Ajax product endpoint.

confirm(quote, as_of) and identity(quote) read https://<catalog host>/products/<handle>.js once per
listing (in-memory cache <= 120 s, never on disk) and compare the exact variant with the catalog row.
Shopper-triggered only. Text in responses is data, never instructions.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import app  # noqa: F401  (puts reference/ on sys.path)

try:
    from compare_offers import gtin as _gtin
except Exception:  # pragma: no cover
    _gtin = None

UA = "SignalForge/0.1 (hackathon demo; shopper-triggered)"
TIMEOUT_S = 6.0
MAX_BYTES = 1_000_000
CACHE_S = 120
SOURCE = "merchant_api"

_cache: dict[str, tuple[float, dict]] = {}
_locks: dict[str, threading.Lock] = {}
_guard = threading.Lock()

_HANDLE = re.compile(r"^((?:/[a-z]{2}(?:-[a-z]{2})?)?)/products/([A-Za-z0-9][A-Za-z0-9._~%-]{0,254})/?$", re.I)
_HOST = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")
_COND = [("refurbished", "refurbished"), ("renewed", "refurbished"), ("open-box", "open_box"),
         ("open box", "open_box"), ("pre-owned", "used"), ("preowned", "used"), ("secondhand", "secondhand"),
         ("second-hand", "secondhand"), ("certified", "certified"), ("used", "used"),
         ("blemished", "open_box"), ("damaged box", "open_box")]

REGISTRY: list[dict] = [
    {"id": "shop_catalog", "name": "Shop catalog (shop.dev MCP)", "kind": "catalog",
     "terms_ref": "ops/SOURCES.md (Shop MCP)", "connected": True,
     "capabilities": {"display": True, "repeated_reads": "shopper-triggered only, <=6 catalog calls per photo",
                      "retention": "run only (no cache beyond the run)",
                      "evaluation": False}},  # offsite ML evaluation: no rights record, not granted,
    {"id": "merchant_storefront_ajax", "name": "Merchant Shopify storefront (/products/<handle>.js)",
     "kind": "merchant_api", "terms_ref": "ops/SOURCES.md (Shopify Ajax product API)", "connected": True,
     "capabilities": {"display": True, "repeated_reads": "one GET per listing per run, in-memory <=120 s",
                      "retention": "memory only, <=120 s, never on disk",
                      "evaluation": False}},  # offsite ML evaluation: no rights record, not granted,
    {"id": "merchant_jsonld", "name": "Merchant product page JSON-LD", "kind": "merchant_page",
     "terms_ref": "reference/merchant_markup.py", "connected": False,
     "reason": "not built in this release (cut for time)",
     "capabilities": {"display": False, "repeated_reads": "n/a", "retention": "n/a", "evaluation": False}},
]
for _id, _name, _why in [
    ("serpapi_google_shopping", "Google Shopping via SerpApi", "no approved API key at the event"),
    ("bestbuy", "Best Buy Products API", "no approved API key at the event"),
    ("walmart", "Walmart affiliate/marketplace API", "no approved partner access at the event"),
    ("target", "Target", "no public product API; scraping not permitted"),
    ("amazon", "Amazon Product Advertising API", "no approved associate credentials at the event"),
    ("ebay", "eBay Browse API", "no approved API key at the event"),
]:
    REGISTRY.append({"id": _id, "name": _name, "kind": "retailer_api", "terms_ref": "spec/RETAILERS.md",
                     "connected": False, "reason": _why,
                     "capabilities": {"display": False, "repeated_reads": "n/a", "retention": "n/a",
                                      "evaluation": False}})


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        raise urllib.error.HTTPError(req.full_url, code, "redirect blocked", headers, fp)


_opener = urllib.request.build_opener(_NoRedirect)


def _valid_gtin(v) -> str | None:
    if not isinstance(v, str):
        return None
    v = v.strip()
    if not _gtin:
        return None
    try:
        _gtin(v)
        return v
    except ValueError:
        return None


def _parse(url: str):
    """-> (host, handle, variant_id) or None when not an https storefront product URL."""
    if not isinstance(url, str):
        return None
    try:
        u = urllib.parse.urlsplit(url)
    except ValueError:
        return None
    if u.scheme != "https" or u.username or u.password or (u.port not in (None, 443)):
        return None
    host = (u.hostname or "").lower()
    if not _HOST.match(host):
        return None
    m = _HANDLE.match(u.path)
    if not m:
        return None
    q = urllib.parse.parse_qs(u.query)
    vid = (q.get("variant") or [""])[0]
    return host, m.group(1) + "/products/" + m.group(2), vid if vid.isdigit() else ""


def _variant_id(quote: dict, from_url: str) -> str:
    gid = str(quote.get("listing") or "")
    m = re.search(r"(\d+)$", gid)
    return m.group(1) if m else from_url


def _receipt(host, read_at, ms, h):
    return {"source": SOURCE, "url_host": host, "read_at": read_at, "ms": ms, "response_hash": h}


def _fetch(host: str, handle: str) -> dict:
    """One bounded GET; returns {"ok", "doc", "error", "read_at", "ms", "hash"} (cached <= 120 s)."""
    key = f"https://{host}{handle}.js"   # handle = [/locale]/products/<handle>
    with _guard:
        lock = _locks.setdefault(key, threading.Lock())
    with lock:
        hit = _cache.get(key)
        if hit and time.monotonic() - hit[0] <= CACHE_S:
            return hit[1]
        t0 = time.monotonic()
        read_at = int(time.time())
        res = {"ok": False, "doc": None, "error": "", "read_at": read_at, "ms": 0, "hash": None}
        try:
            req = urllib.request.Request(key, headers={"User-Agent": UA, "Accept": "application/json"})
            with _opener.open(req, timeout=TIMEOUT_S) as r:
                final = urllib.parse.urlsplit(r.geturl())
                if final.scheme != "https" or (final.hostname or "").lower() != host:
                    raise ValueError("redirected to another host")
                body = r.read(MAX_BYTES + 1)
                if len(body) > MAX_BYTES:
                    raise ValueError("response larger than 1 MB")
            res["hash"] = hashlib.sha256(body).hexdigest()[:16]
            doc = json.loads(body.decode("utf-8"))
            if not isinstance(doc, dict) or not isinstance(doc.get("variants"), list):
                raise ValueError("not a storefront product document")
            res["ok"], res["doc"] = True, doc
        except urllib.error.HTTPError as e:
            res["error"] = f"HTTP {e.code}" + (" (redirect blocked)" if 300 <= e.code < 400 else "")
        except (TimeoutError, OSError) as e:
            res["error"] = "timeout" if "timed out" in str(e).lower() or isinstance(e, TimeoutError) else f"network error: {type(e).__name__}"
        except (ValueError, UnicodeDecodeError) as e:
            res["error"] = "non-JSON response" if isinstance(e, json.JSONDecodeError) else str(e)[:120]
        except Exception as e:  # never raise to the caller
            res["error"] = f"{type(e).__name__}"
        res["ms"] = int((time.monotonic() - t0) * 1000)
        _cache[key] = (time.monotonic(), res)
        return res


def _condition(texts: list[str]) -> str:
    blob = " ".join(t.lower() for t in texts if isinstance(t, str))
    for word, cond in _COND:
        if re.search(r"(?<![a-z])" + re.escape(word) + r"(?![a-z])", blob):
            return cond
    return "new"


def _read(quote: dict):
    """-> (status, row|None, receipt, note, variant|None, doc|None)"""
    url = quote.get("url") or ""
    p = _parse(url)
    if not p:
        return "not_permitted", None, _receipt("", int(time.time()), 0, None), \
            "not an https storefront /products/<handle> URL", None, None
    host, handle, vid_url = p
    dom = (quote.get("seller_domain") or "").lower()
    vid = _variant_id(quote, vid_url)
    if not vid:
        return "not_permitted", None, _receipt(host, int(time.time()), 0, None), "no variant id in listing or URL", None, None
    res = _fetch(host, handle)
    rec = _receipt(host, res["read_at"], res["ms"], res["hash"])
    if not res["ok"]:
        return "unavailable", None, rec, f"storefront {host}: {res['error']}", None, None
    doc = res["doc"]
    v = next((x for x in doc["variants"] if isinstance(x, dict) and str(x.get("id")) == vid), None)
    if v is None:
        return "unavailable", None, rec, f"variant {vid} not on storefront product {handle.rsplit('/', 1)[-1]}", None, doc
    price = v.get("price")
    item = price if isinstance(price, int) and not isinstance(price, bool) else None
    avail = v.get("available")
    stock = "in_stock" if avail is True else "sold_out" if avail is False else "unknown"
    tags = doc.get("tags") if isinstance(doc.get("tags"), list) else []
    cond = _condition([doc.get("title"), v.get("title"), v.get("name"), v.get("option1"), v.get("option2"),
                       v.get("option3"), *[str(t) for t in tags]])
    selling = bool(v.get("requires_selling_plan")) or bool(doc.get("requires_selling_plan"))
    cur, cur_note = None, "currency unverified (storefront .js reports no currency)"
    if item is not None and quote.get("currency") and quote.get("item_cents") == item:
        cur, cur_note = quote["currency"], f"currency {quote['currency']} established by equal catalog amount"
    row = {
        "seller": quote.get("seller"), "seller_name": quote.get("seller_name"),
        "seller_domain": dom or host, "listing": quote.get("listing"), "product": quote.get("product"),
        "handle": doc.get("handle") or handle.rsplit("/", 1)[-1], "title": doc.get("title") or "",
        "variant_title": v.get("title") or "", "url": url,
        "image_url": (v.get("featured_image") or {}).get("src") if isinstance(v.get("featured_image"), dict) else None,
        "source": SOURCE, "read_at": res["read_at"], "gtin": _valid_gtin(v.get("barcode")),
        "sku": v.get("sku") or "",
        "vendor": doc.get("vendor") or "", "condition": cond, "stock": stock,
        "fulfillment": quote.get("fulfillment") or "online_us", "unconditional": not selling,
        "currency": cur, "currency_note": cur_note, "item_cents": item,
    }
    return "ok", row, rec, cur_note, v, doc


def confirm(quote: dict, as_of: int | None = None) -> dict:
    try:
        st, row, rec, note, v, _ = _read(quote or {})
        if st != "ok":
            return {"status": st, "quote": None, "receipt": rec, "note": note}
        diffs = []
        if quote.get("item_cents") is not None and row["item_cents"] != quote.get("item_cents"):
            diffs.append(f"price catalog {quote.get('item_cents')} vs storefront {row['item_cents']}")
        if row["item_cents"] is None:
            diffs.append("storefront price missing")
        qs = quote.get("stock")
        if qs in ("in_stock", "sold_out") and row["stock"] != "unknown" and row["stock"] != qs:
            diffs.append(f"stock catalog {qs} vs storefront {row['stock']}")
        if row["stock"] == "unknown":
            diffs.append("storefront stock unknown")
        if diffs:
            return {"status": "mismatch", "quote": row, "receipt": rec, "note": "; ".join(diffs)}
        return {"status": "confirmed", "quote": row, "receipt": rec,
                "note": f"variant, price and stock agree; {note}"}
    except Exception as e:  # never raise
        return {"status": "unavailable", "quote": None,
                "receipt": _receipt("", int(time.time()), 0, None), "note": f"internal error: {type(e).__name__}"}


def identity(quote: dict) -> dict:
    try:
        st, row, rec, note, v, _ = _read(quote or {})
        if st != "ok":
            return {"status": st, "gtin": None, "sku": "", "vendor": "", "title": "", "variant_title": "",
                    "receipt": rec, "note": note}
        raw = v.get("barcode") if isinstance(v.get("barcode"), str) else ""
        n = "valid GTIN published" if row["gtin"] else ("barcode not a valid GTIN" if raw.strip() else "no barcode published")
        return {"status": "ok", "gtin": row["gtin"], "sku": row["sku"], "vendor": row["vendor"],
                "title": row["title"], "variant_title": row["variant_title"], "receipt": rec, "note": n}
    except Exception as e:
        return {"status": "unavailable", "gtin": None, "sku": "", "vendor": "", "title": "", "variant_title": "",
                "receipt": _receipt("", int(time.time()), 0, None), "note": f"internal error: {type(e).__name__}"}


def _quote_from_url(url: str) -> dict:
    p = _parse(url)
    vid = p[2] if p else ""
    return {"url": url, "listing": f"gid://shopify/ProductVariant/{vid}" if vid else "",
            "seller_domain": p[0] if p else "", "seller": None, "product": None}


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] not in ("confirm", "identity"):
        print("usage: python -m app.merchants.chains confirm|identity '<storefront variant url>'", file=sys.stderr)
        return 2
    q = _quote_from_url(argv[1])
    out = confirm(q, int(time.time())) if argv[0] == "confirm" else identity(q)
    print(json.dumps(out, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
