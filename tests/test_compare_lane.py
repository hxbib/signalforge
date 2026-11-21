"""run_comparison with injected confirm / pair fakes. Synthetic rows only; no real merchant prices."""
import time
import unittest

import app  # noqa: F401
from app.merchants.compare import run_comparison

NOW = 5000
UPID = "gid://shopify/p/target"
UPC = "673419404235"
OTHER_UPC = "4006381333931"
TARGET = {"upid": UPID, "gtin": None, "brand": "Acme", "mpn": "X100", "name": "Acme widget", "variant_details": []}
ITEM = {"basis": "item", "fulfillment": "online_us"}


def row(seller, cents, **kw):
    base = {"seller": "gid://shopify/Shop/" + seller, "seller_name": seller.title(), "seller_domain": seller + ".example",
            "listing": "gid://shopify/ProductVariant/" + seller, "product": UPID, "handle": "w",
            "title": "Acme Widget X100", "url": "https://%s.example/p" % seller, "image_url": None,
            "source": "shop_native", "read_at": NOW - 5, "gtin": None, "sku": "", "condition": "new",
            "stock": "in_stock", "fulfillment": "online_us", "unconditional": True, "currency": "USD", "item_cents": cents}
    base.update(kw)
    return base


def confirming(barcode=UPC, overrides=None, statuses=None, delay=None):
    """Fake storefront: echoes the catalog row as a merchant_api quote, with optional per-seller changes."""
    overrides, statuses, delay = overrides or {}, statuses or {}, delay or {}

    def confirm(quote, as_of):
        name = quote["seller_name"]
        if name in delay:
            time.sleep(delay[name])
        status = statuses.get(name, "confirmed")
        receipt = {"source": "merchant_api", "url_host": quote["seller_domain"], "read_at": as_of, "ms": 1,
                   "response_hash": None}
        if status in ("unavailable", "not_permitted"):
            return {"status": status, "quote": None, "receipt": receipt, "note": "fake"}
        q = dict(quote, source="merchant_api", read_at=as_of, gtin=barcode, sku="S")
        q.update(overrides.get(name, {}))
        return {"status": status, "quote": q, "receipt": receipt, "note": "fake"}
    return confirm


def pair_says(verdict):
    calls = []

    def pair(target, listing):
        calls.append(listing)
        return {"verdict": verdict, "receipts": [{"job": "pair_verdict", "model": "fake", "id": "t", "ms": 1}]}
    pair.calls = calls
    return pair


def run(rows, goal=ITEM, target=TARGET, **kw):
    kw.setdefault("confirm", confirming())
    kw.setdefault("pair", pair_says("abstain"))
    return run_comparison(target, goal, rows, as_of=NOW, **kw)


class CompareLane(unittest.TestCase):
    def test_single_winner(self):
        out = run([row("alpha", 2999), row("beta", 3499)])
        r = out["result"]
        self.assertEqual((r["status"], r["headline"], r["price_cents"]), ("winner", "Lowest verified among the stores checked", 2999))
        self.assertEqual([w["seller_name"] for w in r["winners"]], ["Alpha"])
        w = r["winners"][0]
        self.assertTrue(w["verified"])
        self.assertEqual(w["confirmed_by"], ["merchant_api"])
        self.assertEqual(w["identity_basis"], "merchant_barcode")
        self.assertEqual(out["evaluation"]["target_gtin"], UPC.zfill(14))
        self.assertEqual(out["coverage"]["sellers_eligible"], 2)
        self.assertEqual(out["coverage"]["sources"][1]["status"], "ok")
        self.assertEqual(r["near_miss"], None)

    def test_one_verified_offer(self):
        r = run([row("alpha", 2999)])["result"]
        self.assertEqual((r["status"], r["headline"]), ("single_offer", "One verified offer"))

    def test_two_seller_tie(self):
        r = run([row("alpha", 2999), row("beta", 2999), row("gamma", 4000)])["result"]
        self.assertEqual(r["status"], "tie")
        self.assertEqual(sorted(w["seller_name"] for w in r["winners"]), ["Alpha", "Beta"])

    def test_conflicting_duplicate_excluded(self):
        conf = confirming(overrides={"Alpha": {"item_cents": 3999}}, statuses={"Alpha": "mismatch"})
        out = run([row("alpha", 1999), row("beta", 2999)], confirm=conf)
        r = out["result"]
        self.assertEqual([w["seller_name"] for w in r["winners"]], ["Beta"])
        rej = [x for x in r["rejected"] if x["offer"]["seller_name"] == "Alpha"]
        self.assertEqual(rej[0]["reason"], "conflicting_sources")
        self.assertEqual(rej[0]["sentence"], "The catalog and the merchant's own page disagree")
        self.assertIsNone(r["near_miss"])          # a disputed price is never offered as a saving

    def test_mismatch_with_identical_fields_still_excluded(self):
        out = run([row("alpha", 1999), row("beta", 2999)], confirm=confirming(statuses={"Alpha": "mismatch"}))
        self.assertEqual([w["seller_name"] for w in out["result"]["winners"]], ["Beta"])

    def test_cheaper_used_listing_is_near_miss(self):
        conf = confirming(overrides={"Used": {"condition": "secondhand"}})
        r = run([row("alpha", 2999), row("used", 2599, condition="secondhand"), row("beta", 3200)], confirm=conf)["result"]
        self.assertEqual(r["winners"][0]["seller_name"], "Alpha")
        nm = r["near_miss"]
        self.assertEqual(nm["offer"]["seller_name"], "Used")
        self.assertEqual(nm["sentence"], "$4.00 less, but used or condition not confirmed as new")
        self.assertEqual(nm["delta_cents"], -400)

    def test_cheaper_eur_listing_never_called_less(self):
        conf = confirming(overrides={"Euro": {"currency": "EUR"}})
        r = run([row("alpha", 2999), row("euro", 1500, currency="EUR")], confirm=conf)["result"]
        rej = [x for x in r["rejected"] if x["offer"]["seller_name"] == "Euro"][0]
        self.assertEqual((rej["reason"], rej["sentence"]), ("price_unverified", "Not priced in USD"))
        self.assertIsNone(r["near_miss"])
        self.assertNotIn("less", str(r))

    def test_sold_out_cheaper_listing(self):
        conf = confirming(overrides={"Gone": {"stock": "sold_out"}})
        r = run([row("alpha", 2999), row("gone", 2599, stock="sold_out")], confirm=conf)["result"]
        self.assertEqual(r["near_miss"]["sentence"], "$4.00 less, but sold out")
        self.assertEqual([x["sentence"] for x in r["rejected"]], ["Sold out"])

    def test_unknown_stock(self):
        conf = confirming(overrides={"Maybe": {"stock": "unknown"}})
        r = run([row("maybe", 1999, stock="unknown")], confirm=conf)["result"]
        self.assertEqual((r["status"], r["headline"]), ("no_verified_offer", "No verified offer yet"))
        self.assertEqual(r["rejected"][0]["reason"], "stock_unverified")
        self.assertIsNone(r["near_miss"])

    def test_delivered_unknown_shipping_keeps_item_view(self):
        goal = {"basis": "delivered", "fulfillment": "ship:10001"}
        rows = [row("alpha", 2999, fulfillment="ship:10001"), row("beta", 3100, fulfillment="ship:10001")]
        r = run(rows, goal=goal)["result"]
        self.assertEqual(r["status"], "no_verified_offer")
        self.assertEqual({x["reason"] for x in r["rejected"]}, {"total_unknown"})
        iv = r["item_price_view"]
        self.assertEqual((iv["price_cents"], iv["winners"][0]["seller_name"]), (2999, "Alpha"))
        self.assertIn("Item price", iv["basis_label"])

    def test_confirm_timeout_partial_coverage(self):
        conf = confirming(delay={"Slow": 3.0})
        t0 = time.monotonic()
        out = run([row("alpha", 2999), row("slow", 1999)], confirm=conf, deadline_s=1.0)
        self.assertLess(time.monotonic() - t0, 2.5)
        cov = out["coverage"]["sources"][1]
        self.assertEqual(cov["status"], "limited")
        self.assertEqual(cov["merchants"]["timeout"], 1)
        self.assertIn("1 timed out", cov["note"])
        r = out["result"]
        self.assertIsNotNone(r["status"])
        # the slow seller's catalog row still stands alone (catalog_product tier)
        self.assertEqual(r["winners"][0]["seller_name"], "Slow")
        self.assertEqual(r["winners"][0]["confirmed_by"], [])

    def test_failing_merchant_does_not_block(self):
        def conf(q, as_of):
            if q["seller_name"] == "Bad":
                raise ConnectionError("down")
            return confirming()(q, as_of)
        out = run([row("alpha", 2999), row("bad", 2500)], confirm=conf)
        self.assertEqual(out["coverage"]["sources"][1]["merchants"]["failed"], 1)
        self.assertTrue(out["result"]["winners"])

    def test_unavailable_merchant_counts_in_coverage(self):
        out = run([row("alpha", 2999)], confirm=confirming(statuses={"Alpha": "unavailable"}))
        self.assertEqual(out["result"]["winners"][0]["identity_basis"], "catalog_product")
        self.assertEqual(out["coverage"]["sources"][1]["merchants"]["unavailable"], 1)

    def test_stale_rows(self):
        r = run([row("alpha", 2999, read_at=NOW - 500)], confirm=confirming(statuses={"Alpha": "unavailable"}))["result"]
        self.assertEqual(r["status"], "no_verified_offer")
        self.assertEqual(r["rejected"][0]["reason"], "stale")
        self.assertEqual(r["rejected"][0]["sentence"], "Price reading is too old to trust")

    def test_barcode_disagreement_inside_cluster(self):
        def conf(q, as_of):
            return confirming(barcode=UPC if q["seller_name"] == "Alpha" else OTHER_UPC)(q, as_of)
        out = run([row("alpha", 2999), row("beta", 2500), row("gamma", 3500)],
                  confirm=lambda q, a: conf(q, a) if q["seller_name"] != "Gamma" else
                  {"status": "unavailable", "quote": None, "receipt": {}, "note": ""})
        ev = out["evaluation"]
        self.assertTrue(ev["barcode_conflict"])
        self.assertIsNone(ev["target_gtin"])
        r = out["result"]
        self.assertEqual([w["seller_name"] for w in r["winners"]], ["Gamma"])
        reasons = {x["offer"]["seller_name"]: x["reason"] for x in r["rejected"]}
        self.assertEqual(reasons, {"Alpha": "different_or_unverified_item", "Beta": "different_or_unverified_item"})

    def test_known_gtin_rejects_other_barcode_in_cluster(self):
        def conf(q, as_of):
            return confirming(barcode=UPC if q["seller_name"] == "Alpha" else OTHER_UPC)(q, as_of)
        out = run([row("alpha", 2999), row("beta", 2500)], target=dict(TARGET, gtin=UPC), confirm=conf)
        r = out["result"]
        self.assertEqual([w["seller_name"] for w in r["winners"]], ["Alpha"])
        self.assertEqual(r["rejected"][0]["sentence"], "Different item or identity not confirmed")

    def test_model_number_abstain_stays_lead(self):
        cand = row("outside", 1999, product="gid://shopify/p/other", title="ACME X100 widget, boxed")
        pair = pair_says("abstain")
        out = run([row("alpha", 2999), cand], pair=pair, confirm=confirming(barcode=None))
        r = out["result"]
        self.assertEqual([w["seller_name"] for w in r["winners"]], ["Alpha"])
        self.assertEqual([(x["seller_name"], x["lead_reason"]) for x in r["leads"]],
                         [("Outside", "Listing check could not confirm the same item")])
        self.assertFalse(r["leads"][0]["verified"])
        self.assertEqual(len(pair.calls), 1)

    def test_model_number_match_is_ranked(self):
        cand = row("outside", 1999, product="gid://shopify/p/other", title="ACME X100 widget, boxed")
        out = run([row("alpha", 2999), cand], pair=pair_says("match"), confirm=confirming(barcode=None))
        w = out["result"]["winners"][0]
        self.assertEqual((w["seller_name"], w["identity_basis"]), ("Outside", "model_number"))

    def test_model_number_needs_exact_token(self):
        cand = row("outside", 1999, product="gid://shopify/p/other", title="Acme X1000 widget")
        pair = pair_says("match")
        r = run([row("alpha", 2999), cand], pair=pair)["result"]
        self.assertEqual(pair.calls, [])
        self.assertEqual(r["leads"][0]["seller_name"], "Outside")

    def test_agreeing_barcode_outranks_listing_check(self):
        cand = row("outside", 1999, product="gid://shopify/p/other", title="Acme X100")
        pair = pair_says("different")      # runs concurrently with the confirm; the barcode decides
        r = run([row("alpha", 2999), cand], pair=pair)["result"]
        self.assertEqual((r["winners"][0]["seller_name"], r["winners"][0]["identity_basis"]), ("Outside", "merchant_barcode"))

    def test_malformed_confirm_answer_counts_as_failed(self):
        out = run_comparison(TARGET, ITEM, [row("alpha", 2999)], as_of=NOW, confirm=lambda q, a: None, pair=None)
        self.assertEqual(out["coverage"]["sources"][1]["merchants"]["failed"], 1)
        self.assertEqual(out["result"]["winners"][0]["confirmed_by"], [])

    def test_listing_checks_run_concurrently_after_confirms(self):
        def slow_pair(target, listing):
            time.sleep(0.6)
            return {"verdict": "match", "receipts": []}
        cands = [row("c%d" % i, 1000 + i, product="gid://shopify/p/other", title="Acme X100 widget") for i in range(6)]
        t0 = time.monotonic()
        out = run([row("alpha", 2999)] + cands, pair=slow_pair,
                  confirm=confirming(barcode=None, delay={"Alpha": 0.2}))
        self.assertLess(time.monotonic() - t0, 1.3)       # 4 checks at once (sequential would be 2.4 s)
        lc = out["coverage"]["sources"][2]
        self.assertEqual((lc["id"], lc["status"], lc["offers"], lc["eligible"]), ("listing_checks", "limited", 4, 4))
        self.assertEqual(lc["name"], "Listing check (Gemini 4 Carbon, verified quotes)")
        self.assertIsInstance(lc["ms"], int)
        self.assertIsInstance(out["coverage"]["sources"][1]["ms"], int)

    def test_late_listing_check_stays_lead(self):
        def late_pair(target, listing):
            time.sleep(3.0)
            return {"verdict": "match", "receipts": []}
        cand = row("late", 1999, product="gid://shopify/p/other", title="Acme X100 widget")
        t0 = time.monotonic()
        out = run([row("alpha", 2999), cand], pair=late_pair, deadline_s=1.0, confirm=confirming(barcode=None))
        self.assertLess(time.monotonic() - t0, 2.0)
        r = out["result"]
        self.assertEqual(r["winners"][0]["seller_name"], "Alpha")
        self.assertEqual(r["leads"][0]["lead_reason"], "identity check did not finish in time")
        lc = out["coverage"]["sources"][2]
        self.assertEqual(lc["status"], "limited")
        self.assertIn("1 did not finish in time", lc["note"])

    def test_listing_check_cap_and_cheapest_first(self):
        pair = pair_says("abstain")
        cands = [row("c%d" % i, 2000 - i, product="gid://shopify/p/other", title="Acme X100") for i in range(8)]
        out = run([row("alpha", 2999)] + cands, pair=pair, confirm=confirming(barcode=None))
        self.assertEqual(len(pair.calls), 4)
        self.assertEqual({c["seller_name"] for c in pair.calls}, {"C4", "C5", "C6", "C7"})   # cheapest four
        self.assertIn("4 not checked (limit)", out["coverage"]["sources"][2]["note"])

    def test_merchant_read_after_clock_is_not_stale(self):
        """bug 003: a confirm that reads after run start must not be called stale, nor the winner rejected."""
        now = int(time.time())
        cat = row("alpha", 1000, read_at=now)

        def conf(q, as_of):
            time.sleep(1.1)
            m = dict(q, source="merchant_api", read_at=int(time.time()), gtin=UPC, sku="X")
            return {"status": "confirmed", "quote": m, "receipt": {"source": "merchant_api", "read_at": m["read_at"]}, "note": ""}
        out = run_comparison(TARGET, ITEM, [cat], confirm=conf, pair=None)
        r = out["result"]
        self.assertEqual([w["seller_name"] for w in r["winners"]], ["Alpha"])
        self.assertEqual(r["winners"][0]["confirmed_by"], ["merchant_api"])
        self.assertEqual(r["rejected"], [])

    def test_stale_catalog_duplicate_of_eligible_listing_not_rejected(self):
        out = run([row("alpha", 2999, read_at=NOW - 500)])     # fresh merchant read stands; old catalog read dropped
        r = out["result"]
        self.assertEqual([w["seller_name"] for w in r["winners"]], ["Alpha"])
        self.assertEqual(r["rejected"], [])

    def test_runner_up_is_next_seller(self):
        r = run([row("alpha", 2999), row("beta", 3499), row("gamma", 3999)])["result"]
        self.assertEqual((r["runner_up"]["seller_name"], r["runner_up"]["item_cents"]), ("Beta", 3499))
        self.assertTrue(r["runner_up"]["verified"])

    def test_runner_up_skips_tied_winners_and_null_when_alone(self):
        r = run([row("alpha", 2999), row("beta", 2999), row("gamma", 3999)])["result"]
        self.assertEqual(r["runner_up"]["seller_name"], "Gamma")
        self.assertIsNone(run([row("alpha", 2999)])["result"]["runner_up"])
        self.assertIsNone(run([row("alpha", 2999), row("beta", 2999)])["result"]["runner_up"])

    def test_price_outlier_caution(self):
        r = run([row("cheap", 3050), row("b", 8499), row("c", 8599), row("d", 8999)])["result"]
        c = r["caution"]
        self.assertEqual(r["winners"][0]["seller_name"], "Cheap")          # nothing re-ranked
        self.assertEqual((c["code"], c["median_cents"], c["eligible_sellers"], c["percent_below"]),
                         ("price_outlier", 8549, 4, 64))
        self.assertEqual(c["sentence"], "This price is 64% below the median of the 4 verified offers ($85.49). "
                         "Verified means the merchant's own storefront states this price and stock for this exact item "
                         "\u2014 it is not a rating of the merchant.")

    def test_caution_boundary_at_40_percent(self):
        # median of (6000, 10000, 10000) = 10000; 6000 is exactly 40% below -> caution; 6001 is not
        c = run([row("a", 6000), row("b", 10000), row("c", 10000)])["result"]["caution"]
        self.assertEqual((c["percent_below"], c["median_cents"], c["eligible_sellers"]), (40, 10000, 3))
        self.assertIsNone(run([row("a", 6001), row("b", 10000), row("c", 10000)])["result"]["caution"])

    def test_caution_needs_three_sellers(self):
        self.assertIsNone(run([row("a", 1000), row("b", 10000)])["result"]["caution"])
        self.assertIsNone(run([row("a", 1000)])["result"]["caution"])
        self.assertIsNone(run([row("a", 1000, stock="sold_out"), row("b", 10000), row("c", 10000)],
                              confirm=confirming(overrides={"A": {"stock": "sold_out"}}))["result"]["caution"])

    def test_caution_with_tied_winners(self):
        r = run([row("a", 3000), row("b", 3000), row("c", 9000), row("d", 9000), row("e", 9000)])["result"]
        self.assertEqual(r["status"], "tie")
        self.assertEqual((r["caution"]["median_cents"], r["caution"]["percent_below"]), (9000, 66))
        self.assertEqual(r["runner_up"]["item_cents"], 9000)

    def test_single_outlier_barcode_does_not_wipe_cluster(self):
        """bug 020: 3 sellers agree on one barcode, 1 outlier publishes another -> majority stands."""
        def conf(q, as_of):
            return confirming(barcode=OTHER_UPC if q["seller_name"] == "Odd" else UPC)(q, as_of)
        out = run([row("alpha", 2999), row("beta", 3099), row("gamma", 3199), row("odd", 2500)], confirm=conf)
        ev, r = out["evaluation"], out["result"]
        self.assertTrue(ev["barcode_conflict"])
        self.assertEqual((ev["target_gtin"], ev["gtin_source"]), (UPC.zfill(14), "cluster_merchant_barcode_majority"))
        self.assertEqual([w["seller_name"] for w in r["winners"]], ["Alpha"])
        self.assertEqual({x["offer"]["seller_name"]: x["reason"] for x in r["rejected"]},
                         {"Odd": "different_or_unverified_item"})

    def test_even_barcode_split_stays_conservative(self):
        def conf(q, as_of):
            return confirming(barcode=OTHER_UPC if q["seller_name"] in ("Odd", "Beta") else UPC)(q, as_of)
        out = run([row("alpha", 2999), row("beta", 3099), row("odd", 2500), row("gamma", 3199)], confirm=conf)
        self.assertIsNone(out["evaluation"]["target_gtin"])
        self.assertEqual(out["result"]["status"], "no_verified_offer")

    def test_amount_without_currency_never_shown(self):
        """bug 021: a storefront amount with unknown currency is never displayed as a number."""
        conf = confirming(overrides={"Uk": {"item_cents": 10000, "currency": None}}, statuses={"Uk": "mismatch"})
        r = run([row("alpha", 2999), row("uk", 13347)], confirm=conf)["result"]
        uk = [x for x in r["rejected"] if x["offer"]["seller_name"] == "Uk"][0]
        self.assertIsNone(uk["offer"]["item_cents"])
        self.assertNotIn(10000, [x["offer"]["item_cents"] for x in r["rejected"]])

    def test_leads_cheapest_first(self):
        rows = [row("alpha", 2999)] + [row(n, c, product="gid://shopify/p/other", title="Other thing")
                                       for n, c in (("x", 5000), ("y", 1100), ("z", 3000))]
        r = run(rows)["result"]
        self.assertEqual([l["item_cents"] for l in r["leads"]], [1100, 3000, 5000])

    # ---- C. Decision 1 screen (consumer side): orders, prunes, annotates; never grants identity
    def test_screen_p_carried_into_every_offer_view(self):
        conf = confirming(overrides={"Used": {"condition": "secondhand"}})
        rows = [row("alpha", 2999, screen_p=0.97), row("beta", 3499, screen_p=0.91),
                row("used", 2599, condition="secondhand", screen_p=0.88),
                row("lead", 999, product="gid://shopify/p/other", title="Other thing", screen_p=0.2)]
        r = run(rows, confirm=conf)["result"]
        self.assertEqual(r["winners"][0]["screen_p"], 0.97)
        self.assertEqual(r["runner_up"]["screen_p"], 0.91)
        self.assertEqual(r["near_miss"]["offer"]["screen_p"], 0.88)
        self.assertEqual(r["rejected"][0]["offer"]["screen_p"], 0.88)
        self.assertEqual(r["leads"][0]["screen_p"], 0.2)
        self.assertIsNone(run([row("alpha", 2999)])["result"]["winners"][0]["screen_p"])   # absent when no screen ran

    def test_screen_low_p_keeps_candidate_a_lead_without_check(self):
        pair = pair_says("match")
        cand = row("outside", 1999, product="gid://shopify/p/other", title="Acme X100", screen_p=0.05)
        r = run([row("alpha", 2999), cand], pair=pair, confirm=confirming(barcode=None))["result"]
        self.assertEqual(pair.calls, [])
        self.assertEqual(r["leads"][0]["lead_reason"], "Decision 1 screen: different item (p 0.05)")

    def test_screen_middle_p_not_checked(self):
        pair = pair_says("match")
        cand = row("outside", 1999, product="gid://shopify/p/other", title="Acme X100", screen_p=0.3)
        r = run([row("alpha", 2999), cand], pair=pair, confirm=confirming(barcode=None))["result"]
        self.assertEqual(pair.calls, [])
        self.assertEqual(r["leads"][0]["lead_reason"], "Not checked: Decision 1 screen below 0.5 (p 0.30)")

    def test_screen_high_p_checked_and_match_ranked(self):
        pair = pair_says("match")
        cand = row("outside", 1999, product="gid://shopify/p/other", title="Acme X100", screen_p=0.8)
        r = run([row("alpha", 2999), cand], pair=pair, confirm=confirming(barcode=None))["result"]
        self.assertEqual(len(pair.calls), 1)
        self.assertEqual((r["winners"][0]["seller_name"], r["winners"][0]["identity_basis"]), ("Outside", "model_number"))

    def test_only_candidates_cheaper_than_cluster_are_checked(self):
        pair = pair_says("match")
        cand = row("pricey", 3999, product="gid://shopify/p/other", title="Acme X100")
        r = run([row("alpha", 2999), cand], pair=pair, confirm=confirming(barcode=None))["result"]
        self.assertEqual(pair.calls, [])
        self.assertEqual(r["leads"][0]["lead_reason"], "Not checked: costs more than the verified winner")
        # no eligible cluster offer -> every candidate may be checked
        pair = pair_says("match")
        r = run([row("alpha", 2999, stock="sold_out"), cand], pair=pair,
                confirm=confirming(barcode=None, overrides={"Alpha": {"stock": "sold_out"}}))["result"]
        self.assertEqual(len(pair.calls), 1)
        self.assertEqual(r["winners"][0]["seller_name"], "Pricey")

    def test_screen_never_changes_cluster_or_barcode_ranking(self):
        cand = row("outside", 1999, product="gid://shopify/p/other", title="Acme X100", screen_p=0.01)
        r = run([row("alpha", 2999, screen_p=0.01), row("beta", 3499, screen_p=0.99), cand],
                pair=pair_says("different"))["result"]
        # agreeing published barcode outranks the screen; cluster rows ranked by price only
        self.assertEqual([w["seller_name"] for w in r["winners"]], ["Outside"])
        self.assertEqual(r["winners"][0]["identity_basis"], "merchant_barcode")
        self.assertEqual(r["runner_up"]["seller_name"], "Alpha")

    def test_threshold_is_eligible_winner_after_confirmation(self):
        """16:16 live shape (LEGO 75375): the cluster's cheapest rows are REJECTED after the storefront disagrees;
        a model-number candidate cheaper than the verified winner must get the listing check."""
        conf = confirming(barcode=None, statuses={"Cheap1": "mismatch", "Cheap2": "mismatch"},
                          overrides={"Cheap1": {"item_cents": 9999}, "Cheap2": {"item_cents": 9999}})
        cluster = [row("cheap1", 2040), row("cheap2", 5700), row("galactic", 8495), row("b", 8499), row("c", 8999)]
        cands = [row("eastline", 3050, product="gid://shopify/p/other", title="Acme X100 set", screen_p=0.883),
                 row("pzdeals", 6999, product="gid://shopify/p/other", title="Acme X100", screen_p=0.769),
                 row("dear", 9500, product="gid://shopify/p/other", title="Acme X100")]
        pair = pair_says("match")
        out = run(cluster + cands, pair=pair, confirm=conf)
        r = out["result"]
        self.assertEqual({c["seller_name"] for c in pair.calls}, {"Eastline", "Pzdeals"})
        self.assertEqual((r["winners"][0]["seller_name"], r["winners"][0]["identity_basis"]), ("Eastline", "model_number"))
        self.assertEqual(r["runner_up"]["seller_name"], "Pzdeals")
        c = r["caution"]
        # eligible sellers: eastline 3050, pzdeals 6999, galactic 8495, b 8499, c 8999 -> median 8495
        self.assertEqual((c["median_cents"], c["eligible_sellers"], c["percent_below"]), (8495, 5, 64))
        self.assertEqual({l["seller_name"]: l["lead_reason"] for l in r["leads"]},
                         {"Dear": "Not checked: costs more than the verified winner"})
        self.assertEqual({x["offer"]["seller_name"]: x["reason"] for x in r["rejected"]},
                         {"Cheap1": "conflicting_sources", "Cheap2": "conflicting_sources"})

    def test_no_eligible_offer_checks_cheapest_four(self):
        cands = [row("c%d" % i, 1000 + i, product="gid://shopify/p/other", title="Acme X100") for i in range(6)]
        pair = pair_says("abstain")
        r = run([row("alpha", 2999, stock="sold_out")] + cands, pair=pair,
                confirm=confirming(barcode=None, overrides={"Alpha": {"stock": "sold_out"}}))["result"]
        self.assertEqual({c["seller_name"] for c in pair.calls}, {"C0", "C1", "C2", "C3"})
        reasons = {l["seller_name"]: l["lead_reason"] for l in r["leads"]}
        self.assertEqual((reasons["C4"], reasons["C5"]), ("Not checked: listing check limit (4) reached",) * 2)


if __name__ == "__main__":
    unittest.main()
