# 60-second submission video: shot list

Rules for the take: record the real app at https://reviewing-look-liz-course.trycloudflare.com in a signed-out browser window, on a fresh
photo, in one continuous screen capture with voice-over. No slide, no mock screen, no result recorded earlier and
replayed as live. If any stretch is sped up, put "sped up Nx" on screen and say the real elapsed time the progress
panel shows. Read every number off the screen; do not say a number that is not visible.

| Time | On screen | Voice-over |
|---|---|---|
| 0:00–0:07 | The item on the desk (box with its printed model number visible), then the app's first screen "Show me the item". | "Finding the real cheapest price for an exact product means checking that every listing is the same item, new, and in stock. I just show it the item." |
| 0:07–0:24 | Drop or take the photo with the note left empty. Progress rows appear one by one: Gemini 4 Neon read the photo, Gemini Decision 1 gate, Gemini 4 Carbon read the request, catalog identity, Carbon bound the exact product, merchants checked. Result card: "Lowest verified among the stores checked", price, seller, "in stock". | "Neon reads the brand and the printed model number. Decision 1 confirms there is exactly one target. Carbon binds it to one product in the Shop catalog. Each merchant's own storefront confirms price and stock. No typing." |
| 0:24–0:37 | Scroll to "Why a cheaper listing lost". Open the near miss or the first ruled-out listing so its reason sentence is readable (for example sold out, not priced in USD, used or condition not confirmed). Then the coverage chips. | "This listing looked cheaper. It lost for this exact reason. And this is the coverage: which sources answered, and which did not." |
| 0:37–0:48 | Watch box: type "Notify me when this seller restocks under $25". The scope sentence appears with the status shown (in this build: "Preview only — not armed"), then the "Would this alert?" rows tagged simulation. If the app answers with a question instead, keep it in the take and answer it; do not cut to an earlier recording. | "A watch request becomes a typed rule, tested against counterexamples. Here it is an honest preview: nothing is polled without an authorized merchant feed." |
| 0:48–0:56 | Open /proof. Hold on the listing-check rows, then scroll to the end-to-end table so both arms are readable. | Read the on-screen counts with the denominator, then: "On blind catalog photos with no answers given, one model call beats our full system: ours stops and asks when no model number is printed. On the listing check it makes no false match and trails a single call by a pair. All of it is on this page." |
| 0:56–1:00 | Evidence drawer open on the model receipts (the interaction ids the service issued), then the result card. | "SignalForge. Built today at the Gemini 4 NYC Hackathon: the model pipeline, Shop catalog and merchant verification, ranking, the explainable watch and the evaluation." |

## Before recording

- Fresh browser profile or private window; confirm the page loads signed out.
- Pick the item by draw from the pool of products with a printed model number; do not reuse a photo from an earlier take.
- Microphone check; captions on if the recorder supports them.
- Run once without recording to confirm the demo URL answers: `curl -sS -o /dev/null -w '%{http_code}' https://reviewing-look-liz-course.trycloudflare.com/api/health`.

## After recording

- Watch the whole cut with sound. Check that every spoken number matches what is on screen.
- Upload, open the link in a private window, then put the link in README.md and docs/SUBMISSION.md (both marked TODO-LEAD).
