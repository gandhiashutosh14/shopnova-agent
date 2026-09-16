"""
Unit tests for the four Cloud Functions, invoked in-process through a Flask
test request context. No deployment, network or GCP credentials involved.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib

import pytest
from flask import Flask, request

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_function(name: str):
    """Import functions/<name>/main.py under a unique module name and return the handler."""
    path = ROOT / "functions" / name / "main.py"
    spec = importlib.util.spec_from_file_location(f"shopnova_fn_{name}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module, getattr(module, name)


def invoke(fn, payload: dict):
    """Call an HTTP Cloud Function with a JSON body and return (parsed_body, status)."""
    app = Flask("shopnova-test")
    with app.test_request_context(json=payload):
        body, status, _headers = fn(request)
    return json.loads(body), status


# ---------------------------------------------------------------------------
# get_order_status
# ---------------------------------------------------------------------------
def test_get_order_status_shipped_order_includes_tracking():
    _, fn = load_function("get_order_status")
    body, status = invoke(fn, {"order_id": "ord-001"})  # lower-case: handler upper-cases it
    assert status == 200
    assert body["status"] == "shipped"
    assert "TRK9876543210" in body["message"]


def test_get_order_status_unknown_order_is_404():
    _, fn = load_function("get_order_status")
    body, status = invoke(fn, {"order_id": "ORD-999"})
    assert status == 404
    assert "not found" in body["error"]


def test_get_order_status_missing_id_is_400():
    _, fn = load_function("get_order_status")
    _, status = invoke(fn, {})
    assert status == 400


# ---------------------------------------------------------------------------
# initiate_return
# ---------------------------------------------------------------------------
def test_initiate_return_delivered_order_succeeds():
    _, fn = load_function("initiate_return")
    body, status = invoke(fn, {"order_id": "ORD-003", "reason": "wrong size"})
    assert status == 200
    assert body["success"] is True
    assert body["return_id"].startswith("RET-")
    assert body["reason"] == "wrong size"


def test_initiate_return_processing_order_is_rejected():
    _, fn = load_function("initiate_return")
    body, status = invoke(fn, {"order_id": "ORD-002"})
    assert status == 400
    assert "cannot be returned" in body["error"]


# ---------------------------------------------------------------------------
# issue_refund (guardrail)
# ---------------------------------------------------------------------------
def test_issue_refund_within_policy_is_processed():
    _, fn = load_function("issue_refund")
    body, status = invoke(fn, {"order_id": "ORD-001"})
    assert status == 200
    assert body["success"] is True
    assert body["refund_amount"] == pytest.approx(79.99)


def test_issue_refund_above_threshold_cannot_be_self_approved_by_caller(monkeypatch):
    module, fn = load_function("issue_refund")
    # Lower the policy threshold so a sample order trips the guardrail.
    monkeypatch.setitem(module.REFUND_POLICY, "requires_approval_above", 50.0)

    body, status = invoke(fn, {"order_id": "ORD-001"})
    assert status == 200
    assert body["requires_approval"] is True
    assert body["escalation_needed"] is True
    assert "refund_id" not in body

    # Even if an untrusted caller/model invents an approval field, the tool
    # must not process the refund. Human approval belongs in a trusted control
    # plane, not in model-generated tool arguments.
    bypass, status = invoke(fn, {"order_id": "ORD-001", "approved": True})
    assert status == 200
    assert bypass["requires_approval"] is True
    assert bypass["escalation_needed"] is True
    assert "refund_id" not in bypass
    assert "success" not in bypass


# ---------------------------------------------------------------------------
# search_knowledge_base
# ---------------------------------------------------------------------------
def test_search_knowledge_base_topic_match_is_high_relevance():
    _, fn = load_function("search_knowledge_base")
    body, status = invoke(fn, {"query": "what is your return policy"})
    assert status == 200
    assert body["found"] is True
    assert body["results"][0]["topic"] == "return policy"
    assert body["results"][0]["relevance"] == "high"


def test_search_knowledge_base_falls_back_to_body_match():
    _, fn = load_function("search_knowledge_base")
    body, _ = invoke(fn, {"query": "manager approval"})
    assert body["found"] is True
    assert any(r["relevance"] == "medium" for r in body["results"])


def test_search_knowledge_base_empty_query_is_400():
    _, fn = load_function("search_knowledge_base")
    _, status = invoke(fn, {"query": ""})
    assert status == 400
