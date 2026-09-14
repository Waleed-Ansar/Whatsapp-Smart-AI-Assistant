from fastapi import FastAPI, Request, Response
from models import RequestModel, ResponseModel
import requests, json

from config import config
from services import redis_manager
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
        "Authorization": f"Bearer {config.MCP_SERVER_API_KEY}"
    }
    response = requests.get(config.SERVER_HEALTH_CHECK_URL, headers=headers)

    return {
        "status": response.status_code,
        "message": response.json()
    }

@app.post("/webhook")
async def receive_whatsapp_event(request: Request):
    payload = await request.json()

    try:
        value = payload["entry"][0]["changes"][0]["value"]

        if "messages" in value:
            client_msg = value["messages"][0]
            phone = client_msg["from"]
            text = client_msg["text"]["body"]

            # 1. Append message & refresh 15-minute TTL
            redis_manager.append_client_message(phone=phone, text=text)

            # 2. Fetch entire current client message buffer
            full_history = redis_manager.get_conversation_history(phone=phone)

            # 3. Pass history to your LLM Intent Classifier
            # intent_result = evaluate_intent(full_history)
            
    except (KeyError, IndexError):
        pass

    return {"status": "success"}

@app.post("/fetch-messages", response_model=ResponseModel)
async def listener_agent(request: RequestModel):
    try:
        # Call the serve function from llm.py with the provided query and chat_id
        response_message = await llm_server.serve(query=request.query, chat_id=request.chat_id)
        return ResponseModel(chat_id=request.chat_id, status=True, message=response_message)

    except Exception as e:
        return ResponseModel(chat_id=request.chat_id, status=False, message="", error=str(e))