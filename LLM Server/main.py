from fastapi import FastAPI
from models import RequestModel, ResponseModel
import requests

from llm import llm_server


app = FastAPI(title="Whatsapp Agent")

@app.get("/")
async def root():
    return {
        "status": True,
        "message": "Whatsapp Agent is running."
    }

@app.get("/health")
async def health_check():
    headers = {
        "Authorization": "Bearer fmcp_pZ4dNc02HQM0JVxaekOqEYhdtksik6Y9tl0aKJ3f9vo"
    }
    response = requests.get("http://deep-mcp.fastmcp.app/status", headers=headers)
    print(response.content)
    return {
        "status": response.status_code,
        "message": response.json()
    }

@app.post("/fetch-messages", response_model=ResponseModel)
async def listener_agent(request: RequestModel):
    try:
        # Call the serve function from llm.py with the provided query and chat_id
        response_message = await llm_server.serve(query=request.query, chat_id=request.chat_id)
        return ResponseModel(chat_id=request.chat_id, status=True, message=response_message)

    except Exception as e:
        return ResponseModel(chat_id=request.chat_id, status=False, message="", error=str(e))