"""Offline teaching example. Prints counterfactuals, never a live source or model result."""
from dataclasses import asdict
import argparse
import json
from core import Rule, describe
from proof import resolve, verify


if __name__ == "__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--cents",type=int,default=8000)
    args=p.parse_args()
    draft=Rule("teaching-example","sample-jacket-M","restock","lt",args.cents)
    answers=[]
    while True:
        family,witnesses,world=resolve(draft,answers)
        if world is None:
            break
        def label(row):
            return f"{'in stock' if row.available else 'out of stock'} at USD {row.cents/100:.2f}"
        print(f"Hypothetical: {label(world.before)} → {label(world.after)}.")
        reply=input("Would you want an alert? [y/n/stop] ").strip().lower()
        if reply not in ("y","n"):
            raise SystemExit("No watch approved.")
        answers.append({"id":world.key,"notify":reply=="y"})
    proof=verify(draft,answers,family[0])
    print(json.dumps({"meaning":describe(family[0]),"rule":asdict(family[0]),"proof_hash":proof["proof_hash"],
                      "accepted_rows":len(proof["rows"]),"source":"counterfactual example"},indent=2))
