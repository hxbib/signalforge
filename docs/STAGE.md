# Three-minute live demo script

Demo URL: https://reviewing-look-liz-course.trycloudflare.com (evidence page: https://reviewing-look-liz-course.trycloudflare.com/proof). Laptop browser, private
window, phone ready as camera backup. Everything below is done live on an item chosen after the slot starts.
Say only what the screen shows. If something fails, show the real failure and the reason the app gives.

## 0:00–0:15 · Fresh input
- Draw the item after the clock starts (shuffle the items on the table, or let a judge point at one; a judge taking
  part is optional). It needs a visible printed model or set number.
- Take a new photo or drop a fresh image. Leave the note empty.
- Say: "One photo, no typing. Find me the cheapest new one."

## 0:15–0:50 · The answer
- While it runs, point at the progress rows: Neon read the photo, Decision 1 gate, Carbon read the request, Carbon
  bound the exact product, merchants checked. These are real calls with real timings.
- When the result appears, read the headline exactly as shown ("Lowest verified among the stores checked", "One
  verified offer" or "No verified offer yet"), the price basis line (item price, USD, before shipping and tax), the
  seller and the stock evidence.
- Open the merchant link. If the merchant's page shows a different price now, say so and run the search again.
- If the app asks a question instead (two items in frame, no readable model number), answer it with one tap and say
  that this is the gate refusing to guess.

## 0:50–1:25 · Why a cheaper listing lost
- Open "Why a cheaper listing lost" (or "Listings that were ruled out"). Read the exact reason on the cheapest
  rejected listing: sold out, not priced in USD, used or condition not confirmed, identity not confirmed, and so on.
- If nothing was rejected, open the coverage details instead and show a source that answered only partly or a
  listing left as an unverified lead, and say what that means: a lead is never ranked.

## 1:25–2:05 · The watch
- In the watch box type: "Notify me when this seller restocks under $25".
- Read the scope sentence and the status as shown. In this build the status is a preview ("not armed") because no
  authorized merchant feed is connected; say that plainly.
- Point at the "Would this alert?" rows, each tagged simulation: the price drops while the item stays in stock, and
  a restock watch stays quiet; a real restock under the cap alerts once.
- This explicit request should need no question, but it is not reliable yet: on a live run at 15:58 ET (winner in
  stock at a price far above the cap) the app asked which seller and which event was meant. If a question appears,
  read it aloud, answer it in the box, and say that the app asks instead of guessing. Rehearse this step on the
  frozen build before going on stage.

## 2:05–2:40 · Proof
- Open /proof. Read both arms with the denominator, straight from the page: correct out of the total, abstentions,
  false matches out of the actually different pairs.
- Say what each arm saw: the same text packet from independently labelled public product pairs (WDC v2), ids and
  labels withheld; the baseline is one Carbon call, the system is Neon plus Carbon with agreement and a verbatim
  quote check.
- Say the honest result: the system arm abstains more and scores lower than the single call on this benchmark, with
  no false match in either arm. Show one failing case from the list on the page (a matching pair the system
  abstained on).
- Say that the five-case photo-to-offer experiment was not run and why (no rights-cleared merchant evidence in time).

## 2:40–3:00 · One real trace, then close
- Back on the result, open the evidence drawer once: Neon's reading (brand, model number), Decision 1's choice and
  confidence against the 0.75 threshold, Carbon's receipts with interaction ids, the merchant's confirmation.
  Decision 1 issues no id; the drawer shows none.
- Close: "Models read and disambiguate. Merchant facts and code decide. That is the buyer's answer."

## If things go wrong
| Situation | What to do |
|---|---|
| The run ends "unavailable" | Read the reason shown, retry once with a new photo. Do not switch to a recording. |
| A source times out | The result is marked partial; show the coverage row and continue. |
| The tunnel URL does not load | Use http://localhost:8000 on the event machine and say that the public link is a tunnel to the same server. |
| The winner's page changed | Say it, search again, compare. |
| Asked about anything not on screen | Answer from docs/QA.md; say "not measured" when it was not measured. |
