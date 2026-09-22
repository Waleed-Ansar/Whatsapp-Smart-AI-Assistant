from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
from langgraph.graph import MessagesState
from dataclasses import dataclass


class RequestModel(BaseModel):
    chat_id: str
    query: str


class ResponseModel(BaseModel):
    chat_id: str
    status: bool = False
    message: str
    error: str = None


class IntentDecision(BaseModel):
    is_ready: bool = Field(
        description="True ONLY if the user's thought is complete AND all required fields for their intended tool are present."
    )
    intended_action: str | None = Field(
        default=None,
        description="The name of the tool the user is trying to trigger (e.g., 'compare_properties', 'mar_report')."
    )
    action_parameters: Dict[str, Any] | None = Field(
        default=None,
        description="A JSON object containing the extracted values for the tool's parameters (e.g., {'city': 'london', 'price': 12000000}). Only populate with explicitly mentioned data."
    )
    required_fields: List[str] = Field(
        description="The parameters strictly required by the intended tool."
    )
    missing_fields: List[str] | None = Field(
        default=None,
        description="Which of those required parameters are missing from the conversation."
    )


class GatekeeperState(MessagesState):
    decision: Optional[dict]


@dataclass
class KnowledgeSection:
    category: str
    file_name: str
    relative_path: str
    content: Any


@dataclass
class KnowledgeStats:
    total_files: int
    json_files: int
    markdown_files: int
    text_files: int
    categories: int