from fastapi import FastAPI, Request, Response, Query
from typing import Optional


from redis_manager import redis_manager

app = FastAPI(title="WhatsApp Webhook Demo")

# Initialize Redis connection
r = redis_manager

# Replace with the verify token you set in your Meta Developer Dashboard
VERIFY_TOKEN = "my_demo_secret_token"


@app.get("/webhook")
async def verify_webhook(
    mode: Optional[str] = Query(None, alias="hub.mode"),
    token: Optional[str] = Query(None, alias="hub.verify_token"),
    challenge: Optional[str] = Query(None, alias="hub.challenge")
):
    """Meta Webhook Verification Endpoint (GET)."""
    if mode == "subscribe" and token == VERIFY_TOKEN:
        print(" Verification successful!")
        return Response(content=challenge, media_type="text/plain")
    return Response(content="Verification failed", status_code=403)


@app.post("/webhook")
async def receive_whatsapp_event(request: Request):
    payload = await request.json()

    try:
        value = payload["entry"][0]["changes"][0]["value"]
        
        if "messages" in value:
            client_msg = value["messages"][0]
            phone = "".join(filter(str.isdigit, client_msg["from"]))
            
            if client_msg.get("type") == "text":
                text = client_msg["text"]["body"]
                
                # 1. Append message and refresh TTL via your manager
                redis_manager.append_client_message(phone=phone, text=text)

                # 2. Inspect accumulated buffer
                history = redis_manager.get_conversation_history(phone=phone)
                print(f"[REDIS STORED] {phone}: {history}")

    except (KeyError, IndexError):
        pass

    return {"status": "success"}


@app.get("/demo/history/{phone}")
async def get_client_history(phone: str):
    """Demonstration Endpoint: Inspect accumulated Redis history for a phone number."""
    # 1. Fetch formatted history directly from your manager
    history = redis_manager.get_conversation_history(phone)
    
    # 2. Get TTL from the underlying Redis client instance
    clean_phone = "".join(filter(str.isdigit, phone))
    ttl = redis_manager.r.ttl(f"session:{clean_phone}")

    return {
        "phone": clean_phone,
        "ttl_seconds_remaining": ttl,
        "total_messages": len(history),
        "history": history
    }