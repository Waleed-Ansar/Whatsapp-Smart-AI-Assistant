from typing import List, Dict, Any
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from datetime import datetime
from models import IntentDecision

from config import config
from llm import llm_server


class Gatekeeper:
    def __init__(self):
        self.llm = ChatOpenAI(
            base_url=config.LLM_API_URL,
            model="deepseek-chat",
            api_key=config.LLM_API_KEY,
            temperature=0.0
        ).with_structured_output(IntentDecision, method="function_calling")
        
        self._cached_tool_descriptions = ""

    async def _get_tool_schemas(self) -> str:
        """Fetches tools from the MCP Server and formats them for the prompt."""
        if self._cached_tool_descriptions:
            return self._cached_tool_descriptions

        try:
            tools = await llm_server.get_mcp_tools()
            
            descriptions = []
            for tool in tools:
                schema_dict = {}

                if hasattr(tool, "args_schema") and tool.args_schema:
                    if isinstance(tool.args_schema, dict):
                        schema_dict = tool.args_schema

                    elif hasattr(tool.args_schema, "model_json_schema"):
                        schema_dict = tool.args_schema.model_json_schema()

                    elif hasattr(tool.args_schema, "schema"):
                        schema_dict = tool.args_schema.schema()

                elif hasattr(tool, "input_schema") and isinstance(tool.input_schema, dict):
                    schema_dict = tool.input_schema

                reqs = schema_dict.get("required", [])
                
                descriptions.append(f"- Tool: {tool.name}\n  Requires: {reqs}")
            
            self._cached_tool_descriptions = "\n".join(descriptions)
            return self._cached_tool_descriptions
            
        except Exception as e:
            print(f"Failed to fetch MCP schemas for Gatekeeper: {e}")
            return "No tool schemas available."

    async def evaluate(self, full_history: List[Dict[str, str]]) -> IntentDecision:
        """
        Evaluates the tail end of the conversation to determine execution readiness.
        """
        current_time = datetime.now().strftime("%A, %B %d, %Y %I:%M %p")

        recent_history = full_history[-5:]

        tool_schemas = await self._get_tool_schemas()

        system_instructions = f"""
        You are a strict Gatekeeper for a real estate AI copilot. Your job is to prevent incomplete thoughts from triggering backend tools.
        
        CURRENT DATE AND TIME: {current_time}
        
        Here are the available tools and their strictly REQUIRED parameters:
        {tool_schemas}
        
        RULES:
        1. Read the user's chronological chat log. This log spans multiple days. 
        2. Resolve temporal references: If the user says "yesterday" or "the previous property", look at the older messages in the log to find the missing variables.
        3. Extract any explicitly mentioned parameters and place them in 'action_parameters' as a JSON key-value mapping.
        4. Check if the user has provided ALL the required parameters for their intended Tool.
        5. If a required parameter is missing from the entire context, set 'is_ready' to FALSE and list the missing fields.
        6. If the sentence is grammatically cut off, set 'is_ready' to FALSE.
        """

        messages = [SystemMessage(content=system_instructions)]
        for msg in recent_history:
            messages.append(HumanMessage(content=f"{msg['role']}: {msg['content']}"))

        try:
            decision: IntentDecision = await self.llm.ainvoke(messages)
            return decision

        except Exception as e:
            print(f"Gatekeeper parsing error: {e}")
            return IntentDecision(
                is_ready=False,
                confidence=0.0,
                intended_action=None,
                required_fields=[],
                missing_fields=[]
            )

gatekeeper_agent = Gatekeeper()