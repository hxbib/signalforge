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

    def test_candidate_with_agreeing_barcode_skips_pair(self):
        cand = row("outside", 1999, product="gid://shopify/p/other", title="Acme X100")
        pair = pair_says("different")
        r = run([row("alpha", 2999), cand], pair=pair)["result"]
        self.assertEqual(pair.calls, [])
        self.assertEqual((r["winners"][0]["seller_name"], r["winners"][0]["identity_basis"]), ("Outside", "merchant_barcode"))

    def test_malformed_confirm_answer_counts_as_failed(self):
        out = run_comparison(TARGET, ITEM, [row("alpha", 2999)], as_of=NOW, confirm=lambda q, a: None, pair=None)
        self.assertEqual(out["coverage"]["sources"][1]["merchants"]["failed"], 1)
        self.assertEqual(out["result"]["winners"][0]["confirmed_by"], [])


if __name__ == "__main__":
    unittest.main()
