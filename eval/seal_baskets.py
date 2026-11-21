"""Seal separately adjudicated five-case shopping inputs; keep raw inputs and gold outside the public repo."""
import argparse
import base64
from pathlib import Path
from bench import digest, put, read
from compare_offers import gtin

def seal(spec_path, inputs_path, gold_path):
    rows=read(spec_path)
    if len(rows)!=5 or len({r["id"] for r in rows})!=5:
        raise ValueError("provide the five preregistered cases")
    packets, labels=[],[]
    for r in rows:
        if not r.get("source_rights") or not r.get("gold_evidence"):
            raise ValueError("record source permission and label evidence")
        raw=(Path(spec_path).parent/r["jpeg"]).read_bytes()
        if not raw.startswith(b"\xff\xd8\xff") or not 0<len(raw)<=2_500_000:
            raise ValueError("preprocess the source image with clean_image first")
        p={k:r[k] for k in ("note","goal","as_of","anchors","offers")}
        p["jpeg_b64"]=base64.b64encode(raw).decode("ascii")
        expected=r["expected"]
        if expected["status"]=="winner":
            gtin(expected["gtin"])
            if not expected["winners"] or type(expected["price_cents"]) is not int or expected["price_cents"]<0:
                raise ValueError("invalid winner gold")
        elif expected["status"]!="abstain" or expected["winners"] or expected["price_cents"] is not None:
            raise ValueError("invalid abstention gold")
        if not isinstance(expected["unavailable_sellers"],list) or set(expected["unavailable_sellers"]) & set(expected["winners"]):
            raise ValueError("invalid unavailable seller labels")
        pred={"id":r["id"], "task":"basket","packet":p}
        packets.append(pred)
        labels.append({"id":r["id"],"sha":digest(pred),"expected":expected})
    put(inputs_path,packets)
    put(gold_path,labels)

if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("case_descriptions")
    p.add_argument("inputs")
    p.add_argument("gold")
    args=p.parse_args()
    seal(args.case_descriptions,args.inputs,args.gold)

