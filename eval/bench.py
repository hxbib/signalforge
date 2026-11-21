"""Paired 5 Sigma benchmark. No network except model calls in 'predict'. Never pass gold to a model."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import gzip
import hashlib
import json
from pathlib import Path
import random
import statistics
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"reference"))

def digest(obj):
    raw=json.dumps(obj,sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()

def put(path, rows):
    with Path(path).open("x",encoding="utf8") as f:
        for row in rows:
            f.write(json.dumps(row,ensure_ascii=False,allow_nan=False)+"\n")

def read(path):
    with Path(path).open(encoding="utf8") as f:
        return [json.loads(line) for line in f if line.strip()]

def index(rows):
    result={r["id"]:r for r in rows}
    if not result or len(result)!=len(rows):
        raise ValueError("empty batch or duplicate case IDs")
    return result

def sample(computers, cameras, seed, inputs, gold):
    rng=random.Random(seed)
    selected=[]
    for path in (computers,cameras):
        with gzip.open(path,"rt",encoding="utf8") as f:
            pool=[json.loads(line) for line in f if line.strip()]
        # Gold labels are used only by the sampler and scorer. Select four strata of ten.
        for label in (0,1):
            rows=[r for r in pool if str(r["label"])==str(label)]
            for row in rng.sample(sorted(rows,key=lambda r:str(r["pair_id"])),10):
                packet={}
                for side in ("left","right"):
                    packet[side]={k:row[k+"_"+side] for k in ("title","description","brand","specTableContent")
                                  if row.get(k+"_"+side)}
                    if not packet[side].get("title"):
                        raise ValueError("missing listing title")
                selected.append((packet,"match" if label else "different"))
    rng.shuffle(selected)
    packets, labels=[],[]
    seen=set()
    for i,(data,label) in enumerate(selected):
        key=digest(data)
        if key in seen:
            raise ValueError("duplicate content in draw; record collision, do not silently redraw")
        seen.add(key)
        row={"id":f"pair-{i+1:02d}","task":"pair","packet":data}
        packets.append(row)
        labels.append({"id":row["id"],"sha":digest(row),"expected":label})
    put(inputs,packets)
    put(gold,labels)

def predict_one(row, arm):
    from deal_models import pair_verdict,basket_answer
    start=time.monotonic()
    try:
        if row["task"] == "pair":
            ans=pair_verdict(row["packet"],arm=="full")
        elif row["task"] == "basket":
            ans=basket_answer(row["packet"],arm=="full")
        else:
            raise ValueError("unknown task")
        error=None
    except Exception as exc:
        # No raw request URLs, personal data, or provider keys in reports.
        ans,error=None,type(exc).__name__
    return {"id":row["id"],"sha":digest(row),"answer":ans,"error":error,
            "ms":round((time.monotonic()-start)*1000)}

def predict(rows,arm):
    index(rows)
    with ThreadPoolExecutor(max_workers=3) as pool:
        return list(pool.map(lambda r:predict_one(r,arm),rows))

def compare_basket(gold,answer):
    from compare_offers import same_gtin
    if not isinstance(answer,dict):
        return False
    status=gold["status"]
    if status!=answer.get("status"):
        return False
    predicted=answer.get("winners",[])
    if not isinstance(predicted,list) or len(predicted)!=len(set(predicted)):
        return False
    if status=="winner":
        return (type(answer.get("price_cents")) is int and answer["price_cents"]==gold["price_cents"]
                and set(predicted)==set(gold["winners"]) and same_gtin(gold["gtin"],answer.get("gtin")))
    return status=="abstain" and not predicted and answer.get("price_cents") is None

def score(inputs, gold, baseline, full):
    batches=[index(rows) for rows in (inputs,gold,baseline,full)]
    labels,base,system=batches[1:]
    if any(set(b)!=set(batches[0]) for b in batches[1:]):
        raise ValueError("case sets differ; record errors explicitly")
    by_arm={"baseline":Counter(),"full":Counter()}
    times={"baseline":[],"full":[]}
    paired=Counter()
    for row in inputs:
        key=row["id"]
        if any(b[key]["sha"]!=digest(row) for b in (labels,base,system)):
            raise ValueError("packet digest mismatch")
        task=row["task"]
        expected=labels[key]["expected"]
        success=[]
        for arm,entry in (("baseline",base[key]),("full",system[key])):
            counts=by_arm[arm]
            counts["n"]+=1
            times[arm].append(entry["ms"])
            ans=entry["answer"]
            ok=False
            if entry["error"] or ans is None:
                counts["errors"]+=1
            elif task=="pair":
                ok=ans==expected
                counts["false_matches"]+=int(expected=="different" and ans=="match")
                counts["abstained"]+=int(ans=="abstain")
                counts["answered"]+=int(ans in ("match","different"))
            elif task=="basket":
                ok=compare_basket(expected,ans)
                counts["abstained"]+=int(ans["status"]=="abstain")
                counts["returned_winner"]+=int(ans["status"]=="winner")
                counts["false_in_stock_winners"]+=int(bool(set(ans["winners"]) & set(expected["unavailable_sellers"])))
            else:
                raise ValueError("unknown task")
            counts["correct"]+=int(ok)
            success.append(ok)
        paired[{(True,True):"both",(True,False):"baseline_only",(False,True):"full_only",(False,False):"neither"}[tuple(success)]]+=1
    return {"baseline":dict(by_arm["baseline"]),"full":dict(by_arm["full"]),"paired":dict(paired),
            "median_ms":{k:statistics.median(v) for k,v in times.items()}}

def main():
    cli=argparse.ArgumentParser(description=__doc__)
    subs=cli.add_subparsers(dest="cmd",required=True)
    s=subs.add_parser("sample")
    for key in ("computers","cameras","seed","inputs","gold"):
        s.add_argument("--"+key,required=True)
    p=subs.add_parser("predict")
    p.add_argument("inputs")
    p.add_argument("arm",choices=["baseline","full"])
    p.add_argument("output")
    q=subs.add_parser("score")
    for key in ("inputs","gold","baseline","full"):
        q.add_argument(key)
    args=cli.parse_args()
    if args.cmd=="sample":
        sample(args.computers,args.cameras,args.seed,args.inputs,args.gold)
    elif args.cmd=="predict":
        put(args.output,predict(read(args.inputs),args.arm))
    else:
        print(json.dumps(score(*[read(getattr(args,k)) for k in ("inputs","gold","baseline","full")]),indent=2))

if __name__ == "__main__":
    main()

