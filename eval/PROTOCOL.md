# A blind 5 Sigma evaluation the team can run alone

**Question 1:** Can the app tell whether two merchants sell the exact same item? **Question 2:** Given a fresh product image and a buyer's request, can it identify the actual cheapest eligible offer in the evidence available? These are two separate experiments, with separate denominators. No outsider, judge-supplied item, public survey or volunteer label is needed. A judge may simply observe. Local unit tests and hypothetical watch timelines are development evidence.

## Freeze and split

Use development examples to find one genuine bug, record the before/after on those same examples, and commit the fix. Then freeze prompts, code, field normalization, sampling and metric definitions. Record that commit and wall-clock time. Draw the final sample after this freeze; freeze the five-case evidence as close together in time as feasible. The scoring script, label files and original gold stay outside the model context; do not attach them when dispatching workers. Both arms see the exact same allowed raw case. Disable web/tools for model calls in the paired trial. No retrying individual wrong answers or replacing a no-result. If a final outcome inspires another fix, label the rerun as post-hoc and keep the original.

## Experiment A · 40 public, independently labeled listing pairs

Use the authors' WDC v2 **non-normalized gold-standard** camera and computer archives. Public benchmark labels reflect product correspondence; some ambiguous consumer variants may legitimately make the app abstain. Published labels could be familiar to a pretrained model. This is an external matching check, not a test of live stock or camera input. Cite WDC; publish inputs by reference and aggregate results in the public repo. No raw third-party datasets are included in this handoff. The source page links both small downloads. [WDC v2](https://webdatacommons.org/largescaleproductcorpus/v2/)

Run from the handoff root in the event environment. Set BENCH_DIR to a new **private directory outside the public release**, and FREEZE_SHA to the actual frozen git commit. The exclusive output mode rejects accidental overwrites. Use different directories for pilots and held-out runs.

```sh
: "${BENCH_DIR:?Set an absolute private evaluation directory first}"
: "${FREEZE_SHA:?Set the frozen git commit first}"
mkdir -p "$BENCH_DIR"/in "$BENCH_DIR"/gold "$BENCH_DIR"/runs
curl -fL --retry 2 -o "$BENCH_DIR/computers.gz" https://data.dws.informatik.uni-mannheim.de/largescaleproductcorpus/data/v2_nonnorm/goldstandards/computers_gs.json.gz
curl -fL --retry 2 -o "$BENCH_DIR/cameras.gz" https://data.dws.informatik.uni-mannheim.de/largescaleproductcorpus/data/v2_nonnorm/goldstandards/cameras_gs.json.gz
python eval/bench.py sample --computers "$BENCH_DIR/computers.gz" --cameras "$BENCH_DIR/cameras.gz" --seed "$FREEZE_SHA" --inputs "$BENCH_DIR/in/pairs.jsonl" --gold "$BENCH_DIR/gold/pairs.jsonl"
python eval/bench.py predict "$BENCH_DIR/in/pairs.jsonl" baseline "$BENCH_DIR/runs/pair-base.jsonl"
python eval/bench.py predict "$BENCH_DIR/in/pairs.jsonl" full "$BENCH_DIR/runs/pair-full.jsonl"
python eval/bench.py score "$BENCH_DIR/in/pairs.jsonl" "$BENCH_DIR/gold/pairs.jsonl" "$BENCH_DIR/runs/pair-base.jsonl" "$BENCH_DIR/runs/pair-full.jsonl"
```

The sample contains ten positive and ten negative pairs per category. Only title, description, brand and specification text reach inference; IDs/clusters/labels are withheld. The baseline is one Carbon call. The product's fallback text matcher uses Neon evidence extraction, Carbon adjudication, agreement and verbatim evidence checking. Wire that same callable into production candidate resolution. Report correct/40, false positive matches/20 actual negatives, answered/40, abstentions and runtime errors. Score abstention as incorrect against binary gold and report coverage alongside. Show paired wins and losses. Do not describe this number as full shopping accuracy.

## Experiment B · five genuinely fresh photo-to-offer cases

1. Before freezing, define the sampling frame: an authorized real merchant or manufacturer catalog with multiple candidate sellers and rights to privately replay the evidence to both models. Record source/version, how to enumerate eligible products and the development exclusions. Eligible products may have one, many or zero current in-stock offers. Do not filter by predicted success or by a minimum of two *available* offers. Use a rights-cleared product image or the team's original new capture of each selected item. A private public-page screenshot alone does not establish replay rights.
2. Sort stable product IDs; remove development SKUs; choose the first five after sorting by SHA-256 of frozen commit + slash + ID. Preassign scenarios in draw order: ordinary US item price, ship to one specified ZIP, delivered total, ordinary US item price, pickup at a specified store. Keep unavailable fulfillment/evidence as a legitimate abstention. Capture the source state and actual time, preserve or securely reference only permitted evidence. Historical price fluctuations outside the captured instant do not relabel the case.
3. Human operator adjudicates all five in one pass **before either arm's answer is shown**. For each row verify GTIN/model, exact pack, seller, stock, basis, fulfillment, amount and fees against the original independent source. Copy all tied sellers, integer winning cents, normalized GTIN, or an abstention. Label sellers proven unavailable across their relevant listings separately; unknown stock is not proven sold out. A merchant's authoritative structured snapshot can prefill this worksheet; review only the uncertain rows. Never label with SignalForge's ranking function, the single-call baseline, a third model's unsupported answer or its internal eligibility verdicts.
4. Write a private JSON line per case. Top-level fields: id, jpeg (relative path to clean JPEG), note, goal {basis,fulfillment}, as_of, anchors, offers, source_rights, gold_evidence and expected. The anchors and offers are the actual server-normalized input fields from spec/INTERFACES.md. No internal eligibility verdict, exclusion label, hidden adjudication or demographics goes in those fields. expected = {status,winners,price_cents,gtin,unavailable_sellers}. For abstain use winners=[], price_cents=null, gtin=null or established identity. The scorer accepts an abstention only with zero winners and null price. Record a specific ground-truth reason separately in gold_evidence. Before inference seal the five cases using the command below.
5. Use the identical image, buyer request, **declared goal**, stable merchant pool and frozen observations for both arms. The single Carbon call gets the actual constraints and the same evidence. The full arm runs Decision 1 + Neon + Carbon target binding and the production price evaluator. This comparison measures visual identity and ranking *conditional on the supplied catalog evidence and declared goal*. It does not independently measure search recall, language-to-goal parsing or live network performance. Measure those with the actual app and publish actual end-to-end timings separately.

```sh
python eval/seal_baskets.py "$BENCH_DIR/cases.jsonl" "$BENCH_DIR/in/shop.jsonl" "$BENCH_DIR/gold/shop.jsonl"
python eval/bench.py predict "$BENCH_DIR/in/shop.jsonl" baseline "$BENCH_DIR/runs/shop-base.jsonl"
python eval/bench.py predict "$BENCH_DIR/in/shop.jsonl" full "$BENCH_DIR/runs/shop-full.jsonl"
python eval/bench.py score "$BENCH_DIR/in/shop.jsonl" "$BENCH_DIR/gold/shop.jsonl" "$BENCH_DIR/runs/shop-base.jsonl" "$BENCH_DIR/runs/shop-full.jsonl"
```

Show exact winner set + price + item correct out of five, how many answers named a seller proved unavailable, number of abstentions, per-arm latency, one real disagreement and the source fact that settled it. Returns with the correct price but a wrong pack or tie set count as errors. Errors/timeouts stay in both denominators. If the independent source/rights or five cases are unavailable, report the exact shortfall and the separate WDC result. Never fill the missing cohort with mock prices or present a planned score. Small pilot counts are fair to present as counts.

## Evidence of “magic”

On independent shopping/live runs also record total runs, runs completed with zero follow-up questions, number of interventions, time to first eligible quote, time to final verified result, source coverage, and whether the first clarification actually resolved a material ambiguity. Use the same defaults when comparing interaction counts. Report actuals only. Keep judges' evidence panel compact: two numbers with denominators, one false bargain, one known limitation. Publish only allowed aggregate data, prompt version, source citation, sample rule and failure analysis.

