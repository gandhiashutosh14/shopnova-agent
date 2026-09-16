"""
ShopNova support agent: the orchestrator.

One Flask service that runs the agent loop (Gemini function calling), calls
the four tools, keeps per-session memory, and escalates to a human when a
tool says so or when the loop cannot converge.

Two ways to run it:

* Cloud mode (default): tools are deployed Cloud Functions reached over HTTPS,
  sessions live in Firestore, escalations are Pub/Sub messages.
* Local mode (LOCAL_MODE=1): the same four tool handlers are imported from
  ../functions and invoked in-process, sessions live in a dict, escalations
  are logged and kept in memory. No Google Cloud project required. A Gemini
  API key is still needed for real conversations; the test-suite injects a
  scripted model instead.
"""
from __future__ import annotations

import importlib.util
import json
import logging
import os
import pathlib
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests
from flask import Flask, jsonify, request

from google.genai import types

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("shopnova")


# ── Configuration ─────────────────────────────────────────────────────────────

def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


LOCAL_MODE = _truthy(os.environ.get("LOCAL_MODE"))
PROJECT_ID = os.environ.get("PROJECT_ID", "your-project-id")
REGION = os.environ.get("REGION", "us-central1")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
ESCALATION_TOPIC = os.environ.get("ESCALATION_TOPIC", "shopnova-escalations")
MAX_TOOL_ITERATIONS = int(os.environ.get("MAX_TOOL_ITERATIONS", "5"))
TOOL_TIMEOUT_SECONDS = float(os.environ.get("TOOL_TIMEOUT_SECONDS", "10"))

TOOL_NAMES = ["get_order_status", "initiate_return", "issue_refund", "search_knowledge_base"]

# Deployed Cloud Functions live at https://<region>-<project>.cloudfunctions.net/<kebab-name>.
# Any tool can be pointed elsewhere with TOOL_URL_<NAME>, e.g. at functions-framework on localhost.
_BASE_URL = f"https://{REGION}-{PROJECT_ID}.cloudfunctions.net"
TOOL_URLS: Dict[str, str] = {
    name: os.environ.get(f"TOOL_URL_{name.upper()}", f"{_BASE_URL}/{name.replace('_', '-')}")
    for name in TOOL_NAMES
}

FUNCTIONS_DIR = pathlib.Path(__file__).resolve().parent.parent / "functions"


# ── Tool declarations for Gemini ──────────────────────────────────────────────
# These tell Gemini what tools exist and when to call them. Gemini emits a
# function_call part whenever it decides a tool is needed.

TOOLS = [
    types.Tool(function_declarations=[
        types.FunctionDeclaration(
            name="get_order_status",
            description=(
                "Get the current status of a customer order. "
                "Use when the customer asks about their order, tracking, or delivery."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "order_id": types.Schema(type=types.Type.STRING, description="Order ID, e.g. ORD-001"),
                },
                required=["order_id"],
            ),
        ),
        types.FunctionDeclaration(
            name="initiate_return",
            description="Start a return for a delivered or shipped order.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "order_id": types.Schema(type=types.Type.STRING),
                    "reason": types.Schema(type=types.Type.STRING, description="Reason for the return"),
                },
                required=["order_id", "reason"],
            ),
        ),
        types.FunctionDeclaration(
            name="issue_refund",
            description=(
                "Issue a refund for an order. "
                "Amounts over $200 require human approval (guardrail enforced server-side)."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "order_id": types.Schema(type=types.Type.STRING),
                    "approved": types.Schema(
                        type=types.Type.BOOLEAN,
                        description="Whether human approval has already been granted",
                    ),
                },
                required=["order_id"],
            ),
        ),
        types.FunctionDeclaration(
            name="search_knowledge_base",
            description="Search ShopNova help docs for policy, shipping, returns, and warranty information.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={"query": types.Schema(type=types.Type.STRING)},
                required=["query"],
            ),
        ),
    ])
]

SYSTEM_INSTRUCTION = (
    "You are ShopNova's AI support agent. Be helpful, concise, and empathetic. "
    "Only take actions your tools support. Escalate to a human if the request is "
    "out of scope or requires sensitive decisions. Always be truthful and policy-compliant."
)

CONFIG = types.GenerateContentConfig(system_instruction=SYSTEM_INSTRUCTION, tools=TOOLS)


# ── Tools: HTTP in the cloud, in-process locally ─────────────────────────────

_local_handlers: Dict[str, Callable] = {}
_local_flask = Flask("shopnova-local-tools")


def _load_local_handler(name: str) -> Callable:
    """Import functions/<name>/main.py once and return its HTTP handler."""
    if name not in _local_handlers:
        path = FUNCTIONS_DIR / name / "main.py"
        spec = importlib.util.spec_from_file_location(f"shopnova_fn_{name}", path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        _local_handlers[name] = getattr(module, name)
    return _local_handlers[name]


def _call_tool_in_process(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    handler = _load_local_handler(name)
    with _local_flask.test_request_context(json=args):
        from flask import request as local_request
        result = handler(local_request)
    body = result[0] if isinstance(result, tuple) else result
    return json.loads(body) if isinstance(body, (str, bytes)) else body


def _call_tool_http(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    response = requests.post(TOOL_URLS[name], json=args, timeout=TOOL_TIMEOUT_SECONDS)
    return response.json()


def call_tool(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Run a tool and return its JSON result. Errors come back as {'error': ...} so the model can react."""
    if name not in TOOL_NAMES:
        return {"error": f"Unknown tool: {name}"}
    try:
        return _call_tool_in_process(name, args) if LOCAL_MODE else _call_tool_http(name, args)
    except Exception as exc:  # noqa: BLE001 - surfaced to the model, not swallowed
        log.warning("tool %s failed: %s", name, exc)
        return {"error": str(exc)}


# ── Session memory: Firestore in the cloud, dict locally ─────────────────────

class InMemorySessionStore:
    def __init__(self) -> None:
        self._sessions: Dict[str, List[Dict[str, str]]] = {}

    def load(self, session_id: str) -> List[Dict[str, str]]:
        return list(self._sessions.get(session_id, []))

    def save(self, session_id: str, history: List[Dict[str, str]]) -> None:
        self._sessions[session_id] = list(history)


class FirestoreSessionStore:
    def __init__(self) -> None:
        from google.cloud import firestore  # imported lazily so local mode needs no credentials
        self._db = firestore.Client(project=PROJECT_ID)

    def load(self, session_id: str) -> List[Dict[str, str]]:
        doc = self._db.collection("agent_sessions").document(session_id).get()
        return doc.to_dict().get("history", []) if doc.exists else []

    def save(self, session_id: str, history: List[Dict[str, str]]) -> None:
        self._db.collection("agent_sessions").document(session_id).set(
            {"history": history, "updated_at": _now()}, merge=True
        )


# ── Escalation: Pub/Sub in the cloud, log + list locally ─────────────────────

class LoggingEscalator:
    def __init__(self) -> None:
        self.events: List[Dict[str, str]] = []

    def publish(self, event: Dict[str, str]) -> None:
        self.events.append(event)
        log.info("ESCALATION %s", json.dumps(event))


class PubSubEscalator:
    def __init__(self) -> None:
        from google.cloud import pubsub_v1  # lazy for the same reason as Firestore
        self._publisher = pubsub_v1.PublisherClient()
        self._topic = self._publisher.topic_path(PROJECT_ID, ESCALATION_TOPIC)

    def publish(self, event: Dict[str, str]) -> None:
        self._publisher.publish(self._topic, json.dumps(event).encode())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


session_store = InMemorySessionStore() if LOCAL_MODE else FirestoreSessionStore()
escalator = LoggingEscalator() if LOCAL_MODE else PubSubEscalator()


def escalate(session_id: str, message: str, reason: str) -> str:
    """Hand the conversation to a human and return the user-facing confirmation."""
    escalator.publish({
        "session_id": session_id,
        "customer_message": message,
        "reason": reason,
        "timestamp": _now(),
    })
    return "I've escalated your request to a human agent who will be in touch shortly."


# ── Gemini client (lazy) ──────────────────────────────────────────────────────

_gemini_client = None


def gemini_generate(contents: List[types.Content]):
    """Default model call. Created on first use so import never needs a key."""
    global _gemini_client
    if _gemini_client is None:
        if not GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY is not set; export it or inject a scripted model.")
        from google import genai
        _gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    return _gemini_client.models.generate_content(model=GEMINI_MODEL, contents=contents, config=CONFIG)


# Tests (and local experiments) can replace the model with any callable that
# takes `contents` and returns an object shaped like GenerateContentResponse.
GENERATE_OVERRIDE: Optional[Callable[[List[types.Content]], Any]] = None


# ── The agent loop ────────────────────────────────────────────────────────────

def run_agent_turn(
    message: str,
    history: List[Dict[str, str]],
    session_id: str,
    *,
    generate: Optional[Callable[[List[types.Content]], Any]] = None,
) -> Tuple[str, List[Dict[str, Any]]]:
    """Run one customer turn. Returns (final_response, tool_trace).

    Reason → act → observe, up to MAX_TOOL_ITERATIONS times. Any tool result that
    flags requires_approval / escalation_needed short-circuits to a human hand-off.
    """
    generate = generate or GENERATE_OVERRIDE or gemini_generate

    contents = [
        types.Content(role=turn["role"], parts=[types.Part(text=turn["content"])])
        for turn in history
    ]
    contents.append(types.Content(role="user", parts=[types.Part(text=message)]))

    trace: List[Dict[str, Any]] = []
    final_response = ""

    for _ in range(MAX_TOOL_ITERATIONS):
        response = generate(contents)
        candidate = response.candidates[0]
        parts = list(candidate.content.parts or [])

        function_calls = [p.function_call for p in parts if getattr(p, "function_call", None)]
        if not function_calls:
            final_response = next((p.text for p in parts if getattr(p, "text", None)), "")
            break

        # Keep the model's own tool-call turn in context so it sees its prior actions.
        contents.append(candidate.content)
        tool_results: List[types.Part] = []

        for fc in function_calls:
            args = dict(fc.args or {})
            result = call_tool(fc.name, args)
            trace.append({"tool": fc.name, "args": args, "result": result})

            # GUARDRAIL: a tool asking for human approval ends the automated turn.
            if result.get("requires_approval") or result.get("escalation_needed"):
                amount = result.get("refund_amount", 0) or 0
                final_response = escalate(session_id, message, f"Approval needed for {fc.name}: ${amount:.2f}")
                break

            tool_results.append(types.Part(function_response=types.FunctionResponse(
                name=fc.name, response={"result": result}
            )))

        if final_response:
            break

        if tool_results:
            contents.append(types.Content(role="user", parts=tool_results))

    if not final_response:
        final_response = "I'm unable to process your request right now. Let me connect you with a human agent."
        escalate(session_id, message, "Max agentic loop attempts reached")

    return final_response, trace


# ── Flask routes ──────────────────────────────────────────────────────────────

app = Flask(__name__)


@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    session_id = data.get("session_id") or str(uuid.uuid4())

    if not message:
        return jsonify({"error": "message required"}), 400

    history = session_store.load(session_id)
    try:
        final_response, trace = run_agent_turn(message, history, session_id)
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503

    history.append({"role": "user", "content": message})
    history.append({"role": "model", "content": final_response})
    session_store.save(session_id, history)

    return jsonify({"session_id": session_id, "response": final_response, "tool_calls": trace})


@app.route("/health", methods=["GET"])
def health():
    """Liveness probe for Cloud Run / load balancers."""
    return jsonify({
        "status": "healthy",
        "service": "shopnova-agent",
        "mode": "local" if LOCAL_MODE else "cloud",
        "model": GEMINI_MODEL,
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
