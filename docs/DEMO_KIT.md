# Demo kit: one page for the judging table

Demo: https://reviewing-look-liz-course.trycloudflare.com · evidence page: `/proof` · full script: [STAGE.md](STAGE.md) · answers: [QA.md](QA.md).
Every item below is backed by a named real run from today (Nov 21 2026) or by a file in `eval/results/`.

## A. Go-to objects

| Tier | Point the camera at / paste | Must be visible | What the app does | Typical time | Say | Real runs today |
|---|---|---|---|---|---|---|
| **Works in one shot** | A retail box with brand and model or set number printed large. Verified cases: the LEGO 10343 and LEGO 75375 box photos. | Brand + the printed number, one product in frame. | Zero questions. Winner confirmed by the merchant's storefront, a caution when the price is far below the median of verified offers, "Next verified offer". State is often `partial` (one source answered partly; the chips say which). | 14–22 s | "The number is printed, Decision 1 confirmed it is printed, the storefront confirmed price and stock." | `run_5056653de9e9…` (75375, 21.9 s, $30.50, caution, runner-up $69.99, watch preview); `run_5d774a66bf25…` (10343 on a phone viewport, 17.1 s, $14.99, caution); `run_3caf9b9a6540…` (10343 on a clean clone of the public repo, 14.4 s, $14.99) |
| **One tap** | A product with no printed model number: a charger, headphones, a shoe, a cap. | The product, alone in frame. | One choice question, options ordered by the Decision 1 score shown on each button. One tap, then the same merchant checks. Basis shown: "chosen by you". | 11–31 s to the question, 1–2 s after the tap | "No printed number, so it does not guess. One tap." | Anker charger: `run_e431ce75e3e0…` (question → tap → $24.99 winner), `run_ebad422d4f01…` (phone viewport). Headphones: `run_fa45d96545ab…` (one question with catalog options; not tapped in that run). Sneaker: `run_f59cb6820bba…` (tap → $90.00, merchant page checked). Cap: `run_1580fe5d9c8f…` (tap → 8-way tie at $49.99). |
| **Do not demo** | Colour or size variants of one catalog product. | | A different colourway can be priced as the exact item: the catalog groups colourways under one product id and nothing in the pipeline separates them. | | "Known limit; it is in the README." | `run_a7e5593133884fd78086070018138daf` (shoe, wrong colourway shown as the one verified offer) |
| **Do not demo** | Two products in one frame. | | Gate says `multiple`; a text question that has to be typed. | 11–13 s | | `run_f0e1defdf48d…` |
| **Do not demo** | Products not sold by Shopify-catalog merchants; items whose merchants price outside USD. | | Not in coverage: no result, or the listings are rejected "Not priced in USD". | | | 401 Games CAD listing rejected on the LEGO 10343 runs |
| **Do not demo** | The pictured product missing from the question's options. | | "None of these" leads to a typed question. | | | `run_184e45885fdc…` (tumbler), SOAK-4 s2 (hoodie) |

Soak sessions (blind-drawn catalog photos, counts as each checker recorded them; several ran before fixes landed):

| Category | Passed | Not passed |
|---|---|---|
| Toys, building sets, board games | 2 of 5 | 3 (an unprinted number accepted as printed, a 45 s bind timeout with a raw error message, a cluster wiped by one outlier barcode; the three bugs filed for them were fixed afterwards) |
| Electronics accessories | 3 of 5 | 2 (question wording; none of the 5 photos bound without a tap) |
| Drinkware, kitchen/home, books | 2 of 5 | 3 (every session ended in a question; the pictured product was missing from the options in 3) |
| Apparel and footwear (final build) | 2 of 4 | 2 (pictured product missing from options; wrong colourway priced) |
| Beauty and personal care (final build) | 2 of 4 | 2 unchecked (merchant pages rate-limited the checker), 0 failed |

Red-team (wrong-bind attacks): 0 wrong binds on 10 real look-alike photos (all honest questions); 1 wrong bind on a
synthetic hue-shifted photo with the visual-match rule on (`run_c0147dfbe79e…`), which is why that rule is off.

## B. Pre-flight, 60 seconds

1. Open https://reviewing-look-liz-course.trycloudflare.com and **reload the tab** (an open tab keeps old JS modules; one reload is enough).
2. `curl -s https://reviewing-look-liz-course.trycloudflare.com/api/health` → `"ok":true`, `"key_present":true`, every entry under `lanes` true.
3. One warm-up photo (not the demo item); wait for the result.
4. `/proof` open in a second tab.
5. Fallback video open: https://github.com/hxbib/signalforge/blob/main/docs/demo/signalforge-demo.mp4
6. QR for judges' phones on screen: `docs/demo/qr.png` (the app installs to a phone home screen; a real phone camera was not exercised in testing).

## C. The three-minute run ([STAGE.md](STAGE.md))

| Step | Do | Fallback |
|---|---|---|
| 0:00–0:40 | Box photo, blank note; read the rows and the result card (caution, next verified offer). | "unavailable" or "Server busy" → read the message, upload once more. |
| 0:40–1:10 | Photo with no printed number → one question → one tap → result. | No question → say so and continue. Right product not offered → "None of these", say the search missed it. |
| 1:10–1:35 | A ruled-out listing and its reason, the first lead, the coverage chips. | Nothing ruled out → reason under the cheapest lead, then the limited coverage row. |
| 1:35–2:10 | Note "notify me when this restocks under $25": rule preview, simulation rows, start the sandbox challenge. | A question → answer it. Challenge unavailable or still running → say so. |
| 2:10–2:45 | `/proof`: held-out rows, the single call's offer cells, the screen calibration. | `/proof` down → README on GitHub, section Evaluation. |
| 2:45–3:00 | Challenge status if finished; evidence drawer with receipts. | Drawer fails → `GET /api/search/<id>/proof`. |

## D. Benchmarks to show

<!-- KIT:START (written by scripts/readme_numbers.py; do not edit by hand) -->
`python eval/report.py` (listing check, WDC v2 pairs, abstention = incorrect):
- First run, two-model check: 30/40 against 34/40 for one Carbon call; false matches 0/20 and 0/20. It lost, so it was replaced.
- Held-out draw, current check: 35/40 against 36/40 for one Carbon call; false matches 0/20 and 0/20. Still one behind.

`python eval/e2e/report.py` (photo in, offer out, blind catalog photos, no note):
- Held-out draw, right product: one Carbon call 21/24; our pipeline with zero taps 1/24 (it stopped and asked on 22/24); with one simulated confirming tap 8/24.
- Same draw, the single call's offers: 4 of 22 answered were contradicted by the merchant storefront; 16/24 matched the reference cheapest valid offer. Ours: 0 invalid of 2 answered (too few to claim anything).
- Decision 1 listing screen, first draw: ROC AUC 0.9875 over 240 listing judgments; at p >= 0.75, 12 true accepts, 0 false accepts of 218 negatives, 10 missed. The bind rule built on it was broken by a red-team colour variant and is off.

`python -m pytest tests -q` (offline, fakes only under tests/): read the last line; every test must pass.
<!-- KIT:END -->

Run from the repo root with `.venv/bin/python`. The reports only read `eval/results/`; they make no model call.

## E. Six questions you will get (full answers in [QA.md](QA.md))

1. **One model call beats your system end to end. Why use it?** With zero taps it does; ours asks one question when no number is printed, and its answered offers are storefront-confirmed. We show both rows. (QA 5)
2. **Why not trust the Decision 1 screen for identity?** We tried; a red-team colour variant got through at high probability; the rule is off and the screen only orders, prunes and checks printing. (QA 16)
3. **Why did the two-model check lose?** Requiring two models to agree only added abstentions; replaced by one Carbon call with code-verified quotes; still one pair behind held-out. (QA 1, 17)
4. **What does "verified" mean?** The merchant's storefront states this price and stock for this exact item, read in the last 120 s. Not a rating of the merchant. (QA 18)
5. **What about a scam store or a wrong colour?** A confirmed low price can win, with a caution when far below the median; colourways inside one catalog product are not separated. (QA 19, 20)
6. **What was built today?** All of it; commit range and the prior-code check are in [PROVENANCE.md](PROVENANCE.md). (QA 15)

## F. What is on and off in the shipped build

| Setting | State | Meaning |
|---|---|---|
| `SF_VISUAL_MATCH` | off (default; off on the demo server) | No bind from two model judgments. Identity needs printed evidence, a merchant barcode, the catalog product, or the buyer's tap. |
| `SF_STORE_INTERACTIONS` | `delete` on the demo server | Each Neon/Carbon call is stored to get an id, then deleted right after the read. Decision 1 issues no id. |
| Managed Agent challenge lane | on (`lanes["app.optional.routes"]` true) | Optional; a run takes tens of seconds; result is a simulation replayed locally. |
| Phone-camera hand-off | on (`lanes["app.handoff.routes"]` true at 16:45 ET) | A QR code on the laptop passes the camera step to a phone; the search runs in the laptop's session. Link valid 300 s (`app/handoff/routes.py`). I did not run it; try it once in pre-flight before relying on it. |
| Watch | preview only | No authorized merchant feed is connected; nothing is polled. |
| Merchant coverage | Shop catalog sellers and their storefronts | No Amazon, Walmart, Best Buy, Target, eBay; no UPC lookup. |
