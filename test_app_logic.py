"""Run with: pytest -q
Covers normal operation, missing information, incorrect (adversarial) output, and workflow failure."""
import csv
import json
import threading
import time
from http.server import HTTPServer
from pathlib import Path

import pytest

import config
import drafting
import policies
import rules
import validator
import workflow

DATA = Path(config.DATA)


def tickets():
    with open(DATA / "jirani-development-tickets.csv", newline="") as f:
        return {r["ticket_id"]: r for r in csv.DictReader(f)}


T = tickets()
EXPECTED = {r["ticket_id"]: r for r in csv.DictReader(open(DATA / "dev_expected.csv", newline=""))}


# ---------------------------------------------------------------- policy source selection
def test_only_current_approved_policies_are_sources():
    assert policies.approved_ids() == {"P01", "P02", "P03", "P04", "P05"}
    assert {p["id"] for p in policies.excluded()} == {"A01", "U01"}


def test_archived_and_unapproved_text_never_reaches_the_prompt():
    for t in T.values():
        prompt = drafting.SYSTEM_PROMPT + drafting.build_user_prompt(t, rules.analyse(t))
        assert "A01" not in prompt and "U01" not in prompt
        assert "former change-of-mind" not in prompt and "even when used" not in prompt


# ---------------------------------------------------------------- normal operation: routing
@pytest.mark.parametrize("tid", sorted(EXPECTED))
def test_route_and_policy_for_each_dev_ticket(tid):
    a = rules.analyse(T[tid])
    assert a.route == EXPECTED[tid]["expected_route"]
    assert EXPECTED[tid]["expected_policy_ids"] in a.policy_ids


@pytest.mark.parametrize("tid", sorted(EXPECTED))
def test_template_drafts_pass_validation(tid):
    a = rules.analyse(T[tid])
    d = drafting.template_draft(T[tid], a)
    assert validator.blocking(validator.check(d["draft"], a, T[tid], d["policy_ids"])) == []


# ---------------------------------------------------------------- return-window boundary (day 14 included)
def ticket(**kw):
    base = dict(ticket_id="X", opened_date="2026-09-16", topic="return", customer_id="C1", order_id="O1",
                order_customer_id="C1", delivered_date="2026-09-02", item_unused="yes", original_packaging="yes",
                proof_of_purchase="yes", message="Can I return it?", verified_courier_status="")
    base.update(kw)
    return base


def test_day_14_is_inside_window_day_15_is_outside():
    assert rules.analyse(ticket(delivered_date="2026-09-02")).route == "RETURN_REVIEW_OFFER"       # 14 days
    assert rules.analyse(ticket(delivered_date="2026-09-01")).route == "RETURN_MANAGER_DECISION"   # 15 days


def test_used_item_or_missing_packaging_needs_manager():
    assert rules.analyse(ticket(item_unused="no")).route == "RETURN_MANAGER_DECISION"
    assert rules.analyse(ticket(original_packaging="no")).route == "RETURN_MANAGER_DECISION"


def test_unknown_facts_ask_questions_not_decide():
    a = rules.analyse(ticket(delivered_date="", item_unused="unknown"))
    assert a.route == "NEED_INFO" and len(a.missing) >= 2


def test_delivery_after_ticket_date_is_flagged_not_trusted():
    a = rules.analyse(ticket(delivered_date="2026-09-20"))
    assert a.route == "NEED_INFO" and "DATE_INCONSISTENT" in a.flags


def test_safety_beats_return_window():
    a = rules.analyse(ticket(message="It sparked when I plugged it in, I want to return it", delivered_date="2026-08-01"))
    assert a.route == "SAFETY_QUEUE"


def test_mismatch_hides_order_details_from_facts():
    a = rules.analyse(ticket(order_customer_id="C99"))
    assert a.route == "VERIFY_REFERENCE" and a.facts == {}


def test_override_attempt_is_flagged_but_routing_is_unchanged():
    a = rules.analyse(T["D07"])
    assert "INSTRUCTION_OVERRIDE_ATTEMPT" in a.flags and a.route == "RETURN_REVIEW_OFFER"


# ---------------------------------------------------------------- incorrect output: validator must catch bad drafts
def check(tid, text, cited):
    a = rules.analyse(T[tid])
    return {i["code"] for i in validator.blocking(validator.check(text, a, T[tid], cited))}


def test_catches_refund_approval_when_customer_demands_it():
    assert "UNAUTHORISED_PROMISE" in check("D07", "Done! Your refund has been approved and will be sent today?", ["P01"])


def test_catches_archived_30_day_window():
    assert "OUTDATED_POLICY_REFERENCE" in check("D02", "You have 30 days so we will approve it. Right?", ["P01"])


def test_catches_citation_of_archived_or_unapproved_policy():
    assert "CITATION_NOT_APPROVED" in check("D02", "Outside the window; manager will decide.", ["P01", "A01"])
    assert "CITATION_NOT_APPROVED" in check("D02", "Outside the window; manager will decide.", ["U01"])


def test_catches_missing_required_citation():
    assert "CITATION_MISSING" in check("D01", "We can start a return review. Shall we?", [])


def test_catches_invented_delivery_date():
    assert "UNSUPPORTED_DELIVERY_DATE" in check("D05", "Your cooker will arrive tomorrow.", ["P03"])
    assert "UNSUPPORTED_DELIVERY_DATE" in check("D05", "Delivery is due on 20 September.", ["P03"])


def test_catches_warranty_claim():
    assert "UNSUPPORTED_WARRANTY_CLAIM" in check("D06", "Yes, the motor is covered for two years.", ["P04"])
    assert "UNSUPPORTED_WARRANTY_CLAIM" in check("D06", "Your fan is covered under warranty, free repair.", ["P04"])


def test_hedged_warranty_wording_is_allowed():
    assert check("D06", "I can’t confirm whether your fan is covered. Could you confirm the model?", ["P04"]) == set()


def test_catches_repair_instructions_and_missing_stop_use_advice():
    codes = check("D04", "Try opening the case and replace the fuse.", ["P02"])
    assert "REPAIR_INSTRUCTION" in codes and "SAFETY_ADVICE_MISSING" in codes


def test_catches_leak_of_other_customers_order():
    assert "ORDER_DETAIL_LEAK" in check("D08", "Order O88 was delivered on 10 September.", ["P05"])


def test_catches_following_customer_override():
    assert "FOLLOWED_CUSTOMER_OVERRIDE" in check("D07", "As you requested, ignoring the policy.", ["P01"])


def test_empty_draft_is_blocked():
    assert "EMPTY_DRAFT" in check("D01", "  ", ["P01"])


# ---------------------------------------------------------------- model output parsing / failure
def test_parses_fenced_json():
    obj = drafting._parse_json('```json\n{"draft": "Hi?", "policy_ids": ["P01"]}\n```')
    assert obj["policy_ids"] == ["P01"]


def test_rejects_unusable_model_output():
    with pytest.raises(ValueError):
        drafting._parse_json("Sorry, I cannot help with that.")
    with pytest.raises(ValueError):
        drafting._parse_json('{"draft": 5}')


def test_model_outage_degrades_visibly_to_labelled_fallback():
    a = rules.analyse(T["D01"])
    d = drafting.generate(T["D01"], a, mode="llm", fail_model=True)
    assert d["mode"] == "template-fallback" and "outage" in d["error"]


# ---------------------------------------------------------------- workflow: success, rejection, failure
@pytest.fixture()
def mock_server():
    from tools import mock_n8n
    mock_n8n.FAIL = False
    srv = HTTPServer(("localhost", 0), mock_n8n.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    old = config.N8N_WEBHOOK_URL
    config.N8N_WEBHOOK_URL = f"http://localhost:{srv.server_port}{mock_n8n.PATH}"
    yield mock_n8n
    config.N8N_WEBHOOK_URL = old
    srv.shutdown()


PAYLOAD = {"ticket_id": "D04", "draft_id": 1, "draft_version": 1, "route": "SAFETY_QUEUE",
           "destination": "safety_queue", "action": "approved", "reviewer": "Test", "policy_ids": ["P02"]}


def test_workflow_creates_urgent_safety_task(mock_server):
    r = workflow.trigger(PAYLOAD)
    assert r["status"] == "ok" and r["response"]["task"]["priority"] == "urgent"


def test_workflow_rejects_incomplete_payload(mock_server):
    r = workflow.trigger({"ticket_id": "D04"})
    assert r["status"] == "rejected" and r["http_status"] == 400


def test_workflow_rejected_draft_creates_no_task(mock_server):
    r = workflow.trigger({**PAYLOAD, "action": "rejected"})
    assert r["response"]["status"] == "no_task"


def test_workflow_failure_is_recorded_not_raised(mock_server):
    mock_server.FAIL = True
    r = workflow.trigger(PAYLOAD)
    assert r["status"] == "failed" and r["http_status"] == 500


def test_workflow_unreachable_is_recorded_not_raised():
    old = config.N8N_WEBHOOK_URL
    config.N8N_WEBHOOK_URL = "http://localhost:1/webhook/none"
    try:
        r = workflow.trigger(PAYLOAD)
    finally:
        config.N8N_WEBHOOK_URL = old
    assert r["status"] == "failed" and r["error"]


def test_simulation_is_labelled_and_makes_no_call():
    r = workflow.trigger(PAYLOAD, simulate=True)
    assert r["status"] == "simulated" and "SIMULATED" in r["response"]["note"]


# ---------------------------------------------------------------- Gemini wiring (real SDK types, fake network)
class _FakeResp:
    def __init__(self, text, blocked=False):
        from types import SimpleNamespace
        self.text = text
        self.candidates = [SimpleNamespace(finish_reason="SAFETY" if blocked else "STOP")]
        self.usage_metadata = SimpleNamespace(prompt_token_count=321, candidates_token_count=54)


def _patch_client(monkeypatch, response):
    from google import genai
    captured = {}

    class FakeModels:
        def generate_content(self, **kw):
            captured.update(kw)
            return response

    class FakeClient:
        def __init__(self, **kw):
            captured["client_kwargs"] = kw
            self.models = FakeModels()

    monkeypatch.setattr(genai, "Client", FakeClient)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    return captured


def test_gemini_call_uses_valid_sdk_config_and_only_approved_context(monkeypatch):
    good = '{"draft": "We can start a return review. Shall we?", "policy_ids": ["P01"], "reviewer_note": "P01"}'
    captured = _patch_client(monkeypatch, _FakeResp(good))
    a = rules.analyse(T["D01"])
    d = drafting.generate(T["D01"], a, mode="llm")
    assert d["mode"] == "llm" and d["model"] == config.MODEL and d["error"] == ""
    assert d["policy_ids"] == ["P01"] and d["usage"] == "in:321/out:54 tokens"
    cfg = captured["config"]
    assert cfg.system_instruction == drafting.SYSTEM_PROMPT and cfg.response_mime_type == "application/json"
    assert captured["model"] == config.MODEL
    retry = captured["client_kwargs"]["http_options"].retry_options
    assert retry.attempts == 4 and 503 in retry.http_status_codes and 401 not in retry.http_status_codes
    assert "<customer_message>" in captured["contents"] and "A01" not in captured["contents"]


def test_gemini_blocked_response_falls_back_visibly(monkeypatch):
    _patch_client(monkeypatch, _FakeResp(None, blocked=True))
    d = drafting.generate(T["D04"], rules.analyse(T["D04"]), mode="llm")
    assert d["mode"] == "template-fallback" and "blocked" in d["error"] and "SAFETY" in d["error"]


def test_gemini_garbage_output_falls_back_visibly(monkeypatch):
    _patch_client(monkeypatch, _FakeResp("Sure! Here is a reply for you."))
    d = drafting.generate(T["D01"], rules.analyse(T["D01"]), mode="llm")
    assert d["mode"] == "template-fallback" and d["error"]


def test_auto_mode_uses_templates_without_a_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    d = drafting.generate(T["D01"], rules.analyse(T["D01"]), mode="auto")
    assert d["mode"] == "template" and "TEMPLATE MODE" in drafting.mode_label("auto")


def test_google_api_key_variable_also_works(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "x")
    assert drafting.llm_available() is True


def test_failed_model_run_is_never_scored_as_pass(monkeypatch, tmp_path):
    """A template fallback during a model evaluation must not look like model success."""
    import subprocess, sys, os
    env = dict(os.environ, GEMINI_API_KEY="bad-key", JIRANI_DRAFT_MODE="llm")
    out = tmp_path / "e.csv"
    subprocess.run([sys.executable, "run_eval.py", "--mode", "llm", "--out", str(out)], env=env,
                   capture_output=True, timeout=300, cwd=str(Path(__file__).parent))
    rows = list(csv.DictReader(open(out)))
    assert rows and all(r["result"] == "NOT-MODEL-OUTPUT" for r in rows)
    assert all("MODEL CALL FAILED" in r["evidence"] for r in rows)


# ---------------------------------------------------------------- backup-model chain
def _patch_models(monkeypatch, behaviour):
    """behaviour: {model_name: Exception | _FakeResp}. Records the models tried, in order."""
    from google import genai
    tried = []

    class FakeModels:
        def generate_content(self, **kw):
            tried.append(kw["model"])
            outcome = behaviour[kw["model"]]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    class FakeClient:
        def __init__(self, **kw):
            self.models = FakeModels()

    monkeypatch.setattr(genai, "Client", FakeClient)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    return tried


GOOD = '{"draft": "We can start a return review. Shall we?", "policy_ids": ["P01"]}'


def _srv(code=503):
    from google.genai import errors
    cls = errors.ServerError if code >= 500 else errors.ClientError
    return cls(code, {"error": {"code": code, "message": "x", "status": "X"}})


def test_overloaded_main_model_falls_to_backup_and_records_which_wrote_it(monkeypatch):
    monkeypatch.setattr(config, "MODEL", "main-m")
    monkeypatch.setattr(config, "FALLBACK_MODELS", ["backup-1", "backup-2"])
    tried = _patch_models(monkeypatch, {"main-m": _srv(503), "backup-1": _FakeResp(GOOD), "backup-2": _FakeResp(GOOD)})
    d = drafting.generate(T["D01"], rules.analyse(T["D01"]), mode="llm")
    assert tried == ["main-m", "backup-1"]
    assert d["mode"] == "llm" and d["model"] == "backup-1"          # the model that REALLY wrote it
    assert "main-m" in d["error"] and "backup model" in d["error"]  # visible to the reviewer


def test_chain_continues_through_429_and_404(monkeypatch):
    monkeypatch.setattr(config, "MODEL", "a")
    monkeypatch.setattr(config, "FALLBACK_MODELS", ["b", "c"])
    tried = _patch_models(monkeypatch, {"a": _srv(429), "b": _srv(404), "c": _FakeResp(GOOD)})
    d = drafting.generate(T["D01"], rules.analyse(T["D01"]), mode="llm")
    assert tried == ["a", "b", "c"] and d["model"] == "c"


def test_all_models_failing_ends_in_labelled_template(monkeypatch):
    monkeypatch.setattr(config, "MODEL", "a")
    monkeypatch.setattr(config, "FALLBACK_MODELS", ["b"])
    _patch_models(monkeypatch, {"a": _srv(503), "b": _srv(503)})
    d = drafting.generate(T["D01"], rules.analyse(T["D01"]), mode="llm")
    assert d["mode"] == "template-fallback" and "All models failed" in d["error"]


def test_bad_key_is_not_retried_on_other_models(monkeypatch):
    monkeypatch.setattr(config, "MODEL", "a")
    monkeypatch.setattr(config, "FALLBACK_MODELS", ["b", "c"])
    tried = _patch_models(monkeypatch, {"a": _srv(401), "b": _FakeResp(GOOD), "c": _FakeResp(GOOD)})
    d = drafting.generate(T["D01"], rules.analyse(T["D01"]), mode="llm")
    assert tried == ["a"] and d["mode"] == "template-fallback"
