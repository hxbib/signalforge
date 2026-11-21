"""Synthetic development cases only. They produce no competition accuracy or live-inventory claims."""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
import gzip
import json
import sys
from autowatch import advance
from buyer import evaluate
from core import Rule
from deal_models import Visual, Selection, bind_target
from poll import read_export
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"eval"))
from bench import sample, score, digest, read
from seal_baskets import seal

UPC="673419404235"
GOAL={"basis":"item","fulfillment":"ship:10001"}
OFFER={"seller":"retail","listing":"sku-1","gtin":UPC,"source":"merchant_page","url":"https://shop.example/item",
       "read_at":1000,"currency":"USD","item_cents":2499,"condition":"new","stock":"in_stock",
       "unconditional":True,"fulfillment":"ship:10001"}
RULE=Rule("retail","sku-1","restock","lt",2500)

class ProductTests(TestCase):
    def test_cheaper_misses(self):
        quotes=[OFFER,dict(OFFER,seller="pickup",item_cents=2000,fulfillment="pickup:a"),
                dict(OFFER,seller="unavailable",item_cents=100,stock="sold_out"),
                dict(OFFER,seller="index",item_cents=5,source="shopping_index")]
        r=evaluate(UPC,GOAL,quotes,1000)
        self.assertEqual(r["winners"],["retail"])
        self.assertEqual({x["reason"] for x in r["excluded"]},{"fulfillment_mismatch","sold_out","discovery_only"})

    def test_total_and_ties(self):
        delivered=dict(GOAL,basis="delivered")
        a=dict(OFFER,shipping_cents=599,tax_cents=0,fees_cents=0)
        b=dict(a,seller="other",item_cents=2899,shipping_cents=199)
        r=evaluate(UPC,delivered,[a,b],1000)
        self.assertEqual((r["winners"],r["price_cents"]),(["other","retail"],3098))
        r=evaluate(UPC,delivered,[OFFER],1000)
        self.assertEqual((r["winners"],r["excluded"][0]["reason"]),([],"total_unknown"))

    def test_identity_conflicts_and_age(self):
        for change in ({"gtin":"00000000"},{"condition":"used"},{"stock":"orderable"},{"read_at":1101},
                       {"read_at":800},{"currency":"CAD"},{"unconditional":False},{"item_cents":True}):
            self.assertEqual(evaluate(UPC,GOAL,[dict(OFFER,**change)],1000)["status"],"abstain")
        r=evaluate(UPC,GOAL,[OFFER,dict(OFFER,item_cents=12)],1000)
        self.assertEqual(r["excluded"][0]["reason"],"conflicting_sources")

    def test_missing_zip_never_fabricated(self):
        self.assertTrue(evaluate(UPC,{"basis":"item","fulfillment":"online_us"},[OFFER],1000)["winners"])
        r=evaluate(UPC,GOAL,[dict(OFFER,fulfillment="online_us")],1000)
        self.assertFalse(r["winners"])

    def test_explicit_watch_completes_without_buyer_answers(self):
        for note in ("Notify me when it restocks under $25.","Notify me when it drops below $25."):
            result=advance(RULE,note,{})
            self.assertTrue(result["ready"])
            self.assertTrue(all(x["origin"]=="explicit_request" for x in result["origins"]))
            self.assertTrue(all(x["pass"] for x in result["proof"]["rows"]))
        self.assertEqual(advance(RULE,"Notify me when it drops below $25.",{})["rule"].mode,"becomes_eligible")

    def test_ambiguous_watch_requests_only_material_answer(self):
        note="Notify me when this is under $25."
        partial=advance(RULE,note,{})
        self.assertFalse(partial["ready"])
        actual=advance(RULE,note,{partial["question"].key:True})
        self.assertTrue(actual["ready"])
        self.assertEqual(sum(o["origin"]=="buyer" for o in actual["origins"]),1)

    def test_cap_or_negative_instruction_is_not_silently_overridden(self):
        for note in ("Notify me when it restocks under $20.","Don't notify me when it restocks under $25."):
            self.assertFalse(advance(RULE,note,{})["ready"])

    def test_decision_ambiguity_and_independent_decoder(self):
        visual=Visual(name="product",barcode=UPC,brand="",part_number="",variant_details=[],query="product",
                      target_count="one",evidence=["barcode"])
        d={"choice":"multiple","pass":False}
        args=(b"x","",[{"gtin":UPC}],visual,d)
        with patch("deal_models.clients",return_value=(None,None)), patch("deal_models.structured",
                return_value=(Selection(action="choose",slot=0,reason="seen",question=""),{})):
            self.assertIsNone(bind_target(*args)[0])
            self.assertEqual(bind_target(*args,verified_scan=UPC)[0],UPC.zfill(14))
            self.assertIsNone(bind_target(*args,verified_scan="11111111")[0])
            self.assertIsNone(bind_target(b"x","",[{"gtin":UPC}],visual.model_copy(update={"target_count":"many"}),d,UPC)[0])

    def test_export_rejects_source_mismatch_or_stale(self):
        with TemporaryDirectory() as tmp:
            p=Path(tmp)/"feed"
            p.write_text(json.dumps({"feed_id":"f","observations":[{"seller":"s","variant":"v","available":True,
                "cents":10,"currency":"USD","t":1000}]}))
            binding={"source":"approved_feed","feed_id":"f","seller":"s","variant":"v"}
            self.assertEqual(read_export(p,binding,1000)[0].cents,10)
            for cfg,now in ((dict(binding,feed_id="other"),1000),(binding,1160)):
                with self.assertRaises(ValueError):
                    read_export(p,cfg,now)

class EvidenceTests(TestCase):
    def test_four_strata_no_label_leak_and_reproducible(self):
        with TemporaryDirectory() as tmp:
            p=Path(tmp)
            paths=[]
            for category in ("cam","cpu"):
                path=p/(category+".gz")
                paths.append(path)
                with gzip.open(path,"wt") as f:
                    for n in range(24):
                        f.write(json.dumps({"pair_id":f"{category}{n}","label":n%2,"cluster_id":2,
                              "title_left":f"{category} item {n}","title_right":"other"})+"\n")
            sample(*paths,"freeze",p/"inputs",p/"gold")
            sample(*paths,"freeze",p/"inputs2",p/"gold2")
            packets=read(p/"inputs");labels=read(p/"gold")
            self.assertEqual((len(packets),sum(l["expected"]=="match" for l in labels)),(40,20))
            self.assertEqual((p/"inputs").read_bytes(),(p/"inputs2").read_bytes())
            self.assertEqual(set(packets[0]["packet"]["left"]),{"title"})
            self.assertNotIn("label",packets[0]["packet"])

    def test_scoring_counts_errors_and_rejects_changed_packet(self):
        row={"id":"one","task":"pair","packet":{"left":{},"right":{}}}
        gold={"id":"one","sha":digest(row),"expected":"different"}
        pred={"id":"one","sha":digest(row),"ms":10,"answer":"match","error":None}
        failed=dict(pred,error="Timeout",answer=None)
        report=score([row],[gold],[pred],[failed])
        self.assertEqual(report["baseline"]["false_matches"],1)
        self.assertEqual((report["full"]["errors"],report["full"]["n"]),(1,1))
        with self.assertRaises(ValueError):
            score([row],[gold],[pred],[dict(pred,sha="changed")])

    def test_five_basket_sealing_and_tie_scoring(self):
        with TemporaryDirectory() as tmp:
            from PIL import Image
            p=Path(tmp)
            Image.new("RGB",(3,3)).save(p/"local.jpg")
            rows=[{"id":str(n),"jpeg":"local.jpg","note":"","goal":GOAL,"as_of":1000,"anchors":[],"offers":[],
                   "source_rights":"test fixture","gold_evidence":"synthetic","expected":{"status":"winner",
                   "winners":["a","b"],"price_cents":10,"gtin":UPC,"unavailable_sellers":["c"]}} for n in range(5)]
            (p/"spec").write_text("".join(json.dumps(r)+"\n" for r in rows))
            seal(p/"spec",p/"in",p/"gold")
            packets,labels=read(p/"in"),read(p/"gold")
            self.assertFalse(set(packets[0]["packet"])&{"expected","gold_evidence","unavailable_sellers"})
            preds=[{"id":r["id"],"sha":digest(r),"error":None,"ms":5,"answer":{k:rows[0]["expected"][k]
                    for k in ("status","winners","price_cents","gtin")}} for r in packets]
            bad=[dict(e,answer=dict(e["answer"],winners=["c"])) for e in preds]
            result=score(packets,labels,preds,bad)
            self.assertEqual((result["baseline"]["correct"],result["full"]["false_in_stock_winners"]),(5,5))

