# WhatsApp Smart AI Assistant - LLM Server

An asynchronous **FastAPI** service that turns a WhatsApp business number into a
self-driving **RTD Advisor copilot** for real estate agents.

The service listens to the WhatsApp Cloud API webhook, accepts both **text** and
**voice-note** messages, debounces rapid-fire bursts from a client, decides
through an LLM "Gatekeeper" whether the conversation now contains a complete,
actionable report request, and only when it does, executes the matching tool on
a remote **MCP tool server** and replies with the raw report payload.

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
they want, either by typing it ("give me property details for Citi Housing, Hall
Road Lahore") or by holding the mic button and saying it out loud. This server
silently reads or listens to that conversation, understands that a real estate
report is now warranted, runs the correct backend tool, and posts the report back
into the same WhatsApp thread, with no slash commands, no buttons, and no
explicit "please generate a report" instruction required from the user.

It is deliberately split into three brains, each with one job:

1. **Transcriber** turns a WhatsApp voice note into text.
2. **Gatekeeper** answers *should anything happen right now, and if so, which tool
   with which arguments?*
3. **Executor** answers *run exactly that and give me the raw output.* It is
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

### Message ingestion, text and voice

- **Webhook verification** for Meta's `hub.mode` / `hub.verify_token` /
  `hub.challenge` handshake.
- **Idempotent event handling.** Every inbound WhatsApp message ID is marked in
  Redis for 5 minutes, so Meta's webhook retries cannot double-process a message.
  This guard runs before the message type is inspected, so it protects voice
  notes too.
- **Type dispatch.** The webhook branches on `client_msg["type"]`: `text` is
  processed inline, `audio` is handed to the voice pipeline.
- **Smart reply (swipe/quote) resolution.** When a client replies to an earlier
  message, the quoted text is resolved from Redis, falling back to MongoDB, and
  prepended to the message as `[In reply to: "..."]`. Resolved quotes are
  re-cached for 48 hours so the Gatekeeper can extract parameters from them.
- **Typing debounce and message aggregation.** Rapid consecutive messages from
  the same client are buffered in Redis and flushed only after a short idle
  period (default 10s), then concatenated into one aggregated prompt. This
  prevents half-finished thoughts from triggering premature reports.
- **Unified buffer across modalities.** A voice note and typed text land on the
  same `buffer:{phone}` list and the same typing lock, so "here is what I want"
  typed in three bubbles plus a voice note become a single aggregated request.
- **Single monitor per sender.** An in-process registry ensures only one debounce
  monitor task runs per phone number at any time, whichever modality started it.

### Voice note transcription

- **Cloud speech-to-text.** Voice notes are transcribed with an
  OpenAI-Whisper-family model served through the Hugging Face Inference
  providers API (`hf-inference`), defaulting to `openai/whisper-large-v3`. No
  local model weights or GPU are required.
- **Media resolution and download.** The WhatsApp media ID from the webhook is
  exchanged for a short-lived CDN URL via the Graph API, then streamed down to
  local disk for the transcriber.
- **Transcript becomes first-class conversation text.** The transcript is cached
  under the original voice note's `wamid`, stored in MongoDB as a normal `user`
  turn, and pushed onto the debounce buffer - so everything downstream (quote
  resolution, history, Gatekeeper, report dispatch) treats it exactly like typed
  text.
- **Swipe-reply to a voice note works.** Because the transcript is stored against
  the voice note's `wamid`, quoting a voice note surfaces its transcript to the
  Gatekeeper as `[In reply to: "..."]`.
- **Empty-transcript guard.** If speech-to-text returns nothing (silence, noise,
  unintelligible audio), the pipeline logs it and stops without touching the
  Gatekeeper.
- **Temporary audio cleanup.** Downloaded audio is always deleted in a `finally`
  block, whether transcription succeeded, failed, or returned empty.

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
- **Full text round-trip persistence.** Both inbound user messages (typed or
  transcribed) and outbound assistant messages are archived with their WhatsApp
  message IDs.

### Operations

- **Health endpoint** that proxies a live authenticated check against the MCP
  server.
- **Single source of truth for the Redis URL.** `config.REDIS_URL` builds the
  connection string, including optional username and password, and the lifespan
  checkpointer uses it directly.
- **Self-healing MongoDB indexes**, including a TTL index that expires chat
  windows after 7 days.
- **Auto-created audio workspace.** The `audio/` directory is created on import
  if it does not exist.

---

## Architecture

```
                        WhatsApp Cloud API
                               |
                     GET / POST /webhook
                               v
   +-------------------------------------------------------------+
   |                      main.py  (FastAPI)                     |
   |  verify_webhook  |  receive_whatsapp_event  |  /  | /health  |
   +----------------------------+--------------------------------+
                               |
                dedupe: redis "processed:{msg_id}"
                               |
            +------------------+------------------+
            | type == "text"                      | type == "audio"
            v                                     v
  +--------------------------+    +--------------------------------+
  | resolve quoted reply     |    |  services.process_voice_message|
  | redis -> Mongo -> cache  |    |  (FastAPI BackgroundTasks)     |
  | append [In reply to]     |    +---------------+----------------+
  | cache msg:{wamid} 48h    |                    |
  | save to Mongo 24h window |         get_whatsapp_media_url()
  | stack_incoming_message() |                    v
  | start_gatekeeper_monitor |    graph.facebook.com/v21.0/{media_id}
  +------------+-------------+                    |
               |                     download_whatsapp_audio()
               |                                  v
               |                       audio/{msg_id}.ogg  (disk)
               |                                  |
               |                     transcribe.transcribe_audio()
               |                        Hugging Face Inference
               |                        (provider: hf-inference)
               |                                  |
               |                     cache msg:{wamid} = transcript
               |                     save to Mongo 24h window
               |                     stack_incoming_message()
               |                     asyncio.create_task(monitor)
               |                                  |
               +------------------+---------------+
                                  |  both paths converge
                                  v
   +-------------------------------------------------------------+
   |            redis_manager.py  (RedisSessionManager)          |
   |   buffer:{phone}   lock:{phone}   session:{phone}           |
   |   monitor_typing_lock()  <-- polls 1s until lock expires    |
   +----------------------------+--------------------------------+
                                |  aggregated_text
                                v
   +-------------------------------------------------------------+
   |              services.py  process_gatekeeper_flow()         |
   +-------------+-------------------------------+---------------+
                 |                               ^
      evaluate(agent_id, phone, text)      raw report output
                 v                               |
   +-------------------------------+  +--------+----------------+
   |        gatekeeper.py          |  |        llm.py           |
   |  LangGraph StateGraph (1 node)|  |  create_react_agent     |
   |  ChatOpenAI + structured out  |  |  + MCP tools            |
   |  Redis AsyncRedisSaver        |  |  (MemorySaver)          |
   |  thread: agent:{id}:client:{} |  +--------+----------------+
   +---------------+---------------+           |
                   | IntentDecision            | HTTPS + Bearer
                   v                           v
          is_ready / intended_action    Remote MCP Tool Server
          / action_parameters           (RTD reporting tools)
                   |
        not ready -> stay silent (return)
                   |
        ready -> llm_server.serve(decision, chat_id)
                   |
        send_whatsapp_message() -> graph.facebook.com/v21.0
                   |
        store bot wamid in Redis + MongoDB
```

---

## Request Lifecycle

### Shared front door

1. **Handshake.** Meta calls `GET /webhook` with `hub.mode=subscribe`, the
   configured verify token, and a challenge. The challenge is echoed back
   verbatim when the token matches; otherwise `403`.
2. **Inbound event.** Meta `POST`s a payload to `/webhook`. The handler digs into
   `entry[0].changes[0].value.messages[0]`. Non-message payloads (delivery and
   read receipts) and payloads without a message ID return early with
   `{"status": "success"}`.
3. **Deduplication.** `processed:{msg_id}` is checked and set with a 300-second
   TTL. A repeat returns `{"status": "already_processed"}`.
4. **Routing.** The message `type` decides the branch.

### Text branch

5. The body is extracted from `client_msg["text"]["body"]`.
6. If a `context.id` is present, the quoted message body is fetched from
   `msg:{wamid}` in Redis, then MongoDB, then re-cached for 48h, and prepended as
   `[In reply to: "..."]`.
7. The full text is cached at `msg:{msg_id}` (48h) and appended to the client's
   active 24-hour MongoDB window.
8. `stack_incoming_message()` pushes the text onto `buffer:{phone}` and sets
   `lock:{phone}` with a 10-second TTL.
9. `start_gatekeeper_monitor()` registers the phone in
   `redis_manager.active_monitors` and schedules `monitor_typing_lock` as a
   FastAPI background task, but only if a monitor is not already running for
   that phone.

### Voice branch

5. If the payload has no `audio.id`, the handler returns early.
6. `services.process_voice_message(phone, media_id, msg_id)` is scheduled as a
   FastAPI background task, so Meta gets its fast `200 OK` immediately.
7. `get_whatsapp_media_url()` exchanges the media ID for a short-lived CDN URL
   (`GET graph.facebook.com/v21.0/{media_id}` with the Bearer token) and raises
   if Meta returns no URL.
8. `download_whatsapp_audio()` streams the bytes down (30s timeout) and writes
   them to `audio/{msg_id}.ogg`.
9. `transcribe.transcribe_audio()` sends the file to the Hugging Face Inference
   providers API with `AsyncInferenceClient(provider="hf-inference")` and the
   configured `STT_MODEL_NAME`, and returns the stripped transcript.
10. An empty transcript aborts the flow with a log line.
11. Otherwise the transcript is cached at `msg:{msg_id}` (48h), saved to MongoDB
    as a `user` turn carrying the voice note's `wamid`, and pushed onto the same
    debounce buffer.
12. A monitor is started for the phone if one is not already active. The voice
    path uses `asyncio.create_task` because it is already running inside a task.
13. In `finally`, the temporary audio file is deleted from disk.

### Converged pipeline

14. **Aggregation.** Once per second the monitor checks whether the typing lock
    has expired. When it has, the whole buffer is drained, joined with newlines,
    appended to session history, and passed to the dispatch callback - a typed
    message and a voice note sent in the same burst arrive here as one prompt.
15. **Gatekeeper evaluation.** A LangGraph node builds a system prompt containing
    the current date and time, the live MCP tool schemas, ten messages of prior
    history, and a seven-rule policy. `ChatOpenAI` is called with
    `.with_structured_output(IntentDecision, method="function_calling")` and
    returns a typed `IntentDecision`.
16. **Gate.** If `is_ready` is false or `intended_action` is null, the flow logs
    `[COPILOT SILENT]` and returns without replying.
17. **Execution.** `llm_server.serve()` serializes the decision to JSON and
    invokes a ReAct agent that calls the named MCP tool with exactly the given
    `action_parameters` and returns the raw result.
18. **Formatting and send.** The output is parsed and re-serialized with
    `indent=2` (falling back to the raw string if it is not JSON) and posted to
    the WhatsApp Graph API.
19. **Assistant archival.** On a successful send, the returned bot `wamid` is
    cached at `msg:{bot_wamid}` for 48h and stored in MongoDB as an `assistant`
    turn.

---

## Module Reference

| Module | Purpose | Key components | Primary use case |
| --- | --- | --- | --- |
| `main.py` | FastAPI entry point and WhatsApp webhook surface | `app`, `lifespan`, `verify_webhook`, `receive_whatsapp_event`, `health_check`, `resolve_quoted_message`, `start_gatekeeper_monitor` | Owns the HTTP edge, boots MongoDB indexes and the Redis checkpointer, dedupes events, branches on text versus audio, resolves swipe-replies, and hands work to the debouncer |
| `services.py` | Business orchestration layer | `Services` class, `services` singleton, `send_whatsapp_message`, `get_whatsapp_media_url`, `download_whatsapp_audio`, `process_voice_message`, `process_gatekeeper_flow`, `AUDIO_DIR` | The conductor: fetches and transcribes voice notes, asks the Gatekeeper for a decision, decides whether to stay silent, runs the executor, formats the report, and delivers it to WhatsApp |
| `transcribe.py` | Cloud speech-to-text adapter | `Transcribe` class, `transcribe_audio`, `transcribe` singleton | Converts a downloaded WhatsApp voice note into text through the Hugging Face Inference providers API, keeping the STT provider swappable behind one method |
| `gatekeeper.py` | Reasoning gate and intent classifier | `Gatekeeper` class, `_gatekeeper_node`, `_get_tool_schemas`, `evaluate`, singleton `gatekeeper_agent` | Determines whether and what to execute. Implements the seven-rule policy around chit-chat, duplicate reports, explicit regeneration, follow-up questions, new parameters, and quoted messages |
| `llm.py` | Tool executor | `LLMServer` class, `_get_or_create_agent`, `get_mcp_tools`, `serve`, singleton `llm_server` | Lazily builds a `create_react_agent` wired to the remote MCP server and executes the Gatekeeper's routing decision verbatim, returning raw tool output only |
| `models.py` | Shared Pydantic contracts | `IntentDecision`, `GatekeeperState`, `RequestModel`, `ResponseModel` | Defines the structured decision schema the LLM must fill, and the LangGraph state shape (`MessagesState` plus `decision`) passed between nodes |
| `config.py` | Environment configuration | `Config`, `REDIS_URL` property, singleton `config` | Centralizes every credential and endpoint from `.env` behind one importable object, including LLM, Hugging Face STT, WhatsApp, MCP, Redis, and MongoDB settings |
| `database.py` | Cold storage for chat windows | `DatabaseManager`, `setup_indexes`, `save_message_to_window`, `get_message_by_wamid`, `get_recent_messages`, singleton `db_manager` | Durably stores 24-hour conversation windows in MongoDB so quoted-message lookups and history survive Redis eviction and process restarts. Voice notes enter this store as their transcript |
| `redis_manager.py` | Hot state, debouncing, sessions | `RedisSessionManager`, `stack_incoming_message`, `get_and_clear_buffer`, `monitor_typing_lock`, `append_message`, `get_conversation_history`, `rehydrate_session`, singleton `redis_manager` | Provides the sub-second hot path: message buffering shared by text and voice, typing locks, per-phone session lists, and the inactivity monitor that decides when a burst of messages is done |
| `requirements.txt` | Dependency manifest | n/a | Declares the runtime stack (see the dependency table below) |

### Dependency and library usage

| Library / module | Used in | Why it is here |
| --- | --- | --- |
| `fastapi` | `main.py` | HTTP framework: routing, query parsing, `BackgroundTasks`, CORS middleware, lifespan events |
| `uvicorn` | runtime | ASGI server that hosts the FastAPI app |
| `pydantic` | `models.py` | Declarative validation of the LLM decision structure and API models |
| `langgraph` | `gatekeeper.py`, `llm.py` | `StateGraph` for the gate node, `MessagesState` for graph state, `create_react_agent` for the tool-calling executor |
| `langgraph.checkpoint.redis.aio.AsyncRedisSaver` | `main.py`, `gatekeeper.py` | Async Redis-backed graph checkpointer giving the Gatekeeper durable per-conversation memory |
| `langgraph.checkpoint.memory.MemorySaver` | `llm.py` | In-memory checkpointer for the executor agent, keyed by `rtd_{chat_id}` |
| `langchain_openai.ChatOpenAI` | `gatekeeper.py`, `llm.py` | Points at any OpenAI-compatible endpoint; used for structured intent decisions and tool calling |
| `langchain_mcp_adapters.client.MultiServerMCPClient` | `llm.py` | Discovers remote MCP tools and exposes them as LangChain tools over HTTP with Bearer auth |
| `langchain_core.messages` | `gatekeeper.py` | `SystemMessage` / `HumanMessage` prompt construction |
| `langchain_core.runnables.RunnableConfig` | `llm.py` | Types the per-conversation thread configuration passed to the agent |
| `huggingface_hub.AsyncInferenceClient` | `transcribe.py` | Async client for the Hugging Face Inference providers API; runs `automatic_speech_recognition` against the configured Whisper-family model |
| `httpx` | `main.py`, `services.py` | Async HTTP client for the MCP health check, WhatsApp media resolution and download, and the Graph API send call |
| `redis` (`redis.asyncio`) | `redis_manager.py` | Async Redis client with pipelines for buffers, locks, sessions, dedupe flags, and wamid caches |
| `motor` (`AsyncIOMotorClient`) | `database.py` | Async MongoDB driver for 24-hour chat windows and wamid-indexed lookups |
| `pymongo.errors.OperationFailure` | `database.py` | Detects TTL-index conflicts (code 85) and performs a self-healing index update |
| `python-dotenv` | `config.py` | Loads `.env` into the process environment |
| `asyncio` | `llm.py`, `redis_manager.py`, `services.py` | Initialization locking, the 1-second debounce polling loop, and spawning the monitor task from the voice pipeline |
| `os` | `services.py` | Creates the audio workspace and removes temporary audio files |
| `passlib` | declared only | Present in `requirements.txt`; not referenced by any current module, reserved for credential hashing |

> `requirements.txt` is grouped by purpose and declares every third-party module
> the code imports directly. Four of them are easy to miss, because the import
> path does not match the distribution name: `langgraph.checkpoint.redis` comes
> from **`langgraph-checkpoint-redis`**, `langgraph.prebuilt` comes from
> **`langgraph-prebuilt`**, `langgraph.checkpoint.memory` comes from
> **`langgraph-checkpoint`**, and the `dotenv` module comes from
> **`python-dotenv`**. The PyPI package literally named `dotenv` is a deprecated
> placeholder that ships no code of its own, so it must never be listed in place
> of `python-dotenv`.
>
> The declared `langchain`, `langchain-classic`, and `langchain-community`
> packages are not imported directly; the code imports from `langchain_core`,
> `langchain_openai`, `langchain_mcp_adapters`, and `langgraph`.

---

## API Endpoints

| Method | Path | Description | Response |
| --- | --- | --- | --- |
| `GET` | `/` | Declared to return `index.html` via `FileResponse` | **Broken:** `index.html` was removed from the project, so this route now raises a file-not-found error |
| `GET` | `/health` | Authenticated `GET` against `SERVER_HEALTH_CHECK_URL` using `MCP_SERVER_API_KEY` (5s timeout) | `{"status": <code>, "message": <json>}` or `{"status": 500, "error": "..."}` |
| `GET` | `/webhook` | Meta webhook verification handshake (`hub.mode`, `hub.verify_token`, `hub.challenge`) | Echoes the challenge as `text/plain`, or `403 Verification failed` |
| `POST` | `/webhook` | Inbound WhatsApp events: text messages, audio messages, and ignored receipts | `{"status": "success"}` or `{"status": "already_processed"}` |

---

## Data Stores and Key Layout

### Redis

| Key pattern | Type | TTL | Purpose |
| --- | --- | --- | --- |
| `processed:{wamid}` | String | 300s | Inbound dedupe guard against webhook retries, shared by text and voice |
| `msg:{wamid}` | String | 172800s (48h) | Full text of any message. Holds the **transcript** for a voice note, so quoting a voice note resolves to readable text |
| `buffer:{phone}` | List | cleared on read | Debounce buffer shared by typed text and voice transcripts |
| `lock:{phone}` | String | 10s | Typing lock; its expiry signals the user has stopped sending |
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
      "content": "text body or voice transcript",
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
- Sparse `messages.wamid` for quoted-reply lookups, which is what makes quoting a
  voice note work.

`CRED_COLLECTION_NAME` is exposed as `db_manager.tenants` for tenant or credential
storage; no read or write paths use it yet.

### Local disk

| Path | Content | Lifetime |
| --- | --- | --- |
| `audio/` | Working directory for downloaded WhatsApp voice notes | Auto-created on startup |
| `audio/{msg_id}.ogg` | One downloaded voice note per inbound media message | Deleted in a `finally` block once transcription completes |

`audio/` is not covered by the repository `.gitignore`, so leftover files from a
hard crash would show up as untracked changes.

---

## Configuration

All configuration is read from a `.env` file in the project root by `config.py`.
Never commit `.env`; it holds live credentials.

```dotenv
# LLM (any OpenAI-compatible endpoint)
LLM_API_URL=https://your-llm-endpoint/v1
LLM_API_KEY=sk-xxxxxxxxxxxxxxxxxxxx
LLM_MODEL_NAME=deepseek-chat

# Speech-to-text (Hugging Face Inference providers)
HF_INFERENCE_TOKEN=hf_xxxxxxxxxxxxxxxxxxxx
STT_MODEL_NAME=openai/whisper-large-v3

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
- `HF_INFERENCE_TOKEN` is the Hugging Face access token used by the transcriber;
  `STT_MODEL_NAME` defaults to `openai/whisper-large-v3` if unset.
- `LLM_MODEL_NAME` defaults to `deepseek-chat` if unset.
- `RECIPIENT_PHONE` is read into config but not used in the send path.
- `REDIS_URL` is a derived property; username plus password, password only, or no
  auth are all supported.
- `MCP_SERVER_TRANSPORT = "sse"` is declared in `config.py`, but the MCP client
  is actually constructed with `"transport": "http"` in `llm.py`.

### Tunable constants

| Constant | Location | Default | Effect |
| --- | --- | --- | --- |
| `debounce_ttl` | `redis_manager.RedisSessionManager` | 10 seconds | Idle time after the last message before the burst is dispatched |
| `default_ttl` | `redis_manager.RedisSessionManager` | 900 seconds | Session history lifetime |
| `processed:{id}` TTL | `main.py` | 300 seconds | Retry-dedupe window |
| `msg:{id}` TTL | `main.py`, `services.py` | 172800 seconds (48h) | Quoted-reply and transcript cache lifetime |
| Media URL timeout | `services.get_whatsapp_media_url` | 15 seconds | Graph API media-metadata request |
| Audio download timeout | `services.download_whatsapp_audio` | 30 seconds | Voice note byte download |
| Audio file extension | `services.download_whatsapp_audio` | `.ogg` | Fixed `.ogg` name regardless of the actual MIME type reported by Meta |
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
- A Hugging Face account with an inference-enabled access token
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

Start Uvicorn from the `LLM Server` directory: the audio workspace and the
`FileResponse` path are both relative to the process working directory.

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
3. Send a WhatsApp text message or hold the mic button and send a voice note to
   your business number, then watch the logs.

### Useful local checks

```bash
curl http://localhost:8000/health
curl "http://localhost:8000/webhook?hub.mode=subscribe&hub.verify_token=YOUR_TOKEN&hub.challenge=12345"
```

### Log lines to watch

| Log line | Meaning |
| --- | --- |
| `[VOICE MESSAGE] Received from ...` | Inbound audio detected in the webhook |
| `[WHATSAPP AUDIO] Downloaded N bytes -> audio/...` | Media fetched from the WhatsApp CDN |
| `[STT] Transcribing: ...` / `[STT] Transcript: '...'` | Speech-to-text started and finished |
| `[STT] Empty transcript.` | Nothing usable was recognised, pipeline stops |
| `[VOICE MESSAGE] Temporary audio deleted: ...` | Cleanup completed |
| `[MONITOR] Gatekeeper monitor started for ...` | Debounce monitor armed by the text path |
| `[COPILOT OBSERVER] New message from ...` | Aggregated prompt handed to the Gatekeeper |
| `RTD GATEKEEPER DECISION` block | `is_ready`, `intended_action`, `missing_fields`, `action_parameters` |
| `[COPILOT SILENT] No report action triggered.` | Decision was not actionable, no reply sent |
| `[REPORT DISPATCHED] RTD report sent to ...` | Report delivered to WhatsApp |

---

## Design Details Worth Knowing

**Three brains instead of one.** Separation of concerns turns fuzzy problems into
crisp ones. The transcriber owns audio-to-text. The Gatekeeper carries the nuanced
policy (do not re-trigger reports, ignore chit-chat, read quoted messages) and
emits a small typed object. The executor then has a job it cannot easily get
wrong: one tool, exact arguments, raw output. This also keeps the expensive policy
prompt free of tool-calling state.

**Voice and text converge as early as possible.** The transcript is written into
the exact same keys and collections that typed text uses: `msg:{wamid}`, the
MongoDB window, and `buffer:{phone}`. Nothing downstream needs to know a voice
note was involved. That includes swipe-reply: because the transcript is stored
against the voice note's own `wamid`, quoting it works for free.

**The debounce buffer is modality-agnostic.** A client who types two lines and
then records a voice note produces one aggregated prompt, not three Gatekeeper
calls. The typing lock is reset by whichever path pushed last, so the burst is
judged as a whole.

**Audio never outlives its usefulness.** The file is written to `audio/{msg_id}.ogg`
only because the transcriber needs a path. It is deleted in `finally`, so failed
transcriptions cannot leak disk. The one gap is a hard process kill between
download and cleanup.

**State lives in the checkpointer.** `evaluate()` invokes the graph with only the
latest human message. Prior history is not passed in manually; the
`AsyncRedisSaver` checkpointer restores the accumulated `messages` list for the
thread, and the node reads back the last ten entries for context. That is why
thread identity (`agent:{agent_id}:client:{phone}`) matters: it is the memory key.

**Three tiers of memory serve three different jobs.** Redis `session:*` lists hold
the short-lived hot conversation. The LangGraph checkpointer holds the
Gatekeeper's reasoning state. MongoDB holds the durable 24-hour window that
answers "what was that message the client replied to?", including across Redis
eviction. The `msg:{wamid}` cache exists purely because WhatsApp quotes messages
by ID, not by text.

**Fast acknowledgement, slow work in the background.** Both the text monitor and
the entire voice pipeline (download, transcribe, persist) run outside the request
path, so the webhook always answers Meta quickly and retries are not triggered by
slow speech-to-text.

**MCP keeps the tool surface external.** The Gatekeeper asks the MCP server for
real tool schemas at runtime, and the executor receives the same tool objects. A
new reporting tool can be added on the MCP side without changing this codebase,
although the hardcoded fallback string in `_get_tool_schemas` should be updated
to match if MCP is unreachable at boot.

---

## Known Limitations and Follow-Ups

These are observations from the current code, not blockers. The first three are
the ones most likely to bite in production.

**Broken `/` route.** `main.py` still registers `@app.get("/")` returning
`FileResponse("index.html")`, but `index.html` was deleted from the project. Any
request to `/` now fails. Either drop the route in favour of a JSON health or
status response, or restore the page.

**Placeholder attribute in `Services.__init__`.** `self.token = "toekn"` is
unused leftover scaffolding and should be removed before anyone mistakes it for a
credential.

**Dead commented-out block.** The first ~90 lines of `services.py` are the entire
old module-level implementation, commented out, including a stray
`async def self, ...` signature that would not parse if uncommented. It should be
deleted so the file has one clear implementation.

### Additional notes

- **In-process only monitor registry.** `redis_manager.active_monitors` is a
  Python `set`. Running multiple Uvicorn workers or replicas means a phone number
  can be monitored by two processes at once. A Redis-based lock would be the
  distributed fix.
- **Two scheduling styles for the same monitor.** The text path uses FastAPI
  `BackgroundTasks`; the voice path uses `asyncio.create_task`. Both work, but a
  shared helper would make the lifecycle easier to reason about and to cancel on
  shutdown.
- **MIME type is captured but unused.** The webhook reads `mime_type` and logs it,
  but the download path always writes `{msg_id}.ogg`. Voice notes are OGG/Opus so
  this works today, but forwarded audio files in other formats would be misnamed.
- **Blocking file I/O inside async functions.** `download_whatsapp_audio` and the
  cleanup step use synchronous `open()` / `os.remove()` inside coroutines.
  `aiofiles` or `asyncio.to_thread` would keep the event loop free under load.
- **Relative `AUDIO_DIR`.** `audio/` resolves against the process working
  directory, so launching Uvicorn from elsewhere silently relocates the audio
  workspace.
- **Executor memory is in-memory.** The ReAct agent uses `MemorySaver`, so its
  thread context is lost on restart. The Gatekeeper, by contrast, survives
  restarts via Redis.
- **Transport mismatch.** `MCP_SERVER_TRANSPORT = "sse"` in `config.py` is unused;
  `llm.py` hardcodes `"http"`. Pick one and drive both from config.
- **Unused surface.** `RequestModel`, `ResponseModel`, `get_recent_messages`,
  `get_conversation_history`, `rehydrate_session`, `has_active_session`,
  `delete_session`, `db_manager.tenants`, `RECIPIENT_PHONE`, `MCP_SERVER_TRANSPORT`,
  and `MONGODB_USERNAME` / `MONGODB_PASSWORD` are defined but not wired into the
  live flow. They read as intended building blocks (session rehydration,
  multi-tenant credentials, an agent-facing REST API) that are not finished.
- **No signature verification on `/webhook`.** The POST handler trusts the
  payload contents and does not validate Meta's `X-Hub-Signature-256` header.
  Adding HMAC verification with the app secret would harden the endpoint.
- **Broad exception swallowing.** The webhook handler, the Gatekeeper, and the
  voice pipeline each catch bare `Exception` and continue. This keeps the service
  alive but can hide configuration errors, such as a bad Hugging Face token;
  structured logging with severity levels would help.
- **Delivery-status events are ignored.** Only `messages` payloads are handled,
  so sent, delivered, and read receipts are not persisted.
- **`audio/` is not git-ignored.** Add it to `.gitignore` so crash leftovers never
  end up in a commit.
- **Unresolved tab reference.** A `voice.py` file was open in the editor during
  this review, but no such file exists in the project or its parent directory.
  Voice handling currently lives in `services.py` and `transcribe.py`; if a
  separate `voice.py` module is planned, this document needs another pass once it
  lands.
