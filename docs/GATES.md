# Release gates: walk of ops/gates.csv

Walked line by line against the repository and the running demo (https://reviewing-look-liz-course.trycloudflare.com) at **16:01 ET**.
PASS means the evidence below was produced by a command or a real run today. NOT-YET means the evidence does not
exist yet. FAIL means the evidence contradicts the gate. This file is refreshed at 16:25 ET.

Live evidence used below (public demo URL, fresh signed-out session, blank note):
- Run A `run_ea117edba92f…` at 15:58 ET, photo of a LEGO 75375 box: state `partial` after 23.7 s; Neon read "LEGO 75375";
  Decision 1 `single`, confidence 0.96, pass; Carbon bound the catalog product on "brand and printed model number";
  winner "Lowest verified among the stores checked" 8495 USD, Galactic Toys & Collectibles, in stock, new, identity
  `merchant_barcode`, confirmed by the merchant storefront; 3 listings rejected "The catalog and the merchant's own
  page disagree"; 11 leads; coverage: shop_catalog ok, merchant_storefronts limited ("8 of 12 merchants confirmed;
  3 disagreed; 1 unavailable; 2 not checked (limit)"), listing_checks limited ("6 checked, 0 matched; 6 did not finish in time").
- Run B `run_6ca7fd987b2e…` at 16:00 ET, photo with two products: Decision 1 `multiple`, confidence 0.90, no pass;
  state `needs_detail` after 10.7 s with one question ("I can see more than one possible item. Which one should I
  compare?"); 8 leads shown, nothing ranked.
- Offline tests at 15:57 ET: `.venv/bin/python -m pytest tests -q` → 123 passed, 19 subtests passed;
  `.venv/bin/python -m pytest reference -q` → 57 passed.

| Gate | Acceptance | Status | Evidence |
|---|---|---|---|
| scope | New event work and prior art explicitly named | PASS | docs/PROVENANCE.md names the two layers, the commit range and the prior work (Dealza, Curzoh Task Engine, monitord). Prior-code search found no overlap; the Dealza repository is empty, so that part rests on the author's account. |
| models | Real Neon and Carbon SDK responses and distinct jobs | PASS | Run A receipts: view `gemini-4-neon` 8323 ms, goal `gemini-4-carbon` 4223 ms, bind `gemini-4-carbon` 5001 ms, each with the interaction id the service issued. |
| decisions | Real Decision 1 choice, score and branch on a fresh photo | PASS | Run A: `single` 0.96 → proceeds. Run B: `multiple` 0.90 → question. The Decisions API issues no id; the receipt id is null and shown as such. |
| zero | One fresh photo and blank note yields result without routine questions | PASS | Run A: blank note, zero questions, result in 23.7 s. |
| ambig | Ambiguity gets one recovery and at most one relevant question per turn | PASS (question), NOT-YET (recovery seen live) | Run B asked exactly one question. No automatic second look ran on that photo (no crop was proposed); the second look is covered by `reference/test_visual_recovery.py` only. |
| exact | Exact SKU, seller and edition with corroborating source | PASS | Run A winner: identity `merchant_barcode` (the merchant's storefront publishes the barcode equal to the target GTIN 00673419389488), confirmed_by `merchant_api`. |
| sponsor | Real shop.dev lookup receipt and attributed merchant link | PASS | Run A source receipts: `search_catalog` (text and image) and `get_product`, status ok, with response hashes; the winner links to the merchant's own product URL as returned by the catalog. |
| major | Working direct merchant evidence or honestly reduced coverage | PASS | Merchant storefront confirmation works (Run A: 8 of 12 confirmed). Best Buy, Walmart, Target, Amazon, eBay and Google Shopping are listed as not connected in docs/SOURCES_REGISTRY.md and the README. |
| price | In-stock only, ties, integer USD, condition and fulfillment | PASS | tests/test_ranking_equivalence.py and tests/test_compare_lane.py pass (part of the 123); Run A winner is in stock, new, integer cents, USD. A tie was seen live in run `run_8814c1fbd915…` (status `tie`). |
| reasons | Status explains cheaper mismatch, missing total or no winner | PASS | Run A: three cheaper listings (7700, 7999, 8400 cents) each carry the sentence "The catalog and the merchant's own page disagree". Run B: "No verified offer yet". |
| fail | Timeout and rate limit retain partial result, stale evidence rejected | NOT-YET | Partial results are retained (Run A is `partial` with two limited sources; a run cut by a server restart ended `unavailable` with the reason). Stale rows are rejected in tests. Open: internal bug note 004 (a failed shop.dev call still reports the catalog source as ok) has no fix recorded. |
| rights | Per-source rights for display, retention, background use and eval | PASS | docs/SOURCES_REGISTRY.md and the README table: display, repeated reads, retention and evaluation rights per source; no evaluation right is recorded for merchant data, which is why Experiment B was not run. |
| watch | Explicit watch automatically verified, authorized, pausable or preview | FAIL at 15:58 ET | On Run A the sentence "Notify me when this seller restocks under $25" returned `needs_detail` with a two-part question, no rule, no proof hash and no playground rows (internal bug note 009). Pause worked (`paused`). The same sentence gave a preview with a proof hash at 15:51 ET on another run (WATCH lane), so the behaviour varies. |
| ledger | Retry and restart yield no duplicate alerts | PASS (offline) | tests/test_watch_lane.py::test_authorized_feed_activates_and_ledger_dedupes and reference/test_reference.py (restart replay, duplicate attempt, late duplicate) pass. Not exercised live: no authorized feed is connected. |
| pairs | WDC blind paired results and error denominator recorded | PASS | eval/results/summary.json from pairs-v1.json: 40 pairs frozen before the run, both arms, abstentions and errors in the denominator; shown in the README and on /proof. |
| basket | Frozen five-case run or honestly reported evidence gap | PASS (gap reported) | eval/results/baskets-gap.json: status `not_run` with the reasons; repeated in the README and on /proof. |
| failure | Genuine failure analysis and code version for improvements | PASS | eval/CHANGELOG.md reads the 10 system failures case by case (three classes) with eval/results/diagnostic-v1.json; one change proposed, status pending; results carry the code version. |
| live | Fresh input by operator and original evidence view on stage | NOT-YET | Happens on stage. Script: docs/STAGE.md. |
| repo | Public repo and in-event provenance checked signed out | NOT-YET | https://github.com/hxbib/signalforge answers 200 without credentials, but README.md is not on the remote yet (raw README on `main` → 404 at 15:58 ET). |
| video | One-minute actual demo video tested signed out | NOT-YET | No video link yet (TODO-LEAD in README.md and docs/SUBMISSION.md). Shot list: docs/VIDEO_SHOTLIST.md. |
| links | Demo URL accessible, human team roster on form | PASS (URL), NOT-YET (form) | `curl -sS -o /dev/null -w '%{http_code}' https://reviewing-look-liz-course.trycloudflare.com/api/health` → 200 from outside; `/` and `/proof` → 200. The roster on the form is a human step. |
| receipt | Actual organizer confirmation saved | NOT-YET | Human step after submitting. |
