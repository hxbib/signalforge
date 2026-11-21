# SignalForge integration contract (lead-owned, frozen unless the board carries a `CONTRACT` line)

Stack: Python 3.12, FastAPI + Uvicorn, same-origin static HTML/CSS/ES modules (no build step), one
SQLite file. `reference/` holds the event-day reference modules; lane packages under `app/` wrap them.

## 1. Ownership map
| Lane | Owner | Paths | Port |
|---|---|---|---|
| Integration | LEAD | `app/main.py`, `app/pipeline.py`, `app/contracts.py`, `app/config.py`, `app/store.py`, `requirements.txt` | 8000 |
| Models | MODELS | `app/models/**`, `scripts/smoke.py`, `tests/test_models*.py` | 8101 |
| Shop catalog | SHOP | `app/merchants/shop.py`, `tests/test_shop*.py`, `docs/SHOP_NOTES.md` | 8102 |
| Direct merchant | RETAIL | `app/merchants/chains.py`, `tests/test_chains*.py`, `docs/SOURCES_REGISTRY.md` | 8103 |
| Fan-out + ranking | COMPARE | `app/merchants/compare.py`, `tests/test_compare*.py` | 8104 |
| Browser | UI | `app/web/**` | 8105 |
| Watch | WATCH | `app/watch/**`, `tests/test_watch*.py` | 8106 |
| Proof / eval | EVAL | `eval/**`, `app/evidence/**`, `tests/test_eval*.py` | 8107 |
| Docs / release | DOCS | `README.md`, `docs/**` (except CONTRACT.md, SHOP_NOTES.md, SOURCES_REGISTRY.md), `scripts/tunnel.sh` | 8108 |

## 0. Identity decision (lead, 15:35 ET — from live shop.dev probes)
- `search_catalog` returns universal products (`products[].id` = `gid://shopify/p/…`, the **upid**) each with seller
  variants: `id` (variant gid), `title`, `handle`, `url`, `price{amount: integer cents, currency}`,
  `availability{available}`, `seller{id,name,url,domain}`, `condition` (e.g. `["new"]`, `["secondhand"]`), `source`.
  `get_product {id: upid}` returns that product with the variants of EVERY seller in the catalog cluster.
- The catalog exposes **no GTIN field**, and `lookup_catalog` by UPC/EAN returned `not_found` for every id tried. So the
  reference GTIN-lookup path (`compare_offers.compare_now`) cannot be the spine. Identity is established by evidence tiers:
  1. `merchant_barcode` — the merchant's own storefront publishes a barcode for the exact variant that equals the target GTIN
     (printed barcode read by Neon / local scan, or the barcode other merchants in the cluster publish).
  2. `catalog_product` — the row belongs to the bound Shop universal product (same upid).
  3. `model_number` — the listing's own title/SKU carries the bound brand and the exact printed model/set number as a token,
     no published barcode contradicts it, and the two-model listing check (`app.models.pair`) returns `match`.
  Anything else is a **lead** (shown, never ranked). Model confidence alone never creates identity.
- Ranking is `app/merchants/ranking.py::evaluate`, the reference `buyer.evaluate` rule set with the identity predicate
  generalized to the tiers above (byte-identical results to `buyer.evaluate` for GTIN-identified rows; regression-tested).

## 2. Pipeline (lead, `app/pipeline.py`) — what happens to one photo
1. `clean_image` (reference/live_smoke.py) → JPEG ≤1400 px. State `reading`.
2. Concurrently: `app.models.inspect(image, note)` (Neon view + Decision 1 gate + ≤1 automatic second look) and
   `app.models.parse_goal(image, note)` (Carbon intent). Optional `app.models.scan(image, focus_box)`.
3. State `searching`: `app.merchants.shop.anchors_for(visual, image)` → catalog anchors (universal products) + candidate rows.
4. `app.models.bind(image, note, anchors, visual, decision, verified_scan)` → Carbon picks one anchor slot, deterministic
   corroboration follows; otherwise one question → state `needs_detail` (leads still shown).
5. State `verifying`: `app.merchants.compare.run_comparison(target, goal, rows)` → identity tiers, merchant confirmation
   (`chains.confirm`), ranking, near miss, coverage.
6. State `complete` (every lane answered) | `partial` (a lane failed/timed out, or a constraint was scoped out) |
   `unavailable` (nothing usable; the real reason is shown).
7. If the note carried an explicit watch request: `app.watch.create(run, command)` after the result.

## 3. Lane façades (exact names and shapes — the pipeline imports these)
```python
# app/models/__init__.py — MODELS (wraps reference deal_models / find / visual_recovery / gemini_io)
class ModelCallError(Exception): ...        # (job, model, safe_message); a failed call is never swallowed
def inspect(image: bytes, note: str) -> dict
#  {"image": bytes,            # the image later steps must use (may be the auto-crop)
#   "visual": dict,            # deal_models.Visual.model_dump(): name, barcode, brand, part_number,
#                              # variant_details[], query, target_count, focus_box[], focus_words, evidence[]
#   "decision": dict,          # {"pass":bool,"choice":"single|multiple|other","confidence":float,
#                              #  "threshold":0.75,"id":str|None,"model":"gemini-decision-1","ms":int}
#   "recovery": dict|None,     # {"event":"auto_crop","box":[l,t,r,b], ...} when a second look ran
#   "receipts": list[dict]}    # [{"job":"view","model":"gemini-4-neon","id":str,"ms":int}, {"job":"gate",...}]
def parse_goal(image: bytes, note: str) -> dict
#  {"intent": dict,            # find.Intent.model_dump(): operation, market, basis, fulfillment, location, watch_words, unsupported[]
#   "receipt": {"job":"goal","model":"gemini-4-carbon","id":str,"ms":int}}
def bind(image: bytes, note: str, anchors: list[dict], visual: dict, decision: dict,
         verified_scan: str | None = None) -> dict
#  Carbon (deal_models.CHOOSE prompt + Selection schema) picks one anchor slot from the catalog anchors below, then
#  deterministic corroboration must hold: decision passes (or a verified scan exists) and visual.target_count == "one",
#  AND (a) the printed/scanned barcode equals anchor["gtin"], OR (b) Neon's brand and part_number are both non-empty,
#  the brand equals anchor["brand"] or is a token of anchor["title"] (case-insensitive) and part_number is an exact
#  token of anchor["title"] / anchor["mpn_tokens"]. Otherwise no bind and ONE question.
#  {"slot": int|None, "anchor": dict|None, "basis": "barcode"|"model_number"|None, "question": str,
#   "receipt": {"job":"bind","model":"gemini-4-carbon","id":str,"ms":int}}
def pair(target: dict, listing: dict) -> dict
#  Two-model listing check (deal_models.pair_verdict(full=True) logic: Neon extracts decisive quotes, Carbon rules,
#  both verdicts must agree and each quote must appear verbatim in its side, else abstain).
#  {"verdict": "match"|"different"|"abstain", "receipts": [ {"job":"pair_extract","model":"gemini-4-neon",...}, {"job":"pair_verdict","model":"gemini-4-carbon",...} ]}
def scan(image: bytes, focus_box: list[int] | None = None) -> str | None   # local barcode decode: exactly one valid GTIN or None
def compile_watch(image: bytes, note: str, candidates: list[dict]) -> dict
#  {"proposal": dict (gemini_io.Proposal.model_dump()), "receipt": {"job":"watch_rule","model":"gemini-4-carbon","id":str,"ms":int}}

# app/merchants/shop.py — SHOP (real https://mcp.shop.com/ through reference/shop.py::call)
def search(query: str, image: bytes | None = None, limit: int = 10) -> tuple[dict, dict]   # (catalog doc, receipt)
def product(upid: str) -> tuple[dict, dict]                                                # get_product: every seller variant of one universal product
def rows_from(doc: dict, read_at: int) -> list[dict]                                       # every variant → Quote row (§5), source "shop_native"
def anchors_for(visual: dict, image: bytes | None = None) -> dict
#  {"anchors": [ {"upid": str, "title": str, "brand": str, "mpn_tokens": [str], "gtin": str|None,
#                 "sellers": int, "exact_variant_by_mpn": bool,      # catalog fact, never model confidence
#                 "required_variant_details": [str], "identity_source": "Shop catalog product",
#                 "supporting": [{"seller_name": str, "url": str, "listing": str}]} ],
#   "rows": [Quote],           # every candidate row seen (search + get_product of the top anchors), deduplicated by seller+listing
#   "receipts": [dict]}        # {"source":"shop_catalog","tool":...,"read_at":...,"effective_at":...,"response_hash":...}

# app/merchants/chains.py — RETAIL (evidence straight from the merchant, not via the Shop MCP)
REGISTRY: list[dict]   # per source: id, name, kind, terms_ref, connected: bool, capabilities{display, repeated_reads, retention, evaluation}
def confirm(quote: dict, as_of: int) -> dict
#  {"status": "confirmed"|"mismatch"|"unavailable"|"not_permitted",
#   "quote": dict|None,        # an independent Quote row (source "merchant_api") with the SAME seller + listing keys,
#                              # read from the merchant's own storefront endpoint; carries gtin (published barcode) and sku
#   "receipt": {"source": str, "url_host": str, "read_at": int, "ms": int, "response_hash": str|None},
#   "note": str}
def identity(quote: dict) -> dict     # {"status", "gtin": str|None, "sku": str, "vendor": str, "title": str, "variant_title": str, "receipt": {...}}

# app/merchants/ranking.py + app/merchants/compare.py — COMPARE
def evaluate(target: dict, goal: dict, offers: list[dict], as_of: int) -> dict   # ranking.py; buyer.evaluate rules, tiered identity
def run_comparison(target: dict, goal: dict, rows: list[dict], *, deadline_s: float = 20.0,
                   as_of: int | None = None, confirm=None, pair=None) -> dict
#  target = {"upid": str|None, "gtin": str|None, "brand": str, "mpn": str, "name": str, "variant_details": [str]}
#  goal   = {"basis":"item"|"delivered","fulfillment":"online_us"|"ship:<zip>"|"pickup:<loc>"}
#  rows   = shop_native Quote rows from shop.anchors_for
#  → {"result": Result (§4), "coverage": Coverage (§4), "receipts": [dict], "evaluation": dict}
#  confirm / pair default to app.merchants.chains.confirm / app.models.pair; tests may inject callables.

# app/watch/__init__.py — WATCH (wraps reference autowatch / proof / ledger / core)
def create(run: dict, command: str, replies: dict | None = None) -> dict       # Watch (§4)
def pause(watch_id: str) -> dict                                               # Watch with status "paused" (idempotent)
def what_if(watch: dict, timeline: list[dict]) -> list[dict]                   # each row carries "simulation": True
# also app/watch/routes.py exposing `router = fastapi.APIRouter()` for the watch endpoints in §6

# app/evidence/routes.py — EVAL: `router` serving GET /api/proof/eval from eval/results/*.json

# app/store.py — LEAD (available to every lane)
def get_run(run_id: str) -> dict | None      # the Run JSON of §4
def save_run(run: dict) -> None
def get_image(run_id: str) -> bytes | None   # the cleaned JPEG of that run
```

## 4. Run JSON — `GET /api/search/{run_id}` (what the UI renders; absent sections are `null`)
```jsonc
{
 "run_id": "run_3f9c…", "revision": 1,
 "state": "reading",            // reading → searching → verifying → complete | partial | needs_detail | unavailable
 "created_at": 1795292400, "updated_at": 1795292412, "elapsed_ms": 11873,
 "image_url": "/api/search/run_3f9c…/image",
 "note": "",                    // the buyer's optional words, verbatim
 "assumptions": [ {"id":"condition","label":"New"}, {"id":"market","label":"US online sellers"},
                  {"id":"basis","label":"Item price before shipping & tax"}, {"id":"offer","label":"No membership or coupon"} ],
 "progress": [                  // only events that really happened, in order; status running|done|failed|skipped
   {"id":"view","label":"Gemini 4 Neon read the photo","status":"done","ms":2143,"detail":"<name> · <brand> <part_number>"},
   {"id":"gate","label":"Gemini Decision 1 checked the target","status":"done","ms":412,"detail":"single · confidence 0.97"},
   {"id":"goal","label":"Gemini 4 Carbon read the request","status":"done","ms":1800,"detail":"compare · item price · US online"},
   {"id":"identity","label":"Catalog identity evidence","status":"done","ms":900,"detail":"2 anchors from shop.dev"},
   {"id":"bind","label":"Gemini 4 Carbon bound the exact product","status":"done","ms":2400,"detail":"GTIN 00673419404235"},
   {"id":"offers","label":"Merchants checked","status":"running","ms":null,"detail":""}
 ],
 "target": {"name":"","brand":"","mpn":"","gtin":null,"upid":"","title":"","variant_details":[],"evidence":[],"identity_source":"Shop catalog product","identity_basis":"model_number","bound":true},   // gtin null when no merchant publishes a barcode
 "decision": {"choice":"single","confidence":0.97,"pass":true,"threshold":0.75,"ms":412,"id":"…","model":"gemini-decision-1"},
 "goal": {"operation":"compare","market":"US","basis":"item","fulfillment":"online_us","location":"","watch_words":"","unsupported":[]},
 "question": {"question_id":"q_…","text":"Which item in the picture should I compare?","kind":"choice","options":[{"id":"a","label":"…"}]},  // kind: choice|text|zip
 "result": {
   "status": "winner",          // winner | tie | single_offer | no_verified_offer | needs_identity
   "headline": "Lowest verified among the stores checked",   // "One verified offer" | "No verified offer yet" | …
   "price_cents": 2999, "currency": "USD", "basis_label": "Item price · USD · before shipping & tax",
   "winners": [Offer],
   "near_miss": {"offer": Offer, "reason": "sold_out", "sentence": "$4.00 less, but sold out", "delta_cents": -400},
   "rejected": [ {"offer": Offer, "reason": "sold_out", "sentence": "Sold out"} ],
   "leads": [Offer]             // unverified discovery candidates; never styled as verified
 },
 "coverage": {"attempted": 2, "responded": 2, "sellers_seen": 5, "sellers_eligible": 1, "checked_at": 1795292411,
   "sources": [ {"id":"shop_catalog","name":"Shop catalog (shop.dev)","kind":"catalog","status":"ok",   // ok|failed|timeout|limited|not_connected
                 "offers":5,"eligible":1,"ms":812,"note":""} ]},
 "receipts": {"models":[{"job":"view","model":"gemini-4-neon","id":"…","ms":2143}],
              "sources":[{"source":"shop_catalog","tool":"lookup_catalog","read_at":0,"effective_at":0,"response_hash":"…"}]},
 "watch": null,                 // Watch once created
 "error": null                  // {"code":"…","message":"safe human sentence"}
}
```
`Offer` = `{"seller": str, "seller_name": str, "listing": str, "title": str, "url": "https://…", "image_url": str|null,
"item_cents": int|null, "currency": "USD", "condition": str, "stock": "in_stock"|"sold_out"|"unknown"|…,
"fulfillment": str, "source": "shop_native"|"merchant_api"|"merchant_page"|"discovery", "read_at": int, "verified": bool,
"confirmed_by": [str], "identity_basis": "merchant_barcode"|"catalog_product"|"model_number"|null, "gtin": str|null}`
(`confirmed_by` lists independent sources that agreed, e.g. `["merchant_api"]`).

`Watch` = `{"watch_id": "w_…", "status": "active"|"needs_detail"|"preview_only"|"paused",
"scope_sentence": "Watching <seller> for a restock below $25.00",
"rule": {"seller": str, "variant": str, "mode": "restock"|"becomes_eligible", "op": "lt"|"lte", "cents": int},
"origin": "explicit_request"|"buyer", "proof_hash": str|null, "pause_url": "/api/watch/w_…/pause",
"preview_reason": str|null, "question": {...}|null,
"playground": [ {"label": str, "alerts": bool, "why": str, "simulation": true} ]}`

## 5. Quote row (input to ranking; same fields reference/buyer.py::evaluate reads — read that file)
`{"seller" (seller gid), "seller_name", "seller_domain" (storefront host), "listing" (variant gid), "product" (upid),
"handle", "title", "url", "image_url", "source" (shop_native | merchant_api | merchant_page; anything else is discovery-only),
"read_at" (UTC int, ≤120 s old at evaluation), "gtin" (merchant-published barcode or null), "sku", "condition"
("new" only when the source says exactly new; e.g. "secondhand", "refurbished", "unknown"), "stock"
("in_stock"|"sold_out"|"orderable"|"unknown"), "fulfillment" ("online_us"|"ship:<zip>"|"pickup:<loc>"), "unconditional" (bool),
"currency", "item_cents" (int)}`; delivered basis adds `shipping_cents, tax_cents, fees_cents`.

## 6. HTTP (lead implements; JSON unless noted)
| Method, path | Request | Response |
|---|---|---|
| `POST /api/search` | multipart: `image` (JPEG/PNG/WebP ≤5 MB), `note` (optional, may be empty) | `202 {run_id, revision, state}` |
| `GET /api/search/{id}` | — | Run JSON (§4). Poll every 1 s until state is terminal |
| `GET /api/search/{id}/image` | — | the cleaned JPEG |
| `POST /api/search/{id}/answer` | `{revision, question_id, choice?: id, text?: str}` | `202 {run_id, revision, state}`; stale revision → 409 |
| `POST /api/search/{id}/watch` | `{command?: str}` | Watch |
| `POST /api/watch/{id}/pause` | — | Watch |
| `POST /api/watch/{id}/whatif` | `{timeline: [ {t, available, cents} ]}` | `{simulation: true, rows: [...]}` |
| `GET /api/search/{id}/proof` | — | `{receipts, decision, evaluation, coverage}` |
| `GET /api/proof/eval` | — | `{generated_from: [files], summary, cases}` (EVAL router) |
| `GET /api/health` | — | `{ok, key_present, commit, models}` |
| `GET /`, `/proof` | — | `app/web/index.html`, `app/web/proof.html`; `app/web/*` is served at `/static/*` |
Errors: 400 unsupported input, 404 unknown/foreign id, 409 stale revision, 413 too large, 429 budget.
Error bodies are `{"error": {"code", "message"}}` with safe text only.
