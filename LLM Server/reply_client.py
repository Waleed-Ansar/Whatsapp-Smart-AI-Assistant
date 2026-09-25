# agents/auto_reply.py

from __future__ import annotations
from typing import Optional, List, Dict, Any

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
)
from langgraph.graph import MessagesState, StateGraph, START, END
from langchain_openai import ChatOpenAI

from config import config
from knowledge import knowledge_manager
from database import db_manager


class AutoReplyAgent:
    """
    Real-time conversational AI agent with stateful LangGraph memory.
    """

    def __init__(self):
        self.llm = ChatOpenAI(
            model=config.LLM_MODEL_NAME,
            api_key=config.LLM_API_KEY,
            base_url=config.LLM_API_URL,
            temperature=0.7,
            max_tokens=300,
        )
        
        self.checkpointer = None
        self.graph = None
        self.domain_core: str = ""

    async def initialize(self, checkpointer):
        """Compiles the LangGraph conversation flow backed by the Redis checkpointer."""
        self.checkpointer = checkpointer
        
        # Load Singapore real estate domain knowledge
        try:
            self.domain_core = knowledge_manager.load()
        except Exception:
            self.domain_core = ""

        builder = StateGraph(MessagesState)

        builder.add_node(
            "auto_reply",
            self._auto_reply_node
        )

        builder.add_edge(
            START,
            "auto_reply"
        )

        builder.add_edge(
            "auto_reply",
            END
        )

        self.graph = builder.compile(
            checkpointer=self.checkpointer
        )

    def _build_system_prompt(
        self,
        agent_context: Optional[str] = None,
    ) -> str:
        return f"""
            You are a real Singapore real-estate agent communicating with a client
            through WhatsApp.

            Your job is to respond naturally and promptly to EVERY inbound client
            message.

            You are a conversational agent, not a background analysis agent.

            ==================================================
            CORE BEHAVIOR
            ==================================================

            - Respond to the client's latest message directly.
            - Keep replies concise and natural for WhatsApp (usually 1–3 short sentences).
            - Do not give unnecessarily long explanations.
            - Do not ask several questions at once unless genuinely necessary.
            - If the client provides information, acknowledge and use it.
            - Do not repeatedly ask for information already provided.
            - If the client is simply greeting or chatting, respond naturally.
            - If the client asks a simple question, answer it directly.
            - If the client asks something requiring current or exact information
            that you cannot verify, do not invent an answer.

            ==================================================
            HUMAN-LIKE CONVERSATION
            ==================================================

            Communicate like an experienced property agent.
            Avoid robotic phrases such as:
            "Thank you for your inquiry."
            "I am an AI assistant."
            "How may I assist you today?"
            "Please provide the following information."

            Prefer natural conversational language.

            ==================================================
            SINGAPORE REAL-ESTATE DOMAIN
            ==================================================

            {self.domain_core}

            ==================================================
            IMPORTANT KNOWLEDGE RULE
            ==================================================

            Do NOT invent property listings, prices, rental rates, transactions,
            availability, project details, regulations, or statistics.

            ==================================================
            CONVERSATION CONTEXT
            ==================================================

            Use the previous conversation messages in this thread to understand:
            - client preferences, budget, bedrooms, locations, and intents.
            The latest client message has priority.

            ==================================================
            AGENT CONTEXT
            ==================================================

            {agent_context or "No additional agent-specific instructions."}
        """

    async def _auto_reply_node(self, state: MessagesState):
        """
        LangGraph node: reads the state's message history, prepends the system prompt,
        and generates an AIMessage response.
        """
        messages = state["messages"]
        system_prompt = self._build_system_prompt()

        # Sliding window: keep the last 14 messages for LLM context to prevent token bloat
        recent_turns = messages[-14:]

        full_prompt = [SystemMessage(content=system_prompt)] + list(recent_turns)

        response = await self.llm.ainvoke(full_prompt)
        return {"messages": [response]}

    async def _rehydrate_from_mongodb_if_needed(self, phone: str, graph_config: dict):
        """
        Cold-start recovery: If Redis checkpointer has no state for this thread,
        seed the state using recent messages from MongoDB.
        """
        state = await self.graph.aget_state(graph_config)
        existing_messages = state.values.get("messages", [])

        if not existing_messages:
            raw_docs = await db_manager.get_recent_messages(phone=phone, limit=10)
            if not raw_docs:
                return

            seed_messages = []
            for doc in raw_docs:
                role = doc.get("role")
                content = doc.get("content", "")
                if not content:
                    continue

                if role == "user":
                    seed_messages.append(HumanMessage(content=content))
                elif role == "assistant":
                    seed_messages.append(AIMessage(content=content))

            if seed_messages:
                await self.graph.aupdate_state(graph_config, {"messages": seed_messages})

    async def respond(
        self,
        phone: str,
        client_message: str,
        agent_context: Optional[str] = None,
    ) -> str:
        """
        Processes inbound client message through the stateful LangGraph agent.
        """
        if not client_message or not client_message.strip():
            return ""

        if not self.graph:
            raise RuntimeError("AutoReplyAgent is not initialized. Call initialize() in FastAPI lifespan.")

        graph_config = {
            "configurable": {
                "thread_id": f"autoreply:{phone}"
            }
        }

        # 1. Ensure state exists (seed from MongoDB if Redis is clean)
        await self._rehydrate_from_mongodb_if_needed(phone, graph_config)

        # 2. Invoke the graph (LangGraph automatically appends user message & persists response)
        try:
            result = await self.graph.ainvoke(
                {"messages": [HumanMessage(content=client_message)]},
                config=graph_config
            )

            latest_message = result["messages"][-1]
            reply = latest_message.content

            if isinstance(reply, list):
                reply = "".join(
                    item.get("text", "") for item in reply if isinstance(item, dict)
                )

            return str(reply).strip()

        except Exception as exc:
            print(f"[AUTO REPLY] LangGraph error: {exc}")
            return ""


auto_reply_agent = AutoReplyAgent()