# Source registry (RETAIL lane, mirrors `app.merchants.chains.REGISTRY`)

Every merchant/price source this build knows, with its honest connection state. Reads are shopper-triggered only.

| id | name | kind | connected | display | repeated reads | retention | offsite ML evaluation | terms | reason if not connected |
|---|---|---|---|---|---|---|---|---|---|
| `shop_catalog` | Shop catalog (shop.dev MCP) | catalog | yes | True | shopper-triggered only, <=6 catalog calls per photo | run only (no cache beyond the run) | False (no rights record) | ops/SOURCES.md (Shop MCP) |  |
| `merchant_storefront_ajax` | Merchant Shopify storefront (/products/<handle>.js) | merchant_api | yes | True | one GET per listing per run, in-memory <=120 s | memory only, <=120 s, never on disk | False (no rights record) | ops/SOURCES.md (Shopify Ajax product API) |  |
| `merchant_jsonld` | Merchant product page JSON-LD | merchant_page | no | False | n/a | n/a | False | reference/merchant_markup.py | not built in this release (cut for time) |
| `serpapi_google_shopping` | Google Shopping via SerpApi | retailer_api | no | False | n/a | n/a | False | spec/RETAILERS.md | no approved API key at the event |
| `bestbuy` | Best Buy Products API | retailer_api | no | False | n/a | n/a | False | spec/RETAILERS.md | no approved API key at the event |
| `walmart` | Walmart affiliate/marketplace API | retailer_api | no | False | n/a | n/a | False | spec/RETAILERS.md | no approved partner access at the event |
| `target` | Target | retailer_api | no | False | n/a | n/a | False | spec/RETAILERS.md | no public product API; scraping not permitted |
| `amazon` | Amazon Product Advertising API | retailer_api | no | False | n/a | n/a | False | spec/RETAILERS.md | no approved associate credentials at the event |
| `ebay` | eBay Browse API | retailer_api | no | False | n/a | n/a | False | spec/RETAILERS.md | no approved API key at the event |

## merchant_storefront_ajax — observed on 30 real catalog listings / 30 merchants (Sat Nov 21 2026, 15:54 ET)
- Endpoint: `https://<catalog-returned host>[/<locale>]/products/<handle>.js` (locale prefix kept from the catalog URL) (Shopify Ajax product API). Same host only, https only, redirects refused, 6 s timeout, ≤1 MB, UA `SignalForge/0.1 (hackathon demo; shopper-triggered)`, one GET per listing per run (in-memory cache ≤120 s, never on disk).
- 29/30 answered JSON; 1 blocked (HTTP 403). Reads took 70–430 ms.
- `variants[].price` is an integer in minor units; it equalled the catalog `price.amount` on 23/29.
- No currency field. Currency is set only when the catalog amount equals the storefront amount; otherwise `currency` is null with `currency_note`. All 6 disagreements were storefronts pricing in their own currency → status `mismatch`, both amounts in the note, never the cheaper one picked.
- `barcode` was a valid GTIN (check digit) on 16/29; `sku` filled on 23/29; `available` present on 29/29.
- Condition words seen in titles: Certified, open-box / damaged box. Any used/refurbished/open-box/certified/pre-owned/secondhand wording in product title, variant title/options or tags → that condition; otherwise `new`.
- `requires_selling_plan` true → `unconditional: false`.

## Statuses (`confirm`)
- `confirmed`: exact variant id found, price and stock equal the catalog row.
- `mismatch`: variant found but price or stock disagree (both values in `note`).
- `unavailable`: 404 / 403 / timeout / non-JSON / oversize / cross-host redirect / variant missing. No row is fabricated.
- `not_permitted`: URL is not an https `/products/<handle>` storefront URL (credentials or non-443 ports refused).
