# Submission form text (ready to paste)

Every number in this file is taken from `eval/results/summary.json` (generated 1795295279, files: pairs-v1.json, pairs-v2.json, pairs-b-v2.json, baskets-gap.json).
Fields marked TODO-LEAD need the lead or the owner.

## Project name
SignalForge

## One-line pitch
One photo in, the lowest verified in-stock offer among the stores checked out, with the reason the cheaper-looking listing lost.

## Description (105 words)
SignalForge turns one photo into a shopping answer with no typing. Gemini 4 Neon reads the brand, printed model number and barcode; Gemini Decision 1 gates whether exactly one item is clear; Gemini 4 Carbon reads the request and binds the photo to one exact product in the Shop catalog. Each merchant's own storefront then confirms price and stock, and deterministic rules pick the lowest verified in-stock offer among the stores checked, show why a cheaper-looking listing lost, and report coverage honestly. A sentence like "notify me when this seller restocks under $25" becomes a tested, explainable watch preview. Every model step has a receipt.

## Track
5 Sigma. Bonus track: Build with Commerce (Shopify), using the shop.dev Shop MCP.

## How each Gemini 4 model and the Decisions API is used
- **Gemini 4 Neon (`gemini-4-neon`)** reads the photo: which object, brand, printed model or set number, barcode, variant, and a focus box. When the gate fails it reads one automatic closer crop.
- **Gemini Decision 1 (`gemini-decision-1`, Decisions API)** is the gate: bounded choices `single / multiple / other`. The run proceeds only on `single` with confidence of at least 0.75. A failed gate triggers one automatic closer look, then a single question. Its choice, confidence and latency are shown in the evidence drawer. It is never used to assert a price or stock.
- **Gemini 4 Carbon (`gemini-4-carbon`)** reads the buyer's request (usually empty) into a typed goal, picks which Shop catalog product the photo shows (deterministic code must then corroborate the pick with a barcode or brand plus model number), judges whether a merchant listing is the same product while quoting the decisive text from each side (code verifies both quotes or the check abstains), and compiles an explicit watch sentence into a bounded typed rule.
- **Deterministic code** decides everything a merchant fact can decide: identity tier, condition, stock, currency, freshness, ties, the near miss and each rejection reason.

## Proof (5 Sigma)
Listing identity on independently labelled WDC v2 product pairs, each draw frozen before any arm ran on it, baseline = one Gemini 4 Carbon call, abstentions counted as incorrect. Run v1 on draw A (first run; listing check `app.models.pair v1-neon-carbon-agreement`): system 30/40 correct, 10 abstained, 0/20 false matches; baseline 34/40 correct, 6 abstained, 0/20 false matches. Run v2 on draw A (in-sample: the change was chosen after seeing the first run on these same pairs; listing check `app.models.pair v2-carbon-verified-quotes`): system 33/40 correct, 7 abstained, 0/20 false matches; baseline 34/40 correct, 6 abstained, 0/20 false matches (baseline from the first run, not re-run). Run v2 on draw B (held-out; listing check `app.models.pair v2-carbon-verified-quotes`): system 35/40 correct, 5 abstained, 0/20 false matches; baseline 36/40 correct, 4 abstained, 0/20 false matches.
End to end on 24 catalog photos drawn blind before any arm ran (blank note, no follow-up answer given): SignalForge correct product on 0/24, wrong product 1, no answer 23, errors 0, invalid offers 0 of 1 answered; one Carbon call handed the photo and a listing list: correct product on 20/24, wrong product 0, no answer 4, errors 0, invalid offers 1 of 20 answered. The single call wins this benchmark: the full system stops and asks a question when the photo shows no printed model number or barcode (gate did not pass x2, no model number or barcode in photo x21), and with no answer given that counts as no answer. A Decision 1 listing screen calibrated afterwards on the same cases (240 listing judgments) reached ROC AUC 0.9875 and ranked the drawn product first in 19/22 cases that had one; that is a calibration, not a re-score.
The planned five-case experiment with human-adjudicated prices was not run because rights-cleared merchant evidence could not be assembled in time; that gap is recorded in `eval/results/baskets-gap.json`. Failure analysis by case: `eval/CHANGELOG.md`. Everything above is on the app's /proof page and in the README.

## What was built today
All of it, on November 21, 2026: the product spec and reference modules (prompts, schemas, ranking rules, watch core, evaluation protocol), and on top of them the application: the FastAPI server and pipeline, the live model boundary to the Interactions and Decisions APIs, the Shop catalog lane, merchant storefront confirmation, tiered identity and ranking, the explainable watch, the browser app, the offline tests and the evaluation runs. Details and the commit range: `docs/PROVENANCE.md`. Earlier projects by the author (Dealza, Curzoh Task Engine) and monitord are acknowledged there as influence only.

## Shopify bonus: how shop.dev is used
Product discovery and offers come from the shop.dev Shop MCP (`search_catalog` by text and by image, `get_product` for every seller of the bound product). The winning offer links to the merchant with the catalog's own attributed URL, and each merchant's Shopify storefront is asked to confirm the exact variant, price, stock and barcode. All reads are shopper-triggered; nothing is polled or cached beyond the run.

## Links
- Repository: https://github.com/hxbib/signalforge
- Live demo: https://reviewing-look-liz-course.trycloudflare.com
- One-minute video: TODO-LEAD
- Evidence page: https://reviewing-look-liz-course.trycloudflare.com/proof

## Team
Sadman Habib (GitHub: hxbib), solo.

## Known limits to state if asked
Stores checked are Shop catalog sellers and their own storefronts only; item price in USD before shipping and tax; watches are rule previews without an authorized merchant feed; photos and notes are sent to the model service as stored interactions (the demo server deletes each one right after the answer is read, `SF_STORE_INTERACTIONS=delete`; the code's own default keeps them; `0` makes the calls stateless with null receipt ids).
