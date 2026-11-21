"""Optional venue-only hosted verifier. Never trust a sandbox's prose or accept arbitrary returned code."""
import json
from core import Rule,Seen,decide,integer,rule_hash
from gemini_io import clients


def replay_gap_case(rule, proposed):
    # Verifies a counterexample to a named bad implementation: missing data interpreted as zero stock.
    if type(proposed) is not list or len(proposed)!=3:
        raise ValueError("expected three ticks")
    states=[]
    for i,item in enumerate(proposed):
        if not isinstance(item,dict) or set(item)!={"stock","cents"} or not integer(item["cents"],0,10_000_001):
            raise ValueError("invalid case")
        stock=item["stock"]
        if stock not in ("in","out","unknown"):
            raise ValueError("invalid stock")
        states.append(Seen(rule.seller,rule.variant,None if stock=="unknown" else stock=="in",
                           item["cents"],"USD",100+i*60,"missing" if stock=="unknown" else "ok"))
    if [s.status for s in states]!=["ok","missing","ok"] or not states[-1].available:
        raise ValueError("case does not exercise the proposed missing-data distinction")
    real=decide(rule,states[1],states[2])
    wrong_previous=Seen(rule.seller,rule.variant,False,states[1].cents,"USD",states[1].t)
    wrong=decide(rule,wrong_previous,states[2])
    if real.alert or not wrong.alert:
        raise ValueError("no distinguishing outcome")
    return {"rule_hash":rule_hash(rule),"ticks":proposed,"actual_reason":real.reason,
            "incorrect_implementation_would_alert":wrong.alert,"reproduced":True}


def hosted(rule):
    _,beta=clients()
    job={"op":rule.op,"cap_in_us_cents":rule.cents,"mode":rule.mode,
         "scenario":"a missed catalog reading in the middle of three 60-second ticks"}
    instructions=("Execute a Python enumeration inside the sandbox to find a 3-tick commerce sequence "
        "where treating unobserved stock as sold out causes a false alert. The honest evaluator breaks "
        "continuity. Use only the anonymized parameters provided. Find a qualifying final price and "
        "return only the witness array from executed stdout, each row with stock (in/out/unknown) "
        "and integer cents. No markdown. Do not access network, private files or other systems. "
        +"Parameters: "+json.dumps(job))
    response=beta.interactions.create(agent="antigravity-preview-09-2026",environment="remote",
        agent_config={"type":"antigravity","model":"gemini-4-hydrogen","max_total_tokens":12000},input=instructions)
    proof=replay_gap_case(rule,json.loads(response.output_text))
    step_types=[getattr(s,"type",None) for s in (getattr(response,"steps",None) or [])]
    # A successful replay alone cannot establish that the remote agent executed the program.
    return {"interaction_id":response.id,"environment_id":response.environment_id,"step_types":step_types,
            "local_replay":proof,"remote_execution":"inspect actual SDK tool step or environment log before claiming"}


if __name__=="__main__":
    import argparse
    p=argparse.ArgumentParser()
    p.add_argument("approved_rule_json",help="path to a sanitized approved rule export with the five core fields")
    args=p.parse_args()
    with open(args.approved_rule_json) as f: rule=Rule(**json.load(f))
    print(json.dumps(hosted(rule),indent=2))
