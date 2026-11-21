"""Shape and corroboration tests for the models facade. Fakes exist only here; no network is used."""
import io

import pytest
from PIL import Image

import app  # noqa: F401
from app import models
from app.models import io as model_io


def jpeg(size=(400, 300), color=(200, 200, 200)):
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, "JPEG")
    return out.getvalue()


def visual(**over):
    base = {"name": "Brick Set", "barcode": "", "brand": "Acme", "part_number": "AB-1234", "variant_details": [],
            "query": "acme ab-1234", "target_count": "one", "focus_box": [], "focus_words": "", "evidence": ["box"]}
    return {**base, **over}


def decision(passed=True, choice="single", confidence=0.97):
    return {"pass": passed, "choice": choice, "confidence": confidence, "threshold": 0.75, "id": None,
            "model": "gemini-decision-1", "ms": 5}


ANCHOR = {"upid": "gid://shopify/p/1", "title": "Acme AB-1234 Brick Set (2026 Edition)", "brand": "Acme",
          "mpn_tokens": ["AB-1234"], "gtin": None, "sellers": 2, "exact_variant_by_mpn": True,
          "required_variant_details": [], "identity_source": "Shop catalog product", "supporting": []}
GTIN = "00012345678905"   # UPC-A 012345678905, valid check digit


class Calls:
    """Records structured()/gate() calls and answers from a queue of schema-valid objects."""

    def __init__(self, monkeypatch, answers=None, gates=None):
        self.jobs, self.answers, self.gates = [], list(answers or []), list(gates or [])
        monkeypatch.setattr(models, "structured", self.structured)
        monkeypatch.setattr(models, "gate", self.gate)

    def structured(self, job, model, instruction, schema, data):
        self.jobs.append((job, model))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return schema.model_validate(answer), {"job": job, "model": model, "id": f"int_{len(self.jobs)}", "ms": 7}

    def gate(self, data, job="gate"):
        self.jobs.append((job, "gemini-decision-1"))
        answer = self.gates.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def choose(slot=0, action="choose", question=""):
    return {"action": action, "slot": slot, "reason": "matches", "question": question}


# ---- inspect -------------------------------------------------------------------------------------------------
def test_inspect_shape_single_pass(monkeypatch):
    calls = Calls(monkeypatch, [visual()], [decision()])
    image = jpeg()
    out = models.inspect(image, "")
    assert set(out) == {"image", "visual", "decision", "recovery", "receipts"}
    assert out["image"] is image and out["recovery"] is None
    assert out["visual"]["part_number"] == "AB-1234"
    assert out["decision"]["pass"] is True and out["decision"]["threshold"] == 0.75
    assert [r["job"] for r in out["receipts"]] == ["view", "gate"]
    assert all(set(r) >= {"job", "model", "id", "ms"} for r in out["receipts"])
    assert sorted(calls.jobs) == [("gate", "gemini-decision-1"), ("view", "gemini-4-neon")]


def test_inspect_runs_at_most_one_second_look(monkeypatch):
    low = decision(False, "single", 0.5)
    calls = Calls(monkeypatch, [visual(focus_box=[100, 100, 700, 700]), visual()], [low, decision()])
    image = jpeg()
    out = models.inspect(image, "")
    assert len(calls.jobs) == 4
    assert out["recovery"]["event"] == "auto_crop" and out["recovery"]["accepted"] is True
    assert out["image"] != image and out["decision"]["pass"] is True
    assert [r.get("attempt") for r in out["receipts"]] == [None, None, 2, 2]


def test_inspect_two_items_blank_note_is_not_cropped_or_passed(monkeypatch):
    many = visual(target_count="many", focus_box=[0, 0, 500, 1000])
    calls = Calls(monkeypatch, [many], [decision(False, "multiple", 0.9)])
    out = models.inspect(jpeg(), "")
    assert len(calls.jobs) == 2 and out["recovery"] is None
    assert out["decision"]["pass"] is False and out["visual"]["target_count"] == "many"


def test_inspect_propagates_model_failure(monkeypatch):
    Calls(monkeypatch, [models.ModelCallError("view", "gemini-4-neon", "ServerError 503: unavailable")], [decision()])
    with pytest.raises(models.ModelCallError) as err:
        models.inspect(jpeg(), "")
    assert (err.value.job, err.value.model) == ("view", "gemini-4-neon")


# ---- parse_goal ----------------------------------------------------------------------------------------------
INTENT = {"operation": "compare", "market": "US", "basis": "item", "fulfillment": "online_us", "location": "",
          "watch_words": "", "unsupported": []}


def test_parse_goal_shape(monkeypatch):
    Calls(monkeypatch, [INTENT])
    out = models.parse_goal(jpeg(), "")
    assert out["intent"] == INTENT
    assert out["receipt"]["job"] == "goal" and out["receipt"]["model"] == "gemini-4-carbon"


def test_parse_goal_rejects_unquoted_watch(monkeypatch):
    Calls(monkeypatch, [{**INTENT, "operation": "compare_and_watch", "watch_words": "tell me when under $20"}])
    with pytest.raises(models.ModelCallError):
        models.parse_goal(jpeg(), "")


# ---- bind ----------------------------------------------------------------------------------------------------
def test_bind_by_model_number(monkeypatch):
    Calls(monkeypatch, [choose(0)])
    out = models.bind(jpeg(), "", [ANCHOR], visual(), decision())
    assert out["slot"] == 0 and out["anchor"] is ANCHOR and out["basis"] == "model_number" and out["question"] == ""
    assert out["receipt"]["job"] == "bind" and out["receipt"]["model"] == "gemini-4-carbon"


def test_bind_brand_as_title_token(monkeypatch):
    Calls(monkeypatch, [choose(0)])
    out = models.bind(jpeg(), "", [{**ANCHOR, "brand": ""}], visual(brand="ACME"), decision())
    assert out["basis"] == "model_number"


def test_bind_by_printed_barcode(monkeypatch):
    Calls(monkeypatch, [choose(0)])
    anchor = {**ANCHOR, "gtin": "012345678905"}
    out = models.bind(jpeg(), "", [anchor], visual(brand="", part_number="", barcode="0 12345 67890 5"), decision())
    assert out["slot"] == 0 and out["basis"] == "barcode"


def test_bind_scan_overrides_low_gate(monkeypatch):
    Calls(monkeypatch, [choose(0)])
    anchor = {**ANCHOR, "gtin": GTIN}
    out = models.bind(jpeg(), "", [anchor], visual(brand="", part_number=""), decision(False, "single", 0.4), "012345678905")
    assert out["basis"] == "barcode"


@pytest.mark.parametrize("seen, gate_result, anchor, scan", [
    (visual(part_number=""), decision(), ANCHOR, None),                       # confidence alone, no identifier
    (visual(part_number="AB-123"), decision(), ANCHOR, None),                 # prefix is not an exact token
    (visual(brand="Other"), decision(), ANCHOR, None),                        # brand mismatch
    (visual(), decision(False, "single", 0.6), ANCHOR, None),                 # gate did not pass
    (visual(target_count="many"), decision(), ANCHOR, None),                  # more than one target
    (visual(barcode="036000291452"), decision(), {**ANCHOR, "gtin": GTIN}, None),   # printed code disagrees
    (visual(), decision(), {**ANCHOR, "gtin": GTIN}, "036000291452"),         # scan disagrees with the anchor
])
def test_bind_refuses_without_corroboration(monkeypatch, seen, gate_result, anchor, scan):
    Calls(monkeypatch, [choose(0)])
    out = models.bind(jpeg(), "", [anchor], seen, gate_result, scan)
    assert out["slot"] is None and out["anchor"] is None and out["basis"] is None
    assert out["question"] and "\n" not in out["question"]
    assert out["receipt"]["job"] == "bind"


def test_bind_model_declines_or_bad_slot(monkeypatch):
    Calls(monkeypatch, [choose(None, "clarify", "Which edition is it?"), choose(5)])
    assert models.bind(jpeg(), "", [ANCHOR], visual(), decision())["question"] == "Which edition is it?"
    out = models.bind(jpeg(), "", [ANCHOR], visual(), decision())
    assert out["slot"] is None and out["question"]


def test_bind_without_anchors_makes_no_call(monkeypatch):
    calls = Calls(monkeypatch)
    out = models.bind(jpeg(), "", [], visual(), decision())
    assert out["slot"] is None and out["receipt"] is None and out["question"] and not calls.jobs


def test_bind_failure_propagates(monkeypatch):
    Calls(monkeypatch, [models.ModelCallError("bind", "gemini-4-carbon", "ClientError 429: quota")])
    with pytest.raises(models.ModelCallError):
        models.bind(jpeg(), "", [ANCHOR], visual(), decision())


# ---- pair ----------------------------------------------------------------------------------------------------
LEFT = {"brand": "Acme", "mpn": "AB-1234", "name": "Acme AB-1234 Brick Set"}
RIGHT = {"title": "Acme AB-1234 Brick Set, new in box", "sku": "AB-1234"}


def verdict(v="match", left="AB-1234", right="AB-1234"):
    return {"verdict": v, "left_quote": left, "right_quote": right, "explanation": "same model"}


def test_pair_match_needs_both_models_and_verbatim_quotes(monkeypatch):
    calls = Calls(monkeypatch, [verdict(), verdict()])
    out = models.pair(LEFT, RIGHT)
    assert out["verdict"] == "match"
    assert [(r["job"], r["model"]) for r in out["receipts"]] == [("pair_extract", "gemini-4-neon"),
                                                                  ("pair_verdict", "gemini-4-carbon")]
    assert calls.jobs == [("pair_extract", "gemini-4-neon"), ("pair_verdict", "gemini-4-carbon")]


@pytest.mark.parametrize("first, second", [
    (verdict("different"), verdict("match")),              # the models disagree
    (verdict(), verdict(right="ZZ-9999")),                 # quote is not in the listing
    (verdict(left=""), verdict()),                         # missing quote
    (verdict("abstain"), verdict("abstain")),
])
def test_pair_abstains(monkeypatch, first, second):
    Calls(monkeypatch, [first, second])
    out = models.pair(LEFT, RIGHT)
    assert out["verdict"] == "abstain" and len(out["receipts"]) == 2


def test_pair_different(monkeypatch):
    Calls(monkeypatch, [verdict("different"), verdict("different")])
    assert models.pair(LEFT, RIGHT)["verdict"] == "different"


# ---- scan / watch / io ---------------------------------------------------------------------------------------
def test_scan_no_barcode_is_none():
    assert models.scan(jpeg()) is None
    assert models.scan(b"not an image") is None


def test_scan_requires_exactly_one_valid_code():
    assert models._one_gtin(["012345678905"]) == GTIN
    assert models._one_gtin(["012345678905", "012345678905"]) == GTIN
    assert models._one_gtin(["012345678905", "036000291452"]) is None
    assert models._one_gtin(["012345678906"]) is None
    assert models._one_gtin([]) is None


def test_scan_decodes_a_rendered_barcode():
    zxingcpp = pytest.importorskip("zxingcpp")
    if not hasattr(zxingcpp, "write_barcode"):
        pytest.skip("this zxing-cpp build cannot render a barcode")
    code = zxingcpp.write_barcode(zxingcpp.BarcodeFormat.EAN13, "0012345678905", width=400, height=160)
    out = io.BytesIO()
    canvas = Image.new("RGB", (600, 400), "white")
    canvas.paste(Image.fromarray(code).convert("RGB"), (100, 120))
    canvas.save(out, "JPEG", quality=95)
    assert models.scan(out.getvalue()) == GTIN
    assert models.scan(out.getvalue(), [0, 0, 1000, 1000]) == GTIN


def test_compile_watch_shape(monkeypatch):
    proposal = {"reason": "ok", "status": "ready", "candidate": 0, "mode": "restock", "op": "lt", "cents": 2500,
                "cap_evidence": "under $25", "binding_evidence": "slot 0", "blockers": [], "question": ""}
    Calls(monkeypatch, [proposal])
    out = models.compile_watch(jpeg(), "tell me when it is back under $25", [{"slot": 0, "seller": "s", "variant": "v"}])
    assert out["proposal"] == proposal
    assert out["receipt"]["job"] == "watch_rule" and out["receipt"]["model"] == "gemini-4-carbon"


def test_wire_schema_is_self_contained():
    from deal_models import Visual
    from gemini_io import Grounding
    for schema in (Visual, Grounding):
        text = str(model_io.wire_schema(schema))
        assert "$ref" not in text and "$defs" not in text and "'title'" not in text
    assert set(model_io.wire_schema(Visual)["required"]) == set(Visual.model_fields)


def test_model_call_error_is_safe(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "secret-value-123")
    message = model_io.safe_message(RuntimeError("bad key secret-value-123 rejected"))
    assert "secret-value-123" not in message and "RuntimeError" in message
    err = models.ModelCallError("view", "gemini-4-neon", message)
    assert (err.job, err.model, err.safe_message) == ("view", "gemini-4-neon", message)


# ---- retention of interactions at the model service -----------------------------------------------------------
class FakeInteractions:
    def __init__(self, fail_delete=False):
        self.created, self.deleted, self.fail_delete = [], [], fail_delete

    def create(self, **body):
        self.created.append(body)
        stored = body["store"]
        return type("R", (), {"id": "v1_fake" if stored else "", "status": "completed",
                              "output_text": '{"verdict":"abstain","left_quote":"","right_quote":"","explanation":""}'})()

    def delete(self, id):
        if self.fail_delete:
            raise RuntimeError("delete refused")
        self.deleted.append(id)


def run_structured(monkeypatch, mode, fail_delete=False):
    from deal_models import Pair
    fake = FakeInteractions(fail_delete)
    monkeypatch.setattr(model_io, "RETENTION", mode)
    monkeypatch.setattr(model_io, "clients", lambda: (type("C", (), {"interactions": fake})(), None))
    _, receipt = model_io.structured("pair_verdict", "gemini-4-carbon", "instruction", Pair, model_io.text_part({}))
    return fake, receipt


def test_retention_switch(monkeypatch):
    import importlib
    try:
        for value, mode in (("0", "none"), ("delete", "delete"), ("1", "keep")):
            monkeypatch.setenv("SF_STORE_INTERACTIONS", value)
            assert importlib.reload(model_io).RETENTION == mode
    finally:
        monkeypatch.undo()
        importlib.reload(model_io)


def test_stateless_call_stores_nothing_and_has_no_id(monkeypatch):
    fake, receipt = run_structured(monkeypatch, "none")
    assert fake.created[0]["store"] is False and not fake.deleted
    assert receipt["id"] is None and "retained" not in receipt


def test_delete_mode_removes_the_interaction_and_keeps_the_issued_id(monkeypatch):
    fake, receipt = run_structured(monkeypatch, "delete")
    assert fake.created[0]["store"] is True and fake.deleted == ["v1_fake"]
    assert receipt["id"] == "v1_fake" and receipt["retained"] is False


def test_delete_failure_is_reported_on_the_receipt(monkeypatch):
    _, receipt = run_structured(monkeypatch, "delete", fail_delete=True)
    assert receipt["retained"] is True and "delete refused" in receipt["delete_error"]


def test_keep_mode_says_retained(monkeypatch):
    fake, receipt = run_structured(monkeypatch, "keep")
    assert fake.created[0]["store"] is True and not fake.deleted and receipt["retained"] is True
