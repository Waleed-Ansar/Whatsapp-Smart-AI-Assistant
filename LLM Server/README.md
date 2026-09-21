# WhatsApp Smart AI Assistant - LLM Server

An asynchronous **FastAPI** service that turns a WhatsApp business number into a
self-driving **RTD Advisor copilot** for real estate agents.

The service listens to the WhatsApp Cloud API webhook, debounces rapid-fire
messages from a client, decides through an LLM "Gatekeeper" whether the
conversation now contains a complete, actionable report request, and only when it
does, executes the matching tool on a remote **MCP tool server** and replies with
the raw report payload.

---

## Table of Contents

- [What This Project Does](#what-this-project-does)
- [Feature Overview](#feature-overview)
- [Architecture](#architecture)
- [Request Lifecycle](#request-lifecycle)
- [Module Reference](#module-reference)
- [API Endpoints](#api-endpoints)
- [Data Stores and Key Layout](#data-stores-and-key-layout)
- [Configuration](#configuration)
- [Setup and Running](#setup-and-running)
- [Design Details Worth Knowing](#design-details-worth-knowing)
- [Known Limitations and Follow-Ups](#known-limitations-and-follow-ups)

---

## What This Project Does

A real-estate agent chats with a client on WhatsApp. The client describes what
they want ("give me property details for Citi Housing, Hall Road Lahore"). This
server silently reads that conversation, understands that a real estate report is
now warranted, runs the correct backend tool, and posts the report back into the
same WhatsApp thread, with no slash commands, no buttons, and no explicit
"please generate a report" instruction required from the user.

It is deliberately split into two brains:

1. **Gatekeeper** answers *should anything happen right now, and if so, which tool
   with which arguments?*
2. **Executor** answers *run exactly that and give me the raw output.* It is
   forbidden from re-interpreting the user.

---

## Feature Overview

### Conversational intelligence

- **Silent intent detection.** A LangGraph-orchestrated LLM inspects the latest
  message plus recent history and returns a structured decision: is a report
  warranted, which tool, which parameters, which fields are missing.
- **No magic keyword required.** The user never has to type "RTD report". If a
  message maps to an available MCP tool with all required parameters present, it
  triggers.
- **Chit-chat suppression.** Greetings, "thanks", "ok", and general small talk
  return `intended_action = null` so the assistant stays quiet.
- **Duplicate-report prevention.** If a report was already produced for the same
  property or project, merely mentioning it again does not regenerate it. Repeat
  generation requires an explicit ask ("regenerate", "run it again", "repeat that
  report").
- **Follow-up question handling.** "What is the rental yield?" after a report is
  treated as a question, not as a request to rebuild the report.
- **Per-conversation memory.** Conversation state is persisted in a Redis-backed
  LangGraph checkpointer, keyed per agent phone number and per client phone
  number, so the Gatekeeper retains context across restarts.

### Message ingestion and reliability

- **Webhook verification** for Meta's `hub.mode` / `hub.verify_token` /
  `hub.challenge` handshake.
- **Idempotent event handling.** Every inbound WhatsApp message ID is marked in
  Redis for 5 minutes, so Meta's webhook retries cannot double-process a message.
- **Smart reply (swipe/quote) resolution.** When a client replies to an earlier
  message, the quoted text is resolved from Redis, falling back to MongoDB, and
  prepended to the message as `[In reply to: "..."]`. Resolved quotes are
  re-cached for 48 hours so the Gatekeeper can extract parameters from them.
- **Typing debounce and message aggregation.** Rapid consecutive messages from
  the same client are buffered in Redis and flushed only after a short idle
  period (default 10s), then concatenated into one aggregated prompt. This
  prevents half-finished thoughts from triggering premature reports.
- **Single monitor per sender.** An in-process registry ensures only one debounce
  monitor task runs per phone number at any time.
- **Background processing.** The dispatch flow runs in a FastAPI
  `BackgroundTasks` worker so Meta always receives a fast `200 OK`.

### Execution and delivery

- **MCP tool federation.** Tools are discovered at runtime from a remote MCP
  server over HTTP with Bearer authentication; no tool list is hardcoded here.
- **Live tool schema injection.** The Gatekeeper fetches each MCP tool's real
  required-argument list and injects it into its system prompt, so the LLM judges
  "completeness" against the actual tool contract. Schemas are cached in memory
  after first load, with a graceful textual fallback if MCP is unreachable.
- **Strict executor contract.** The ReAct executor takes the Gatekeeper's JSON
  decision as its only input and returns the tool's raw payload: no
  conversational filler, no opinions, no re-analysis.
- **Pretty-printed dispatch.** Report JSON is reformatted with indentation before
  being sent, so it reads cleanly inside WhatsApp.
- **Full text round-trip persistence.** Both inbound user messages and outbound
  assistant messages (with their WhatsApp message IDs) are archived.

### Operations and onboarding

- **Health endpoint** that proxies a live authenticated check against the MCP
  server.
- **Bundled onboarding page** (`index.html`) implementing Meta's Embedded Signup
  OAuth dialog to obtain an authorization code for a business number.
- **CORS enabled** for all origins, methods, and headers.
- **Self-healing MongoDB indexes**, including a TTL index that expires chat
  windows after 7 days.

---

## Architecture

```
                         WhatsApp Cloud API
                                |
                    (1) GET / POST /webhook
                                v
    +-----------------------------------------------------------+
    |                      main.py  (FastAPI)                   |
    |  verify_webhook | receive_whatsapp_event | / | /health     |
    +---------------------------+-------------------------------+
                                |
             (2) dedupe: redis "processed:{msg_id}"
             (3) resolve quoted reply: redis -> MongoDB -> re-cache
             (4) persist to MongoDB 24h window
             (5) buffer + debounce lock in Redis
                                |
    +---------------------------v-------------------------------+
    |            redis_manager.py  (RedisSessionManager)        |
    |  buffer:{phone}   lock:{phone}   session:{phone}          |
    |  monitor_typing_lock()  <-- polls 1s until lock expires   |
    +---------------------------+-------------------------------+
                                |  aggregated_text
                                v
    +-----------------------------------------------------------+
    |              services.py  process_gatekeeper_flow()       |
    +------------+----------------------------------+-----------+
                 |                                  ^
     (6) evaluate(agent_id, phone, text)     (9) raw report output
                 v                                  |
    +-------------------------------+   +-----------+-----------+
    |        gatekeeper.py          |   |       llm.py          |
    |  LangGraph StateGraph (1 node)|   |  create_react_agent   |
    |  ChatOpenAI + structured out  |   |  + MCP tools          |
    |  Redis AsyncRedisSaver        |   |  (MemorySaver)        |
    |  thread: agent:{id}:client:{} |   +-----------+-----------+
    +---------------+---------------+               |
                    | IntentDecision                | HTTPS + Bearer
                    v                               v
            is_ready / intended_action      Remote MCP Tool Server
            / action_parameters             (RTD reporting tools)
                    |
          (7) not ready -> stay silent (return)
                    |
          (8) ready -> llm_server.serve(decision, chat_id)
                    |
         (10) send_whatsapp_message() -> graph.facebook.com/v21.0
                    |
         (11) store bot wamid in Redis + MongoDB
```

---

## Request Lifecycle

1. **Handshake.** Meta calls `GET /webhook` with `hub.mode=subscribe`, the
   configured verify token, and a challenge. The challenge is echoed back
   verbatim when the token matches; otherwise `403`.
2. **Inbound event.** Meta `POST`s a payload to `/webhook`. The handler digs into
   `entry[0].changes[0].value.messages[0]` and ignores payloads with no messages
   (delivery receipts, status updates).
3. **Deduplication.** `processed:{msg_id}` is checked and set with a 300-second
   TTL. A repeat returns `{"status": "already_processed"}`.
4. **Text extraction.** Only `type == "text"` messages are processed.
5. **Quote resolution.** If a `context.id` is present, the quoted message body is
   fetched from `msg:{wamid}` in Redis, then MongoDB, then re-cached for 48h, and
   prepended to the text as `[In reply to: "..."]`.
6. **Persistence.** The full text is cached at `msg:{msg_id}` (48h) and appended
   to the client's active 24-hour MongoDB window.
7. **Debounce.** `stack_incoming_message()` pushes the text onto `buffer:{phone}`
   and sets `lock:{phone}` with a 10-second TTL. A `monitor_typing_lock`
   background task is started only if the phone is not already being monitored.
8. **Aggregation.** Once per second the monitor checks whether the lock has
   expired. When it has, the whole buffer is drained, joined with newlines,
   appended to session history, and passed to the dispatch callback.
9. **Gatekeeper evaluation.** A LangGraph node builds a system prompt containing
   the current date and time, the live MCP tool schemas, ten messages of prior
   history, and a seven-rule policy. `ChatOpenAI` is called with
   `.with_structured_output(IntentDecision, method="function_calling")` and
   returns a typed `IntentDecision`.
10. **Gate.** If `is_ready` is false or `intended_action` is null, the flow logs
    `[COPILOT SILENT]` and returns without replying.
11. **Execution.** `llm_server.serve()` serializes the decision to JSON and
    invokes a ReAct agent that calls the named MCP tool with exactly the given
    `action_parameters` and returns the raw result.
12. **Formatting and send.** The output is parsed and re-serialized with
    `indent=2` (falling back to the raw string if it is not JSON) and posted to
    the WhatsApp Graph API.
13. **Assistant archival.** On a successful send, the returned bot `wamid` is
    cached at `msg:{bot_wamid}` for 48h and stored in MongoDB as an `assistant`
    turn.

---

## Module Reference

| Module | Purpose | Key components | Primary use case |
| --- | --- | --- | --- |
| `main.py` | FastAPI entry point and WhatsApp webhook surface | `app`, `lifespan`, `verify_webhook`, `receive_whatsapp_event`, `health_check`, `resolve_quoted_message` | Owns the HTTP edge, boots MongoDB indexes and the Redis checkpointer, dedupes events, resolves swipe-replies, and hands work to the debouncer |
| `services.py` | Business orchestration layer | `send_whatsapp_message`, `process_gatekeeper_flow` | The conductor: asks the Gatekeeper for a decision, decides whether to stay silent, runs the executor, formats the report, and delivers it to WhatsApp |
| `gatekeeper.py` | Reasoning gate and intent classifier | `Gatekeeper` class, `_gatekeeper_node`, `_get_tool_schemas`, `evaluate`, singleton `gatekeeper_agent` | Determines whether and what to execute. Implements the seven-rule policy around chit-chat, duplicate reports, explicit regeneration, follow-up questions, new parameters, and quoted messages |
| `llm.py` | Tool executor | `LLMServer` class, `_get_or_create_agent`, `get_mcp_tools`, `serve`, singleton `llm_server` | Lazily builds a `create_react_agent` wired to the remote MCP server and executes the Gatekeeper's routing decision verbatim, returning raw tool output only |
| `models.py` | Shared Pydantic contracts | `IntentDecision`, `GatekeeperState`, `RequestModel`, `ResponseModel` | Defines the structured decision schema the LLM must fill, and the LangGraph state shape (`MessagesState` plus `decision`) passed between nodes |
| `config.py` | Environment configuration | `Config`, `REDIS_URL` property, singleton `config` | Centralizes every credential and endpoint from `.env` behind one importable object, including a username/password-aware Redis URL builder |
| `database.py` | Cold storage for chat windows | `DatabaseManager`, `setup_indexes`, `save_message_to_window`, `get_message_by_wamid`, `get_recent_messages`, singleton `db_manager` | Durably stores 24-hour conversation windows in MongoDB so quoted-message lookups and history survive Redis eviction and process restarts |
| `redis_manager.py` | Hot state, debouncing, sessions | `RedisSessionManager`, `stack_incoming_message`, `get_and_clear_buffer`, `monitor_typing_lock`, `append_message`, `get_conversation_history`, `rehydrate_session`, singleton `redis_manager` | Provides the sub-second hot path: message buffering, typing locks, per-phone session lists, and the inactivity monitor that decides when a burst of messages is done |
| `index.html` | Meta onboarding UI | OAuth dialog URL builder, redirect-parameter parser | Served at `/`; lets an operator launch Meta's Embedded Signup flow and read back the returned authorization code (or error) needed to mint a long-lived WhatsApp token |
| `requirements.txt` | Dependency manifest | n/a | Declares the runtime stack (see the dependency table below) |

### Dependency and library usage

| Library / module | Used in | Why it is here |
| --- | --- | --- |
| `fastapi` | `main.py` | HTTP framework: routing, query parsing, `BackgroundTasks`, CORS middleware, `FileResponse`, lifespan events |
| `uvicorn` | runtime | ASGI server that hosts the FastAPI app |
| `pydantic` | `models.py` | Declarative validation of the LLM decision structure and API models |
| `langgraph` | `gatekeeper.py`, `llm.py` | `StateGraph` for the gate node, `MessagesState` for graph state, `create_react_agent` for the tool-calling executor |
| `langgraph.checkpoint.redis.aio.AsyncRedisSaver` | `main.py`, `gatekeeper.py` | Async Redis-backed graph checkpointer giving the Gatekeeper durable per-conversation memory |
| `langgraph.checkpoint.memory.MemorySaver` | `llm.py` | In-memory checkpointer for the executor agent, keyed by `rtd_{chat_id}` |
| `langchain_openai.ChatOpenAI` | `gatekeeper.py`, `llm.py` | Points at any OpenAI-compatible endpoint; used for structured intent decisions and tool calling |
| `langchain_mcp_adapters.client.MultiServerMCPClient` | `llm.py` | Discovers remote MCP tools and exposes them as LangChain tools over HTTP with Bearer auth |
| `langchain_core.messages` | `gatekeeper.py` | `SystemMessage` / `HumanMessage` prompt construction |
| `langchain_core.runnables.RunnableConfig` | `llm.py` | Types the per-conversation thread configuration passed to the agent |
| `httpx` | `main.py`, `services.py` | Async HTTP client for the MCP health check and the WhatsApp Graph API send call |
| `redis` (`redis.asyncio`) | `redis_manager.py` | Async Redis client with pipelines for buffers, locks, sessions, dedupe flags, and wamid caches |
| `motor` (`AsyncIOMotorClient`) | `database.py` | Async MongoDB driver for 24-hour chat windows and wamid-indexed lookups |
| `pymongo.errors.OperationFailure` | `database.py` | Detects TTL-index conflicts (code 85) and performs a self-healing index update |
| `python-dotenv` | `config.py` | Loads `.env` into the process environment |
| `asyncio` | `llm.py`, `redis_manager.py` | Initialization locking and the 1-second debounce polling loop |
| `passlib` | declared only | Present in `requirements.txt`; not referenced by any current module, reserved for credential hashing |

The declared `langchain`, `langchain-classic`, and `langchain-community` packages
are not imported directly; the code imports from `langchain_core`,
`langchain_openai`, `langchain_mcp_adapters`, and `langgraph`.

---

## API Endpoints

| Method | Path | Description | Response |
| --- | --- | --- | --- |
| `GET` | `/` | Serves the Meta WhatsApp onboarding page | `index.html` |
| `GET` | `/health` | Authenticated `GET` against `SERVER_HEALTH_CHECK_URL` using `MCP_SERVER_API_KEY` (5s timeout) | `{"status": <code>, "message": <json>}` or `{"status": 500, "error": "..."}` |
| `GET` | `/webhook` | Meta webhook verification handshake (`hub.mode`, `hub.verify_token`, `hub.challenge`) | Echoes the challenge as `text/plain`, or `403 Verification failed` |
| `POST` | `/webhook` | Inbound WhatsApp events | `{"status": "success"}` or `{"status": "already_processed"}` |

---

## Data Stores and Key Layout

### Redis

| Key pattern | Type | TTL | Purpose |
| --- | --- | --- | --- |
| `processed:{wamid}` | String | 300s | Inbound dedupe guard against webhook retries |
| `msg:{wamid}` | String | 172800s (48h) | Full text of any message (user or bot) for quoted-reply resolution |
| `buffer:{phone}` | List | cleared on read | Debounce buffer of un-aggregated inbound messages |
| `lock:{phone}` | String | 10s | Typing lock; its expiry signals the user has stopped typing |
| `session:{phone}` | List of JSON | 900s | Rolling per-client conversation history (`{"role", "content"}`) |

LangGraph also maintains its own keys through the `AsyncRedisSaver` checkpointer,
keyed by thread ID `agent:{PHONE_NUMBER_ID}:client:{client_phone}`.

Phone numbers are normalized with `"".join(filter(str.isdigit, phone))` before
being used in a key, so formatting differences cannot fragment a session.

### MongoDB

Collection: `CHAT_COLLECTION_NAME` inside database `DB_NAME`.

```jsonc
{
  "number": "923001234567",          // client phone
  "created_at": "ISODate",           // TTL anchor (7 days)
  "expires_at": "ISODate",           // conversation window end (24 hours)
  "messages": [
    {
      "role": "user",                // "user" | "assistant"
      "content": "text body",
      "created_at": "ISODate",
      "wamid": "wamid.HBg..."        // optional
    }
  ]
}
```

Indexes created at startup by `setup_indexes()`:

- `created_at` ascending with `expireAfterSeconds = 604800` (7-day TTL). A
  pre-existing conflicting TTL index is dropped and recreated automatically.
- Compound `(number, expires_at)` for window lookups.
- Sparse `messages.wamid` for quoted-reply lookups.

`CRED_COLLECTION_NAME` is exposed as `db_manager.tenants` for tenant or credential
storage; no read or write paths use it yet.

---

## Configuration

All configuration is read from a `.env` file in the project root by `config.py`.
Never commit `.env`; it holds live credentials.

```dotenv
# LLM (any OpenAI-compatible endpoint)
LLM_API_URL=https://your-llm-endpoint/v1
LLM_API_KEY=sk-xxxxxxxxxxxxxxxxxxxx
LLM_MODEL_NAME=deepseek-chat

# WhatsApp Cloud API
WHATSAPP_VERIFY_TOKEN=your_webhook_verify_token
WA_ACCESS_TOKEN=EAAG...
PHONE_NUMBER_ID=123456789012345
RECIPIENT_PHONE=923001234567

# MCP tool server
MCP_SERVER_URL=https://your-mcp-server/mcp
MCP_SERVER_API_KEY=your_mcp_bearer_token
SERVER_HEALTH_CHECK_URL=https://your-mcp-server/health

# Redis
REDIS_HOST=your-redis-host
REDIS_PORT=6379
REDIS_USERNAME=default
REDIS_PASSWORD=your_redis_password

# MongoDB
MONGODB_URI=mongodb+srv://user:pass@cluster.mongodb.net
DB_NAME=rtd_assistant
CHAT_COLLECTION_NAME=chat_windows
CRED_COLLECTION_NAME=credentials
```

Notes:

- `PHONE_NUMBER_ID` serves double duty: it is the WhatsApp sender ID and the
  `agent_id` used to scope Gatekeeper threads.
- `RECIPIENT_PHONE` is read into config but not used in the send path.
- Redis credentials are optional; the code builds a URL with username plus
  password, password only, or no auth accordingly.
- `MCP_SERVER_TRANSPORT = "sse"` is declared in `config.py`, but the MCP client
  is actually constructed with `"transport": "http"` in `llm.py`.

### Tunable constants

| Constant | Location | Default | Effect |
| --- | --- | --- | --- |
| `debounce_ttl` | `redis_manager.RedisSessionManager` | 10 seconds | Idle time after the last message before the burst is dispatched |
| `default_ttl` | `redis_manager.RedisSessionManager` | 900 seconds | Session history lifetime |
| `processed:{id}` TTL | `main.py` | 300 seconds | Retry-dedupe window |
| `msg:{id}` TTL | `main.py`, `services.py` | 172800 seconds (48h) | Quoted-reply cache lifetime |
| Conversation window | `database.py` | 24 hours | Length of an active chat window |
| Document TTL | `database.py` | 604800 seconds (7 days) | Retention before MongoDB auto-deletes |
| History depth | `gatekeeper.py` | last 10 messages | Context fed to the Gatekeeper |
| LLM timeout / retries | `llm.py` | 60s / 5 | Executor API resilience |

---

## Setup and Running

### Prerequisites

- Python 3.10+ (the code uses `str | None` union syntax in `models.py`)
- A Redis instance (Redis Cloud, Upstash, or local)
- A MongoDB deployment (Atlas or local)
- A WhatsApp Business Cloud API app with a verified webhook URL
- An OpenAI-compatible LLM endpoint
- A reachable MCP tool server exposing the RTD reporting tools

### Install

```bash
python -m venv .venv

# Windows (PowerShell)
.venv\Scripts\Activate.ps1

# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

### Configure

Create `.env` in the project root using the template above, and make sure `.env`
is git-ignored.

### Run

```bash
uvicorn main:app --host 0.0.0.0 --port 8000

# development with reload
uvicorn main:app --reload --port 8000
```

On boot you should see, in order:

```
Booting up: Setting up MongoDB Indexes...
MongoDB indexes verified and ready for swipe-reply lookups.
[LANGGRAPH] Connecting to Redis...
[LANGGRAPH] Redis checkpointer created
[LANGGRAPH] Redis checkpointer setup complete
[GATEKEEPER] LangGraph + Redis initialized
[LANGGRAPH] Gatekeeper initialized
```

### Expose and connect

1. Expose port 8000 publicly (ngrok, Cloudflare Tunnel, or a real host).
2. In the Meta app dashboard, set the webhook callback to
   `https://<your-domain>/webhook` with the verify token from `.env`, and
   subscribe to the `messages` field.
3. Open `https://<your-domain>/` and run the onboarding flow to obtain an
   authorization code, exchange it for a permanent access token, and place it in
   `WA_ACCESS_TOKEN`.
4. Send a WhatsApp message to your business number and watch the logs for
   `[COPILOT OBSERVER]` and `RTD GATEKEEPER DECISION`.

### Useful local checks

```bash
curl http://localhost:8000/health
curl "http://localhost:8000/webhook?hub.mode=subscribe&hub.verify_token=YOUR_TOKEN&hub.challenge=12345"
```

---

## Design Details Worth Knowing

**Two LLMs instead of one.** Separating intent detection from execution turns one
fuzzy problem into two crisp ones. The Gatekeeper carries the nuanced policy (do
not re-trigger reports, ignore chit-chat, read quoted messages) and emits a small
typed object. The executor then has a job it cannot easily get wrong: one tool,
exact arguments, raw output. This also keeps the expensive policy prompt free of
tool-calling state.

**State lives in the checkpointer.** `evaluate()` invokes the graph with only the
latest human message. Prior history is not passed in manually; the
`AsyncRedisSaver` checkpointer restores the accumulated `messages` list for the
thread, and the node reads back the last ten entries for context. That is why
thread identity (`agent:{agent_id}:client:{phone}`) matters: it is the memory
key.

**Three tiers of memory serve three different jobs.** Redis `session:*` lists
hold the short-lived hot conversation. The LangGraph checkpointer holds the
Gatekeeper's reasoning state. MongoDB holds the durable 24-hour window that
answers "what was that message the client replied to?", including across Redis
eviction. The `msg:{wamid}` cache exists purely because WhatsApp quotes messages
by ID, not by text.

**Debouncing is what makes the copilot feel smart.** Without it, a client typing
"I want details for... / Citi Housing / in Lahore" across three bubbles would be
evaluated three times, the first two with missing parameters. The typing lock
turns that burst into one complete request.

**MCP keeps the tool surface external.** The Gatekeeper asks the MCP server for
real tool schemas at runtime, and the executor receives the same tool objects. A
new reporting tool can be added on the MCP side without changing this codebase,
although the hardcoded fallback string in `_get_tool_schemas` should be updated
to match if MCP is unreachable at boot.

---

## Known Limitations and Follow-Ups

These are observations from the current code, not blockers:

- **In-process only monitor registry.** `redis_manager.active_monitors` is a
  Python `set`. Running multiple Uvicorn workers or replicas means a phone number
  can be monitored by two processes at once. A Redis-based lock would be the
  distributed fix.
- **Executor memory is in-memory.** The ReAct agent uses `MemorySaver`, so its
  thread context is lost on restart. The Gatekeeper, by contrast, survives
  restarts via Redis.
- **Hardcoded onboarding identifiers.** `index.html` contains a literal Meta
  `APP_ID`, `CONFIG_ID`, and a specific ngrok redirect URI. These should be
  templated from environment variables before production use.
- **Transport mismatch.** `MCP_SERVER_TRANSPORT = "sse"` in `config.py` is
  unused; `llm.py` hardcodes `"http"`. Pick one and drive both from config.
- **Unused surface.** `RequestModel`, `ResponseModel`,
  `get_recent_messages`, `get_conversation_history`, `rehydrate_session`,
  `has_active_session`, `delete_session`, `db_manager.tenants`, `RECIPIENT_PHONE`,
  and `MONGODB_USERNAME` / `MONGODB_PASSWORD` are defined but not wired into the
  live flow. They read as intended building blocks (session rehydration,
  multi-tenant credentials, an agent-facing REST API) that are not finished.
- **No signature verification on `/webhook`.** The POST handler trusts the
  payload contents and does not validate Meta's `X-Hub-Signature-256` header.
  Adding HMAC verification with the app secret would harden the endpoint.
- **Broad exception swallowing.** Both the webhook handler and the Gatekeeper
  catch bare `Exception` and continue. This keeps the service alive but can hide
  configuration errors; structured logging with severity levels would help.
- **Delivery-status events are ignored.** Only `messages` payloads are handled,
  so sent, delivered, and read receipts are not persisted.
