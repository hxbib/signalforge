import unittest
from copy import deepcopy
from compare_offers import compare,gtin

UPC="673419404235"


def item(seller,amount):
    return {"seller":{"id":"gid://shopify/Shop/"+seller,"name":seller},"id":"gid://shopify/ProductVariant/"+seller,
            "inputs":[{"id":UPC,"match":"exact"}],"source":"shopify","price":{"amount":amount,"currency":"USD"},
            "availability":{"available":True,"status":"in_stock"},"condition":["new"],"url":"https://example.org/p?ref=keep"}


def result(*items,**extra):
    return compare({"products":[{"variants":list(items)}],**extra},UPC)


class Compare(unittest.TestCase):
    def test_barcode(self):
        self.assertEqual(gtin(UPC),gtin("00"+UPC))
        for code in ["673419404230","00000000","123","１２３４５６７８"]:
            with self.assertRaises(ValueError):gtin(code)

    def test_actual_minimum_and_ties(self):
        out=result(item("c",3600),item("a",2999),item("b",2999))
        self.assertEqual(out["price_cents"],2999)
        self.assertEqual(len(out["winning_sellers"]),2)

    def test_false_bargains(self):
        good=item("A",2900)
        for patch in ({"availability":{"available":False}},{"inputs":[{"id":UPC,"match":"featured"}]},
                      {"condition":["secondhand"]},{"requires":{"selling_plan":True}},{"requires":{"components":True}},
                      {"availability":{"available":True,"status":"preorder"}},{"availability":{"available":True}},
                      {"price":{"amount":15,"currency":"CAD"}},{"price":{"amount":True,"currency":"USD"}},
                      {"source":"external"},{"id":"gid://catalog/ExternalVariant/55","source":None},
                      {"inputs":[{"id":"00000000","match":"exact"}]}):
            other=item("B",1500);other.update(patch)
            self.assertEqual(result(good,other)["price_cents"],2900,patch)

    def test_missing_and_conflicts(self):
        self.assertIsNone(result()["price_cents"])
        self.assertIsNone(result(item("A",4000),item("A",3000))["price_cents"])
        self.assertEqual(result(item("A",4000),item("A",4000))["seller_count_eligible"],1)
        self.assertIsNone(result(item("A",4000),messages=[{"type":"warning","code":"delayed_fulfillment"}])["price_cents"])

    def test_each_store_is_only_one_retailer(self):
        a=item("A",3300);b=deepcopy(a);b["id"]+="-other";b["price"]["amount"]=2999
        out=result(a,b)
        self.assertEqual(out["seller_count_returned"],1)
        self.assertEqual(out["seller_count_eligible"],1)
        self.assertEqual(out["price_cents"],2999)


if __name__=="__main__":
    unittest.main()

class Roster(unittest.TestCase):
    def test_scoped_read_does_not_leak_another_seller(self):
        from unittest.mock import patch
        import time
        from compare_offers import compare_now
        def read(tool,args):
            store=args["filters"]["shops"][0]
            v=item("a",2400);v["seller"]["domain"]="a.example"
            return {"products":[{"variants":[v]}]},{"read_at":int(time.time()),"response_hash":"sample-only"}
        with patch("compare_offers.call",side_effect=read):
            out=compare_now(UPC,["a.example","b.example"])
        self.assertEqual(out["scope_results"][1]["state"],"no_exact_result_yet")
        self.assertEqual(out["seller_count_eligible"],1)

    def test_throttle_discloses_unchecked_stores(self):
        from unittest.mock import patch
        from urllib.error import HTTPError
        from compare_offers import compare_now
        with patch("compare_offers.call",side_effect=HTTPError("https://example.org/",429,"limit",{},None)):
            out=compare_now(UPC,["a.example","b.example"])
        self.assertEqual(out["attempted_scopes"],["a.example"]);self.assertIsNone(out["price_cents"])
