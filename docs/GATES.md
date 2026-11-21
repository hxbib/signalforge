# Release gates: walk of ops/gates.csv

Walked line by line against the repository and the running demo (https://reviewing-look-liz-course.trycloudflare.com) at **16:29 ET**,
on the frozen build (server restarted 16:27:31 ET with `SF_VISUAL_MATCH=0`, `SF_STORE_INTERACTIONS=delete`; restarted
once more at 16:34:14 ET with the same settings, after these runs; `/api/health` → 200 and all lanes loaded at 16:34 ET).
PASS means the evidence below was produced by a command or a real run today. NOT-YET means the evidence does not
exist yet. FAIL means the evidence contradicts the gate. An earlier walk at 16:01 ET found two failures (the explicit
watch asked a question; a failed catalog call was reported as ok); both were fixed by their lanes and re-checked here.

Live evidence (public demo URL, fresh signed-out session each, blank note):
- Run A `run_5056653de9e9…` at 16:27 ET, photo of a LEGO 75375 box: state `partial` after 21.9 s. Neon read "LEGO 75375";
  Decision 1 gate `single`, confidence 0.96, pass; Carbon bound the catalog product on brand plus printed model number;
  the Decision 1 screen scored 18 listings in one call (647 ms); winner "Lowest verified among the stores checked"
  3050 USD, Eastline Collective, in stock, new, identity `model_number`, confirmed by the merchant storefront; a caution
  that this price is 64% below the median of the 6 verified offers; next verified offer 6999 USD (PzDeals); 4 listings
  rejected "The catalog and the merchant's own page disagree"; 8 leads; coverage: shop_catalog ok, merchant_storefronts
  limited ("7 of 12 merchants confirmed; 4 disagreed; 1 unavailable; 1 not checked (limit)"), listing_checks ok
  ("2 checked, 2 matched", 5881 ms).
- Watch on Run A, "Notify me when this seller restocks under $25": HTTP 200, status `preview_only`, scope "Watching
  Eastline Collective for a restock below $25.00", rule restock / lt / 2500 on the seller and variant ids, origin
  `explicit_request`, a proof hash, no question, playground rows all `simulation: true` (a price drop while in stock does
  not alert; a restock just under the cap alerts). Pause → `paused`, and the run JSON shows `paused` afterwards.
- Run B `run_f0e1defdf48d…` at 16:28 ET, photo with two products: gate `multiple`, confidence 0.90, no pass; state
  `needs_detail` after 13.3 s with one text question; 8 leads, nothing ranked.
- Run C `run_fa45d96545ab…` at 16:28 ET, headphones with no printed model number: gate `single` 0.97; bind "No exact
  product could be confirmed from the photo alone"; state `needs_detail` after 29.0 s with one choice question whose
  options are catalog products; nothing ranked. (Neon's read took 22.9 s on this photo.)
- A run that was in flight during the 16:27 restart ended `unavailable` with "This search was interrupted by a server
  restart. Please try again."
- Another session reading any of these runs → 404.
- Offline tests at 16:29 ET: `.venv/bin/python -m pytest tests -q` → 324 passed, 3 warnings, 19 subtests passed in 14.41s;
  `.venv/bin/python -m pytest reference -q` → 57 passed in 0.75s.

| Gate | Acceptance | Status | Evidence |
|---|---|---|---|
| scope | New event work and prior art explicitly named | PASS | docs/PROVENANCE.md names the two layers, the commit range and the prior work (Dealza, Curzoh Task Engine, monitord). The prior-code search found no overlap; the Dealza repository is empty, so that part rests on the author's account. |
| models | Real Neon and Carbon SDK responses and distinct jobs | PASS | Run A receipts: view `gemini-4-neon` 8383 ms; goal, bind and two listing-check verdicts `gemini-4-carbon`; each with the interaction id the service issued, `retained: false`. |
| decisions | Real Decision 1 choice, score and branch on a fresh photo | PASS | Run A: `single` 0.96 → proceeds. Run B: `multiple` 0.90 → question. Listing screen: one call, 18 listings. The Decisions API issues no id; receipts show none. |
| zero | One fresh photo and blank note yields result without routine questions | PASS for a photo with a printed model number | Run A: blank note, zero questions, result in 21.9 s. Run C (no printed number) gets a question; the end-to-end benchmark shows that this is the common case for bare product photos. |
| ambig | Ambiguity gets one recovery and at most one relevant question per turn | PASS (question), NOT-YET (recovery seen live) | Runs B and C each asked exactly one question. No automatic second look ran on these photos; the second look is covered offline by `reference/test_visual_recovery.py`. |
| exact | Exact SKU, seller and edition with corroborating source | PASS | Run A: target GTIN 00673419389488 established from merchant storefront barcodes; winner confirmed by the merchant storefront (`confirmed_by: merchant_api`), identity `model_number` after a listing check that matched. |
| sponsor | Real shop.dev lookup receipt and attributed merchant link | PASS | Run A source receipts: `search_catalog` ×2 (text, image) and `get_product` ×4, status ok; the winner links to the merchant's own product URL as returned by the catalog. |
| major | Working direct merchant evidence or honestly reduced coverage | PASS | Merchant storefront confirmation works (Run A: 7 of 12 confirmed, 4 disagreed and were rejected). Best Buy, Walmart, Target, Amazon, eBay and Google Shopping are listed as not connected in docs/SOURCES_REGISTRY.md and the README. |
| price | In-stock only, ties, integer USD, condition and fulfillment | PASS | Ranking and compare tests pass (part of the suite above); Run A winner is in stock, new, integer cents, USD. A tie was seen live earlier today (`run_8814c1fbd915…`, status `tie`). |
| reasons | Status explains cheaper mismatch, missing total or no winner | PASS | Run A: four rejected listings each carry "The catalog and the merchant's own page disagree", plus the price-outlier caution. Runs B and C: "No verified offer yet". |
| fail | Timeout and rate limit retain partial result, stale evidence rejected | PASS | Run A is `partial` with a limited source and still shows its result; an interrupted run ends `unavailable` with the reason; stale rows are rejected in tests; the earlier open item (a failed catalog call reported as ok) is fixed and verified by the reviewer (internal bug note 004). A rate-limit response was not provoked live. |
| rights | Per-source rights for display, retention, background use and eval | PASS | docs/SOURCES_REGISTRY.md and the README table: display, repeated reads, retention and evaluation rights per source. No evaluation right is recorded for merchant data, which is why the five-case experiment was not run. |
| watch | Explicit watch automatically verified, authorized, pausable or preview | PASS | Watch on Run A (above): preview with scope sentence, typed rule, proof hash, no question, simulation rows; pause persists. It failed this gate at 16:01 ET (asked a question) and was fixed by the watch lane. |
| ledger | Retry and restart yield no duplicate alerts | PASS (offline) | tests/test_watch_lane.py (authorized feed activates, ledger dedupes) and reference/test_reference.py (restart replay, duplicate attempt, late duplicate) pass. Not exercised live: no authorized feed is connected. |
| pairs | WDC blind paired results and error denominator recorded | PASS | eval/results/summary.json: three runs on two frozen draws, abstentions and errors in the denominator; in the README and on /proof. |
| basket | Frozen five-case run or honestly reported evidence gap | PASS (gap reported) | eval/results/baskets-gap.json: status `not_run` with reasons, repeated in the README. A separate blind end-to-end benchmark on catalog photos was run instead and is reported with its unfavourable result (eval/results/e2e-summary.json). |
| failure | Genuine failure analysis and code version for improvements | PASS | eval/CHANGELOG.md reads the listing-check failures case by case; the one change (listing check v2) is reported in-sample and held-out with code versions. The end-to-end failures are diagnosed post hoc (eval/results/e2e-diagnostic-*.json), and the attempted fix (`visual_match`) is reported as measured and then switched off. |
| live | Fresh input by operator and original evidence view on stage | NOT-YET | Happens on stage. Script: docs/STAGE.md. |
| repo | Public repo and in-event provenance checked signed out | PASS (reachable), NOT-YET (final push) | Without credentials: https://github.com/hxbib/signalforge → 200 and the raw README.md on `main` → 200 at 16:29 ET. The README, docs/PROVENANCE.md and docs/GATES.md edited after the last commit are on disk and still need the lead's final commit and push. |
| video | One-minute actual demo video tested signed out | PASS (link opens), NOT-YET (final take pushed) | https://github.com/hxbib/signalforge/blob/main/docs/demo/signalforge-demo.mp4 → 200 without credentials at 16:33 ET; it is linked from README.md and docs/SUBMISSION.md. The take recorded on the frozen build (58.7 s, 16:28–16:29 ET) is on disk and replaces the pushed file with the lead's final push. Watching it with sound in a signed-out window is a human step. |
| links | Demo URL accessible, human team roster on form | PASS (URL), NOT-YET (form) | From outside: `https://reviewing-look-liz-course.trycloudflare.com/api/health` → 200, `/` → 200, `/proof` → 200. The roster on the form is a human step. |
| receipt | Actual organizer confirmation saved | NOT-YET | Human step after submitting. |
