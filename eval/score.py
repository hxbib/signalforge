"""Score frozen paired predictions. Run only after both arm files are sealed."""
import argparse
from collections import Counter
import json
import statistics
from pathlib import Path

OUTCOMES={"ready","clarify","unsupported"}
FIELDS={"seller","variant","cents","op","mode"}


def rows(path):
    result={}
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        r=json.loads(line)
        if r["id"] in result:
            raise ValueError("duplicate case id")
        result[r["id"]]=r
    if not result:
        raise ValueError("empty batch")
    return result


def check_rule(rule):
    if not isinstance(rule,dict) or set(rule)!=FIELDS:
        raise ValueError("invalid contract")
    if any(not isinstance(rule[k],str) or not rule[k] for k in ("seller","variant")):
        raise ValueError("missing identity")
    if type(rule["cents"]) is not int or not 1<=rule["cents"]<=10_000_000 or rule["op"] not in ("lt","lte") or rule["mode"] not in ("restock","becomes_eligible"):
        raise ValueError("invalid rule parameters")


def validate_case(row, gold):
    outcome=row["outcome"]
    if outcome not in (OUTCOMES if gold else OUTCOMES|{"error"}):
        raise ValueError("invalid outcome")
    if outcome=="ready":
        check_rule(row["rule"])
    elif row.get("rule") is not None or not row.get("reason"):
        raise ValueError("unexplained abstention or error")
    if not isinstance(row.get("packet_sha256"),str) or len(row["packet_sha256"])!=64:
        raise ValueError("missing packet digest")
    int(row["packet_sha256"],16)


def one(labels, predictions):
    if set(labels)!=set(predictions):
        raise ValueError("paired ids differ; record every failure as an error row")
    counts=Counter(dict.fromkeys(("task_correct","ready_on_blocked","wrong_ready","errors","tp","fp","fn","tn","extra_alerts","event_slots","clarifications"),0))
    detail={}
    ms=[]
    calls=[]
    for key,gold in sorted(labels.items()):
        pred=predictions[key]
        validate_case(gold,True)
        validate_case(pred,False)
        if gold["packet_sha256"]!=pred["packet_sha256"]:
            raise ValueError("different task packet for "+key)
        n=gold["steps"]
        if type(n) is not int or n<0:
            raise ValueError("invalid event horizon")
        if any(type(i) is not int or not 0<=i<n for i in gold["alerts"]+pred["alerts"]):
            raise ValueError("invalid event position")
        if len(set(gold["alerts"]))!=len(gold["alerts"]):
            raise ValueError("duplicate gold label")
        if (gold["outcome"]!="ready" and gold["alerts"]) or (pred["outcome"]!="ready" and pred["alerts"]):
            raise ValueError("alert on non-executable task")
        for k in ("ms","calls","clarifications"):
            if type(pred[k]) is not int or pred[k]<0:
                raise ValueError("missing run measurement")
        same=(gold["outcome"]==pred["outcome"] and
              (gold["rule"]==pred["rule"] if gold["outcome"]=="ready" else gold["reason"]==pred["reason"]))
        extra=len(pred["alerts"])-len(set(pred["alerts"]))
        want,got=set(gold["alerts"]),set(pred["alerts"])
        behavior=(gold["outcome"]==pred["outcome"] and want==got and extra==0)
        success=same and behavior
        counts["task_correct"]+=success
        counts["ready_on_blocked"]+=gold["outcome"]!="ready" and pred["outcome"]=="ready"
        counts["wrong_ready"]+=pred["outcome"]=="ready" and not same
        counts["errors"]+=pred["outcome"]=="error"
        counts["extra_alerts"]+=extra
        counts["event_slots"]+=n
        counts["tp"]+=len(want&got)
        counts["fp"]+=len(got-want)+extra
        counts["fn"]+=len(want-got)
        counts["tn"]+=n-len(want|got)
        counts["clarifications"]+=pred["clarifications"]
        ms.append(pred["ms"]);calls.append(pred["calls"])
        detail[key]={"correct":success,"contract_or_abstention_correct":same,"behavior_correct":behavior,
                     "expected":gold["outcome"],"actual":pred["outcome"],"missed_steps":sorted(want-got),
                     "unexpected_steps":sorted(got-want),"duplicates":extra}
    return {**counts,"n":len(labels),"total_calls":sum(calls),"median_ms":statistics.median(ms),"per_case":detail}


def compare(labels,base,full):
    b,f=one(labels,base),one(labels,full)
    pairs=Counter(dict.fromkeys(("both","baseline_only","full_only","neither"),0))
    for k in labels:
        x,y=b["per_case"][k]["correct"],f["per_case"][k]["correct"]
        pairs["both" if x and y else "baseline_only" if x else "full_only" if y else "neither"]+=1
    return {"baseline":b,"full":f,"paired":dict(pairs),"delta_tasks":f["task_correct"]-b["task_correct"]}


if __name__=="__main__":
    p=argparse.ArgumentParser()
    for k in ("gold","baseline","full"):
        p.add_argument(k)
    args=p.parse_args()
    print(json.dumps(compare(rows(args.gold),rows(args.baseline),rows(args.full)),indent=2))
