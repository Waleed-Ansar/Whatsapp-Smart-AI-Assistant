from pydantic import BaseModel


class RequestModel(BaseModel):
    chat_id: str
    query: str

class ResponseModel(BaseModel):
    chat_id: str
    status: bool = False
    message: str
    error: str = None