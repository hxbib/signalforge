# SignalForge

**One photo in. The lowest verified in-stock offer among the stores checked out, the reason a cheaper-looking
listing lost, and a watch that can explain itself.**

Built on Saturday, November 21, 2026 at the Gemini 4 NYC Hackathon. Track: **5 Sigma**, plus the **Build with
Commerce (Shopify)** bonus track.

| | |
|---|---|
| Live demo | https://reviewing-look-liz-course.trycloudflare.com (temporary tunnel to the event machine, up through judging) |
| Repository | https://github.com/hxbib/signalforge |
| One-minute video | TODO-LEAD |
| What was built today | [docs/PROVENANCE.md](docs/PROVENANCE.md) |
| Team | Sadman Habib ([hxbib](https://github.com/hxbib)), solo |

## What it does

A shopper pastes a screenshot, drops a photo or takes one. No typed query, no retailer checklist, no login.

1. The photo is read for visible evidence: brand, printed model or set number, barcode, variant.
2. A gate decides whether exactly one target is clear. If it is not, the app tries one automatic closer look and
   otherwise asks one question instead of guessing.
3. The photo is bound to one exact product that the Shop catalog actually lists. A model picks the candidate;
   deterministic code then requires a matching barcode, or a matching brand plus printed model number, before the
   pick counts.
4. Sellers of that product are read from the Shop catalog (shop.dev) and each merchant's own storefront is asked to
   confirm the exact variant, price and stock.
5. Deterministic rules decide which offers qualify (identity, new condition, in stock, USD, unconditional price,
   reading at most 120 seconds old) and rank them.
6. The page shows the lowest verified in-stock offer **among the stores checked**, the cheaper-looking listing that
   lost with the exact reason, the coverage (which sources answered), and on request every model receipt.
7. A note such as "Notify me when this seller restocks under $25" is compiled into a typed rule, tested against
   generated counterexample timelines and shown as an in-app watch. Without an authorized merchant feed it is a
   clearly labelled rule preview; nothing is polled.

Models search, read and disambiguate. Merchant facts and executable rules decide. A model's confidence never
makes a price, a stock state or a product identity on its own.

## Runtime model jobs

Every call is a real call to the Gemini API through `google-genai`. There is no mock, canned or cached answer path
in the application; a failed call is shown as a failure.

| Job | Model | Where it lives |
|---|---|---|
| Read the photo: object, brand, printed identifiers, variant, focus box | `gemini-4-neon` | `app/models/__init__.py::inspect` (prompt and schema: `reference/deal_models.py` `VIEW` / `Visual`) |
| Gate: is exactly one target clear? Choices `single / multiple / other`, pass needs `single` at confidence >= 0.75 | `gemini-decision-1` (Decisions API) | `app/models/io.py::gate`; threshold in `app/config.py` |
| One automatic second look on a crop when the gate does not pass | `gemini-4-neon` + `gemini-decision-1` | `reference/visual_recovery.py::second_look`, called from `inspect` |
| Read the request (usually an empty note): compare or compare-and-watch, price basis, fulfillment | `gemini-4-carbon` | `app/models/__init__.py::parse_goal` (`reference/find.py` `INTENT` / `Intent`) |
| Bind the photo to one catalog product | `gemini-4-carbon`, then deterministic corroboration | `app/models/__init__.py::bind` and `corroborate` |
| Cross-check a listing against the bound product: Neon extracts, Carbon judges, both must agree and both must quote the listing verbatim, otherwise abstain | `gemini-4-neon` + `gemini-4-carbon` | `app/models/__init__.py::pair`, used by `app/merchants/compare.py` |
| Compile an explicit watch request into a bounded typed rule | `gemini-4-carbon` | `app/models/__init__.py::compile_watch`, used by `app/watch/__init__.py` |
| Local barcode decode of the focus crop (no model) | zxing-cpp | `app/models/__init__.py::scan` |
| Offer eligibility and ranking (no model) | deterministic code | `app/merchants/ranking.py::evaluate` |
| Watch semantics, counterexample timelines, proof hash, alert ledger (no model) | deterministic code | `app/watch/__init__.py` over `reference/autowatch.py`, `proof.py`, `ledger.py`, `core.py` |

The single boundary to the model service is `app/models/io.py` (`structured` for Neon and Carbon through the
Interactions API, `gate` for the Decisions API). No custom temperature, top-p or top-k is set; the thinking level
of every job is `low` by default, and `minimal` is refused.

## Architecture

```
 browser (app/web: static HTML + ES modules, no build step)
   paste / drop / camera ──POST /api/search──▶ FastAPI  app/main.py ──▶ app/pipeline.py (one worker per run)
   poll GET /api/search/{id} every 1 s ◀── run JSON (state, steps, result, coverage, receipts)  ◀── SQLite app/store.py

 app/pipeline.py
   │ reading    clean_image (JPEG, <= 1400 px)
   │            ┌─ app.models.inspect      Gemini 4 Neon view ─┬─ Gemini Decision 1 gate ─▶ <= 1 automatic second look
   │            └─ app.models.parse_goal   Gemini 4 Carbon intent        (in parallel with inspect)
   │            app.models.scan            local barcode decode of the focus crop (zxing-cpp)
   │ searching  app.merchants.shop.anchors_for   shop.dev Shop MCP: text + image search, get_product  ──▶ anchors + seller rows
   │            app.models.bind            Gemini 4 Carbon picks one anchor ─▶ deterministic corroboration
   │                                       (barcode equal, or brand + printed model number) ─▶ else ONE question
   │ verifying  app.merchants.compare.run_comparison
   │              ├─ app.merchants.chains.confirm   merchant's own storefront: exact variant, price, stock, barcode
   │              ├─ app.models.pair                Neon + Carbon listing check (grants the model_number tier only)
   │              └─ app.merchants.ranking.evaluate deterministic eligibility, ties, near miss, rejections
   ▼ complete | partial | needs_detail | unavailable   (the real reason is always shown)
   optional: app.watch.create  Carbon rule ─▶ counterexample timelines ─▶ proof hash ─▶ preview or active watch

 /proof  app/web/proof.html ◀── GET /api/proof/eval (app/evidence/routes.py) ◀── eval/results/*.json
```

### Identity without a catalog barcode

The Shop catalog exposes no GTIN field, and `lookup_catalog` by UPC returned `not_found` for every id tried at the
event (details in [docs/CONTRACT.md](docs/CONTRACT.md) section 0 and [docs/SHOP_NOTES.md](docs/SHOP_NOTES.md)). So an
offer counts as "the same item" only through one of three evidence tiers:

1. `merchant_barcode`: the merchant's own storefront publishes a barcode for the exact variant that equals the target barcode.
2. `catalog_product`: the row belongs to the bound Shop universal product.
3. `model_number`: the listing's own title or SKU carries the bound brand and the exact printed model number, no
   published barcode contradicts it, and the two-model listing check returns `match`.

Everything else is shown as a **lead** and is never ranked or called verified.

## Run it

Python 3.12.

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install google-genai fastapi uvicorn python-multipart pydantic Pillow zxing-cpp httpx pytest
printf 'GEMINI_API_KEY=your-key\n' > .env        # read by app/config.py; .env is gitignored
.venv/bin/uvicorn app.main:app --port 8000        # then open http://localhost:8000
.venv/bin/python -m pytest tests -q               # offline tests; fakes exist only under tests/
.venv/bin/python scripts/smoke.py path/to/photo.jpg   # three real model calls on one photo
```

Versions used at the event: google-genai 3.3.0, FastAPI 0.162.7, Uvicorn 0.54.0, Pydantic 2.14.3, Pillow 13.0.0,
zxing-cpp 3.1.1.

Settings (environment): `SF_DB` (SQLite path), `SF_STORE_INTERACTIONS` (see Privacy), `SIGNALFORGE_WATCH_FEED`
(an authorized merchant feed; without it every watch is a preview), `SF_MODEL_TIMEOUT_MS`, `SF_DECISION_TIMEOUT_MS`,
`SF_THINKING_VIEW` / `_GOAL` / `_BIND` / `_WATCH` (`low`, `medium`, `high` or `xhigh`).

HTTP surface (`app/main.py`): `POST /api/search` (multipart `image` up to 5 MB, optional `note`),
`GET /api/search/{id}`, `POST /api/search/{id}/answer`, `GET /api/search/{id}/proof`, `POST /api/search/{id}/watch`,
`POST /api/watch/{id}/pause`, `POST /api/watch/{id}/whatif`, `GET /api/proof/eval`, `GET /api/health`. Runs belong to
the browser session that created them.

## Evaluation

The numbers below are inserted by `python scripts/readme_numbers.py` from `eval/results/summary.json`, which
`eval/summarize.py` builds from the raw per-case results of a real run. Nothing in this section is typed by hand.
Protocol: [eval/PROTOCOL.md](eval/PROTOCOL.md).

<!-- EVAL:START (written by scripts/readme_numbers.py; do not edit by hand) -->
**Experiment A — WDC v2 product-pair identity (independent public gold).** 40 pairs, drawn before either arm ran (frozen 2026-11-21T20:37:17+0000, seed `0e6fbd2`), results version `v1`, code `6cf1194`, models `gemini-4-carbon`, `gemini-4-neon`. An abstention counts as incorrect.

| Arm | Correct | Wrong answers | Abstained | Runtime errors | False matches / actual different | False rejections / actual same | Median ms | p90 ms |
|---|---|---|---|---|---|---|---|---|
| Baseline: one Gemini 4 Carbon call | 34/40 | 0 | 6 | 0 | 0/20 | 0/20 | 4516 | 6382 |
| System: Neon extraction + Carbon verdict + agreement + verbatim-quote check | 30/40 | 0 | 10 | 0 | 0/20 | 0/20 | 10779 | 14174 |

Paired on the same 40 pairs: both arms correct 30, only the baseline correct 4, only the system correct 0, neither 6.

| Stratum | Baseline correct | Baseline abstained | System correct | System abstained |
|---|---|---|---|---|
| cameras/different | 10/10 | 0 | 10/10 | 0 |
| cameras/match | 6/10 | 4 | 5/10 | 5 |
| computers/different | 10/10 | 0 | 10/10 | 0 |
| computers/match | 8/10 | 2 | 5/10 | 5 |

**Experiment B — five fresh photo-to-offer cases:** status `not_run`. The five-case photo-to-offer check (eval/seal_baskets.py; baseline basket_answer(packet, False) vs system basket_answer(packet, True)) was not run: rights-cleared merchant evidence for five preregistered cases could not be assembled before the 16:05 ET proof cutoff. No case, price or score was substituted.

Source files: `eval/results/summary.json`, `eval/results/pairs-v1.json`, `eval/results/baskets-gap.json`.
<!-- EVAL:END -->

How to read it:

- Experiment A tests one component, the listing identity check (`app.models.pair`), on independently labelled public
  pairs: the WDC Product Data Corpus v2 gold standard (cameras and computers). Both arms see the same text packet;
  ids, clusters and labels are withheld. It is a text matching check. It is not a measure of photo reading, live
  stock or end-to-end shopping accuracy.
- The baseline is a single Gemini 4 Carbon call. The system arm adds Neon extraction, agreement between the two
  models and a verbatim-quote check, and abstains whenever any of those fail. Abstentions are scored as incorrect.
- Read the table as it is: where the system arm scores below the baseline, the difference is abstentions. In the
  product an abstention keeps a listing as an unranked lead; a false match would put a wrong item at the top.
- The public WDC labels may be familiar to a pretrained model.

## Limitations and failure cases

- **"Lowest verified among the stores checked" is not a market-wide claim.** The stores checked are the sellers in
  the Shop catalog cluster for the product and those merchants' own Shopify storefronts. Best Buy, Walmart, Target,
  Amazon, eBay and Google Shopping are not connected (no approved access at the event); see the table below.
- **Item price only.** USD, before shipping and tax, US online. The catalog provides no shipping, tax, ZIP or pickup
  data, so a request for a delivered total, a ZIP or store pickup is reported as "Not compared" and the run ends
  `partial` with item prices.
- **No catalog barcode.** Identity rests on the tiers above. An item with no readable brand plus model number and
  no barcode gets a question, not a guess. A listing whose identity check abstains or runs out of time stays a lead,
  so a genuinely cheaper offer can be left unranked.
- **The listing check is conservative and varies run to run.** In the evaluation every system failure was an
  abstention on a truly matching pair: Carbon abstains when no manufacturer model number is printed, the verbatim
  quote check rejected two genuine quotes (a collapsed newline, two fragments joined with "..."), and one case
  flipped on a re-run. Case-by-case notes: [eval/CHANGELOG.md](eval/CHANGELOG.md).
- **Catalog clusters are fragmented**: the same physical item can appear as several catalog products, so coverage of
  sellers can be incomplete. Non-USD sellers that appear in a US search are rejected, not converted.
- **Listing checks often run out of time in a live search.** They share a 9-second cap (`PAIR_PHASE_S` in
  `app/merchants/compare.py`) and at most 6 are started per search, while one check takes about as long as the
  evaluation's measured median above. A check that does not finish leaves its listing as a lead, and the coverage
  row says how many did not finish.
- **Watches are previews in this build** unless an authorized feed for the exact seller and variant is configured.
  A watch covers one named seller and one variant; "any store" is refused with an explanation. What-if timelines are
  simulations and are labelled `simulation`. An explicit watch sentence can still come back with a question instead
  of a rule (seen live when the winning offer was already in stock far above the cap).
- **Decision 1 responses carry no id**, so the gate receipt shows no id; nothing is invented in its place.
- **Not instant.** One photo takes several model calls; the page shows the real elapsed time of every step.
- **Experiment B (five fresh photo-to-offer cases) was not run**; the reason is recorded in
  `eval/results/baskets-gap.json` and repeated above. There is no measured end-to-end accuracy number.
- No checkout, no accounts, no notifications outside the app.

## Privacy and retention

- Each Neon and Carbon step is sent to the Gemini API as a **stored** interaction (`store=True` in
  `app/models/io.py`), because the service only issues an interaction id for a stored interaction and every receipt
  is meant to carry a real id. So **the uploaded photo, the note and the listing text sent to the models are held by
  the model service as stored interactions.** How long is set by `SF_STORE_INTERACTIONS`:
  - unset or `1` (the code's default when you start `uvicorn` yourself): stored and kept; receipts show `retained: true`.
  - `delete` (what `scripts/serve.sh` sets, and what the event demo server runs): stored for the duration of the
    call and deleted right after the answer is read; receipts carry the id the service issued and `retained: false`.
  - `0`: stateless calls; nothing is stored and receipt ids are null.
- The cleaned photo and the run document are kept in the server's SQLite file so the result page can be reloaded.
  They are readable only by the browser session that created the run.
- All text inside images, listings and pages is treated as data, never as instructions.

## Data sources and permissions

Summary of [docs/SOURCES_REGISTRY.md](docs/SOURCES_REGISTRY.md), which mirrors the registry in
`app/merchants/chains.py` (`REGISTRY`).

| Source | Used for | Connected | Display | Repeated reads | Retention | Offsite model evaluation |
|---|---|---|---|---|---|---|
| Shop catalog, shop.dev Shop MCP (`https://mcp.shop.com/`) | product discovery, seller variants, price, availability, merchant links | yes | yes | shopper-triggered only, at most 6 catalog calls per photo | the run only | no rights record, so not used for evaluation |
| Merchant's own Shopify storefront (`/products/<handle>.js`) | confirm the exact variant, price, stock and published barcode | yes | yes | one GET per listing per run | memory only, at most 120 s, never on disk | no rights record, so not used for evaluation |
| Merchant product page JSON-LD | none | no: not built in this release | n/a | n/a | n/a | n/a |
| Google Shopping (SerpApi), Best Buy, eBay | none | no: no approved API key at the event | n/a | n/a | n/a | n/a |
| Walmart, Amazon | none | no: no approved partner or associate access at the event | n/a | n/a | n/a | n/a |
| Target | none | no: no public product API; scraping not permitted | n/a | n/a | n/a | n/a |
| WDC Product Data Corpus v2 gold standard (Peeters, Primpeli, Bizer) | Experiment A only | offline | aggregate results and pair ids only; the raw corpus and labels are not redistributed here | n/a | private evaluation directory | public research corpus, cited |
| Gemini API (Interactions, Decisions) | every model step | yes | n/a | per shopper action | stored interactions, see Privacy | n/a |

No background polling of any merchant or of shop.dev. No merchant price is hard-coded in code or tests. The merchant
storefront endpoint has no currency field: a storefront price is accepted only when it equals the catalog amount,
otherwise the row is reported as a mismatch and is not ranked.

## Repository map

```
app/main.py, pipeline.py, store.py, contracts.py, config.py   HTTP surface, orchestration, storage
app/models/        model jobs (io.py is the only place that calls the model service)
app/merchants/     shop.py (Shop catalog), chains.py (merchant storefront), compare.py, ranking.py
app/watch/         explicit watch: rule, proof, playground, routes
app/evidence/      evaluation evidence route
app/web/           the browser app
reference/         event-day reference modules the app imports (prompts, schemas, ranking rules, watch core)
eval/              protocol, sampler, runners, results
spec/, docs/       product spec, integration contract, notes, provenance, submission text
tests/             offline tests
```
