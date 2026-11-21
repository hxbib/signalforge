# SignalForge

**One photo in. The lowest verified in-stock offer among the stores checked out, the reason a cheaper-looking
listing lost, and a watch that can explain itself.**

Built on Saturday, November 21, 2026 at the Gemini 4 NYC Hackathon. Track: **5 Sigma**, plus the **Build with
Commerce (Shopify)** bonus track.

| | |
|---|---|
| Live demo | https://reviewing-look-liz-course.trycloudflare.com (temporary tunnel to the event machine, up through judging) |
| Repository | https://github.com/hxbib/signalforge |
| One-minute video | [docs/demo/signalforge-demo.mp4](https://github.com/hxbib/signalforge/blob/main/docs/demo/signalforge-demo.mp4) |
| What was built today | [docs/PROVENANCE.md](docs/PROVENANCE.md) |
| Demo kit (objects, pre-flight, numbers to say) | [docs/DEMO_KIT.md](docs/DEMO_KIT.md) |
| Team | Sadman Habib ([hxbib](https://github.com/hxbib)), solo |

## What it does

A shopper pastes a screenshot, drops a photo or takes one. No typed query, no retailer checklist, no login.

1. The photo is read for visible evidence: brand, printed model or set number, barcode, variant.
2. A gate decides whether exactly one target is clear. If it is not, the app tries one automatic closer look and
   otherwise asks one question instead of guessing.
3. The photo is bound to one exact product that the Shop catalog actually lists. Carbon picks the candidate; code
   then accepts the pick only on a matching barcode, or on a matching brand plus a model number that is really
   printed in the photo. Otherwise the app asks one question. Decision 1 scores every catalog listing against the
   photo in one pass, and the question's options are ordered by that score, so the right product is usually one tap.
4. Sellers of that product are read from the Shop catalog (shop.dev) and each merchant's own storefront is asked to
   confirm the exact variant, price and stock.
5. Deterministic rules decide which offers qualify (identity, new condition, in stock, USD, unconditional price,
   reading at most 120 seconds old) and rank them.
6. The page shows the lowest verified in-stock offer **among the stores checked**, the next verified offer
   (`result.runner_up`), the cheaper-looking listing that lost with the exact reason, the coverage (which sources
   answered), and on request every model receipt. If the winning price is 40% or more below the median of at least
   three verified offers, a caution says so (`result.caution`): "verified" states what the merchant's storefront
   says about this item, it is not a rating of the merchant.
7. A note such as "Notify me when this seller restocks under $25" is compiled into a typed rule, tested against
   generated counterexample timelines and shown as an in-app watch. Without an authorized merchant feed it is a
   clearly labelled rule preview; nothing is polled.

Models search, read and disambiguate. Merchant facts and executable rules decide. No model output is ever used as
a price or a stock state. A model's say-so never creates identity: it takes printed evidence, a merchant barcode or
the buyer's own choice. (A fourth basis, two agreeing model judgments, was built and measured today and is switched
off in the shipped build; see `visual_match` below.)

## Runtime model jobs

Every call is a real call to the Gemini API through `google-genai`. There is no mock or canned answer path in the
application and no model answer is cached; a failed call is shown as a failure. (The only cache anywhere is the
120-second in-memory copy of a merchant storefront response, see Data sources.)

| Job | Model | Where it lives |
|---|---|---|
| Read the photo: object, brand, printed identifiers, variant, focus box | `gemini-4-neon` | `app/models/__init__.py::inspect` (prompt and schema: `reference/deal_models.py` `VIEW` / `Visual`) |
| Gate: is exactly one target clear? Calibrated probabilities for `single / multiple / other`; pass needs `single` at confidence >= 0.75 | `gemini-decision-1` (Decisions API) | `app/models/io.py::gate`; threshold in `app/config.py` |
| One automatic second look on a crop when the gate does not pass | `gemini-4-neon` + `gemini-decision-1` | `reference/visual_recovery.py::second_look`, called from `inspect` |
| Read the request (usually an empty note): compare or compare-and-watch, price basis, fulfillment | `gemini-4-carbon` | `app/models/__init__.py::parse_goal` (`reference/find.py` `INTENT` / `Intent`) |
| Bind the photo to one catalog product | `gemini-4-carbon`, then deterministic corroboration | `app/models/__init__.py::bind` and `corroborate` |
| Screen every catalog listing against the photo in ONE pass (a yes/no probability per listing), and check that the brand and model number Neon read are visibly printed | `gemini-decision-1` (Decisions API) | `app/models/__init__.py::screen`, called beside the bind in `app/pipeline.py`. It orders question options, annotates offers (`screen_p`), limits which listing checks are worth running, and refuses a model-number bind when the printed number scores below 0.75. (It is also one of the two judgments behind `visual_match`, which is off by default) |
| Check a listing against the bound product (listing check v2): one Carbon verdict with one decisive quote per side; code then verifies each quote inside its own side (case-folded, whitespace-normalised) and anything unverifiable abstains | `gemini-4-carbon`, then a deterministic quote check | `app/models/__init__.py::pair`, used by `app/merchants/compare.py`. The first version (Neon extracts, Carbon judges, both must agree) is kept as `pair_v1` for the evaluation record |
| Compile an explicit watch request into a bounded typed rule | `gemini-4-carbon` | `app/models/__init__.py::compile_watch`, used by `app/watch/__init__.py` |
| Local barcode decode of the focus crop (no model) | zxing-cpp | `app/models/__init__.py::scan` |
| Offer eligibility and ranking (no model) | deterministic code | `app/merchants/ranking.py::evaluate` |
| Watch semantics, counterexample timelines, proof hash, alert ledger (no model) | deterministic code | `app/watch/__init__.py` over `reference/autowatch.py`, `proof.py`, `ledger.py`, `core.py` |
| Optional: try to break a watch rule in a hosted sandbox. Whatever the agent reports is replayed locally through the same evaluator and labelled simulation | Gemini Managed Agent (`gemini-4-neon` inside) | `app/optional/`, `POST /api/watch/{id}/challenge`. One real run on the demo server at 16:19 ET returned `no_counterexample` in 32 s; the agent reported 1554 timelines tried (its own count, not verified) and `replayed_locally` was false because there was no counterexample to replay |

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
   │            app.models.bind            Gemini 4 Carbon picks one anchor ─┐ run side by side
   │            app.models.screen          Gemini Decision 1 scores every listing against the photo in one pass ─┘
   │                                       accepted on: barcode equal | brand + printed model number
   │                                       ─▶ else ONE question, options ordered by the screen (visual_match: off by default)
   │ verifying  app.merchants.compare.run_comparison
   │              ├─ app.merchants.chains.confirm   merchant's own storefront: exact variant, price, stock, barcode
   │              ├─ app.models.pair                Carbon listing check + verified quotes (grants the model_number tier only)
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
   published barcode contradicts it, and the listing check returns `match`.

Everything else is shown as a **lead** and is never ranked or called verified.

Those tiers decide whether an *offer* is the bound product. The *photo* is bound to a catalog product on one of three
bases, shown on the result: `barcode`, `model_number`, or `buyer_choice` (the shopper answered the question).

**`visual_match`, built today and switched off.** What happened, in order: the calibration of the Decision 1 screen on
blind-labelled listings (table below) showed no false accept at 0.75 and above. On that basis a fourth binding rule
was added: accept Carbon's pick when the screen independently ranks the same product first at 0.75 or more and at
least 0.25 ahead of the next (`VISUAL_MATCH_P`, `VISUAL_MATCH_MARGIN` in `app/pipeline.py`). A red-team run then got
it to bind and price a different colour variant, from a hue-shifted photo, at a screen probability of 0.85 with no
question. The rule was switched off; `SF_VISUAL_MATCH=1` re-enables it. Benchmark rows that ran with it on are
labelled "visual_match on".

So the identity rule in the shipped build is: printed evidence (a barcode, or brand plus a model number that Decision 1
confirms is printed), a merchant-published barcode, the bound catalog product, or the buyer's one tap.

## Run it

Python 3.12.

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt -r requirements-dev.txt   # runtime + pytest, pinned
printf 'GEMINI_API_KEY=your-key\n' > .env        # read by app/config.py; .env is gitignored
.venv/bin/uvicorn app.main:app --port 8000        # then open http://localhost:8000
.venv/bin/python -m pytest tests -q               # offline tests; fakes exist only under tests/
.venv/bin/python scripts/smoke.py path/to/photo.jpg   # three real model calls on one photo
```

Versions used at the event: google-genai 3.3.0, FastAPI 0.162.7, Uvicorn 0.54.0, Pydantic 2.14.3, Pillow 13.0.0,
zxing-cpp 3.1.1.

Settings (environment): `SF_DB` (SQLite path), `SF_STORE_INTERACTIONS` (see Privacy), `SIGNALFORGE_WATCH_FEED`
(an authorized merchant feed; without it every watch is a preview), `SF_MODEL_TIMEOUT_MS`, `SF_DECISION_TIMEOUT_MS`,
`SF_THINKING_VIEW` / `_GOAL` / `_BIND` / `_WATCH` (`low`, `medium`, `high` or `xhigh`), `SF_VISUAL_MATCH` (`1` = on; off by default).

HTTP surface (`app/main.py`): `POST /api/search` (multipart `image` up to 5 MB, optional `note`),
`GET /api/search/{id}`, `POST /api/search/{id}/answer`, `GET /api/search/{id}/proof`, `POST /api/search/{id}/watch`,
`GET /api/watch/{id}`, `POST /api/watch/{id}/pause`, `POST /api/watch/{id}/whatif`, `POST /api/watch/{id}/challenge`,
`GET /api/proof/eval`, `GET /api/health`. Runs belong to
the browser session that created them.

Mobile: the same web app, installable to a phone home screen (`app/web/manifest.webmanifest` plus icons; no service
worker, no native app). QR code to the public URL: `docs/demo/qr.png`.

## Evaluation

The numbers below are inserted by `python scripts/readme_numbers.py` from `eval/results/summary.json`, which
`eval/summarize.py` builds from the raw per-case results of a real run. Nothing in this section is typed by hand.
Protocol: [eval/PROTOCOL.md](eval/PROTOCOL.md).

<!-- EVAL:START (written by scripts/readme_numbers.py; do not edit by hand) -->
**Summary.** Strongest: 0 false matches in every arm of every listing-check run (5 arm runs, each 0/20 on the actually different pairs). Decision 1 listing screen on the first blind draw: ROC AUC 0.9875 over 240 listing judgments, median 384 ms per call. Weakest, stated as plainly: on the held-out pair draw the listing check is correct on 35/40 against 36/40 for one Carbon call. End to end on held-out blind catalog photos one Carbon call names the right product on 21/24; the full system on 1/24 with zero taps and 8/24 with one simulated confirming tap.

**Experiment A: WDC v2 product-pair identity (independent public gold): is this listing the exact same product?** An abstention or an error counts as incorrect.

- Draw A: 40 pairs, frozen 2026-11-21T20:37:17+0000 before any arm ran on it, seed `0e6fbd26c128`.
- Draw B: 40 pairs, frozen 2026-11-21T21:01:59+0000 before any arm ran on it, seed `294e9214059d` (sha256 of eval/results/pairs-v1.json).

| Run | Arm | Correct | Wrong answers | Abstained | Runtime errors | False matches / actual different | Median ms | p90 ms |
|---|---|---|---|---|---|---|---|---|
| v1 · draw A, code `6cf1194` | Baseline: one Gemini 4 Carbon call | 34/40 | 0 | 6 | 0 | 0/20 | 4516 | 6382 |
| v1 · draw A, code `6cf1194` | System listing check (`app.models.pair v1-neon-carbon-agreement`) | 30/40 | 0 | 10 | 0 | 0/20 | 10779 | 14174 |
| v2 · draw A · IN-SAMPLE: the change was chosen after seeing v1 on these packets, code `6fc8711` | Baseline: one Gemini 4 Carbon call (from pairs-v1.json (not re-run)) | 34/40 | 0 | 6 | 0 | 0/20 | 4516 | 6382 |
| v2 · draw A · IN-SAMPLE: the change was chosen after seeing v1 on these packets, code `6fc8711` | System listing check (`app.models.pair v2-carbon-verified-quotes`) | 33/40 | 0 | 7 | 0 | 0/20 | 4359 | 6077 |
| v2 · held-out draw B (new seed, disjoint from A, frozen before any call), code `6fc8711` | Baseline: one Gemini 4 Carbon call | 36/40 | 0 | 4 | 0 | 0/20 | 4326 | 5893 |
| v2 · held-out draw B (new seed, disjoint from A, frozen before any call), code `6fc8711` | System listing check (`app.models.pair v2-carbon-verified-quotes`) | 35/40 | 0 | 5 | 0 | 0/20 | 4418 | 6758 |

- v1 · draw A: paired on the same pairs, both correct 30, only the baseline 4, only the system 0, neither 6.
- v2 · draw A · IN-SAMPLE: the change was chosen after seeing v1 on these packets: paired on the same pairs, both correct 33, only the baseline 1, only the system 0, neither 6.
- v2 · held-out draw B (new seed, disjoint from A, frozen before any call): paired on the same pairs, both correct 35, only the baseline 1, only the system 0, neither 4.

Reading written by `eval/summarize.py`:

- v1 · draw A: the system listing check trails a single Carbon call on correctness (30/40 vs 34/40); false accepts 0/20 vs 0/20; abstentions 10 vs 6.
- v2 · draw A · IN-SAMPLE: the change was chosen after seeing v1 on these packets: the system listing check trails a single Carbon call on correctness (33/40 vs 34/40); false accepts 0/20 vs 0/20; abstentions 7 vs 6.
- v2 · held-out draw B (new seed, disjoint from A, frozen before any call): the system listing check trails a single Carbon call on correctness (35/40 vs 36/40); false accepts 0/20 vs 0/20; abstentions 5 vs 4.

Latency: ms = the final successful attempt only; HTTP 429 back-off sleeps are excluded (bug 007), wall_ms kept per case.

**End-to-end benchmark (photo in, offer out):** catalog photos drawn blind and frozen before any arm ran on them, blank note, no follow-up answer given, so a question from the app counts as no answer. Baseline: one Gemini 4 Carbon call handed the photo and a list of listings. Protocol: `eval/e2e/README.md`. Headline run: `b`.

| Run | Arm | Correct product | Wrong product | Stopped without an answer | Errors | Invalid offers / answered | Cheapest agrees with reference | Median ms |
|---|---|---|---|---|---|---|---|---|
| Draw A, first run: strict printed-evidence identity (the visual_match basis did not exist yet), code `6fc8711` plus uncommitted changes, visual_match off | SignalForge full system, zero taps | 0/24 | 1/24 | 23/24 | 0/24 | 0/1 | 0/24 | 14360 |
| Draw A, first run: strict printed-evidence identity (the visual_match basis did not exist yet), code `6fc8711` plus uncommitted changes, visual_match off | Baseline: one Gemini 4 Carbon call | 20/24 | 0/24 | 4/24 | 0/24 | 1/20 | 12/24 | 4529 |
| Draw A again after the visual_match change, system arm only. IN-SAMPLE: the change and its 0.75 threshold were read off this draw's own results and calibration table (in-sample), code `8906319` plus uncommitted changes, visual_match on | SignalForge full system, zero taps | 1/24 | 1/24 | 22/24 | 0/24 | 0/2 | 1/24 | 15276 |
| Draw A again after the visual_match change, system arm only. IN-SAMPLE: the change and its 0.75 threshold were read off this draw's own results and calibration table (in-sample), code `8906319` plus uncommitted changes, visual_match on | SignalForge with ONE tap: a simulated buyer answers one choice question using the gold label (this arm: code `10d8e58` plus uncommitted changes, visual_match off) | 12/24 | 1/24 | 11/24 | 0/24 | 0/13 | 12/24 | 14650 |
| Draw A again after the visual_match change, system arm only. IN-SAMPLE: the change and its 0.75 threshold were read off this draw's own results and calibration table (in-sample), code `8906319` plus uncommitted changes, visual_match on | Baseline: one Gemini 4 Carbon call (from e2e-v1.json (not re-run)) | 20/24 | 0/24 | 4/24 | 0/24 | 1/20 | 12/24 | 4529 |
| Held-out draw B: new products, frozen before any call, both arms (held-out), code `8906319` plus uncommitted changes, visual_match on | SignalForge full system, zero taps | 1/24 | 1/24 | 22/24 | 0/24 | 0/2 | 0/24 | 15296 |
| Held-out draw B: new products, frozen before any call, both arms (held-out), code `8906319` plus uncommitted changes, visual_match on | SignalForge with ONE tap: a simulated buyer answers one choice question using the gold label (this arm: code `5bc338c` plus uncommitted changes, visual_match on) | 8/24 | 1/24 | 15/24 | 0/24 | 0/9 | 7/24 | 15605 |
| Held-out draw B: new products, frozen before any call, both arms (held-out), code `8906319` plus uncommitted changes, visual_match on | Baseline: one Gemini 4 Carbon call | 21/24 | 1/24 | 2/24 | 0/24 | 4/22 | 16/24 | 4559 |

One-tap arm of Draw A again after the visual_match change, system arm only. IN-SAMPLE: the change and its 0.75 threshold were read off this draw's own results and calibration table: cases 24, choice questions 21, text questions 2, answered without question 1, gold offered 14, gold first 11, answered gold 14, answered none 7. Note recorded with the run: system_one_tap: the system run plus at most one answer to a choice question, given by a simulated buyer who knows what they are holding (the gold option if it is offered, otherwise 'None of these'). The tap uses the gold label, so this arm measures the system given one correct human confirmation; the baseline arm gets no such help. It is a separate fresh run of the system, so its first leg can differ from the zero-tap system row, and it carries its own code SHA and SF_VISUAL_MATCH setting: the one-tap run on draw B had visual_match on, the one on draw A ran after the shipped default became off.

One-tap arm of Held-out draw B: new products, frozen before any call, both arms: cases 24, choice questions 17, text questions 5, answered without question 2, gold offered 11, gold first 8, answered gold 11, answered none 6. Note recorded with the run: system_one_tap: the system run plus at most one answer to a choice question, given by a simulated buyer who knows what they are holding (the gold option if it is offered, otherwise 'None of these'). The tap uses the gold label, so this arm measures the system given one correct human confirmation; the baseline arm gets no such help. It is a separate fresh run of the system, so its first leg can differ from the zero-tap system row, and it carries its own code SHA and SF_VISUAL_MATCH setting: the one-tap run on draw B had visual_match on, the one on draw A ran after the shipped default became off.

Reading of Draw A, first run: strict printed-evidence identity (the visual_match basis did not exist yet), written by the benchmark script itself (`eval/e2e`):

- Full system: answered 1 of 24 photos (0 the gold product, 1 another catalog product id), stopped without an answer on 23, 0 errors; 0 of its 1 answered offers were invalid.
- One-call baseline: answered 20 of 24 (20 the gold product, 0 another product), abstained on 4, 0 errors; 1 of its 20 answered offers were invalid and 12 matched the reference cheapest valid offer.
- False accepts by catalog product id, system: 1 strict, of which 1 carry mechanical evidence of being the same item under another catalog product id (same model number or identical catalog title) and 0 carry none.
- False accepts by catalog product id, baseline: 0 strict, of which 0 carry mechanical evidence of being the same item under another catalog product id (same model number or identical catalog title) and 0 carry none.

Reading of Draw A again after the visual_match change, system arm only. IN-SAMPLE: the change and its 0.75 threshold were read off this draw's own results and calibration table, written by the benchmark script itself (`eval/e2e`):

- Full system: answered 2 of 24 photos (1 the gold product, 1 another catalog product id), stopped without an answer on 22, 0 errors; 0 of its 2 answered offers were invalid.
- One-call baseline: answered 20 of 24 (20 the gold product, 0 another product), abstained on 4, 0 errors; 1 of its 20 answered offers were invalid and 12 matched the reference cheapest valid offer.
- With one correct tap (system_one_tap, the tap uses the gold label; this arm ran on code 10d8e58be457 with SF_VISUAL_MATCH off (the shipped default)): 12 correct (12 through the tap, 0 without one), 1 false accepts, 11 abstained, 0 errors of 24; 0 of its 13 answered offers were invalid and 12 matched the reference cheapest valid offer. The system asked a choice question in 21 of 24 cases; the gold product was among the options in 14 and was the first option (ordered by the Decision 1 screen) in 11; 2 gold taps ended with no verified offer. The baseline arm gets no such help.
- Why visual_match did not fire on the 20 abstentions that reached the screen (post-hoc, from the probabilities on the question's options): screen_margin_below_threshold x8, screen_top_below_threshold x11, second_model_pick_differs_or_missing x1. The gold product was the top-scored option in 10 of them and among the options in 12.
- False accepts by catalog product id, system: 1 strict, of which 1 carry mechanical evidence of being the same item under another catalog product id (same model number or identical catalog title) and 0 carry none.
- False accepts by catalog product id, system_one_tap: 1 strict, of which 1 carry mechanical evidence of being the same item under another catalog product id (same model number or identical catalog title) and 0 carry none.
- Documented next step (a rule without the lead requirement), NOT deployed and NOT a score: not computed: this run's recording never shows the second model's pick differing, so it cannot tell whether the pick agreed with the top-scored product.

Reading of Held-out draw B: new products, frozen before any call, both arms, written by the benchmark script itself (`eval/e2e`):

- Full system: answered 2 of 24 photos (1 the gold product, 1 another catalog product id), stopped without an answer on 22, 0 errors; 0 of its 2 answered offers were invalid.
- One-call baseline: answered 22 of 24 (21 the gold product, 1 another product), abstained on 2, 0 errors; 4 of its 22 answered offers were invalid and 16 matched the reference cheapest valid offer.
- With one correct tap (system_one_tap, the tap uses the gold label; this arm ran on code 5bc338c6e604 with SF_VISUAL_MATCH on): 8 correct (7 through the tap, 1 without one), 1 false accepts, 15 abstained, 0 errors of 24; 0 of its 9 answered offers were invalid and 7 matched the reference cheapest valid offer. The system asked a choice question in 17 of 24 cases; the gold product was among the options in 11 and was the first option (ordered by the Decision 1 screen) in 8; 4 gold taps ended with no verified offer. The baseline arm gets no such help.
- Why visual_match did not fire on the 17 abstentions that reached the screen (post-hoc, from the probabilities on the question's options): screen_margin_below_threshold x3, screen_top_below_threshold x10, second_model_pick_differs_or_missing x4. The gold product was the top-scored option in 5 of them and among the options in 12.
- False accepts by catalog product id, system: 1 strict, of which 1 carry mechanical evidence of being the same item under another catalog product id (same model number or identical catalog title) and 0 carry none.
- False accepts by catalog product id, baseline: 1 strict, of which 0 carry mechanical evidence of being the same item under another catalog product id (same model number or identical catalog title) and 1 carry none.
- False accepts by catalog product id, system_one_tap: 1 strict, of which 1 carry mechanical evidence of being the same item under another catalog product id (same model number or identical catalog title) and 0 carry none.
- Documented next step, NOT deployed and NOT a score (computed from the recorded runs, nothing re-run): a rule without the lead requirement (top catalog product's screen probability >= 0.75 and the second model picked the same product; no lead requirement) would have accepted 2 of 24 cases (1 of them also accepted by the deployed rule): 1 the gold product, 1 another catalog product id.

**Decision 1 listing screen, calibration on draw A** (run `screen-v1`, `gemini-decision-1`, one call per case, 24 cases): 240 listing judgments (22 positive, 218 negative), 0 failed calls, ROC AUC 0.9875, Brier 0.0325, top-scored listing is the drawn product in 19/22 cases that had one.

| Threshold | True accepts | False accepts | Missed | Precision | Recall | False-accept rate |
|---|---|---|---|---|---|---|
| p >= 0.5 | 15 | 1 | 7 | 0.9375 | 0.6818 | 0.0046 |
| p >= 0.75 | 12 | 0 | 10 | 1.0 | 0.5455 | 0.0 |
| p >= 0.9 | 2 | 0 | 20 | 1.0 | 0.0909 | 0.0 |

**Decision 1 listing screen, calibration on draw B** (run `screen-b`, `gemini-decision-1`, one call per case, 24 cases): 240 listing judgments (23 positive, 217 negative), 0 failed calls, ROC AUC 0.9882, Brier 0.035, top-scored listing is the drawn product in 20/23 cases that had one.

| Threshold | True accepts | False accepts | Missed | Precision | Recall | False-accept rate |
|---|---|---|---|---|---|---|
| p >= 0.5 | 15 | 1 | 8 | 0.9375 | 0.6522 | 0.0046 |
| p >= 0.75 | 11 | 0 | 12 | 1.0 | 0.4783 | 0.0 |
| p >= 0.9 | 0 | 0 | 23 | unknown | 0.0 | 0.0 |

**Experiment B (five rights-cleared photo-to-offer cases):** status `not_run`. The five-case photo-to-offer check (eval/seal_baskets.py; baseline basket_answer(packet, False) vs system basket_answer(packet, True)) was not run: rights-cleared merchant evidence for five preregistered cases could not be assembled before the 16:05 ET proof cutoff. No case, price or score was substituted.

Source files: `eval/results/summary.json`, `eval/results/pairs-v1.json`, `eval/results/pairs-v2.json`, `eval/results/pairs-b-v2.json`, `eval/results/baskets-gap.json`, `eval/results/e2e-summary.json`, `eval/results/e2e-freeze.json`, `eval/results/e2e-freeze-b.json`.
<!-- EVAL:END -->

How to read it:

- Experiment A tests one component, the listing check (`app.models.pair`), on independently labelled public pairs:
  the WDC Product Data Corpus v2 gold standard (cameras and computers). Every arm sees the same text packet; ids,
  clusters and labels are withheld. It is a text matching check. It is not a measure of photo reading or live stock.
- The baseline is a single Gemini 4 Carbon call. Listing check v1 added Neon extraction, agreement between the two
  models and a verbatim-quote check. After v1 trailed the baseline, the check was changed once (v2: one Carbon call
  plus deterministic quote verification). A v2 run on draw A is in-sample, because the change was chosen after
  seeing v1 fail on those same pairs; only a run on the separately frozen draw B is a held-out result for v2.
- Read the table as it is. Where the system arm scores below the baseline, the difference is abstentions. In the
  product an abstention keeps a listing as an unranked lead; a false match would put a wrong item at the top.
- The end-to-end benchmark runs the whole pipeline on catalog photos drawn blind, with a blank note and **no
  follow-up answer**, and compares it with one Carbon call that is handed the photo and a list of listings. The
  benchmark gives no answers, so every question the app asks counts as no answer.
- **The single call wins it clearly, on the first draw and on the held-out draw.** With zero taps, one Carbon call
  that sees the photo and the catalog rows names the right product on most studio photos. The full system refuses to
  name a product without printed evidence, a merchant barcode or the buyer's choice, and asks one question instead,
  on almost every photo.
- The first run is why `visual_match` was tried. Rows marked "visual_match on" were measured with that switch on:
  the row on draw A is in-sample (the change and its threshold were read off that draw), the row on the separately
  frozen draw B is held-out. They show it firing rarely. **The shipped build has `visual_match` off** (see above),
  so it behaves like the strict "visual_match off" row, not like the "on" rows.
- The "ONE tap" row answers the app's choice question once, with a simulated buyer who picks the drawn product when
  it is among the options and "none" otherwise; text questions stay unanswered. It is an upper bound on what one
  honest tap can do, not a measurement of real shoppers, and its label says which code and settings it ran on.
- What the single call does less well is in the same table: its chosen offer agrees with the reference cheapest valid
  offer on fewer photos than it names the right product, and some of its offers were contradicted by the merchant's
  own storefront (invalid offers). The full system's offers are storefront-confirmed, but it answered so few photos
  that its offer columns carry almost no information.
- The Decision 1 listing screen was calibrated on the benchmark photos after the first two-arm run, and the
  `visual_match` thresholds were chosen from that calibration. It is a calibration of one signal, not a re-score.
- The benchmark photos are the catalog's own images, not shopper snapshots of a box.
- The public WDC labels may be familiar to a pretrained model.

## Limitations and failure cases

- **A bare product photo is the weak case, and with zero taps a single model call beats this build on it.** On
  blind-drawn catalog photos the full system stopped and asked a question on almost every photo, on the first draw
  and on the held-out draw, while one Carbon call named the right product on most of them (end-to-end table above).
  The path the product is designed around is a photo of the box or label with the model number printed, or one
  confirming tap on the question. The one-tap row in the table measures that second path with a simulated buyer who
  taps the right option whenever it is offered; even then the single call names the right product more often.
- **The attempt to fix that, `visual_match`, failed a red-team test and is off.** With it on, a hue-shifted photo of
  a different colourway was bound and priced with no question. Off is the default in the code and on the demo.
- **A different colourway can be priced as the exact item.** The Shop catalog can group two colourways under one
  catalog product, and neither the catalog product id nor the Decision 1 screen separates them. Seen in a soak run
  on the frozen build (`run_a7e5593133884fd78086070018138daf`): the photo showed one colourway of a shoe, the single
  "verified" offer was another colourway of the same catalog product, and the target barcode had been taken from
  that other variant, so the `merchant_barcode` tier confirmed itself. No code change was made; the result card
  states the basis and links to the merchant page so the variant can be checked there.
- **The Decision 1 screen does not separate an own-brand copy from the original when no number is visible** (seen in
  a red-team run), which is one more reason it only orders options and never binds on its own.
- **"Lowest verified among the stores checked" is not a market-wide claim.** The stores checked are the sellers in
  the Shop catalog cluster for the product and those merchants' own Shopify storefronts. Lookup by UPC is not
  available from the catalog. Best Buy, Walmart, Target,
  Amazon, eBay and Google Shopping are not connected (no approved access at the event); see the table below.
- **"Verified" does not vouch for a merchant.** It means the merchant's own storefront states this price and stock
  for this exact item. A store that lists a real product at an implausible price can win; the price-outlier caution
  is the only signal shown for that.
- **Item price only.** USD, before shipping and tax, US online. The catalog provides no shipping, tax, ZIP or pickup
  data, so a request for a delivered total, a ZIP or store pickup is reported as "Not compared" and the run ends
  `partial` with item prices.
- **No catalog barcode.** Identity rests on the tiers above. An item with no printed model number and no barcode
  gets a question, not a guess. A listing whose identity check abstains or runs out of time stays a lead,
  so a genuinely cheaper offer can be left unranked.
- **The listing check is conservative.** In the first evaluation run every system failure was an abstention on a
  truly matching pair: Carbon abstains when no manufacturer model number is printed, the strict quote check rejected
  two genuine quotes, and one case flipped on a re-run. Case-by-case notes: [eval/CHANGELOG.md](eval/CHANGELOG.md).
- **Catalog clusters are fragmented**: the same physical item can appear as several catalog products, so coverage of
  sellers can be incomplete. Non-USD sellers that appear in a US search are rejected, not converted.
- **Listing checks are capped in a live search.** Only a limited number are started per search and they share a
  time cap (`MAX_PAIR_CALLS` and `PAIR_PHASE_S` in `app/merchants/compare.py`). A check that is not started or does
  not finish leaves its listing as a lead, and the coverage row says how many.
- **Watches are previews in this build** unless an authorized feed for the exact seller and variant is configured.
  A watch covers one named seller and one variant; "any store" is refused with an explanation. What-if timelines are
  simulations and are labelled `simulation`. (At 15:58 ET an explicit watch sentence came back with a question
  instead of a rule; the watch lane fixed that, and on the frozen build the same sentence returned a preview with a
  proof hash and no question in a signed-out run at 16:27 ET.)
- **Decision 1 responses carry no id**, so the gate receipt shows no id; nothing is invented in its place.
- **Not instant.** One photo takes several model calls; the page shows the real elapsed time of every step.
- **Experiment B (five rights-cleared photo-to-offer cases with human-adjudicated prices) was not run**; the reason
  is recorded in `eval/results/baskets-gap.json` and repeated above.
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
| Merchant's own Shopify storefront (`/products/<handle>.js`) | confirm the exact variant, price, stock and published barcode | yes | yes | at most one GET per listing per 120 s, only when a shopper's search needs it | process-wide memory cache, at most 120 s (a second search within that time can reuse it), never on disk | no rights record, so not used for evaluation |
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
