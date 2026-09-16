# Agentic Support Assistant on Google Cloud
### Project Brief & Hands-On Learning Roadmap

> This is the original brief the ShopNova agent was built from. It was written by the repository
> author for a 1:1 mentoring engagement, and is kept here unedited (apart from this note) because
> it documents the business-objective-to-architecture reasoning behind the code.

**Prepared for:** a mentee (name withheld)
**Engagement:** 1:1 mentoring · ~5 sessions (≈5 hrs) · Beginner → Intermediate · Remote
**Focus:** Designing, architecting, and deploying an Agentic AI solution on GCP — *Vertex AI · Cloud Functions · Pub/Sub*

---

## 1. What this document is

This is the project we'll build together, end to end. It's deliberately structured the way a real cloud/AI engagement runs: we start from a business problem, translate it into technical requirements, design the architecture, build it on Google Cloud, and deploy it.

By the end you'll have a working agentic AI system you can demo and put on your portfolio — and, more importantly, a **repeatable method** for taking *any* business objective and turning it into a deployed GCP solution.

If you only have a few minutes before our first session, skim **Sections 9 and 10**. That's all the prep you need.

---

## 2. The business scenario

**Company:** *ShopNova* — a mid-sized online retailer (fictional, so we can move fast without legal/data hassle).

**The problem:** Their support team is drowning in repetitive Tier-1 tickets — *"Where's my order?"*, *"How do I return this?"*, *"Has my refund gone through?"*. Human agents spend most of their day on these instead of complex issues. Response times are slow, it's expensive, and there's no 24/7 coverage.

**What they want:** An AI *agent* (not just a chatbot) that can understand a customer's request, look things up, take real actions — check an order, start a return, issue a refund within policy — and hand off cleanly to a human when it's out of its depth.

This is an ideal first agentic project because it forces us to use every core building block: reasoning, tool use, memory, event-driven actions, guardrails, and human-in-the-loop.

---

## 3. Business objectives & success metrics

| Goal | Why it matters | Example target |
|---|---|---|
| Deflect repetitive tickets | Free human agents for complex work | 50–60% of Tier-1 tickets resolved without a human |
| Faster responses | Better customer experience | First response in seconds, 24/7 |
| Lower cost per ticket | Direct savings | ~40% reduction on automated tickets |
| Maintain quality & trust | No wrong refunds, no made-up answers | 100% policy-compliant actions; grounded answers only |
| Visibility | Know what the agent is doing | Every action logged and traceable |

We'll keep coming back to these. In real projects, the architecture exists to serve the metrics — not the other way around.

---

## 4. From business objectives → technical requirements

This is the skill you said you most want to learn, so we'll do it **explicitly**. Every business need maps to something technical:

| Business need | Technical requirement |
|---|---|
| "Understand any customer message" | LLM for intent + entity extraction (Gemini on Vertex AI) |
| "Look things up" | Tools that query order data + a knowledge base (RAG) |
| "Take real actions" | Callable functions (Cloud Functions) with clear contracts |
| "Decide *what* to do" | An agent reasoning loop with function calling |
| "Remember the conversation" | Session / state store (Firestore) |
| "Hand off to a human" | Event-driven escalation (Pub/Sub → ticket / Slack / email) |
| "Never break policy" | Guardrails + an approval step before sensitive actions |
| "24/7, handle spikes" | Serverless, autoscaling (Cloud Run + Cloud Functions) |
| "Be secure" | IAM, Secret Manager, PII handling (DLP API) |
| "Be observable" | Cloud Logging, Trace, Monitoring |

**Non-functional requirements** we'll design for: low latency (a few seconds), scalability, security & PII compliance, cost-efficiency, and observability.

---

## 5. Solution architecture

High-level flow on Google Cloud:

```
              Customer (chat / email)
                       │
                       ▼
        ┌───────────────────────────┐      ┌───────────────────────────┐
        │   Agent Orchestrator       │◄────►│   Vertex AI – Gemini       │
        │   (Cloud Run service)      │      │   reasoning + function     │
        │   = the agent loop         │      │   calling                  │
        └────────────┬──────────────┘      └───────────────────────────┘
                     │ calls tools                      ▲
                     ▼                                  │ results
        ┌─────────────────────────────────────┐        │
        │   Tools  =  Cloud Functions          │────────┘
        │   • get_order_status(order_id)       │
        │   • initiate_return(order_id, reason)│
        │   • issue_refund(order_id) [+approval]│
        │   • search_knowledge_base(query) ────┼──► Vertex AI Search (RAG over help docs)
        └────────────┬─────────────────────────┘
                     │ reads / writes
          ┌──────────┴───────────┐
          ▼                      ▼
   ┌──────────────┐      ┌──────────────────┐
   │  Firestore    │      │  BigQuery         │
   │  memory/state │      │  orders + logs    │
   └──────────────┘      └──────────────────┘

                     │ "human needed" / async events
                     ▼
              ┌──────────────┐
              │   Pub/Sub     │──► Escalation handler → Slack / email / ticketing
              └──────────────┘

   Cross-cutting:  IAM · Secret Manager · DLP (PII) · Cloud Logging / Trace / Monitoring
```

**Why each service:**

- **Vertex AI (Gemini)** — the "brain." Understands the request and decides which tools to call via *function calling*.
- **Cloud Run** — hosts the agent orchestrator (the loop). Serverless, scales to zero, easy to deploy.
- **Cloud Functions** — each capability is one small function with a clear input/output contract: easy to test, secure, and reuse.
- **Vertex AI Search** — grounds answers in ShopNova's real help docs (RAG) so the agent doesn't invent things.
- **Pub/Sub** — decouples slow or human steps (escalations, notifications) from the live conversation, so the system stays responsive and event-driven.
- **Firestore** — short-term memory and session state.
- **BigQuery** — order data plus a log of every interaction, for analytics and evaluation.

> **Managed alternative:** GCP also offers **Vertex AI Agent Builder / Agent Engine**, which packages a lot of this for you. We'll build the core ourselves first (so you understand what's under the hood), then look at where the managed option saves work.

---

## 6. How the agent actually "thinks" — the agent loop

What makes this *agentic* (not a scripted bot) is the loop:

1. **Perceive** — receive the customer message + recent history.
2. **Reason / Plan** — Gemini decides: do I have enough info? Which tool do I call next?
3. **Act** — the orchestrator calls the chosen Cloud Function.
4. **Observe** — feed the function's result back to Gemini.
5. **Repeat** — loop steps 2–4 until the task is done. *(This is what lets it handle multi-step requests like "return this **and** refund me.")*
6. **Respond or Escalate** — reply to the customer, or publish an escalation event.
7. **Remember** — persist the outcome to Firestore.

This is the **ReAct pattern** (Reason + Act). Once you've built it once, every other agent you build is a variation of this same loop.

---

## 7. Build & learning roadmap (~5 sessions, ~1 hr each)

**Session 1 — Foundations & setup**
Core concepts (agentic AI vs chatbot, the agent loop, function calling vs RAG vs fine-tuning). Set up the GCP project, enable APIs, IAM basics, and make our first call to Gemini on Vertex AI.

**Session 2 — Objective → design** *(the part you care about most)*
We take ShopNova's goals and, together, derive the requirements, choose the services, define the tool contracts, and model the data. You leave with the architecture diagram drawn by *you*.

**Session 3 — Build the tools + knowledge base**
Write 2–3 Cloud Functions (`get_order_status`, `initiate_return`, …) and stand up a small RAG knowledge base with Vertex AI Search. Test each tool on its own.

**Session 4 — Build the agent**
Wire Gemini's function calling to the tools, implement the agent loop, add Firestore memory, and add a Pub/Sub escalation path.

**Session 5 — Deploy, guardrail, observe**
Deploy on Cloud Run. Add guardrails (policy checks + an approval step before refunds), PII handling, logging/monitoring, a simple evaluation, and cost tips. Then: how to extend and productionize.

> Pace is flexible — if a topic needs more time, we slow down. The schedule serves you, not the reverse.

---

## 8. What you'll walk away with

- A **deployed, working** agentic support assistant on GCP you can demo live.
- The code (orchestrator + Cloud Functions) and the architecture diagram.
- A reusable mental model: **business goal → requirements → architecture → deploy.**
- Real confidence with Vertex AI / Gemini, Cloud Functions, Pub/Sub, Firestore, and Cloud Run.

---

## 9. Before our first session — quick setup

You don't need to install much; **Cloud Shell** handles most of it. Ideally have ready:

- A **Google Cloud account with billing enabled** (new accounts get free credits — more than enough for this project).
- A **GCP project** created (we'll configure it together).
- Comfort running a few **Python** snippets (you don't need to be an expert).
- Either **Cloud Shell** (zero install, recommended) or local `gcloud` CLI + VS Code.

We'll enable these APIs together in Session 1: *Vertex AI, Cloud Functions, Pub/Sub, Firestore, Cloud Run, Cloud Build.*

> Tip: if you create the GCP account + project before we meet, we get to the fun part faster.

---

## 10. Get up to speed — 20-minute skim list

These are the only concepts worth glancing at beforehand. Don't study them deeply — we'll cover them properly together. Just get the *vocabulary* into your head:

- **Agentic AI** — an LLM that pursues a goal over multiple steps using tools, vs a chatbot that just replies. Keywords: *autonomy, tool use, planning.*
- **The agent loop / ReAct** — reason → act → observe → repeat.
- **Function calling (tool use)** — how an LLM is given a menu of functions and decides which to call, with what arguments.
- **RAG (Retrieval-Augmented Generation)** — giving the model relevant documents so its answers are grounded, not invented.
- **Vertex AI & Gemini** — Google's managed AI platform and model family.
- **Supporting services, one line each** — Cloud Functions (run small bits of code), Pub/Sub (messaging between components), Firestore (NoSQL store), Cloud Run (run containers, serverless).
- **Guardrails / responsible AI** — keeping the agent grounded, policy-compliant, and safe.

*Optional, if curious:* Vertex AI Agent Builder, and orchestration frameworks like **LangGraph** or Google's **Agent Development Kit (ADK)**.

---

## 11. One-line service cheat-sheet

| Service | Role in our project |
|---|---|
| Vertex AI (Gemini) | Reasoning + function calling (the brain) |
| Cloud Run | Hosts the agent orchestrator |
| Cloud Functions | The agent's tools / actions |
| Vertex AI Search | RAG knowledge base |
| Pub/Sub | Event-driven escalations & async tasks |
| Firestore | Conversation memory / state |
| BigQuery | Order data + interaction logs |
| IAM / Secret Manager / DLP | Access control, secrets, PII protection |
| Cloud Logging / Trace / Monitoring | Observability |

---

## 12. Where this goes next (stretch goals)

Once the core works, natural extensions: multi-channel (WhatsApp, voice), multiple cooperating agents (a "router" agent + specialists), proactive agents triggered by events (e.g. a delayed-shipment alert), automated evaluation pipelines, and CI/CD for safe deploys. Good fuel for an intermediate follow-on.

---

*This brief uses a fictional company so we can focus on the engineering. We can easily swap in a domain you actually care about — finance, healthcare, logistics, your own startup idea — and the architecture barely changes. Tailoring it is great practice in itself.*
