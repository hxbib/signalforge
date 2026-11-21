from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
from merchant_feed import select_anchors,compare_export
from test_final import UPC,OFFER

class Feed(TestCase):
    def test_model_number_candidates_stay_brand_scoped(self):
        visual=SimpleNamespace(barcode="",brand="Mfg",part_number="764")
        anchors=[{"brand":"mfg","mpn":"764","gtin":UPC},{"brand":"other","mpn":"764","gtin":"other"}]
        self.assertEqual(select_anchors(visual,anchors),anchors[:1])

    def test_real_paths_join_with_source_and_location_boundaries(self):
        import json
        feed={"feed_id":"dev","anchors":[{"gtin":UPC}],"offers":[OFFER]}
        shop={"scope_results":[],"read_receipts":[{"read_at":1000}],"excluded":[],"warnings":[],
              "offers":[{"seller":"store","variant":"v","url":"https://store.example/p","cents":1999}]}
        intent=SimpleNamespace(market="US",basis="item",fulfillment="online_us",location="",unsupported=[])
        visual=SimpleNamespace(barcode=UPC)
        with TemporaryDirectory() as tmp, patch("merchant_feed.bind_target",return_value=(UPC,"",{})), patch("merchant_feed.compare_now",return_value=shop), patch("merchant_feed.time.time",return_value=1000):
            f=Path(tmp)/"feed";f.write_text(json.dumps(feed))
            self.assertEqual(compare_export(b"","",visual,{},intent,f)["winners"],["store"])
            intent.fulfillment,intent.location="ship","10001"
            result=compare_export(b"","",visual,{},intent,f)
            self.assertEqual(result["winners"],["retail"])
            self.assertEqual(result["excluded"][0]["reason"],"fulfillment_mismatch")

