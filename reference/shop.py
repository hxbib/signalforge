"""Read-only Shop MCP connector. The only network destination is the fixed Shopify endpoint."""
import base64
import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from core import Seen, integer

ENDPOINT = "https://mcp.shop.com/"
PROTOCOL = "2026-07-28"
CONTEXT = {"address_country": "US", "currency": "USD"}


class SourceError(ValueError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def call(tool, catalog):
    if tool not in {"search_catalog", "get_product", "lookup_catalog"}:
        raise SourceError("unsupported operation")
    rid = uuid.uuid4().hex
    meta = {"io.modelcontextprotocol/protocolVersion": PROTOCOL,
            "io.modelcontextprotocol/clientInfo": {"name": "signalforge", "version": "0.1"},
            "io.modelcontextprotocol/clientCapabilities": {}}
    params = {"name": tool, "arguments": {"catalog": {**catalog, "context": CONTEXT}}, "_meta": meta}
    body = json.dumps({"jsonrpc": "2.0", "id": rid, "method": "tools/call", "params": params}).encode()
    if len(body) > 4 * 1024 * 1024:
        raise SourceError("image request too large")
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
               "MCP-Protocol-Version": PROTOCOL, "Mcp-Method": "tools/call", "Mcp-Name": tool}
    request = Request(ENDPOINT, data=body, headers=headers, method="POST")
    with build_opener(NoRedirect).open(request, timeout=7) as response:
        raw = response.read(2*1024*1024+1)
        if len(raw) > 2*1024*1024:
            raise SourceError("oversize response")
        age = response.headers.get("Age", "0")
        if not age.isdecimal() or int(age) > 90:
            raise SourceError("stale response")
    payload = json.loads(raw)
    result = payload.get("result") or {}
    if payload.get("id") != rid or "error" in payload or result.get("isError"):
        raise SourceError("Shop tool error")
    doc = result.get("structuredContent")
    if not isinstance(doc, dict):
        raise SourceError("unrecognized response")
    read_at = int(time.time())
    return doc, {"read_at": read_at, "effective_at": read_at-int(age), "response_hash": hashlib.sha256(raw).hexdigest(), "tool": tool}


def search(query, image=None, mime="image/jpeg"):
    args = {"query": query[:200], "pagination": {"limit": 5}}
    if image:
        args["like"] = [{"image": {"content_type": mime, "data": base64.b64encode(image).decode()}}]
    # Do not filter out sold-out or expensive items. Those may be precisely what the user wants to watch.
    return call("search_catalog", args)


def get_product(product, choices):
    return call("get_product", {"id": product, "selected": choices})


def options(value):
    if not isinstance(value, list):
        raise SourceError("option list missing")
    out = {}
    for item in value:
        if not isinstance(item, dict):
            raise SourceError("invalid option")
        n, v = item.get("name"), item.get("label")
        if not isinstance(n,str) or not n or not isinstance(v,str) or not v or n in out:
            raise SourceError("invalid or duplicate option")
        out[n] = v
    return out


@dataclass(frozen=True)
class Binding:
    product: str
    seller: str
    variant: str
    choices: tuple
    url: str

    def selected(self):
        return [{"name": n, "label": v} for n,v in self.choices]


def exact(doc, product_id, requested, t, pinned=None):
    product = doc.get("product") or {}
    want = options(requested)
    if product.get("id") != product_id or options(product.get("selected")) != want:
        raise SourceError("catalog relaxed or changed the requested selection")
    matches = [v for v in product.get("variants", []) if options(v.get("options")) == want]
    if len(matches) != 1:
        raise SourceError("no unique exact variant")
    v = matches[0]
    seller, vid, url = (v.get("seller") or {}).get("domain"), v.get("id"), v.get("url")
    if not all(isinstance(s,str) and s for s in (seller, vid, url)) or urlsplit(url).scheme != "https" or not urlsplit(url).hostname:
        raise SourceError("missing catalog identity or merchant link")
    bound = Binding(product_id, seller, vid, tuple(sorted(want.items())), url)
    if pinned and (pinned.product, pinned.seller, pinned.variant, pinned.choices) != (product_id, seller, vid, bound.choices):
        raise SourceError("pinned identity changed")
    price, stock = v.get("price") or {}, (v.get("availability") or {}).get("available")
    if price.get("currency") != "USD" or not integer(price.get("amount"),0,2**53-1) or type(stock) is not bool:
        raise SourceError("missing stock or verified USD price")
    return bound, Seen(seller, vid, stock, price["amount"], "USD", t)
