# shop.dev catalog — observed behaviour (live probes, Sat Nov 21 2026, US/USD context)

All reads go through `reference/shop.py::call` → `https://mcp.shop.com/` (MCP `tools/call`, protocol `2026-07-28`,
7 s timeout, no redirects, ≤2 MB response, `Age` ≤ 90 s). Lane code: `app/merchants/shop.py`.

## search_catalog
- Arguments used: `{"query": str[:200], "pagination": {"limit": n}, "like": [{"image": {content_type, data}}]?, "context": {"address_country":"US","currency":"USD"}}`.
- Top-level keys: `ucp {version}`, `products[]`, `messages[]`, `pagination {has_next_page, total_count, cursor}`, `next[]` (agent guidance strings).
- `pagination.limit`: 10, 20 and 50 accepted (50 returned 43 products); 300 → tool error. The lane defaults to 10 and clamps to 50.
  The cursor is an opaque base64 offset; the lane does not paginate (one page per run).
- `products[]` keys: `id` (`gid://shopify/p/…`, the universal product id / upid), `title`, `handle`, `description{plain}`, `media[]`,
  `metadata{unique_selling_points, top_features}`, `price_range{min,max}`, `rating`, `variants[]`.
- In search results each product carries **one featured variant** only. `get_product` is needed to see the other sellers.
- Titles can contain search highlight markup (`Anker <b>735</b> Charger …`); the lane strips tags from titles only, never from URLs.
- `messages` was `[]` on every successful search observed; the lane copies any non-empty list into the receipt and onto each row (`warnings`).

## get_product `{id: upid}`
- Top-level keys: `ucp`, `product`, `next[]`. `product` adds `selected` (`[]` without options) to the search keys.
- `product.variants[]` = the variants of every seller in the catalog cluster (observed 2–6 sellers for a LEGO set).
- Some upids returned by search come back `isError: true` with
  `messages: [{type:"error", code:"not_found", content:"Product not found: <upid>", severity:"unrecoverable"}]`
  (seen for non-US storefront products, e.g. a German-language listing). The lane records a `failed` receipt and moves on.

## Variant fields
`id` (ProductVariant gid), `title`, `handle`, `url` (seller storefront link with `variant`, `_gsid`, `utm_*` params — kept untouched),
`checkout_url`, `seller_product_id`, `price{amount: integer minor units, currency}`, `availability{available: bool}`, `media[]`,
`seller{id, name, url, domain (*.myshopify.com)}`, `condition` (list, e.g. `["new"]`, `["secondhand"]`), `rating`, `source` (`"shopify"`).
- No `requires`, selling-plan, subscription or components field was seen on any variant; the lane still marks a row
  `unconditional: false` if any such key appears.
- Currencies are returned as the seller prices them: a CAD seller appeared inside a US-context cluster. The lane never converts;
  ranking rejects non-USD rows.

## What the catalog does NOT provide
- No GTIN/UPC/EAN, MPN or SKU field on products or variants (handle sometimes looks like a barcode but is just a slug).
  `lookup_catalog` by UPC/EAN returned `not_found` for every id tried (lead probe, CONTRACT §0).
- No shipping cost, tax, delivery date, ZIP or pickup availability — item price only, US-wide.
- No stock quantity, only `available` true/false.
- Brand/vendor is not a field; the lane infers an anchor brand only when Neon's brand appears in the catalog title.
- Clusters are fragmented: the same physical item can appear as several upids (e.g. one per seller for headphones,
  separate upids for "Used"/"Certified"/"Blemished Box" listings). `exact_variant_by_mpn` therefore needs ≥ 2 sellers'
  own titles to carry the printed number.

## Budget per photo (anchors_for)
1 text search + 1 image search (when a photo is given) + ≤ 4 `get_product` = ≤ 6 calls, run concurrently, 12 s wall budget.
Observed totals 0.5–0.7 s. Shopper-triggered only; nothing is cached beyond the run.
