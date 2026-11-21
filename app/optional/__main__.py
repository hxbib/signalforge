"""python -m app.optional demo [--hand-draft MODE:OP:CENTS] [--command "<buyer words>"]

Builds a watch from an explicit hand-built rule (no model call for the rule), runs ONE real hosted challenge and
prints the ids, the agent-reported count, the returned timeline and the local replay verdict."""
import argparse
import json
import os
import sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["demo"])
    ap.add_argument("--hand-draft", default="restock:lt:2500")
    ap.add_argument("--command", default="Notify me when this seller restocks under $25")
    args = ap.parse_args()
    os.environ["SF_DB"] = ":memory:"  # a hand-built draft is never persisted
    import app.watch as watch
    from app.watch.__main__ import _fixture, _hand
    import app.optional as optional
    run, image = _fixture()
    doc = watch.create(run, args.command, compile=_hand(args.hand_draft), image=image)
    if not doc.get("rule"):
        sys.exit("the hand-built draft did not produce a rule: " + json.dumps({k: doc.get(k) for k in ("status", "question")}))
    print(json.dumps({"watch_rule": doc["rule"], "rule_origin": "hand-built draft (no model call)", "command": doc["command"]}))
    out = optional.challenge(doc)
    print(json.dumps(out, indent=2))
    print(f"environment_id={out['environment_id']} interaction_id={out['interaction_id']} "
          f"timelines_tested(agent-reported)={out['attempts']} timeline={json.dumps(out['timeline'])} "
          f"local_replay_verdict={out['status']} replayed_locally={out['replayed_locally']} ms={out['ms']}")


if __name__ == "__main__":
    main()
