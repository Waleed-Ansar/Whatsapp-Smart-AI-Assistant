import asyncio
import hashlib
import json
import random
from typing import Any, Dict, List, Optional

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from config import config
from redis_manager import redis_manager


class AgentDecision(BaseModel):
    """
    Internal decision made by the SAME LLM that owns tool execution.
    This replaces the old Gatekeeper IntentDecision.
    """

    has_actionable_intent: bool = False
    intended_action: Optional[str] = None
    relevant_to_pending_request: bool = False

    # True only when the client explicitly asks to repeat/regenerate/rerun.
    explicit_rerun: bool = False

    # True only when this is clearly a new materially different request.
    new_request: bool = False

    # Parameters extracted only from the conversation.
    # Missing values are NOT invented here.
    action_parameters: Dict[str, Any] = Field(default_factory=dict)

    # Allows a very explicit, already-useful request to run before
    # the 3-5 message observation window is exhausted.
    execute_early: bool = False


class LLMServer:
    """
    Single AI agent for:
    - reading raw client messages
    - understanding intent
    - collecting parameters across messages
    - waiting up to 3-5 relevant messages
    - calling the MCP tool
    - preventing duplicate report/tool execution
    """

    STATE_TTL_SECONDS = 604800  # 7 days
    MAX_COMPLETED_ACTIONS = 50

    def __init__(self):
        self.API_KEY = config.LLM_API_KEY
        self.MODEL = config.LLM_MODEL_NAME
        self.API_URL = config.LLM_API_URL
        self.MCP_SERVER_URL = config.MCP_SERVER_URL
        self.MCP_SERVER_API_KEY = config.MCP_SERVER_API_KEY

        self._mcp_client = None
        self._tools = []
        self._tools_by_name = {}
        self._tool_schemas = {}
        self._tool_descriptions = ""
        self._init_lock = asyncio.Lock()
        self._chat_locks: Dict[str, asyncio.Lock] = {}

        self.llm = ChatOpenAI(
            model=self.MODEL,
            base_url=self.API_URL,
            api_key=self.API_KEY,
            temperature=0.0,
            max_retries=5,
            timeout=60,
        )

        # Same underlying LLM model, structured only for the internal
        # intent/parameter decision.
        self.classifier = self.llm.with_structured_output(
            AgentDecision,
            method="function_calling",
        )

    async def initialize(self):
        await self._get_or_create_tools()
        print("[MAIN AGENT] MCP tools loaded and single-agent flow initialized")

    async def _get_or_create_tools(self):
        if self._tools:
            return self._tools

        async with self._init_lock:
            if self._tools:
                return self._tools

            self._mcp_client = MultiServerMCPClient({
                "rtd_tools": {
                    "url": self.MCP_SERVER_URL,
                    "transport": "http",
                    "headers": {
                        "Authorization": f"Bearer {self.MCP_SERVER_API_KEY}"
                    }
                }
            })

            self._tools = await self._mcp_client.get_tools()
            self._tools_by_name = {
                tool.name: tool
                for tool in self._tools
            }

            descriptions = []

            for tool in self._tools:
                schema = (
                    getattr(tool, "args_schema", None)
                    or getattr(tool, "input_schema", None)
                    or {}
                )

                if hasattr(schema, "model_json_schema"):
                    schema = schema.model_json_schema()

                if not isinstance(schema, dict):
                    schema = {}

                self._tool_schemas[tool.name] = schema

                descriptions.append(
                    f"- Tool: {tool.name}\n"
                    f"  Schema: {json.dumps(schema, ensure_ascii=False)}"
                )

            self._tool_descriptions = "\n".join(descriptions)

            return self._tools

    async def get_mcp_tools(self) -> list:
        return await self._get_or_create_tools()

    def _state_key(self, chat_id: str) -> str:
        clean_id = "".join(
            ch for ch in str(chat_id)
            if ch.isalnum() or ch in ("_", "-", ":")
        )
        return f"main_agent_state:{clean_id}"

    async def _load_state(self, chat_id: str) -> Dict[str, Any]:
        raw = await redis_manager.r.get(
            self._state_key(chat_id)
        )

        if not raw:
            return {
                "pending_action": None,
                "observation_count": 0,
                "observation_target": None,
                "collected_parameters": {},
                "completed_actions": [],
            }

        try:
            state = json.loads(raw)
        except Exception:
            state = {}

        state.setdefault("pending_action", None)
        state.setdefault("observation_count", 0)
        state.setdefault("observation_target", None)
        state.setdefault("collected_parameters", {})
        state.setdefault("completed_actions", [])

        return state

    async def _save_state(
        self,
        chat_id: str,
        state: Dict[str, Any],
    ) -> None:
        await redis_manager.r.set(
            self._state_key(chat_id),
            json.dumps(
                state,
                ensure_ascii=False,
                default=str,
            ),
            ex=self.STATE_TTL_SECONDS,
        )

    @staticmethod
    def _normalize_for_fingerprint(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                str(k).strip().lower():
                    LLMServer._normalize_for_fingerprint(v)
                for k, v in sorted(
                    value.items(),
                    key=lambda item: str(item[0]).lower(),
                )
            }

        if isinstance(value, list):
            return [
                LLMServer._normalize_for_fingerprint(v)
                for v in value
            ]

        if isinstance(value, str):
            return " ".join(
                value.strip().lower().split()
            )

        return value

    def _fingerprint(
        self,
        tool_name: str,
        parameters: Dict[str, Any],
    ) -> str:
        normalized = {
            "tool": tool_name.strip().lower(),
            "parameters":
                self._normalize_for_fingerprint(parameters),
        }

        serialized = json.dumps(
            normalized,
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )

        return hashlib.sha256(
            serialized.encode("utf-8")
        ).hexdigest()

    def _schema_parameter_names(
        self,
        tool_name: str,
    ) -> List[str]:
        schema = self._tool_schemas.get(
            tool_name,
            {},
        )

        properties = schema.get(
            "properties",
            {},
        )

        if not isinstance(properties, dict):
            return []

        return list(properties.keys())

    def _complete_parameters_with_none(
        self,
        tool_name: str,
        provided: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        User requirement:
        pass the values actually available from the client and set every
        remaining tool field to None, regardless of how many are missing.
        """
        parameter_names = self._schema_parameter_names(
            tool_name
        )

        if not parameter_names:
            return dict(provided)

        complete = {
            name: None
            for name in parameter_names
        }

        for key, value in provided.items():
            if key in complete:
                complete[key] = value

        return complete

    @staticmethod
    def _merge_parameters(
        old: Dict[str, Any],
        new: Dict[str, Any],
    ) -> Dict[str, Any]:
        merged = dict(old)

        for key, value in new.items():
            # A later explicit value/correction wins.
            # Do not overwrite an already-known value with null.
            if value is not None:
                merged[key] = value

        return merged

    @staticmethod
    def _format_history(
        conversation_history: List[Dict[str, Any]],
    ) -> str:
        if not conversation_history:
            return "No prior conversation."

        lines = []

        for msg in conversation_history[-20:]:
            role = str(
                msg.get("role", "user")
            ).upper()

            content = str(
                msg.get("content", "")
            ).strip()

            if not content:
                continue

            lines.append(
                f"{role}: {content}"
            )

        return (
            "\n".join(lines)
            if lines
            else "No prior conversation."
        )

    @staticmethod
    def _completed_summary(
        completed_actions: List[Dict[str, Any]],
    ) -> str:
        if not completed_actions:
            return "No completed actions."

        compact = []

        for item in completed_actions[-10:]:
            compact.append({
                "tool": item.get("tool"),
                "parameters": item.get(
                    "parameters",
                    {},
                ),
            })

        return json.dumps(
            compact,
            ensure_ascii=False,
            indent=2,
            default=str,
        )

    async def _classify(
        self,
        client_message: str,
        conversation_history: List[Dict[str, Any]],
        state: Dict[str, Any],
    ) -> AgentDecision:
        await self._get_or_create_tools()

        history_block = self._format_history(
            conversation_history
        )

        completed_block = self._completed_summary(
            state.get(
                "completed_actions",
                [],
            )
        )

        pending_action = state.get(
            "pending_action"
        )

        collected_parameters = state.get(
            "collected_parameters",
            {},
        )

        observation_count = state.get(
            "observation_count",
            0,
        )

        observation_target = state.get(
            "observation_target"
        )

        system_prompt = f"""
You are the ONLY operational AI agent for an RTD Advisor WhatsApp workflow.

You receive the client's raw WhatsApp conversation directly.
There is NO Gatekeeper before you.

Your internal job is to determine whether the client's latest message
belongs to an actionable MCP-tool request, determine the correct tool,
and extract ONLY the parameters actually supplied by the client or
unambiguously stated in the conversation.

You do NOT generate a property report yourself.
Python will execute the MCP tool you select.

==================================================
AVAILABLE MCP TOOLS
==================================================

{self._tool_descriptions}

==================================================
CORE RULES
==================================================

1. READ THE CLIENT
- Understand what the client is actually trying to do.
- Do not require the client to name a tool or say "RTD report".
- Match a genuine actionable real-estate request to one available tool.
- Greetings, acknowledgements, thanks, ordinary follow-up discussion,
  and questions about an already-generated report are NOT automatically
  new tool requests.

2. OBSERVATION WINDOW
- When a new actionable request begins, the application observes up to
  3-5 RELEVANT client messages for additional useful details.
- A relevant message is one that contributes to, changes, continues,
  or directly discusses the pending actionable request.
- Small talk and unrelated messages are not relevant.
- Do not fabricate values just because the observation window is ending.
- `execute_early` may be true only if the client has made an explicit,
  clearly actionable request and the information already supplied makes
  immediate execution sensible.
- Otherwise allow the application to continue observing.

3. PARAMETERS
- Extract parameters from the latest message and prior messages that
  belong to the SAME request.
- A later correction overrides an earlier value.
- NEVER invent a missing value.
- NEVER guess a missing value.
- Do not copy values from an unrelated older request.
- It is valid for many parameters to remain unavailable.
- Python will set every unavailable tool parameter to None before the
  tool is called.

4. DUPLICATE PREVENTION -- CRITICAL
- COMPLETED ACTIONS below are reports/tool executions already delivered.
- Merely continuing to talk about the same property, location, project,
  report, valuation, yield, result, or recommendation is NOT a new action.
- Do not mark an old completed request as a new request simply because
  its values are still visible in conversation history.
- If the latest message is only discussing, acknowledging, questioning,
  or clarifying an already-generated report, do NOT request another tool
  execution.
- `explicit_rerun=true` ONLY when the client clearly asks to run,
  regenerate, recalculate, refresh, or generate that report again.
- `new_request=true` ONLY when the client clearly begins a materially
  different actionable request (different property/project/location/tool
  or a clearly changed task).

5. TOOL SELECTION
- `intended_action` MUST exactly match one tool name from AVAILABLE MCP
  TOOLS.
- If no actionable tool request exists, set `intended_action=null`.
- Do not choose a tool merely because the conversation mentions property.

6. OUTPUT
Return only the structured AgentDecision required by the schema.

==================================================
CURRENT PENDING STATE
==================================================

pending_action = {pending_action}
observation_count = {observation_count}
observation_target = {observation_target}
collected_parameters =
{json.dumps(collected_parameters, ensure_ascii=False, indent=2, default=str)}

==================================================
COMPLETED ACTIONS ALREADY DELIVERED
==================================================

{completed_block}
""".strip()

        user_prompt = f"""
PRIOR CONVERSATION:
{history_block}

LATEST CLIENT MESSAGE:
{client_message}
""".strip()

        return await self.classifier.ainvoke([
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": user_prompt,
            },
        ])

    async def _invoke_tool(
        self,
        tool_name: str,
        parameters: Dict[str, Any],
    ) -> Any:
        await self._get_or_create_tools()

        tool = self._tools_by_name.get(
            tool_name
        )

        if tool is None:
            raise RuntimeError(
                f"Unknown MCP tool: {tool_name}"
            )

        print(
            f"[MAIN AGENT] Executing tool: "
            f"{tool_name}"
        )
        print(
            f"[MAIN AGENT] Parameters: "
            f"{parameters}"
        )

        return await tool.ainvoke(
            parameters
        )

    @staticmethod
    def _tool_result_to_string(
        result: Any,
    ) -> str:
        if result is None:
            return ""

        if isinstance(result, str):
            return result

        if isinstance(
            result,
            (dict, list, int, float, bool),
        ):
            return json.dumps(
                result,
                ensure_ascii=False,
                indent=2,
                default=str,
            )

        content = getattr(
            result,
            "content",
            None,
        )

        if content is not None:
            if isinstance(content, str):
                return content

            return json.dumps(
                content,
                ensure_ascii=False,
                indent=2,
                default=str,
            )

        return str(result)

    async def _serve_locked(
        self,
        client_message: str,
        chat_id: str,
        conversation_history: Optional[
            List[Dict[str, Any]]
        ] = None,
    ) -> Optional[str]:
        """
        Process ONE raw client message.

        Returns:
        - None while silently observing / when there is no new action.
        - Tool output when a tool is executed.
        """
        if not client_message:
            return None

        conversation_history = (
            conversation_history or []
        )

        state = await self._load_state(
            chat_id
        )

        decision = await self._classify(
            client_message=client_message,
            conversation_history=conversation_history,
            state=state,
        )

        print(
            "\n" + "=" * 52
        )
        print(
            "MAIN AGENT DECISION"
        )
        print(
            "=" * 52
        )
        print(
            f"Actionable Intent: "
            f"{decision.has_actionable_intent}"
        )
        print(
            f"Intended Action:   "
            f"{decision.intended_action}"
        )
        print(
            f"Relevant Message:  "
            f"{decision.relevant_to_pending_request}"
        )
        print(
            f"New Request:       "
            f"{decision.new_request}"
        )
        print(
            f"Explicit Rerun:    "
            f"{decision.explicit_rerun}"
        )
        print(
            f"Execute Early:     "
            f"{decision.execute_early}"
        )
        print(
            f"Parameters:        "
            f"{decision.action_parameters}"
        )
        print(
            "=" * 52 + "\n"
        )

        # ----------------------------------------------------------
        # No actionable request.
        # ----------------------------------------------------------
        if (
            not decision.has_actionable_intent
            or not decision.intended_action
        ):
            await self._save_state(
                chat_id,
                state,
            )

            print(
                "[MAIN AGENT] No new tool action."
            )
            return None

        if (
            decision.intended_action
            not in self._tools_by_name
        ):
            print(
                "[MAIN AGENT] LLM selected an "
                "unknown tool. Ignoring."
            )
            return None

        pending_action = state.get(
            "pending_action"
        )

        # ----------------------------------------------------------
        # Start a new observation window when needed.
        # ----------------------------------------------------------
        if (
            pending_action is None
            or decision.new_request
            or decision.explicit_rerun
            or pending_action != decision.intended_action
        ):
            state["pending_action"] = (
                decision.intended_action
            )
            state["observation_count"] = 0
            state["observation_target"] = (
                random.randint(3, 5)
            )
            state["collected_parameters"] = {}

            print(
                "[MAIN AGENT] New observation "
                f"window started: target="
                f"{state['observation_target']}"
            )

        # ----------------------------------------------------------
        # Merge newly extracted values.
        # ----------------------------------------------------------
        state["collected_parameters"] = (
            self._merge_parameters(
                state.get(
                    "collected_parameters",
                    {},
                ),
                decision.action_parameters,
            )
        )

        # Count only relevant client messages.
        # First actionable message is relevant even if the classifier
        # does not explicitly mark it.
        if (
            decision.relevant_to_pending_request
            or state["observation_count"] == 0
        ):
            state["observation_count"] += 1

        observation_target = (
            state.get("observation_target")
            or 3
        )

        should_execute = (
            decision.execute_early
            or state["observation_count"]
            >= observation_target
        )

        if not should_execute:
            await self._save_state(
                chat_id,
                state,
            )

            print(
                "[MAIN AGENT] Observing request "
                f"({state['observation_count']}/"
                f"{observation_target})."
            )
            return None

        tool_name = state["pending_action"]

        complete_parameters = (
            self._complete_parameters_with_none(
                tool_name,
                state.get(
                    "collected_parameters",
                    {},
                ),
            )
        )

        fingerprint = self._fingerprint(
            tool_name,
            complete_parameters,
        )

        completed_actions = state.get(
            "completed_actions",
            [],
        )

        duplicate = any(
            item.get("fingerprint")
            == fingerprint
            for item in completed_actions
        )

        # Exact same action is always blocked unless the client
        # explicitly asked to rerun it.
        if (
            duplicate
            and not decision.explicit_rerun
        ):
            print(
                "[MAIN AGENT] Duplicate action "
                "blocked."
            )

            state["pending_action"] = None
            state["observation_count"] = 0
            state["observation_target"] = None
            state["collected_parameters"] = {}

            await self._save_state(
                chat_id,
                state,
            )

            return None

        try:
            result = await self._invoke_tool(
                tool_name,
                complete_parameters,
            )

        except Exception as exc:
            # The user explicitly wants missing tool fields passed as
            # None. If an MCP tool schema itself rejects null for one of
            # those fields, the real tool/schema error is returned.
            print(
                f"[MAIN AGENT TOOL ERROR] {exc}"
            )

            await self._save_state(
                chat_id,
                state,
            )

            return str(exc)

        completed_actions.append({
            "tool": tool_name,
            "parameters": complete_parameters,
            "fingerprint": fingerprint,
        })

        state["completed_actions"] = (
            completed_actions[
                -self.MAX_COMPLETED_ACTIONS:
            ]
        )

        # Clear current observation state after successful execution.
        state["pending_action"] = None
        state["observation_count"] = 0
        state["observation_target"] = None
        state["collected_parameters"] = {}

        await self._save_state(
            chat_id,
            state,
        )

        print(
            "[MAIN AGENT] Tool completed and "
            "fingerprint stored."
        )

        return self._tool_result_to_string(
            result
        )


    async def serve(
        self,
        client_message: str,
        chat_id: str,
        conversation_history: Optional[
            List[Dict[str, Any]]
        ] = None,
    ) -> Optional[str]:
        """
        Serialize processing per client so rapid WhatsApp messages cannot
        race the observation counter or duplicate-action ledger.
        """
        lock = self._chat_locks.setdefault(
            str(chat_id),
            asyncio.Lock(),
        )

        async with lock:
            return await self._serve_locked(
                client_message=client_message,
                chat_id=chat_id,
                conversation_history=conversation_history,
            )


llm_server = LLMServer()
