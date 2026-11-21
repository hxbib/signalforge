import unittest
from unittest.mock import patch
from major_retailers import bestbuy_rows,sellers
from test_compare_offers import UPC

class Retailers(unittest.TestCase):
    def test_online_buyability_qualified(self):
        row={"upc":UPC,"salePrice":"29.99","onlineAvailability":True,"preowned":False,"active":True}
        out=bestbuy_rows({"products":[row]},UPC)
        self.assertEqual(out[0]["status"],"available_to_order_online")
        self.assertEqual(out[0]["item_price_cents"],2999)
        self.assertFalse(bestbuy_rows({"products":[dict(row,upc="111111111111")]},UPC))
        for change in ({"priceRestriction":"MAP"},{"preowned":True},{"specialOrder":True}):
            self.assertIsNone(bestbuy_rows({"products":[dict(row,**change)]},UPC)[0]["item_price_cents"])

    def test_index_is_always_candidate(self):
        sample={"name":"Target","price":"$24.99","details_and_offers":["In stock"],"tag":"Best price"}
        with patch("major_retailers.serp",return_value={"product_results":{"stores":[sample]}}):
            self.assertEqual(sellers("test")[0]["status"],"candidate")

class Markup(unittest.TestCase):
    def test_exact_product_offer_only(self):
        import json
        from merchant_markup import extract
        offer={"@type":"Offer","price":"19.95","priceCurrency":"USD","availability":"https://schema.org/InStock",
               "seller":{"name":"shop"},"itemCondition":"https://schema.org/NewCondition"}
        def check(offers):
            data={"@graph":[{"@type":"Product","gtin12":UPC,"offers":offers}]}
            html='<script type="application/ld+json">'+json.dumps(data)+'</script>'
            return extract(html,"https://example.org/exact",UPC)
        self.assertEqual(check(offer)[0]["item_price_cents"],1995)
        self.assertTrue(check(offer)[0]["in_stock"])
        self.assertFalse(check(dict(offer,availability="PreOrder"))[0]["in_stock"])
        self.assertEqual(check(dict(offer,**{"@type":"AggregateOffer"})),[])
        self.assertEqual(check(dict(offer,url="https://other.example/u")),[])

class Unified(unittest.TestCase):
    def test_storefront_and_major_retailer_share_floor(self):
        from dataclasses import replace
        from rank_verified import Quote,rank
        a=Quote("shop","s",UPC,2800,"USD","new","in_stock","ship:10001","shop_native",100,"https://a.example/u")
        b=replace(a,seller="large",listing="b",source="merchant_page",cents=2500)
        rejected=[replace(a,seller="nearby",fulfillment="pickup:42",cents=2000),
                  replace(b,seller="old",read_at=0,cents=1000),replace(b,seller="offer",stock="orderable",cents=900),
                  replace(b,seller="index",source="shopping_index",cents=750)]
        floor,winners,accepted,excluded=rank(UPC,[a,b,*rejected],"ship:10001",160)
        self.assertEqual((floor,[q.seller for q in winners],len(accepted),len(excluded)),(2500,["large"],2,4))
        self.assertEqual(rank(UPC,[b,replace(b,cents=2600)],"ship:10001",160)[0],None)
