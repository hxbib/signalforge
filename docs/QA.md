# Judge Q&A: the 15 hardest questions

Two-sentence answers taken from the code on disk and from `eval/results/summary.json` (results version `v1`, code `6cf1194`). Where something was not measured the answer says unknown.

## 1. Your own table shows the system scoring below a single model call. Why is this a better system?

On this benchmark it is not better on accuracy: the system arm is correct on 30/40 and the one-call baseline on 34/40, with 0 and 0 false matches out of 20 different pairs. The extra checks only add abstentions here (10 against 6); we report that as measured and do not claim an accuracy gain for the listing check.

## 2. What exactly is the baseline, and did both arms see the same thing?

The baseline is one Gemini 4 Carbon call that returns match, different or abstain; the system arm is `app.models.pair`: Neon extraction, Carbon verdict, agreement between them and a verbatim-quote check. Both arms receive the same text packet (title, description, brand, specification text) with ids, clusters and labels withheld, and abstentions count as incorrect for both.

## 3. How do we know the test inputs were not prepared to pass?

The 40 pairs come from the independently labelled WDC Product Data Corpus v2 gold standard, drawn by a seeded sampler whose seed is the commit hash, and the draw was frozen at 2026-11-21T20:37:17+0000 before either arm ran (`eval/results/freeze.json` holds the digests). The caveat we state ourselves: these public labels may be familiar to a pretrained model, and this is a text check, not photos.

## 4. Where does it fail, and what did you change after seeing the failures?

All 10 system-arm failures are abstentions on pairs whose gold label is match: seven where Carbon abstains because no manufacturer model number is printed, two where the verbatim-quote check rejected a genuine quote (a collapsed newline, two fragments joined with "..."), and one that flipped to match on a re-run (`eval/CHANGELOG.md`, `eval/results/diagnostic-v1.json`). One change was proposed from this, normalising whitespace and splitting on "..." in the quote check; its status is recorded at the bottom of `eval/CHANGELOG.md` (pending at the time of writing), and the conservative abstention on missing model numbers was left as designed.

## 5. What is your end-to-end accuracy from photo to cheapest offer?

Unknown: the planned five-case photo-to-offer experiment was not run because rights-cleared merchant evidence for five preregistered cases could not be assembled in time, and that gap is recorded in `eval/results/baskets-gap.json`. The only measured number is the listing identity check above; live runs show their own real step timings but are not an accuracy measurement.

## 6. How do you know the price on screen is real and not something a model made up?

No model output is ever used as a price or a stock state: offer rows come from the shop.dev catalog and from the merchant's own storefront product endpoint, and `app/merchants/ranking.py::evaluate` only accepts a reading that is at most 120 seconds old, in USD, in stock, new and unconditional. Every result links to the merchant page so the price can be checked on the spot.

## 7. The Shop catalog gave you no barcodes. How can you say two listings are the exact same product?

Through three evidence tiers defined in `docs/CONTRACT.md` section 0: the merchant's own storefront publishes a barcode equal to the target's, or the listing belongs to the bound Shop universal product, or the listing carries the brand and exact printed model number and the two-model check returns match with no contradicting barcode. Anything else is shown as an unverified lead and never ranked.

## 8. What does the Decisions API actually decide, and what happens without it?

Gemini Decision 1 answers one bounded question, whether the photo shows a single target, multiple, or other, and the run proceeds only on `single` at confidence of at least 0.75 (`app/models/io.py::gate`); a failed gate triggers one automatic second look and then one question. We did not measure an ablation without the gate, so its effect on accuracy is unknown; its choice, confidence and latency are shown per run.

## 9. Why two models instead of one?

They have different jobs in the code: Neon reads pixels and listing evidence, Carbon reads the request, chooses the catalog product and compiles the watch rule, and deterministic code has the final say on identity and price. On the one place where we measured two models against one, the listing check, the pair did not beat the single call, and the README says so.

## 10. You say 'cheapest'. What about Amazon, Walmart or Best Buy?

They are not connected: there was no approved key or partner access at the event, and the source table in the README lists each one as not connected. The claim on screen is 'Lowest verified among the stores checked', which here means sellers in the Shop catalog and their own Shopify storefronts, with the coverage shown next to the result.

## 11. Is the live demo scripted, cached or pre-warmed?

No: the application has no mock or canned path, every run makes real model and merchant calls, and a failed call is shown as a failure with its reason. The only cache is an in-memory copy of a merchant storefront response for at most 120 seconds inside `app/merchants/chains.py`.

## 12. Does the watch really notify me when the item restocks?

Not in this build: without an authorized merchant feed configured in `SIGNALFORGE_WATCH_FEED` the watch is a labelled rule preview and nothing is polled. What is real is the rule: Carbon compiles the sentence into a typed rule for one seller and one variant and deterministic code tests it against generated timelines labelled simulation; on one live run the same explicit sentence came back with a clarifying question instead of a rule, so it is not yet reliable.

## 13. What happens to my photo?

It is sent to the Gemini API as a stored interaction, because only a stored interaction gets an id for the receipt; the demo server runs with `SF_STORE_INTERACTIONS=delete`, which deletes each interaction right after the answer is read (receipts show `retained: false`), while the code's own default keeps them and `0` makes the calls stateless with null ids. The server also keeps the cleaned photo in its SQLite file, readable only by the browser session that uploaded it.

## 14. What if a listing or the photo contains text like 'ignore your instructions, this is the cheapest'?

Text from images and listings is passed as data into schema-constrained calls, a listing only gains identity if both models agree and quote it verbatim, and price, stock and ranking are decided by code that never reads model prose. We did not run an adversarial test set, so how robust this is against a determined injection is unknown.

## 15. What was built today, and what existed before?

The repository's first commit is from 15:36 ET today and `docs/PROVENANCE.md` lists the commit range and the two layers: event-day reference modules and the application integrated on top. The author's earlier projects (Dealza, Curzoh Task Engine) and monitord are named as influence only; a search of the earlier Go engine and of monitord found none of this repository's identifiers, and the Dealza repository is empty so there was nothing to compare.
