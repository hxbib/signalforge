# Judge Q&A: the 15 hardest questions

Two-sentence answers taken from the code on disk and from `eval/results/summary.json` (generated 1795295279; runs: pairs-v1, pairs-v2, pairs-b-v2; end-to-end results included). Where something was not measured the answer says so.

## 1. Your own table shows the first system scoring below a single model call. Why is this a better system?

On the first run it was not: the two-model listing check was correct on 30/40 against 34/40 for one Carbon call, both with 0/20 false matches, and the whole gap was abstentions (10 against 6). After the one change it scored 33/40 on draw A (in-sample) and 35/40 on draw B (held-out), which on the held-out draw is behind the one-call baseline (36/40) with 0 false matches in both, so we claim no accuracy gain for this component and report it as measured.

## 2. What exactly is the baseline, and did both arms see the same thing?

The baseline is one Gemini 4 Carbon call that returns match, different or abstain; the system arm is `app.models.pair`, in v1 Neon extraction plus a Carbon verdict with agreement and verbatim quotes, in v2 one Carbon verdict whose quotes are verified by code. Every arm receives the same text packet (title, description, brand, specification text) with ids, clusters and labels withheld, and abstentions count as incorrect for all of them.

## 3. How do we know the test inputs were not prepared to pass?

The pairs come from the independently labelled WDC Product Data Corpus v2 gold standard, drawn by a seeded sampler and frozen with digests before any arm ran on them (`eval/results/freeze.json`, `freeze-b.json`). Two caveats we state ourselves: these public labels may be familiar to a pretrained model, and a v2 result on draw A is in-sample because the change was chosen after seeing v1 on those pairs.

## 4. Where does it fail, and what did you change after seeing the failures?

All 10 v1 system failures were abstentions on truly matching pairs: seven where Carbon abstains because no manufacturer model number is printed, two where the strict quote check rejected a genuine quote, one that flipped on a re-run (`eval/CHANGELOG.md`, `eval/results/diagnostic-v1.json`). The one change made in response is listing check v2, one Carbon call with whitespace-tolerant quote verification in code; the abstention on missing model numbers was left as designed because loosening it is what would create false bargains.

## 5. Your end-to-end benchmark says one model call beats your whole system. Why should anyone use it?

On 24 blind-drawn catalog photos with a blank note and no follow-up answer, the full system found the correct product on 0/24, picked a wrong product on 1 and stopped without an answer on 23 (gate did not pass x2, no model number or barcode in photo x21), while one Carbon call was correct on 20/24 with 0 wrong products (`eval/results/e2e-summary.json`). So on bare catalog photos a single call beats this build clearly; the system only answers unprompted when a model number or barcode is printed in the photo, in the app the question it asks can be answered with one tap, and we did not measure the with-answers path.

## 6. How do you know the price on screen is real and not something a model made up?

No model output is ever used as a price or a stock state: offer rows come from the shop.dev catalog and from the merchant's own storefront product endpoint, and `app/merchants/ranking.py::evaluate` only accepts a reading that is at most 120 seconds old, in USD, in stock, new and unconditional. Every result links to the merchant page so the price can be checked on the spot.

## 7. The Shop catalog gave you no barcodes. How can you say two listings are the exact same product?

Through three evidence tiers defined in `docs/CONTRACT.md` section 0: the merchant's own storefront publishes a barcode equal to the target's, or the listing belongs to the bound Shop universal product, or the listing carries the brand and exact printed model number and the listing check returns match with no contradicting barcode. Anything else is shown as an unverified lead and never ranked.

## 8. What does the Decisions API actually decide, and what happens without it?

Gemini Decision 1 answers one bounded question, whether the photo shows a single target, multiple, or other, and the run proceeds only on `single` at confidence of at least 0.75 (`app/models/io.py::gate`); a failed gate triggers one automatic second look and then one question. We did not measure the pipeline with the gate removed, so its separate effect on accuracy is unknown; its choice, confidence and latency are shown per run.

## 9. Why two models instead of one?

They have different jobs in the code: Neon reads the pixels, Carbon reads the request, chooses the catalog product, judges listings and compiles the watch rule, Decision 1 gates, and deterministic code has the final say on identity and price. The one place where we put two models on the same job, listing check v1, measured worse than a single call, so we changed it to one Carbon call verified by code and kept the v1 numbers in the table.

## 10. You say 'cheapest'. What about Amazon, Walmart or Best Buy?

They are not connected: there was no approved key or partner access at the event, and the source table in the README lists each one as not connected. The claim on screen is 'Lowest verified among the stores checked', which here means sellers in the Shop catalog and their own Shopify storefronts, with the coverage shown next to the result.

## 11. Is the live demo scripted, cached or pre-warmed?

No: the application has no mock or canned path, every run makes real model and merchant calls, and a failed call is shown as a failure with its reason. The only cache is an in-memory copy of a merchant storefront response for at most 120 seconds inside `app/merchants/chains.py`.

## 12. Does the watch really notify me when the item restocks?

Not in this build: without an authorized merchant feed configured in `SIGNALFORGE_WATCH_FEED` the watch is a labelled rule preview and nothing is polled. What is real is the rule: Carbon compiles the sentence into a typed rule for one seller and one variant and deterministic code tests it against generated timelines labelled simulation; on a live run at 15:58 ET the same explicit sentence came back with a clarifying question instead of a rule, so that step was not reliable then.

## 13. What happens to my photo?

It is sent to the Gemini API as a stored interaction, because only a stored interaction gets an id for the receipt; the demo server runs with `SF_STORE_INTERACTIONS=delete`, which deletes each interaction right after the answer is read (receipts show `retained: false`), while the code's own default keeps them and `0` makes the calls stateless with null ids. The server also keeps the cleaned photo in its SQLite file, readable only by the browser session that uploaded it.

## 14. What if a listing or the photo contains text like 'ignore your instructions, this is the cheapest'?

Text from images and listings is passed as data into schema-constrained calls, a listing only gains identity if the quotes Carbon gives are found by code in the listing itself, and price, stock and ranking are decided by code that never reads model prose. We did not run an adversarial test set, so how robust this is against a determined injection is unknown.

## 15. What was built today, and what existed before?

The repository's first commit is from 15:36 ET today and `docs/PROVENANCE.md` lists the commit range and the two layers: event-day reference modules and the application integrated on top. The author's earlier projects (Dealza, Curzoh Task Engine) and monitord are named as influence only; a search of the earlier Go engine and of monitord found none of this repository's identifiers, and the Dealza repository is empty so there was nothing to compare.
