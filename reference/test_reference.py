import json
import os
from pathlib import Path
import sqlite3
import sys
from dataclasses import asdict, replace
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from core import Rule, Seen, decide, rule_hash
from proof import alternatives, predicts, question, resolve, verify, safety_checks
from ledger import Ledger, approval_digest
from shop import exact, SourceError
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"eval"))
from score import compare

R=Rule("costume-shop","v-m","restock","lt",8000)
B={"product":"p","seller":R.seller,"variant":R.variant,"choices":[["Size","M"]],"url":"https://example.org/m"}
O=Seen(R.seller,R.variant,False,7999,"USD",1000)
Y=replace(O,available=True,t=1060)


def answer_for(candidate):
    # Independent preference oracle for the candidate. Deliberately avoid calling production decide.
    out=[]
    while True:
        _,_,w=resolve(R,out)
        if w is None:
            return out
        old,new=w.before,w.after
        limit=candidate.cents-(candidate.op=="lt")
        old_ok=old.available and old.cents<=limit
        new_ok=new.available and new.cents<=limit
        expected=new_ok and (not old.available if candidate.mode=="restock" else not old_ok)
        out.append({"id":w.key,"notify":expected})


class Domain(unittest.TestCase):
    def test_stock_vs_price(self):
        before=replace(O,available=True,cents=8001)
        self.assertFalse(decide(R,before,Y).alert)
        self.assertTrue(decide(replace(R,mode="becomes_eligible"),before,Y).alert)

    def test_exact_boundary(self):
        for mode in ("restock","becomes_eligible"):
            self.assertFalse(decide(replace(R,mode=mode),O,replace(Y,cents=8000)).alert)
            self.assertTrue(decide(replace(R,mode=mode,op="lte"),O,replace(Y,cents=8000)).alert)

    def test_gaps_and_unknowns(self):
        for before in (None,replace(O,currency="EUR"),replace(O,status="timeout"),replace(O,t=100),replace(O,t=1061)):
            self.assertFalse(decide(R,before,Y).alert)
        self.assertTrue(decide(R,replace(O,t=910),Y).alert)

    def test_invalid_values_fail_closed(self):
        for value in (True,"7999",-1,80.0,None,2**53):
            self.assertFalse(decide(R,O,replace(Y,cents=value)).alert)
        for value in (False,-1,"8000",0,10_000_001):
            with self.assertRaises(ValueError): replace(R,cents=value)

    def test_own_binding(self):
        for key in ("seller","variant"):
            self.assertFalse(decide(R,O,replace(Y,**{key:"other"})).alert)

    def test_four_distinct_policies(self):
        for candidate in alternatives(R):
            answers=answer_for(candidate)
            family,_,w=resolve(R,answers)
            self.assertEqual(family,[candidate])
            self.assertIsNone(w)
            self.assertEqual(len(answers),2)
            self.assertEqual(len(verify(R,answers,candidate)["rows"]),14)

    def test_safety_suite_is_executed(self):
        for case in safety_checks(R):
            self.assertTrue(case["pass"],case)

    def test_wrong_candidate_blocks(self):
        with self.assertRaises(ValueError): verify(R,answer_for(R),replace(R,op="lte"))
        with self.assertRaises(ValueError): verify(R,[],R)

    def test_stale_answer_blocks(self):
        ans=answer_for(R)
        ans[0]["id"]="changed"
        with self.assertRaises(ValueError): resolve(R,ans)

    def test_fingerprint_changes(self):
        self.assertNotEqual(rule_hash(R),rule_hash(replace(R,cents=8200)))
        self.assertNotEqual(rule_hash(R),rule_hash(replace(R,mode="becomes_eligible")))


class Storage(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory()
        self.path=os.path.join(self.tmp.name,"watch.db")
        self.store=Ledger(self.path)
        self.answers=answer_for(R)
        self.proof=verify(R,self.answers,R)
        self.bundle=approval_digest(B,"capturehash",self.proof)
        self.activate()

    def activate(self):
        return self.store.activate("w",R,self.answers,R,B,"capturehash",self.bundle)

    def tearDown(self):
        self.store.close();self.tmp.cleanup()

    def test_restart_replay_and_evidence_pair(self):
        self.store.ingest("w","a",O,{"origin":"test"})
        self.store.ingest("w","b",Y,{"origin":"test"})
        self.store.close();self.store=Ledger(self.path)
        self.assertEqual(self.store.ingest("w","b",Y,{"origin":"test"}),"duplicate")
        self.assertEqual(self.store.db.execute("SELECT * FROM alert").fetchall(),[("w","b","a")])

    def test_duplicate_attempt_conflict(self):
        self.store.ingest("w","a",O,{})
        with self.assertRaises(ValueError): self.store.ingest("w","a",Y,{})

    def test_atomic_rollback(self):
        self.store.ingest("w","a",O,{})
        self.store.db.execute("CREATE TRIGGER fail BEFORE INSERT ON alert BEGIN SELECT RAISE(ABORT,'test'); END;")
        with self.assertRaises(sqlite3.IntegrityError): self.store.ingest("w","b",Y,{})
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM reading").fetchone()[0],1)
        self.store.db.execute("DROP TRIGGER fail")
        self.assertEqual(self.store.ingest("w","b",Y,{}),"stock_returned")

    def test_late_duplicate_does_not_rearm(self):
        self.store.ingest("w","a",O,{})
        self.store.ingest("w","b",Y,{})
        self.assertEqual(self.store.ingest("w","late",replace(O,t=1010),{}),"out_of_order")
        self.assertEqual(self.store.ingest("w","current",replace(Y,t=1120),{}),"already_in_stock")

    def test_rearm_after_new_real_outage_and_return(self):
        for attempt,row in [("a",O),("b",Y),("c",replace(O,t=1120)),("d",replace(Y,t=1180))]:
            self.store.ingest("w",attempt,row,{})
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM alert").fetchone()[0],2)

    def test_error_breaks_continuity(self):
        for attempt,row in [("a",O),("b",replace(O,status="error",t=1030)),("c",Y)]:
            self.store.ingest("w",attempt,row,{})
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM alert").fetchone()[0],0)

    def test_approval_and_pause(self):
        self.assertEqual(self.activate(),self.bundle)
        with self.assertRaises(ValueError):
            self.store.activate("w2",R,self.answers,R,B,"changed",self.bundle)
        self.store.pause("w")
        with self.assertRaises(ValueError): self.activate()
        with self.assertRaises(ValueError): self.store.ingest("w","a",O,{})


def catalog(selected="M",on_variant="M"):
    return {"product":{"id":"p","selected":[{"name":"Size","label":selected}],"variants":[
        {"id":"v-m","options":[{"name":"Size","label":on_variant}],"seller":{"domain":"costume-shop"},
         "url":"https://example.org/m?a=1","availability":{"available":True},"price":{"currency":"USD","amount":7999}}]}}


class Adapter(unittest.TestCase):
    def setUp(self): self.request=[{"name":"Size","label":"M"}]

    def test_selected_and_variant_checked(self):
        for doc in [catalog("S"),catalog(on_variant="S")]:
            with self.assertRaises(SourceError): exact(doc,"p",self.request,100)

    def test_unique_and_typed_stock(self):
        doc=catalog()
        bound,row=exact(doc,"p",self.request,100)
        self.assertEqual(bound.url,"https://example.org/m?a=1")
        self.assertEqual(row.cents,7999)
        doc["product"]["variants"]*=2
        with self.assertRaises(SourceError): exact(doc,"p",self.request,100)
        for field,value in [("price",{"currency":"CAD","amount":7999}),("availability",{"available":"true"})]:
            doc=catalog();doc["product"]["variants"][0][field]=value
            with self.assertRaises(SourceError): exact(doc,"p",self.request,100)

    def test_seller_drift(self):
        bound,_=exact(catalog(),"p",self.request,100)
        doc=catalog();doc["product"]["variants"][0]["seller"]["domain"]="elsewhere"
        with self.assertRaises(SourceError): exact(doc,"p",self.request,101,bound)


class Scoring(unittest.TestCase):
    def test_paired_counts_and_error_denominator(self):
        common={"id":"one","packet_sha256":"a"*64,"rule":asdict(R),"outcome":"ready","alerts":[1],"reason":"ok"}
        g={"one":{**common,"steps":3}}
        p={"one":{**common,"ms":10,"calls":1,"clarifications":0}}
        q={"one":{**p["one"],"alerts":[1,1]}}
        report=compare(g,p,q)
        self.assertEqual(report["paired"]["baseline_only"],1)
        self.assertEqual(report["full"]["extra_alerts"],1)
        self.assertEqual(report["full"]["fp"],1)
        q["one"].update(outcome="error",reason="Timeout",rule=None,alerts=[])
        self.assertEqual(compare(g,p,q)["full"]["fn"],1)
        with self.assertRaises(ValueError): compare(g,p,{})
        q["one"]["packet_sha256"]="b"*64
        with self.assertRaises(ValueError): compare(g,p,q)


if __name__=="__main__": unittest.main()
