"""
Tests for the agent loop, using a scripted model in place of Gemini and the
four tools running in-process (LOCAL_MODE). Exercises: a tool-calling turn,
the human-approval guardrail, the max-iteration fallback, session memory and
the HTTP surface. Nothing here touches the network.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
from typing import Any, Dict, List

import pytest
from google.genai import types

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture()
def orchestrator(monkeypatch):
    """Load orchestrator/main.py fresh in local mode with no API key."""
    monkeypatch.setenv("LOCAL_MODE", "1")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("MAX_TOOL_ITERATIONS", "5")
    spec = importlib.util.spec_from_file_location("shopnova_orchestrator_under_test", ROOT / "orchestrator" / "main.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Helpers to build responses shaped exactly like the google-genai SDK returns
# ---------------------------------------------------------------------------
def model_tool_call(name: str, args: Dict[str, Any]) -> types.GenerateContentResponse:
    part = types.Part(function_call=types.FunctionCall(name=name, args=args))
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=[part]))]
    )


def model_text(text: str) -> types.GenerateContentResponse:
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=[types.Part(text=text)]))]
    )


class ScriptedModel:
    """Returns pre-scripted responses in order and records what it was shown."""

    def __init__(self, responses: List[types.GenerateContentResponse]):
        self._responses = list(responses)
        self.calls: List[List[types.Content]] = []

    def __call__(self, contents: List[types.Content]):
        self.calls.append(list(contents))
        if len(self._responses) > 1:
            return self._responses.pop(0)
        return self._responses[0]  # last response repeats (used by the loop-exhaustion test)


# ---------------------------------------------------------------------------
def test_tool_call_result_is_fed_back_to_the_model(orchestrator):
    model = ScriptedModel([
        model_tool_call("get_order_status", {"order_id": "ORD-001"}),
        model_text("Your order ORD-001 has shipped with tracking TRK9876543210."),
    ])

    reply, trace = orchestrator.run_agent_turn("Where is my order ORD-001?", [], "s1", generate=model)

    assert "shipped" in reply
    assert len(trace) == 1
    assert trace[0]["tool"] == "get_order_status"
    assert trace[0]["result"]["status"] == "shipped"

    # Second model call must contain: user msg, model tool-call turn, and the function response.
    second_call = model.calls[1]
    assert len(second_call) == 3
    fn_response_parts = [p for p in second_call[2].parts if p.function_response]
    assert fn_response_parts[0].function_response.name == "get_order_status"
    assert fn_response_parts[0].function_response.response["result"]["status"] == "shipped"


def test_multiple_tool_calls_in_one_turn_run_in_order(orchestrator):
    both = types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(role="model", parts=[
        types.Part(function_call=types.FunctionCall(name="get_order_status", args={"order_id": "ORD-003"})),
        types.Part(function_call=types.FunctionCall(name="initiate_return", args={"order_id": "ORD-003", "reason": "faulty"})),
    ]))])
    model = ScriptedModel([both, model_text("Return started.")])

    reply, trace = orchestrator.run_agent_turn("Return ORD-003, it is faulty", [], "s2", generate=model)

    assert reply == "Return started."
    assert [t["tool"] for t in trace] == ["get_order_status", "initiate_return"]
    assert trace[1]["result"]["success"] is True


def test_guardrail_escalates_when_tool_requires_approval(orchestrator, monkeypatch):
    # Make the refund tool report that approval is needed, as it does above the policy threshold.
    monkeypatch.setattr(orchestrator, "call_tool", lambda name, args: {
        "requires_approval": True, "escalation_needed": True, "order_id": args["order_id"], "refund_amount": 250.0,
    })
    model = ScriptedModel([model_tool_call("issue_refund", {"order_id": "ORD-001"})])

    reply, trace = orchestrator.run_agent_turn("Refund ORD-001 please", [], "s3", generate=model)

    assert "escalated" in reply
    assert len(model.calls) == 1, "no further model calls after an escalation"
    assert orchestrator.escalator.events[-1]["reason"] == "Approval needed for issue_refund: $250.00"
    assert orchestrator.escalator.events[-1]["session_id"] == "s3"


def test_loop_exhaustion_falls_back_to_human(orchestrator):
    # A model that never stops asking for tools must not spin forever.
    model = ScriptedModel([model_tool_call("search_knowledge_base", {"query": "shipping"})])

    reply, trace = orchestrator.run_agent_turn("hello", [], "s4", generate=model)

    assert len(model.calls) == orchestrator.MAX_TOOL_ITERATIONS
    assert len(trace) == orchestrator.MAX_TOOL_ITERATIONS
    assert "human agent" in reply
    assert orchestrator.escalator.events[-1]["reason"] == "Max agentic loop attempts reached"


def test_unknown_tool_is_reported_not_raised(orchestrator):
    model = ScriptedModel([model_tool_call("delete_everything", {}), model_text("I cannot do that.")])
    reply, trace = orchestrator.run_agent_turn("wipe it", [], "s5", generate=model)
    assert trace[0]["result"] == {"error": "Unknown tool: delete_everything"}
    assert reply == "I cannot do that."


def test_chat_route_persists_session_history(orchestrator):
    orchestrator.GENERATE_OVERRIDE = ScriptedModel([model_text("Hi! How can I help?")])
    client = orchestrator.app.test_client()

    first = client.post("/chat", json={"message": "hi"}).get_json()
    assert first["response"] == "Hi! How can I help?"
    assert first["tool_calls"] == []
    session_id = first["session_id"]

    orchestrator.GENERATE_OVERRIDE = ScriptedModel([model_text("Still here.")])
    second = client.post("/chat", json={"message": "are you there?", "session_id": session_id}).get_json()
    assert second["session_id"] == session_id

    history = orchestrator.session_store.load(session_id)
    assert [h["role"] for h in history] == ["user", "model", "user", "model"]
    assert history[0]["content"] == "hi"


def test_chat_route_without_key_or_override_returns_503(orchestrator):
    orchestrator.GENERATE_OVERRIDE = None
    resp = orchestrator.app.test_client().post("/chat", json={"message": "hi"})
    assert resp.status_code == 503
    assert "GEMINI_API_KEY" in resp.get_json()["error"]


def test_health_reports_local_mode(orchestrator):
    body = orchestrator.app.test_client().get("/health").get_json()
    assert body["status"] == "healthy"
    assert body["mode"] == "local"
