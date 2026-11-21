"""ranking.evaluate must equal reference buyer.evaluate on GTIN-identified rows (cases from reference/test_final.py)."""
import unittest

import app  # noqa: F401
from buyer import evaluate as reference_evaluate
from app.merchants.ranking import evaluate

UPC = "673419404235"
GOAL = {"basis": "item", "fulfillment": "ship:10001"}
OFFER = {"seller": "retail", "listing": "sku-1", "gtin": UPC, "source": "merchant_page", "url": "https://shop.example/item",
         "read_at": 1000, "currency": "USD", "item_cents": 2499, "condition": "new", "stock": "in_stock",
         "unconditional": True, "fulfillment": "ship:10001"}
DELIVERED = dict(GOAL, basis="delivered")
A = dict(OFFER, shipping_cents=599, tax_cents=0, fees_cents=0)
B = dict(A, seller="other", item_cents=2899, shipping_cents=199)

CASES = [
    ("cheaper_misses", GOAL, [OFFER, dict(OFFER, seller="pickup", item_cents=2000, fulfillment="pickup:a"),
                              dict(OFFER, seller="unavailable", item_cents=100, stock="sold_out"),
                              dict(OFFER, seller="index", item_cents=5, source="shopping_index")]),
    ("delivered_tie", DELIVERED, [A, B]),
    ("delivered_unknown", DELIVERED, [OFFER]),
    ("conflict", GOAL, [OFFER, dict(OFFER, item_cents=12)]),
    ("online_us_accepts_ship", {"basis": "item", "fulfillment": "online_us"}, [OFFER]),
    ("zip_never_fabricated", GOAL, [dict(OFFER, fulfillment="online_us")]),
] + [("change_%d" % i, GOAL, [dict(OFFER, **change)]) for i, change in enumerate(
    ({"gtin": "00000000"}, {"condition": "used"}, {"stock": "orderable"}, {"read_at": 1101},
     {"read_at": 800}, {"currency": "CAD"}, {"unconditional": False}, {"item_cents": True}))] + [
    ("zero_padded_gtin", GOAL, [dict(OFFER, gtin="00" + UPC)]),
    ("missing_gtin", GOAL, [dict(OFFER, gtin=None)]),
    ("other_valid_gtin", GOAL, [dict(OFFER, gtin="4006381333931")]),
    ("unknown_stock", GOAL, [dict(OFFER, stock="unknown")]),
    ("bad_link", GOAL, [dict(OFFER, url="http://shop.example/item")]),
]


def strip(out):
    out = dict(out)
    out["eligible"] = [{k: v for k, v in e.items() if k != "identity_basis"} for e in out["eligible"]]
    return out


class Equivalence(unittest.TestCase):
    def test_identical_to_reference(self):
        for name, goal, offers in CASES:
            with self.subTest(name):
                ref = reference_evaluate(UPC, goal, offers, 1000)
                mine = evaluate({"upid": None, "gtin": UPC}, goal, offers, 1000)
                self.assertEqual(strip(mine), ref)
                for e in mine["eligible"]:
                    self.assertEqual(e["identity_basis"], "merchant_barcode")

    def test_string_target_still_accepted(self):
        self.assertEqual(strip(evaluate(UPC, GOAL, [OFFER], 1000)), reference_evaluate(UPC, GOAL, [OFFER], 1000))

    def test_same_validation_errors(self):
        for target in ("123", "673419404230"):
            with self.assertRaises(ValueError):
                reference_evaluate(target, GOAL, [OFFER], 1000)
            with self.assertRaises(ValueError):
                evaluate({"gtin": target}, GOAL, [OFFER], 1000)
        for goal in ({"basis": "delivered", "fulfillment": "online_us"}, {"basis": "item", "fulfillment": "ship:1"}):
            with self.assertRaises(ValueError):
                evaluate({"gtin": UPC}, goal, [OFFER], 1000)


class Tiers(unittest.TestCase):
    def test_catalog_product_tier_without_gtin(self):
        row = dict(OFFER, gtin=None, source="shop_native", product="gid://shopify/p/1", identity_basis="catalog_product")
        out = evaluate({"upid": "gid://shopify/p/1", "gtin": None}, GOAL, [row], 1000)
        self.assertEqual(out["winners"], ["retail"])
        self.assertEqual(out["eligible"][0]["identity_basis"], "catalog_product")
        out = evaluate({"upid": "gid://shopify/p/2", "gtin": None}, GOAL, [row], 1000)
        self.assertEqual(out["excluded"][0]["reason"], "different_or_unverified_item")

    def test_published_barcode_contradiction_beats_tier(self):
        row = dict(OFFER, gtin="4006381333931", product="gid://shopify/p/1", identity_basis="catalog_product")
        out = evaluate({"upid": "gid://shopify/p/1", "gtin": UPC}, GOAL, [row], 1000)
        self.assertEqual(out["excluded"][0]["reason"], "different_or_unverified_item")
        row = dict(row, identity_basis="model_number")
        out = evaluate({"upid": None, "gtin": UPC}, GOAL, [row], 1000)
        self.assertEqual(out["excluded"][0]["reason"], "different_or_unverified_item")

    def test_model_number_tier(self):
        row = dict(OFFER, gtin=None, identity_basis="model_number")
        out = evaluate({"upid": None, "gtin": None}, GOAL, [row], 1000)
        self.assertEqual(out["eligible"][0]["identity_basis"], "model_number")


if __name__ == "__main__":
    unittest.main()
