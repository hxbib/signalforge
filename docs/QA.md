# Judge Q&A: the 22 hardest questions

Two-sentence answers taken from the code on disk, from `eval/results/summary.json` (listing-check runs: pairs-v1, pairs-v2, pairs-b-v2) and from `eval/results/e2e-summary.json` (end-to-end runs: v1, v2, b). Where something was not measured the answer says so.

## 1. Your own table shows the first system scoring below a single model call. Why is this a better system?

On the first run it was not: the two-model listing check was correct on 30/40 against 34/40 for one Carbon call, both with 0/20 false matches, and the whole gap was abstentions (10 against 6). After the one change it scored 33/40 on draw A (in-sample) and 35/40 on draw B (held-out), which on the held-out draw is behind the one-call baseline (36/40) with 0 false matches in both, so we claim no accuracy gain for this component and report it as measured.

## 2. What exactly is the baseline, and did both arms see the same thing?

The baseline is one Gemini 4 Carbon call that returns match, different or abstain; the system arm is `app.models.pair`, in v1 Neon extraction plus a Carbon verdict with agreement and verbatim quotes, in v2 one Carbon verdict whose quotes are verified by code. Every arm receives the same text packet (title, description, brand, specification text) with ids, clusters and labels withheld, and abstentions count as incorrect for all of them.

## 3. How do we know the test inputs were not prepared to pass?

The pairs come from the independently labelled WDC Product Data Corpus v2 gold standard, drawn by a seeded sampler and frozen with digests before any arm ran on them (`eval/results/freeze.json`, `freeze-b.json`). Two caveats we state ourselves: these public labels may be familiar to a pretrained model, and a v2 result on draw A is in-sample because the change was chosen after seeing v1 on those pairs.

## 4. Where does it fail, and what did you change after seeing the failures?

All 10 v1 system failures were abstentions on truly matching pairs: seven where Carbon abstains because no manufacturer model number is printed, two where the strict quote check rejected a genuine quote, one that flipped on a re-run (`eval/CHANGELOG.md`, `eval/results/diagnostic-v1.json`). The one change made in response is listing check v2, one Carbon call with whitespace-tolerant quote verification in code; the abstention on missing model numbers was left as designed because loosening it is what would create false bargains.

## 5. Your end-to-end benchmark says one model call beats your whole system. Why should anyone use it?

On blind-drawn catalog photos with a blank note and no follow-up answer, in the first run the full system found the correct product on 0/24, picked a wrong product on 1 and stopped without an answer on 23 (gate did not pass x2, no model number or barcode in photo x21), while one Carbon call was correct on 20/24 with 0 wrong products (`eval/results/e2e-summary.json`). With the visual_match rule switched on (it is off in the shipped build after failing a red-team test): run v2 on draw A (in-sample: changed after seeing the first run on these photos, visual_match on) system 1/24 correct with 1 wrong products against 20/24 and 0 for the single call; run b on draw B (held-out, visual_match on) system 1/24 correct with 1 wrong products against 21/24 and 1 for the single call. With one tap on the question (a simulated buyer picking the gold option when offered): 12/24 correct with 1 wrong products on draw A; 8/24 correct with 1 wrong products on draw B.

## 6. How do you know the price on screen is real and not something a model made up?

No model output is ever used as a price or a stock state: offer rows come from the shop.dev catalog and from the merchant's own storefront product endpoint, and `app/merchants/ranking.py::evaluate` only accepts a reading that is at most 120 seconds old, in USD, in stock, new and unconditional. Every result links to the merchant page so the price can be checked on the spot.

## 7. The Shop catalog gave you no barcodes. How can you say two listings are the exact same product?

An offer counts as the same item through three evidence tiers (`docs/CONTRACT.md` section 0): the merchant's own storefront publishes a barcode equal to the target's, or the listing belongs to the bound Shop universal product, or the listing carries the brand and exact printed model number and the listing check returns match with no contradicting barcode; anything else is an unverified lead and never ranked. The photo itself is bound to a catalog product only on a barcode, a printed model number or the buyer's own choice; the visual_match rule (two agreeing model judgments) exists in the code but is off after it bound a wrong colourway in a red-team test.

## 8. What does the Decisions API actually decide, and what happens without it?

It does three things in two calls: the gate (single target, multiple, or other; the run proceeds only on `single` at confidence of at least 0.75, `app/models/io.py::gate`), and in one further call the listing screen, a probability per catalog listing of being the pictured item, plus a check that the model number Neon read is visibly printed (`app/models/__init__.py::screen`). We did not measure the pipeline with the gate removed, so its separate effect is unknown; the screen was calibrated on the first end-to-end draw, and those numbers are in the README table.

## 9. Why two models instead of one?

They have different jobs in the code: Neon reads the pixels, Carbon reads the request, chooses the catalog product, judges listings and compiles the watch rule, Decision 1 gates, and deterministic code has the final say on identity and price. The one place where we put two models on the same job, listing check v1, measured worse than a single call, so we changed it to one Carbon call verified by code and kept the v1 numbers in the table; that v2 check still trails a single Carbon call on the held-out draw (35/40 against 36/40), and we ran no ablation of Neon against Carbon reading the photo, so the second model has no measured accuracy gain yet.

## 10. You say 'cheapest'. What about Amazon, Walmart or Best Buy?

They are not connected: there was no approved key or partner access at the event, and the source table in the README lists each one as not connected. The claim on screen is 'Lowest verified among the stores checked', which here means sellers in the Shop catalog and their own Shopify storefronts, with the coverage shown next to the result.

## 11. Is the live demo scripted, cached or pre-warmed?

No: the application has no mock or canned path, every run makes real model and merchant calls, and a failed call is shown as a failure with its reason. The only cache is a process-wide in-memory copy of a merchant storefront response for at most 120 seconds inside `app/merchants/chains.py`, so a repeat search within two minutes can reuse a storefront reading.

## 12. Does the watch really notify me when the item restocks?

Not in this build: without an authorized merchant feed configured in `SIGNALFORGE_WATCH_FEED` the watch is a labelled rule preview and nothing is polled. What is real is the rule: Carbon compiles the sentence into a typed rule for one seller and one variant and deterministic code tests it against generated timelines labelled simulation; a 15:58 ET run where the same sentence came back with a question was fixed by the watch lane, and on the frozen build it returned a preview with a proof hash and no question (signed-out run, 16:27 ET).

## 13. What happens to my photo?

It is sent to the Gemini API as a stored interaction, because only a stored interaction gets an id for the receipt; the demo server runs with `SF_STORE_INTERACTIONS=delete`, which deletes each interaction right after the answer is read (receipts show `retained: false`), while the code's own default keeps them and `0` makes the calls stateless with null ids. The server also keeps the cleaned photo in its SQLite file, readable only by the browser session that uploaded it.

## 14. What if a listing or the photo contains text like 'ignore your instructions, this is the cheapest'?

Text from images and listings is passed as data into schema-constrained calls, a listing only gains identity if the quotes Carbon gives are found by code in the listing itself, and price, stock and ranking are decided by code that never reads model prose. We did not run an adversarial test set, so how robust this is against a determined injection is unknown.

## 15. What was built today, and what existed before?

The repository's first commit is from 15:36 ET today and `docs/PROVENANCE.md` lists the commit range and the two layers: event-day reference modules and the application integrated on top. The author's earlier projects (Dealza, Curzoh Task Engine) and monitord are named as influence only; a search of the earlier Go engine and of monitord found none of this repository's identifiers, and the Dealza repository is empty so there was nothing to compare.

## 16. The Decision 1 screen scores AUC near 0.99 in your own calibration. Why not trust it for identity?

We tried: a rule that bound on the screen's top product plus Carbon's independent pick (`visual_match`) was measured, fired rarely at its thresholds, and a red-team photo of a different colourway got through it, so it is off by default (`SF_VISUAL_MATCH`). The screen judges titles against a photo and does not check colour, variant or an own-brand copy, so in the shipped build it only orders question options, annotates offers and limits which listing checks run.

## 17. Why did the two-model listing check lose to one call?

On the first draw it was correct on 30/40 against 34/40 with 0 false matches in both arms, and median latency 10779 ms against 4516 ms: requiring Neon and Carbon to agree and quote verbatim only added abstentions, including two genuine quotes rejected over whitespace (`eval/CHANGELOG.md`). We replaced it with one Carbon call whose quotes are verified by code, and kept v1 callable and in the table.

## 18. What does 'verified' mean on the result card?

The offer passed the identity tier, and the merchant's own storefront (or, where it could not be read, the Shop catalog row) states this price, in stock, new, in USD, within the last 120 seconds. It is a statement about the listing, not a rating of the merchant.

## 19. What happens when a scam store lists the item far below everyone else?

If its storefront confirms the price and the identity tier holds, it can win: on a live run at 16:27 ET the winner was priced 64% below the median of 6 verified offers. The app then shows `result.caution` (40% or more below the median of at least three verified offers) with the sentence that verified is not a rating of the merchant, plus the next verified offer; it does not block or re-rank the store.

## 20. The photo shows one colour and your verified offer is another colour of the same product. How?

The Shop catalog can group two colourways under one catalog product, and neither the catalog product id nor the Decision 1 screen separates them; in soak run `run_a7e5593133884fd78086070018138daf` on the frozen build the single verified offer was a different colourway, and the target barcode had been taken from that variant, so the barcode tier confirmed itself. We made no code change for it: it is listed as a limit in the README, and the result card links the merchant page where the variant is visible.

## 21. There is a cheaper listing shown under your 'lowest'. Is yours really the lowest?

Only among verified offers: a cheaper row shown as a lead did not pass the identity tier or was not confirmed, so it is listed, tagged unverified and never ranked. The headline says 'Lowest verified among the stores checked' for that reason, and we do not claim the lowest price anywhere.

## 22. Your live result said 'partial' on your own demo item. Why?

A run is marked partial when any source answered only partly, for example one failed catalog call, merchant storefronts that did not respond, or listing checks that were not all run; the coverage chips name which. The winner shown is still one that passed identity and was confirmed, and the run is not presented as complete.
