from datetime import datetime

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from langgraph.graph import StateGraph, START, END

from models import IntentDecision, GatekeeperState
from config import config
from llm import llm_server
from knowledge import knowledge_manager


class Gatekeeper:

    def __init__(self):
        self.llm = ChatOpenAI(
            base_url=config.LLM_API_URL,
            model=config.LLM_MODEL_NAME or "deepseek-chat",
            api_key=config.LLM_API_KEY,
            temperature=0.0
        ).with_structured_output(
            IntentDecision,
            method="function_calling"
        )

        self._cached_tool_descriptions = ""

        # LangGraph / Redis
        self.checkpointer = None
        self.graph = None

        # Domain knowledge
        self.knowledge_context = None

    # ============================================================
    # INITIALIZATION
    # ============================================================

    async def initialize(self, checkpointer):
        """
        Initialize LangGraph with the Redis checkpointer.

        The Redis checkpointer is created and managed by FastAPI's
        lifespan. We only receive it here and compile the graph with it.
        """

        self.checkpointer = checkpointer

        # --------------------------------------------------------
        # Load domain knowledge
        # --------------------------------------------------------

        if not knowledge_manager.is_loaded():
            knowledge_manager.load()

        self.knowledge_context = (
            knowledge_manager.get_llm_context()
        )

        # --------------------------------------------------------
        # Build LangGraph
        # --------------------------------------------------------

        builder = StateGraph(GatekeeperState)

        builder.add_node(
            "gatekeeper",
            self._gatekeeper_node
        )

        builder.add_edge(
            START,
            "gatekeeper"
        )

        builder.add_edge(
            "gatekeeper",
            END
        )

        self.graph = builder.compile(
            checkpointer=self.checkpointer
        )

        print(
            "[GATEKEEPER] LangGraph + Redis initialized"
        )

        print(
            "[GATEKEEPER] Domain knowledge loaded"
        )

    # ============================================================
    # MCP TOOL SCHEMAS
    # ============================================================

    async def _get_tool_schemas(self) -> str:

        if self._cached_tool_descriptions:
            return self._cached_tool_descriptions

        try:

            tools = await llm_server.get_mcp_tools()

            descriptions = []

            for tool in tools:

                schema_dict = (
                    getattr(tool, "args_schema", None)
                    or getattr(tool, "input_schema", None)
                    or {}
                )

                if hasattr(
                    schema_dict,
                    "model_json_schema"
                ):
                    schema_dict = (
                        schema_dict.model_json_schema()
                    )

                reqs = (
                    schema_dict.get(
                        "required",
                        []
                    )
                    if isinstance(schema_dict, dict)
                    else []
                )

                descriptions.append(
                    f"- Tool: {tool.name}\n"
                    f"  Required Arguments: {reqs}"
                )

            self._cached_tool_descriptions = (
                "\n".join(descriptions)
            )

            return self._cached_tool_descriptions

        except Exception as e:

            print(
                f"[GATEKEEPER] Error loading MCP schemas: {e}"
            )

            return (
                "RTD Tools: MAR, Intelligence, Upcoming, "
                "Objections, Dual Key, Investment Index, "
                "Project Comparison."
            )

    # ============================================================
    # LANGGRAPH NODE
    # ============================================================

    async def _gatekeeper_node(
        self,
        state: GatekeeperState
    ):

        messages = state["messages"]

        # --------------------------------------------------------
        # Safety check
        # --------------------------------------------------------

        if not messages:

            decision = IntentDecision(
                is_ready=False,
                intended_action=None,
                required_fields=[],
                missing_fields=[]
            )

            return {
                "decision": decision.model_dump()
            }

        # --------------------------------------------------------
        # Current date/time
        # --------------------------------------------------------

        current_time = datetime.now().strftime(
            "%A, %B %d, %Y %I:%M %p"
        )

        # --------------------------------------------------------
        # MCP tools
        # --------------------------------------------------------

        tool_schemas = await self._get_tool_schemas()

        # --------------------------------------------------------
        # Conversation history
        # --------------------------------------------------------

        prior_messages = messages[:-1]

        history_lines = []

        for message in prior_messages[-10:]:

            role = getattr(
                message,
                "type",
                "user"
            )

            content = getattr(
                message,
                "content",
                ""
            )

            if role == "human":
                role = "USER"

            elif role == "ai":
                role = "ASSISTANT"

            elif role == "tool":
                role = "TOOL"

            else:
                role = role.upper()

            history_lines.append(
                f"{role}: {content}"
            )

        history_block = (
            "\n".join(history_lines)
            if history_lines
            else "No prior history."
        )

        # --------------------------------------------------------
        # Latest message
        # --------------------------------------------------------

        latest_message = messages[-1]

        latest_content = getattr(
            latest_message,
            "content",
            ""
        )

        # --------------------------------------------------------
        # Make sure knowledge is available
        # --------------------------------------------------------

        if self.knowledge_context is None:

            knowledge_manager.load()

            self.knowledge_context = (
                knowledge_manager.get_llm_context()
            )

        # ========================================================
        # YOUR ORIGINAL PROMPT
        #
        # DO NOT CHANGE
        # ========================================================

        system_instructions = f"""
            You are the silent RTD Advisor Background Copilot
            for real estate agents.

            Your job is to detect when a client conversation
            requires generating an RTD Advisor Report.

            CURRENT DATE & TIME:
            {current_time}

            AVAILABLE MCP TOOLS:
            {tool_schemas}


            RULES:

            1. SILENT ON CHIT-CHAT & GRATITUDE

            If the latest message is a greeting ("hi", "hello"),
            gratitude ("thank you", "thanks"), acknowledgment
            ("ok", "got it"), or general chat:

            - intended_action = null
            - is_ready = false


            2. DO NOT RE-TRIGGER COMPLETED REPORTS

            Use the prior conversation history.

            If the assistant already generated a report or
            provided report details for a property/project,
            do NOT automatically generate that same report again.

            A user merely mentioning the same property is NOT
            enough to regenerate the report.


            3. EXPLICIT REGENERATION

            Generate the same report again ONLY if the user
            explicitly requests it.

            Examples:

            - generate the report again
            - regenerate the report
            - run the report again
            - give me that report again
            - repeat the report


            4. FOLLOW-UP QUESTIONS

            A question about information contained in a
            previous report is NOT automatically a request
            to regenerate the report.

            Example:

            Previous:
            "RTD report for Project X has been generated."

            User:
            "What is the rental yield?"

            Do NOT regenerate the report.


            5. NEW INFORMATION

            If the user explicitly requests an RTD report
            using materially new property/data parameters,
            treat it as a new report request.


            6. QUOTED / SWIPED MESSAGES

            If the latest message contains:

            [In reply to: "..."]

            extract relevant parameters from the quoted
            message.

            Possible parameters include:

            - project
            - property
            - location
            - budget
            - bedrooms
            - other required report parameters


            7. TRIGGERING CONDITIONS

            Set is_ready = true when ALL of the following are true:

            - The latest user message contains an actionable request
            corresponding to one of the available MCP tools.
            - The intended_action matches that requested operation.
            - All required parameters for that action are available.
            - The request has not already been fulfilled for the same
            parameters, unless the user explicitly asks to repeat,
            regenerate, or run it again.

            IMPORTANT:

            Do NOT require the user to explicitly use the words
            "RTD report".

            If the available MCP tool is the appropriate action for
            the user's request, treat that as an actionable request.

            For example:

            User:
            "Give me property details for Orchard Road in Orchard, Singapore"

            If the required parameters are:

            city = Singapore
            district = Orchard
            area = Orchard Road

            then:

            intended_action = get_property_details
            missing_fields = []
            is_ready = true

            provided that this exact request has not already been fulfilled.
            
            READINESS RULE — CRITICAL:

            You MUST determine `is_ready` from the information available in the CURRENT conversation.

            Set `is_ready` to TRUE when ALL required information for the selected `intended_action` has been identified and:
            - `missing_fields` is an empty list []
            - `intended_action` is a valid action
            - `action_parameters` contains the required parameters for that action

            Set `is_ready` to FALSE when ANY required information is still missing.

            IMPORTANT:
            `is_ready` and `missing_fields` MUST always agree.

            If:
            - `missing_fields = []`
            - `intended_action` is valid
            - all required `action_parameters` are available

            THEN:
            `is_ready = true`

            If:
            - `missing_fields` contains one or more fields

            THEN:
            `is_ready = false`

            NEVER return `is_ready = false` when `missing_fields = []` and all required parameters for the intended action are present.

            You must not invent information.

            ==============================
            DOMAIN KNOWLEDGE
            ==============================

            {self.knowledge_context}

            ==============================
            END DOMAIN KNOWLEDGE
            ==============================
        """

        # ========================================================
        # USER INPUT
        # ========================================================

        user_content = f"""
            ### PRIOR CONVERSATION HISTORY:

            {history_block}


            ### LATEST INCOMING MESSAGE:

            {latest_content}
        """

        # ========================================================
        # LLM CALL
        # ========================================================

        try:

            decision = await self.llm.ainvoke(
                [
                    SystemMessage(
                        content=system_instructions
                    ),
                    HumanMessage(
                        content=user_content
                    )
                ]
            )

            print(
                "[GATEKEEPER] Decision generated"
            )

            return {
                "decision": decision.model_dump()
            }

        except Exception as e:

            print(
                f"[GATEKEEPER ERROR]: {e}"
            )

            decision = IntentDecision(
                is_ready=False,
                intended_action=None,
                required_fields=[],
                missing_fields=[]
            )

            return {
                "decision": decision.model_dump()
            }

    # ============================================================
    # PUBLIC EVALUATION
    # ============================================================

    async def evaluate(
        self,
        agent_id: str,
        client_phone: str,
        latest_message: str
    ) -> IntentDecision:

        if not self.graph:

            raise RuntimeError(
                "Gatekeeper has not been initialized. "
                "Call initialize() during FastAPI startup."
            )

        # --------------------------------------------------------
        # Unique conversation thread
        # --------------------------------------------------------

        thread_id = (
            f"agent:{agent_id}:client:{client_phone}"
        )

        graph_config = {
            "configurable": {
                "thread_id": thread_id
            }
        }

        # --------------------------------------------------------
        # Send new user message to LangGraph
        #
        # The Redis checkpointer keeps the previous state for
        # this agent/client thread.
        # --------------------------------------------------------

        result = await self.graph.ainvoke(
            {
                "messages": [
                    HumanMessage(
                        content=latest_message
                    )
                ]
            },
            graph_config
        )

        # --------------------------------------------------------
        # Extract decision
        # --------------------------------------------------------

        decision_data = result.get(
            "decision"
        )

        if not decision_data:

            return IntentDecision(
                is_ready=False,
                intended_action=None,
                required_fields=[],
                missing_fields=[]
            )

        return IntentDecision(
            **decision_data
        )


# ================================================================
# GLOBAL GATEKEEPER INSTANCE
# ================================================================

gatekeeper_agent = Gatekeeper()