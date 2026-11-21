# Submission form text (ready to paste)

Numbers in this file come from `eval/results/summary.json` (results version `v1`, code `6cf1194`).
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
- **Gemini 4 Neon (`gemini-4-neon`)** reads the photo: which object, brand, printed model or set number, barcode, variant, and a focus box. It also extracts evidence from merchant listings for the listing identity check.
- **Gemini Decision 1 (`gemini-decision-1`, Decisions API)** is the gate: bounded choices `single / multiple / other`. The run proceeds only on `single` with confidence of at least 0.75. A failed gate triggers one automatic closer look on a crop, then a single question. Its choice, confidence and latency are shown in the evidence drawer. It is never used to assert a price or stock.
- **Gemini 4 Carbon (`gemini-4-carbon`)** reads the buyer's request (usually empty) into a typed goal, picks which Shop catalog product the photo shows (deterministic code must then corroborate the pick with a barcode or brand plus model number), gives the verdict in the listing identity check, and compiles an explicit watch sentence into a bounded typed rule.
- **Neon and Carbon together**: a listing only earns the model-number identity tier when both models agree and both quote the listing verbatim; otherwise the check abstains and the listing stays an unranked lead.
- **Deterministic code** decides everything a merchant fact can decide: identity tier, condition, stock, currency, freshness, ties, the near miss and each rejection reason.

## Proof (5 Sigma)
On 40 independently labelled WDC v2 product pairs drawn before either arm ran, the two-model listing check made 0 false matches out of 20 different pairs and gave 0 wrong answers; it was correct on 30/40 and abstained on 10. The single-call Gemini 4 Carbon baseline was correct on 34/40 with 0 false matches and 6 abstentions. Abstentions count as incorrect, so the system arm scores below the baseline on this text-only check (paired: both correct 30, baseline only 4, system only 0, neither 6).
What the check buys in the product is a refusal to guess: an abstention keeps a listing as a lead instead of ranking a wrong item first. The planned second experiment (five fresh photo-to-offer cases) was not run because rights-cleared merchant evidence could not be assembled in time; that gap is recorded in `eval/results/baskets-gap.json` and in the README. There is no end-to-end accuracy number.

## What was built today
All of it, on November 21, 2026: the product spec and reference modules (prompts, schemas, ranking rules, watch core, evaluation protocol), and on top of them the application: the FastAPI server and pipeline, the live model boundary to the Interactions and Decisions APIs, the Shop catalog lane, merchant storefront confirmation, tiered identity and ranking, the explainable watch, the browser app, the offline tests and the paired evaluation run. Details and the commit range: `docs/PROVENANCE.md`. Earlier projects by the author (Dealza, Curzoh Task Engine) and monitord are acknowledged there as influence only.

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
