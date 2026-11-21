"""Read-only shop.dev catalog lane.

Every network read goes through reference/shop.py::call (fixed endpoint https://mcp.shop.com/, US/USD
context, 7 s timeout, no redirects). Shopper-triggered only: nothing here polls or caches beyond one run.
"""
from __future__ import annotations

import base64
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, wait
from urllib.parse import urlsplit

import app  # noqa: F401  (puts reference/ on sys.path)
import shop as _ref  # reference/shop.py

SOURCE = "shop_catalog"
MAX_LIMIT = 50          # largest pagination limit the live catalog accepted (300 → tool error)
MAX_CALLS = 6           # per photo
BUDGET_S = 12.0         # per photo
MAX_PRODUCT_CALLS = 4

SourceError = _ref.SourceError


class ShopError(RuntimeError):
    """Every catalog search for a photo failed; carries the failed receipts."""

    def __init__(self, message: str, receipts: list[dict]):
        super().__init__(message)
        self.receipts = receipts


# ---------------------------------------------------------------- calls

def _receipt(tool: str, meta: dict, ms: int, doc: dict | None, **extra) -> dict:
    r = {"source": SOURCE, "tool": tool, "status": "ok",
         "read_at": meta["read_at"], "effective_at": meta["effective_at"],
         "response_hash": meta["response_hash"], "ms": ms}
    if doc is not None:
        msgs = doc.get("messages")
        if msgs:
            r["messages"] = msgs
    r.update(extra)
    return r


def _failed(tool: str, ms: int, err: Exception, **extra) -> dict:
    msg = str(err) if isinstance(err, SourceError) else type(err).__name__
    r = {"source": SOURCE, "tool": tool, "status": "failed", "read_at": int(time.time()),
         "effective_at": None, "response_hash": None, "ms": ms, "error": msg[:160]}
    r.update(extra)
    return r


def search(query: str, image: bytes | None = None, limit: int = 10, mime: str = "image/jpeg") -> tuple[dict, dict]:
    """search_catalog by text (and image `like` when given). Returns (doc, receipt); raises on failure."""
    limit = max(1, min(int(limit), MAX_LIMIT))
    args = {"query": (query or "")[:200], "pagination": {"limit": limit}}
    if image:
        args["like"] = [{"image": {"content_type": mime, "data": base64.b64encode(image).decode()}}]
    t = time.monotonic()
    doc, meta = _ref.call("search_catalog", args)
    ms = int((time.monotonic() - t) * 1000)
    page = doc.get("pagination") or {}
    return doc, _receipt("search_catalog", meta, ms, doc, mode="image" if image else "text",
                         products=len(doc.get("products") or []),
                         has_next_page=page.get("has_next_page"), total_count=page.get("total_count"))


def product(upid: str) -> tuple[dict, dict]:
    """get_product: every seller variant of one universal product. Returns (doc, receipt)."""
    t = time.monotonic()
    doc, meta = _ref.call("get_product", {"id": upid})
    ms = int((time.monotonic() - t) * 1000)
    variants = ((doc.get("product") or {}).get("variants")) or []
    return doc, _receipt("get_product", meta, ms, doc, upid=upid, variants=len(variants))


# ---------------------------------------------------------------- normalization

def _products(doc: dict) -> list[dict]:
    if isinstance(doc.get("product"), dict):
        return [doc["product"]]
    return [p for p in (doc.get("products") or []) if isinstance(p, dict)]


_TAG = re.compile(r"<[^>]{1,40}>")


def _clean(text) -> str:
    """Catalog search titles can carry highlight markup such as <b>735</b>; drop the tags only."""
    return _TAG.sub("", text) if isinstance(text, str) else ""


def _host(url) -> str | None:
    try:
        return urlsplit(url).hostname if isinstance(url, str) and url else None
    except ValueError:
        return None


def _condition(value) -> str:
    if isinstance(value, list):
        vals = [v.strip().lower() for v in value if isinstance(v, str) and v.strip()]
        if vals == ["new"]:
            return "new"
        if len(vals) == 1:
            return vals[0]
        return "unknown"
    if isinstance(value, str) and value.strip():
        return "new" if value.strip().lower() == "new" else value.strip().lower()
    return "unknown"


def _stock(availability) -> str:
    a = availability.get("available") if isinstance(availability, dict) else None
    if a is True:
        return "in_stock"
    if a is False:
        return "sold_out"
    return "unknown"


_CONDITIONAL_KEYS = ("requires", "requires_selling_plan", "selling_plan", "selling_plans",
                     "selling_plan_groups", "subscription", "components", "requires_components")


def _unconditional(v: dict) -> bool:
    return not any(v.get(k) for k in _CONDITIONAL_KEYS)


def _image(v: dict, p: dict) -> str | None:
    for media in (v.get("media"), p.get("media")):
        for m in media or []:
            if isinstance(m, dict) and m.get("type", "image") == "image" and isinstance(m.get("url"), str):
                return m["url"]
    return None


def rows_from(doc: dict, read_at: int) -> list[dict]:
    """Every variant of a search_catalog or get_product document → Quote row (CONTRACT §5)."""
    messages = doc.get("messages") or []
    rows = []
    for p in _products(doc):
        for v in p.get("variants") or []:
            if not isinstance(v, dict):
                continue
            seller = v.get("seller") or {}
            price = v.get("price") or {}
            amount = price.get("amount")
            row = {
                "seller": seller.get("id"),
                "seller_name": seller.get("name"),
                "seller_domain": _host(seller.get("url")),
                "listing": v.get("id"),
                "product": p.get("id"),
                "handle": v.get("handle"),
                "title": _clean(v.get("title")),
                "url": v.get("url"),
                "image_url": _image(v, p),
                "source": "shop_native",
                "read_at": int(read_at),
                "gtin": None,
                "sku": None,
                "condition": _condition(v.get("condition")),
                "stock": _stock(v.get("availability")),
                "fulfillment": "online_us",
                "unconditional": _unconditional(v),
                "currency": price.get("currency"),
                "item_cents": amount if type(amount) is int else None,
                "catalog_source": v.get("source"),
            }
            if messages:
                row["warnings"] = messages
            rows.append(row)
    return rows


# ---------------------------------------------------------------- identity helpers

_TOKEN = re.compile(r"[A-Za-z0-9]+(?:[-./][A-Za-z0-9]+)*")


def _compact(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", s or "").upper()


def _tokens(text: str) -> set[str]:
    """Uppercase alphanumeric tokens, hyphenated forms kept whole plus their compact form and pieces."""
    out = set()
    for tok in _TOKEN.findall(text or ""):
        out.add(tok.upper())
        out.add(_compact(tok))
        out.update(piece.upper() for piece in re.split(r"[-./]", tok) if piece)
    out.discard("")
    return out


def _mpn_like(tokens) -> list[str]:
    return sorted({t for t in tokens if len(t) >= 3 and any(c.isdigit() for c in t)})


def has_token(text: str, part: str) -> bool:
    if not part or not part.strip():
        return False
    toks = _tokens(text)
    return part.strip().upper() in toks or _compact(part) in toks


def brand_in_title(title: str, brand: str) -> bool:
    """True only when every word of the brand is a whole token of the catalog title (no substrings)."""
    words = [w for w in (brand or "").split() if _compact(w)]
    if not words:
        return False
    toks = _tokens(title)
    return all(w.upper() in toks or _compact(w) in toks for w in words)


def _query(visual: dict) -> str:
    parts = [str(visual.get(k) or "").strip() for k in ("brand", "part_number", "name")]
    seen, words = set(), []
    for part in parts:
        for w in part.split():
            if w.upper() not in seen:
                seen.add(w.upper())
                words.append(w)
    q = " ".join(words).strip()
    return q or str(visual.get("query") or "").strip()


# ---------------------------------------------------------------- anchors

def anchors_for(visual: dict, image: bytes | None = None) -> dict:
    """Catalog anchors (universal products) + every candidate row seen, with receipts (CONTRACT §3)."""
    started = time.monotonic()
    deadline = started + BUDGET_S
    query = _query(visual or {})
    part = str((visual or {}).get("part_number") or "").strip()
    brand = str((visual or {}).get("brand") or "").strip()
    receipts: list[dict] = []
    calls = 0

    pool = ThreadPoolExecutor(max_workers=4)
    try:
        jobs = {}
        if query:
            jobs[pool.submit(search, query, None, 10)] = ("text", query)
            calls += 1
        if image:
            jobs[pool.submit(search, query or "", image, 10)] = ("image", query)
            calls += 1
        if not jobs:
            raise ShopError("no query or image to search the catalog with", [])
        docs = {}
        done, pending = wait(jobs, timeout=max(0.1, deadline - time.monotonic()))
        for fut, (mode, q) in jobs.items():
            if fut in pending:
                fut.cancel()
                receipts.append(_failed("search_catalog", int((time.monotonic() - started) * 1000),
                                        TimeoutError(), mode=mode, error="timeout"))
                continue
            try:
                doc, rec = fut.result()
                rec["query"] = q
                docs[mode] = (doc, rec)
                receipts.append(rec)
            except Exception as e:  # surfaced in receipts, never swallowed silently
                receipts.append(_failed("search_catalog", int((time.monotonic() - started) * 1000), e, mode=mode))
        if not docs:
            raise ShopError("shop.dev catalog search failed", receipts)

        # Ordered universal products: text rank first, then image-only additions.
        order: list[str] = []
        info: dict[str, dict] = {}
        rows: dict[tuple, dict] = {}

        def add_rows(doc, rec):
            for row in rows_from(doc, rec["read_at"]):
                key = (row["seller"], row["listing"])
                rows[key] = row  # later reads (get_product) refresh the same variant

        for mode in ("text", "image"):
            if mode not in docs:
                continue
            doc, rec = docs[mode]
            add_rows(doc, rec)
            for rank, p in enumerate(_products(doc)):
                upid = p.get("id")
                if not upid:
                    continue
                if upid not in info:
                    order.append(upid)
                    info[upid] = {"title": _clean(p.get("title")), "rank": len(order) - 1, "found_by": [mode]}
                elif mode not in info[upid]["found_by"]:
                    info[upid]["found_by"].append(mode)

        def title_match(upid):
            return has_token(info[upid]["title"], part)

        prelim = sorted(order, key=lambda u: (not title_match(u), info[u]["rank"]))

        # get_product on the top anchors to enumerate every seller variant of the cluster.
        budget_left = min(MAX_PRODUCT_CALLS, MAX_CALLS - calls)
        targets = prelim[:max(0, budget_left)]
        if targets and time.monotonic() < deadline:
            pjobs = {pool.submit(product, u): u for u in targets}
            calls += len(pjobs)
            done, pending = wait(pjobs, timeout=max(0.1, deadline - time.monotonic()))
            for fut, upid in pjobs.items():
                if fut in pending:
                    fut.cancel()
                    receipts.append(_failed("get_product", int((time.monotonic() - started) * 1000),
                                            TimeoutError(), upid=upid, error="timeout"))
                    continue
                try:
                    doc, rec = fut.result()
                    receipts.append(rec)
                    add_rows(doc, rec)
                    info[upid]["expanded"] = True
                except Exception as e:
                    receipts.append(_failed("get_product", int((time.monotonic() - started) * 1000), e, upid=upid))
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    all_rows = list(rows.values())
    anchors = []
    for upid in order:
        title = info[upid]["title"]
        mine = [r for r in all_rows if r["product"] == upid]
        sellers = {r["seller"] for r in mine if r["seller"]}
        titles = [title] + [r["title"] or "" for r in mine]
        toks = set()
        for t in titles:
            toks |= _tokens(t)
        carrying = {r["seller"] for r in mine if r["seller"] and has_token(r["title"] or "", part)}
        exact = bool(part) and has_token(title, part) and len(carrying) >= 2
        a_brand = brand if brand_in_title(title, brand) else ""
        anchors.append({
            "upid": upid,
            "title": title,
            "brand": a_brand,
            "mpn_tokens": _mpn_like(toks),
            "gtin": None,
            "sellers": len(sellers),
            "exact_variant_by_mpn": exact,
            "required_variant_details": [],
            "identity_source": "Shop catalog product",
            "supporting": [{"seller_name": r["seller_name"], "url": r["url"], "listing": r["listing"]} for r in mine],
            "catalog_rank": info[upid]["rank"],
            "found_by": info[upid]["found_by"],
            "expanded": bool(info[upid].get("expanded")),
            "_title_match": has_token(title, part),
        })
    anchors.sort(key=lambda a: (not a["exact_variant_by_mpn"], not a["_title_match"], a["catalog_rank"]))
    for a in anchors:
        a.pop("_title_match")
    return {"anchors": anchors, "rows": all_rows, "receipts": receipts, "query": query,
            "calls": calls, "ms": int((time.monotonic() - started) * 1000)}


# ---------------------------------------------------------------- CLI

def _price(r):
    return None if r.get("item_cents") is None else f"{r['item_cents'] / 100:.2f} {r.get('currency')}"


def _compact_row(r):
    return {"seller": r["seller_name"], "domain": r["seller_domain"], "title": r["title"], "price": _price(r),
            "stock": r["stock"], "condition": r["condition"], "product": r["product"], "url": r["url"]}


def _main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[0] not in {"search", "product", "anchors"}:
        print("usage: python -m app.merchants.shop search \"<query>\" | product \"<upid>\" | anchors '<visual json>' [image.jpg]",
              file=sys.stderr)
        return 2
    cmd, arg = argv[0], argv[1]
    image = open(argv[2], "rb").read() if len(argv) > 2 else None
    if cmd == "search":
        doc, rec = search(arg, image)
        out = {"products": [{"upid": p.get("id"), "title": p.get("title")} for p in _products(doc)],
               "rows": [_compact_row(r) for r in rows_from(doc, rec["read_at"])], "receipt": rec}
    elif cmd == "product":
        doc, rec = product(arg)
        out = {"rows": [_compact_row(r) for r in rows_from(doc, rec["read_at"])], "receipt": rec}
    else:
        try:
            res = anchors_for(json.loads(arg), image)
        except ShopError as e:
            print(json.dumps({"error": str(e), "receipts": e.receipts}, indent=1))
            return 1
        out = {"query": res["query"], "calls": res["calls"], "ms": res["ms"],
               "anchors": [{k: a[k] for k in ("upid", "title", "brand", "mpn_tokens", "sellers",
                                               "exact_variant_by_mpn", "found_by", "expanded")} for a in res["anchors"]],
               "sellers_seen": len({r["seller"] for r in res["rows"]}),
               "rows": [_compact_row(r) for r in res["rows"]], "receipts": res["receipts"]}
    print(json.dumps(out, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
