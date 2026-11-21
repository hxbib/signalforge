# End-to-end blind benchmark: the whole system vs one model call

The pair benchmark in `eval/` measures one component (is this listing the same product?) on an academic
gold standard. This one measures the whole product path, photo in, offer out, on product photos nobody
on the team chose, against the simplest thing that could work: one Gemini 4 Carbon call that is handed the
photo and a list of store listings and asked to pick the cheapest valid one.

No number is typed into this file. Print the table from the recorded files:

```
.venv/bin/python eval/e2e/report.py              # table, reason codes, failed cases, notes
.venv/bin/python eval/e2e/report.py --markdown   # the same table as Markdown
```

## Protocol

1. **Blind draw, frozen before any arm ran** (`draw.py` → `eval/results/e2e-freeze.json`).
   A list of 30 generic product-family queries was written first (it is in `draw.py` and in the freeze file).
   For each query, in order, the Shop catalog is searched (`app.merchants.shop.search(query, limit=10)`); among the
   returned universal products that have an image on the catalog's image host, one is picked by a RNG seeded with the
   code SHA and the query (`pick_order`); `shop.product(upid)` counts its sellers; its first catalog image is
   downloaded to `private/e2e/` (gitignored: merchant photos are never committed). The draw stops at 24 cases.
   The freeze file holds ids, hashes and counts only. `draw.py` refuses to run again once the freeze exists.
2. **Two arms on the same photo**, blank note, zero follow-up answers, one after the other (`run.py`):
   - `system`: the real pipeline in-process (`pipeline._guard`) with its own database
     (`SF_DB=data/e2e.sqlite`, `SF_STORE_INTERACTIONS=delete`). It has to find the product in the whole catalog
     from the photo, pass the gate, bind an identity, confirm with merchants and rank.
   - `baseline`: one catalog search by the draw query, then ONE Gemini 4 Carbon call
     (`app.models.io.structured`, default thinking level) with the photo and those listing rows
     (index, title, seller name, condition, stock, currency, integer cents), asked for the index of the cheapest
     valid offer for the exact pictured product (new, in stock, USD) or to abstain. No gate, no catalog
     corroboration, no merchant confirmation, no deterministic ranking.
   - Gold (the drawn product id) is never part of either arm's input.
3. **Scoring** (`e2e_score.py`, deterministic, unit-tested in `tests/test_e2e_scoring.py`):
   - identity: answered with the gold product → `correct`; answered with another product → `false_accept`;
     no answer → `abstained`; a failed run or exception → `error`.
   - offer validity for every answered case: the chosen row must be new, in stock and USD, and the merchant's
     own storefront is re-read at scoring time (`app.merchants.chains.confirm`: confirmed / mismatch /
     unavailable). A row that fails a field or is contradicted by the storefront is an invalid offer.
   - cheapest-valid agreement: the arm's price against the reference winner from
     `app.merchants.ranking.evaluate` over the gold product's rows, read right after the arms.
   - every frozen case stays in the denominator. Nothing is dropped, re-drawn or re-run after results are
     seen; a case interrupted mid-run is recorded as an error (`run.py` keeps a start/done journal for that).
4. **Output**: `eval/results/e2e-v1.json` (per case: outcome, reason codes, timings; no prices, no merchant
   text) and `eval/results/e2e-summary.json` (counts per arm, code SHA, listing-check version, a plain reading
   generated from the counts by `e2e_summary.py`, notes). `GET /api/proof/eval` serves the summary under `e2e`.

## Read the result with these caveats

- **Abstention is the expected answer on many of these photos.** They are merchants' studio shots; many show
  no printed model number or barcode. The product rule is that model confidence alone never creates identity,
  so the system stops and asks one question. The benchmark answers no question, so those cases count as
  abstained. The comparison that matters is coverage against false accepts, not the correct count alone.
- **The baseline has the easier search problem.** It receives the listing rows of the very query the product
  was drawn from (normally about ten products, one of them gold). The system gets only the photo.
- **Gold is a catalog product id.** The same physical item can exist in the catalog under another product id;
  an answer bound to such a duplicate is counted as a false accept here. No case was adjudicated by hand.
- **Cheapest-valid agreement is circular for the system arm**: the reference uses the same evaluator the
  system ranks with. For the system it tests binding and lane wiring; for the baseline it tests whether one
  model call follows the constraints. A system price below the reference (`cheaper_than_reference`) can be a
  listing verified outside the gold product's catalog cluster.
- **Small sample, one run.** 24 cases, each arm run once, live catalog and live merchant storefronts at that
  minute. Counts are reported as counts; no significance is claimed.
- The run shares model quota with the live demo: at most 2 cases in flight; on an HTTP 429 the runner waits
  30 s and continues with one. Latency was measured under that load.

## What happened during run v1 that a reader should know

- No case was dropped, re-drawn or re-run.
- The arms were started only after the listing check (`app.models.pair`) reached the version recorded in the
  result files (`pair_version`); the code SHA and whether the shared tree had uncommitted changes are recorded
  next to it.
- The runner's rate-limit detector matched a step duration whose digits happened to contain "429"; no
  rate-limit error occurred. The runner paused 30 s and ran one case at a time from that point on (two at a time
  before it). No result was affected; latency after that point was measured under less load of our own. The
  detector now reads error text only (tested).
- `diagnose.py` adds a post-hoc failure analysis (`eval/results/e2e-diagnostic-v1.json`): why the system
  abstained (flags from what it read in the photo) and, for a system false accept, whether the gold product's
  own catalog titles carry the model number the system bound on. It changes no score.

## Second experiment on the same draw: is the listing screen calibrated?

`screen.py` asks one more question of the same 24 frozen cases. For each case it reads the catalog search rows of
the draw query (at most 60), makes ONE `app.models.screen` call (Gemini Decision 1: a probability per listing
that it is the pictured item) with the case photo and a target built from the photo alone (what Gemini 4 Neon
read in the system-arm run: name, brand, model number, variant details; no catalog id), and labels every listing
from the draw: positive iff the row belongs to the gold catalog product id. Gold never enters the call.

`e2e_calibration.py` (pure, unit-tested) then computes, over all listing judgments: precision, recall and
false-accept rate at p >= 0.5, 0.75 and 0.9, a 5-bin reliability table (mean predicted vs observed positive
rate), ROC AUC as a rank statistic, the Brier score, and in how many cases the top-scored listing is the gold
product. Output: `eval/results/e2e-screen.json` (per listing: index, listing id, probability, confidence, label)
and the `screen` block of `e2e-summary.json`; `report.py` prints both tables.

- **Label noise**: the same product listed by a seller outside the gold catalog cluster counts as a negative, so
  precision is a lower bound and the false-accept rate an upper bound.
- Catalog search returned one row per product for these queries, so each case contributes few listings and at
  most a handful of positives; the positives are a small set. Counts are printed next to every rate.
- The rows are read again at screen time, so a case can have no positive when the gold product has dropped out
  of the search results; such cases stay in (all their listings are negatives).
- Every case stays in the denominator; a failed call is an error case. The run is not repeated.
- This was added after the two-arm results were known, on the same draw. The labels come from the draw, and no
  threshold in the product was tuned on these numbers before they were recorded.

## Reproduce

```
.venv/bin/python eval/e2e/draw.py     # only with no e2e-freeze.json: makes a NEW draw (live catalog, new photos)
.venv/bin/python eval/e2e/run.py      # both arms on the frozen cases; needs private/e2e/*.img and GEMINI_API_KEY
.venv/bin/python eval/e2e/screen.py    # one Decision 1 call per frozen case; writes e2e-screen.json, rebuilds the summary
.venv/bin/python eval/e2e/diagnose.py  # post-hoc flags from the private run journal (re-reads the catalog for false accepts)
.venv/bin/python eval/e2e/e2e_summary.py # rebuilds e2e-summary.json (counts, generated reading, notes) from e2e-v1.json; runs no arm
.venv/bin/python eval/e2e/report.py
.venv/bin/python -m pytest tests/test_e2e_scoring.py -q
```

The catalog and storefronts are live, so a later run sees different prices, stock and search results; the
recorded files are the record of this run. The photos are not in the repository; their SHA-256 hashes are
in the freeze file.
