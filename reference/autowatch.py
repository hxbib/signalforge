"""A fully explicit buyer request can pass the same verifier without answering generated questions."""
import re
from proof import alternatives, predicts, resolve, verify

# Narrow, auditable phrases. Models may translate into a suggestion but never manufacture evidence.
RESTOCK = re.compile(r"\b(?:restocks?|back in stock|returns? to stock|comes? back in stock)\b", re.I)
ELIGIBLE = re.compile(r"\b(?:becomes? eligible|first qualifies?|first becomes? available under|price drops? (?:under|below|to)|drops? below|first goes below)\b", re.I)
CAP = re.compile(r"\b(under|below|at most|up to|at or below|no more than)\s+(?:USD\s*)?\$?(\d+(?:\.\d{1,2})?)(?![\w.,]\d|[\w])",re.I)
NEGATION = re.compile(r"\b(?:not|never|except|unless|instead|either|or if|don't|do not)\b",re.I)


def explicit_family(note, draft):
    from decimal import Decimal
    limits = list(CAP.finditer(note))
    if NEGATION.search(note) or len(limits) != 1:
        return alternatives(draft)
    cap = limits[0]
    if int(Decimal(cap.group(2)) * 100) != draft.cents:
        return alternatives(draft)
    bound = "lt" if cap.group(1).lower() in ("under", "below") else "lte"
    modes = [m for m, rx in (("restock", RESTOCK), ("becomes_eligible", ELIGIBLE)) if rx.search(note)]
    return [r for r in alternatives(draft) if r.op == bound and (len(modes) != 1 or r.mode == modes[0])]


def advance(draft, note, replies):
    """replies maps witness ID to a real buyer boolean. Generated labels have distinct provenance."""
    from decimal import Decimal
    limits = list(CAP.finditer(note))
    if len(limits) != 1 or int(Decimal(limits[0].group(2)) * 100) != draft.cents or NEGATION.search(note):
        return {"ready":False,"needs":"clarify_cap_or_condition","origins":[]}
    candidates = explicit_family(note, draft)
    answers, origins = [], []
    while True:
        family, _, witness = resolve(draft, answers)
        if witness is None:
            if set(replies) != {x["id"] for x in origins if x["origin"] == "buyer"}:
                raise ValueError("stale witness reply")
            selected = family[0]
            return {"ready":True,"rule":selected,"proof":verify(draft, answers, selected),
                    "answers":answers,"origins":origins}
        choices = {predicts(rule, witness) for rule in candidates}
        if len(choices) == 1:
            notify, origin = next(iter(choices)), "explicit_request"
        elif witness.key in replies and type(replies[witness.key]) is bool:
            notify, origin = replies[witness.key], "buyer"
        else:
            return {"ready":False,"question":witness,"origins":origins}
        answers.append({"id":witness.key,"notify":notify})
        origins.append({"id":witness.key,"origin":origin})
        candidates = [r for r in candidates if predicts(r, witness) == notify]

