NEW FLOW

Gatekeeper:
- Removed / no longer used.

Auto Reply Agent:
- KEPT.
- reply_client.py remains.
- Initialized with the Redis LangGraph checkpointer.
- Runs for every inbound text message for conversational testing.
- Has its own stateful memory as already implemented in reply_client.py.

Main Agent:
- Receives each raw client message directly.
- Classifies intent itself.
- Observes up to 3-5 relevant messages.
- Calls MCP tools.
- Missing tool parameters are passed as None.
- Duplicate actions are protected by Redis fingerprints.

Webhook text flow:
1. Save inbound message.
2. Auto Reply Agent runs.
3. Main tool-calling Agent runs.

gatekeeper.py can still be deleted because nothing imports it.
