"""Model jobs facade (docs/CONTRACT.md section 3): dict in / dict out, real calls only.

Gemini 4 Neon reads the photo, Gemini Decision 1 gates the target, Gemini 4 Carbon reads the request and
selects an identity. Deterministic code decides whether a selection is corroborated; a model's confidence
never binds a product on its own.
"""
import app as _app  # noqa: F401  (puts reference/ on sys.path)

import io as _io
import re
from concurrent.futures import ThreadPoolExecutor

from app.models.io import ModelCallError, THRESHOLD, gate, parts, structured, text_part, yes_no
from gemini_io import CARBON, NEON

__all__ = ["ModelCallError", "PAIR_VERSION", "inspect", "parse_goal", "bind", "pair", "pair_v1", "screen", "scan", "compile_watch"]

MAX_ANCHORS = 8
MAX_SCREEN_LISTINGS = 60
PAIR_VERSION = "v2-carbon-verified-quotes"
PAIR_V1_VERSION = "v1-neon-extract-carbon-verdict"


PRINTED_ONLY = """
part_number and barcode hold ONLY characters you can literally see printed in this image. Never fill them from
recognising the product or from memory: a bare item with no printed model or set number has an empty part_number.
Whenever part_number or barcode is not empty, one evidence entry must quote the printed text that contains it."""


def _look(raw, note):
    from deal_models import VIEW, Visual
    VIEW = VIEW + PRINTED_ONLY
    data = parts(raw, note)
    with ThreadPoolExecutor(max_workers=2) as pool:
        gate_job = pool.submit(gate, data)
        view_job = pool.submit(structured, "view", NEON, VIEW, Visual, data)
        visual, trace = view_job.result()
        decision = gate_job.result()
    return visual, decision, trace


def inspect(image: bytes, note: str) -> dict:
    from visual_recovery import second_look
    visual, decision, trace = _look(image, note)
    receipts = [trace, _gate_receipt(decision)]
    used, seen, final, record = second_look(image, note, visual, decision, _look)
    recovery = None
    if record:
        retry_view, retry_gate = record["neon"], record["decision"]
        receipts += [{**retry_view, "attempt": 2}, {**_gate_receipt(retry_gate), "attempt": 2}]
        recovery = {"event": record["event"], "box": list(record["box"]), "accepted": used is not image,
                    "view": retry_view, "decision": retry_gate}
    return {"image": used, "visual": seen.model_dump(), "decision": dict(final), "recovery": recovery,
            "receipts": receipts}


def _gate_receipt(decision):
    return {"job": "gate", "model": decision["model"], "id": decision["id"], "ms": decision["ms"]}


def parse_goal(image: bytes, note: str) -> dict:
    from find import INTENT, Intent
    intent, trace = structured("goal", CARBON, INTENT, Intent, parts(image, note))
    if intent.operation == "compare_and_watch":
        words = " ".join(intent.watch_words.split()).casefold()
        if not words or words not in " ".join(note.split()).casefold():
            raise ModelCallError("goal", CARBON, "watch request was not quoted verbatim from the buyer's note")
    return {"intent": intent.model_dump(), "receipt": trace}


def _norm(value):
    return re.sub(r"[^0-9a-z]+", "", str(value or "").casefold())


def _tokens(value):
    return [t for t in (_norm(w) for w in str(value or "").split()) if t]


def _brand_ok(brand, anchor):
    want = _tokens(brand)
    if not want:
        return False
    if _norm(brand) == _norm(anchor.get("brand")):
        return True
    title = _tokens(anchor.get("title"))
    return any(title[i:i + len(want)] == want for i in range(len(title) - len(want) + 1))


def _part_ok(part, anchor):
    want = _norm(part)
    if len(want) < 3:
        return False
    pool = set(_tokens(anchor.get("title"))) | {_norm(t) for t in anchor.get("mpn_tokens") or []}
    return want in pool


def _quoted_in_evidence(part, visual):
    """Neon's own evidence lines must quote the part number as printed text (whole tokens, punctuation ignored)."""
    want = _tokens(part)
    joined = _norm(part)
    if not joined:
        return False
    for line in visual.get("evidence") or []:
        have = _tokens(line)
        if joined in have or any(have[i:i + len(want)] == want for i in range(len(have) - len(want) + 1)):
            return True
    return False


def _valid_gtin(value):
    from compare_offers import gtin
    try:
        return gtin(value)
    except ValueError:
        return None


def corroborate(anchor: dict, visual: dict, decision: dict, verified_scan: str | None = None):
    """Deterministic identity check after Carbon's pick. Returns (basis | None, question)."""
    scan_code = _valid_gtin(verified_scan) if verified_scan else None
    printed = _valid_gtin(re.sub(r"[\s-]", "", visual.get("barcode") or ""))
    anchor_code = _valid_gtin(anchor.get("gtin")) if anchor.get("gtin") else None
    if scan_code and printed and scan_code != printed:
        return None, "The barcode numbers disagree. Could you show the label?"
    if visual.get("target_count") != "one" or not (decision.get("pass") or scan_code):
        return None, "Which item in the picture should I compare?"
    if anchor_code:
        if scan_code and scan_code != anchor_code:
            return None, "Which barcode belongs to your item?"
        if printed and printed != anchor_code:
            return None, "The printed numbers disagree. Could you show the label?"
        if scan_code or printed:
            return "barcode", ""
    if _brand_ok(visual.get("brand"), anchor) and _part_ok(visual.get("part_number"), anchor):
        # a model number that Neon did not quote from visible print is a recollection, not printed evidence
        if _quoted_in_evidence(visual.get("part_number"), visual):
            return "model_number", ""
        return None, "Could you show the printed model number or barcode?"
    return None, "Could you show the model number or barcode?"


def bind(image: bytes, note: str, anchors: list[dict], visual: dict, decision: dict,
         verified_scan: str | None = None) -> dict:
    from deal_models import CHOOSE, Selection
    none = {"slot": None, "anchor": None, "basis": None}
    anchors = list(anchors or [])[:MAX_ANCHORS]
    if not anchors:  # nothing to choose from: no model call is made, so there is no receipt and no choice
        return {**none, "question": "Could you show the model number or barcode?", "receipt": None,
                "choice_slot": None, "choice_action": None}
    listed = [{"slot": i, **{k: v for k, v in a.items() if k != "supporting"},
               "supporting": (a.get("supporting") or [])[:3]} for i, a in enumerate(anchors)]
    data = parts(image, note, {"anchors": listed, "visual": visual, "route": decision.get("choice")})
    choice, trace = structured("bind", CARBON, CHOOSE, Selection, data)
    # What Carbon itself answered, reported on every path: the slot it named (when it is a real anchor slot) and its
    # action. It is Carbon's pick only when choice_action == "choose"; it is not an identity until corroborated.
    named = choice.slot if type(choice.slot) is int and 0 <= choice.slot < len(anchors) else None
    said = {"choice_slot": named, "choice_action": choice.action}
    if choice.action != "choose" or named is None:
        return {**none, "question": choice.question.strip() or "Which item did you mean?", "receipt": trace, **said}
    anchor = anchors[named]
    basis, question = corroborate(anchor, visual, decision, verified_scan)
    if not basis:
        return {**none, "question": question, "receipt": trace, **said}
    return {"slot": named, "anchor": anchor, "basis": basis, "question": "", "receipt": trace, **said}


def _side(value):
    return {str(k): v for k, v in (value or {}).items() if isinstance(v, (str, int, float, bool, list)) and v not in ("", [])}


def _fold(value):
    return " ".join(str(value).split()).casefold()


def _quote_verified(quote, side):
    """Every non-empty fragment of the quote (split on an ellipsis) must appear in one field of its own side,
    compared after whitespace normalisation and case-folding. An empty quote is never verified."""
    fragments = [f for f in (_fold(x) for x in re.split(r"\.\.\.|\u2026", quote or "")) if f]
    fields = [_fold(x) for v in side.values() for x in (v if isinstance(v, list) else [v])]
    return bool(fragments) and all(any(f in field for field in fields) for f in fragments)


def pair(target: dict, listing: dict) -> dict:
    """Listing check v2: one Gemini 4 Carbon verdict with one decisive quote per side, then deterministic
    verification of both quotes against their own side. Anything unverifiable abstains."""
    from deal_models import PAIR, Pair
    packet = {"left": _side(target), "right": _side(listing)}
    final, receipt = structured("pair_verdict", CARBON, PAIR, Pair, text_part(packet))
    verdict = final.verdict
    if not (_quote_verified(final.left_quote, packet["left"]) and _quote_verified(final.right_quote, packet["right"])):
        verdict = "abstain"
    return {"verdict": verdict, "version": PAIR_VERSION, "receipts": [receipt]}


def pair_v1(target: dict, listing: dict) -> dict:
    """Listing check v1 (kept callable for the evaluation record): Neon extracts, Carbon rules, both must agree
    and each quote must appear verbatim in its side."""
    from deal_models import EXTRACT, PAIR, Pair
    packet = {"left": _side(target), "right": _side(listing)}
    data = text_part(packet)
    hints, first = structured("pair_extract", NEON, EXTRACT, Pair, data)
    final, second = structured("pair_verdict", CARBON, PAIR, Pair, data + text_part({"inspection": hints.model_dump()}))
    verdict = final.verdict
    if hints.verdict != final.verdict:
        verdict = "abstain"
    for inspection in (hints, final):
        for side in ("left", "right"):
            quote = getattr(inspection, side + "_quote").strip().casefold()
            if not quote or not any(quote in str(v).casefold() for v in packet[side].values()):
                verdict = "abstain"
    return {"verdict": verdict, "version": PAIR_V1_VERSION, "receipts": [first, second]}


def screen(image: bytes, target: dict, listings: list[dict], claims: dict | None = None) -> dict:
    """ONE Gemini Decision 1 forward pass: a calibrated yes/no per listing ("same sellable product as the pictured
    item?") and, when claims are given, per claim ("is this text visibly printed on the item?"). It orders and
    annotates; it never grants identity. At most MAX_SCREEN_LISTINGS listings are scored; the rest get no score."""
    import base64
    import json
    scored = list(listings or [])[:MAX_SCREEN_LISTINGS]
    asked = {str(k): str(v).strip() for k, v in (claims or {}).items() if str(v or "").strip()}
    printed = {"mpn": None, "brand": None, **{k: None for k in (claims or {})}}
    if not scored and not asked:   # nothing to ask: no model call, so no receipt
        return {"scores": [], "printed": printed, "receipt": None}
    packet = {"target": _side(target), "listings": [
        {"i": i, "title": str(row.get("title") or ""), "seller": str(row.get("seller_name") or row.get("seller") or ""),
         "condition": str(row.get("condition") or "")} for i, row in enumerate(scored)]}
    data = [{"type": "image", "mime_type": "image/jpeg", "data": base64.b64encode(image).decode()},
            {"type": "text", "text": json.dumps(packet, ensure_ascii=False)}]
    questions = {f"listing_{i}": f"Is listing {i} the exact same sellable product as the buyer's pictured item — "
                                 "same brand, model or set number, edition and pack size?" for i in range(len(scored))}
    for key, value in asked.items():
        questions[f"claim_{key}"] = f"Is the text {value!r} visibly printed on the pictured item or its packaging?"
    answers, ms = yes_no("screen", data, questions)
    for key in asked:
        printed[key] = answers[f"claim_{key}"]
    receipt = {"job": "screen", "model": "gemini-decision-1", "id": None, "ms": ms, "questions": len(questions)}
    if len(listings or []) > len(scored):
        receipt["not_scored"] = len(listings) - len(scored)
    return {"scores": [{"index": i, "p_same": answers[f"listing_{i}"]["p"],
                        "confidence": answers[f"listing_{i}"]["confidence"]} for i in range(len(scored))],
            "printed": printed, "receipt": receipt}


def scan(image: bytes, focus_box: list[int] | None = None) -> str | None:
    """Local decode (zxing-cpp) of the focus crop: exactly one code with a valid check digit, else None."""
    import zxingcpp
    from PIL import Image
    try:
        with Image.open(_io.BytesIO(image)) as im:
            im = im.convert("RGB")
            box = focus_box
            if box and len(box) == 4 and all(type(p) is int and 0 <= p <= 1000 for p in box) \
                    and box[2] - box[0] >= 20 and box[3] - box[1] >= 20:
                im = im.crop((box[0] * im.width // 1000, box[1] * im.height // 1000,
                              box[2] * im.width // 1000, box[3] * im.height // 1000))
            found = zxingcpp.read_barcodes(im)
    except (OSError, ValueError):
        return None
    return _one_gtin([getattr(f, "text", "") for f in found if getattr(f, "text", "").isdigit()])


def _one_gtin(texts):
    """Exactly one distinct numeric code, and its check digit must hold; anything else is no scan."""
    codes = {t.strip() for t in texts if t.strip()}
    return _valid_gtin(next(iter(codes))) if len(codes) == 1 else None


def compile_watch(image: bytes, note: str, candidates: list[dict]) -> dict:
    from gemini_io import COMPILE, Proposal
    proposal, trace = structured("watch_rule", CARBON, COMPILE, Proposal, parts(image, note, {"candidates": candidates}))
    return {"proposal": proposal.model_dump(), "receipt": trace}
