"""Extra dependency checks for the reference. All synthetic provider values live in tests only."""
import io
import sqlite3
import time
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.error import HTTPError
from PIL import Image
from pydantic import ValidationError
from core import Rule,Seen
from gemini_io import Proposal,RequestedOption,bound_rule,option_conflict
from live_smoke import clean_image
from managed_challenge import replay_gap_case
from poll import retry_seconds,sample,source_deadline
from proof import alternatives,question,resolve,verify
from test_reference import B,R,O,answer_for
from ledger import Ledger,approval_digest
from shop import Binding


class ModelBoundary(unittest.TestCase):
    def test_request_and_selection_must_agree(self):
        wants=[RequestedOption(name="Size",value="medium",evidence="size medium")]
        self.assertFalse(option_conflict(wants,{"Size":"M"}))
        self.assertTrue(option_conflict(wants,{"Size":"S"}))
        self.assertTrue(option_conflict(wants,{"Color":"Navy"}))

    def test_no_fabricated_slot(self):
        shape=Proposal(reason="ok",status="ready",candidate=8,mode="restock",op="lt",cents=8000,
                       cap_evidence="under $80",binding_evidence="explicit choice",blockers=[],question="")
        with self.assertRaises(ValueError):bound_rule(shape,[])
        for patch_fields in ({"cents":"8000"},{"extra":"not accepted"},{"blockers":["requires shipping"]},{"reason":"missing_budget"}):
            with self.assertRaises(ValidationError):Proposal.model_validate({**shape.model_dump(),**patch_fields})

    def test_media_reencoding(self):
        original=io.BytesIO();Image.new("RGB",(16,10),(10,20,30)).save(original,format="PNG")
        self.assertTrue(clean_image(original.getvalue()).startswith(bytes.fromhex("ffd8ff")))
        with self.assertRaises((ValueError,OSError)):clean_image(b"not an image")

    def test_independent_gap_witness(self):
        case=[{"stock":"in","cents":7999},{"stock":"unknown","cents":7999},{"stock":"in","cents":7999}]
        self.assertTrue(replay_gap_case(R,case)["reproduced"])
        with self.assertRaises(ValueError):replay_gap_case(R,[*case[:2],{"stock":"in","cents":8000}])

    def test_every_supported_boundary_leaf_at_extreme_caps(self):
        for cap in (1,2,97,100,8000,9_999_999,10_000_000):
            r=replace(R,cents=cap)
            def walk(answers):
                family,_,world=resolve(r,answers)
                if world is None:
                    self.assertEqual(len(answers),2)
                    self.assertTrue(all(x["pass"] for x in verify(r,answers,family[0])["rows"]))
                    return {family[0]}
                return walk(answers+[{"id":world.key,"notify":False}]) | walk(answers+[{"id":world.key,"notify":True}])
            self.assertEqual(walk([]),set(alternatives(r)))


class Polling(unittest.TestCase):
    def test_global_limit_persists(self):
        with TemporaryDirectory() as tmp:
            path=str(Path(tmp)/"watch.db")
            db=Ledger(path)
            ans=answer_for(R)
            bundle=approval_digest(B,"cap",verify(R,ans,R))
            for wid in ("a","b"):
                db.activate(wid,R,ans,R,B,"cap",bundle)
            error=HTTPError("https://mcp.shop.com/",429,"rate limit",{"Retry-After":"180"},None)
            config = {**B,"source":"approved_feed","feed_id":"test-feed"}
            with patch("poll.read_export",side_effect=error) as get:
                self.assertEqual(sample(db,"a",config,get),180)
                self.assertGreater(sample(db,"b",config,get),0)
                self.assertEqual(get.call_count,1)
            self.assertEqual(db.db.execute("SELECT count(*) FROM reading").fetchone()[0],2)
            db.close()
            db=Ledger(path)
            self.assertGreater(source_deadline(db.db),int(time.time()))
            db.close()

    def test_retry_date_or_seconds(self):
        self.assertEqual(retry_seconds("180",1),180)
        self.assertEqual(retry_seconds("Thu, 01 Jan 1970 00:05:00 GMT",100),200)
        self.assertEqual(retry_seconds("",0),300)


if __name__=="__main__":unittest.main()
