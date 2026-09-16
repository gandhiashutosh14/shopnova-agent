# Development notes

How the ShopNova agent was built, refined and verified. Where something did not happen or could
not be verified, this file says so.

## Original development

The ShopNova agent was written by Ashutosh Gandhi as the reference implementation for a
five-session 1:1 mentoring engagement on agentic AI with Google Cloud. The brief that drove it,
[`MENTORING_BRIEF.md`](MENTORING_BRIEF.md), was written first; the code followed its
architecture: a Flask orchestrator running the Gemini function-calling loop, four Cloud Functions
as tools, Firestore for memory and Pub/Sub for escalation. The brief and the code (four function
handlers, one orchestrator, about 500 lines) survive from that period.

## Refinement, 2026-09-16

### Planning

- The project was checked for client identifiers and secrets by grep on the staged copy; the only
  hit was the literal placeholder `your-project-id`.
- The plan called for: a fresh git history, a README with an architecture diagram, folding the
  mentoring brief in as documentation, a local run path with a Gemini key or mocks, and an honest
  proof-of-concept label.

### Iterations

1. Staged the project in a clean folder and created a virtual environment with
   `functions-framework 3.10.2`, `google-genai 2.23.0`, `google-cloud-firestore 2.30.0`,
   `google-cloud-pubsub 2.40.0`, `Flask 3.0.3`, `pytest 9.1.1`. No `gcloud` CLI on the machine.
2. Read the orchestrator and found that `firestore.Client(...)` and `pubsub_v1.PublisherClient()`
   were constructed at import time, so the module could not even be imported without Google Cloud
   credentials, let alone tested.
3. Rewrote `orchestrator/main.py` with the behaviour preserved and three structural changes:
   - `LOCAL_MODE=1` switches to an in-memory session store, a logging escalator, and in-process
     invocation of the four handlers imported from `functions/`.
   - Firestore, Pub/Sub and the Gemini client are created lazily on first use.
   - The model call is a parameter of `run_agent_turn`, with a module-level override hook, so tests
     can script the model.
   Also added: `TOOL_URL_<NAME>` overrides, a `tool_calls` trace in the `/chat` response, a `503`
   with a plain message when no key is configured, and `mode` in `/health`.
4. Wrote 18 tests. Function tests call each handler through a Flask test request context. Loop
   tests use a scripted model whose responses are built from the real `google.genai.types`
   classes (`GenerateContentResponse`, `Candidate`, `FunctionCall`, `FunctionResponse`), so a
   change in the SDK's shapes would break the tests rather than only production.
5. Added `.gitignore`, MIT `LICENSE`, `.env.example`, `requirements-dev.txt`, `scripts/deploy.sh`,
   a GitHub Actions workflow that runs the tests, the README, and this document.

### Debugging

The refactor and both test files passed on the first run. The one thing worth recording is a
design decision rather than a bug: in local mode, tool errors are returned as `{"error": ...}`
JSON exactly as the HTTP path returns them, so the model sees the same failure shape in both
modes and the tests for the loop are valid for production.

### Verification

| Check | Result |
|---|---|
| `pytest -q` (functions) | 10 passed in 0.20s |
| `pytest -q` (orchestrator) | 8 passed in 0.82s |
| `pytest -q` (all) | 18 passed in 0.64s |
| `functions-framework --source functions/get_order_status/main.py --target get_order_status --port 8081` + `curl` | correct JSON for ORD-002 |
| `LOCAL_MODE=1 python orchestrator/main.py`, `GET /health` | `{"mode":"local","status":"healthy",...}` |
| `POST /chat` with no `GEMINI_API_KEY` | `503 {"error":"GEMINI_API_KEY is not set; ..."}` |

**Not verified yet:** a live Gemini conversation (no API key was available), the Firestore and
Pub/Sub code paths, and `scripts/deploy.sh` (no `gcloud`). The README says so.

### What changed and what did not

Changed: the orchestrator's structure (modes, lazy clients, injectable model, trace, error
handling). Unchanged: the four function handlers, the tool declarations, the system instruction,
the five-iteration limit, and the guardrail semantics.

### Outcome

Published to https://github.com/gandhiashutosh14/shopnova-agent as a public repository with a
fresh history: application code, tests, then documentation and packaging.
