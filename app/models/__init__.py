"""Model jobs facade (docs/CONTRACT.md section 3): dict in / dict out, real calls only.

Gemini 4 Neon reads the photo, Gemini Decision 1 gates the target, Gemini 4 Carbon reads the request and
selects an identity. Deterministic code decides whether a selection is corroborated; a model's confidence
never binds a product on its own.
"""
import app as _app  # noqa: F401  (puts reference/ on sys.path)

import io as _io
import re
from concurrent.futures import ThreadPoolExecutor

from app.models.io import ModelCallError, THRESHOLD, gate, parts, structured, text_part
from gemini_io import CARBON, NEON

__all__ = ["ModelCallError", "inspect", "parse_goal", "bind", "pair", "scan", "compile_watch"]

MAX_ANCHORS = 8


def _look(raw, note):
    from deal_models import VIEW, Visual
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
        return "model_number", ""
    return None, "Could you show the model number or barcode?"


def bind(image: bytes, note: str, anchors: list[dict], visual: dict, decision: dict,
         verified_scan: str | None = None) -> dict:
    from deal_models import CHOOSE, Selection
    none = {"slot": None, "anchor": None, "basis": None}
    anchors = list(anchors or [])[:MAX_ANCHORS]
    if not anchors:  # nothing to choose from: no model call is made, so there is no receipt
        return {**none, "question": "Could you show the model number or barcode?", "receipt": None}
    listed = [{"slot": i, **{k: v for k, v in a.items() if k != "supporting"},
               "supporting": (a.get("supporting") or [])[:3]} for i, a in enumerate(anchors)]
    data = parts(image, note, {"anchors": listed, "visual": visual, "route": decision.get("choice")})
    choice, trace = structured("bind", CARBON, CHOOSE, Selection, data)
    if choice.action != "choose" or type(choice.slot) is not int or not 0 <= choice.slot < len(anchors):
        return {**none, "question": choice.question.strip() or "Which item did you mean?", "receipt": trace}
    anchor = anchors[choice.slot]
    basis, question = corroborate(anchor, visual, decision, verified_scan)
    if not basis:
        return {**none, "question": question, "receipt": trace}
    return {"slot": choice.slot, "anchor": anchor, "basis": basis, "question": "", "receipt": trace}


def _side(value):
    return {str(k): v for k, v in (value or {}).items() if isinstance(v, (str, int, float, bool, list)) and v not in ("", [])}


def pair(target: dict, listing: dict) -> dict:
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
    return {"verdict": verdict, "receipts": [first, second]}


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
