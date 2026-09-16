from typing import Optional
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Query, Response, BackgroundTasks
import requests

from config import config
from redis_manager import redis_manager
from gatekeeper import gatekeeper_agent
from database import db_manager


# --- Lifespan: Ensures Mongo Indexes on Startup ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("Booting up: Setting up MongoDB Indexes...")
    await db_manager.setup_indexes()
    yield
    print("Shutting down...")
    db_manager.close()


app = FastAPI(title="Whatsapp Agent", lifespan=lifespan)


# --- The Debounce Callback ---
async def process_gatekeeper_flow(phone: str, aggregated_text: str):
    print(f"\n[10-SECOND LOCK CLEARED] Processing for {phone}: '{aggregated_text}'")
    
    # 1. Save text to MongoDB
    await db_manager.save_message_to_window(phone, role="user", text=aggregated_text)
    
    # 2. Fetch history (7-day memory!)
    history = await db_manager.get_recent_messages(phone, limit=5)
    
    # 3. Evaluate via DeepSeek-Chat Gatekeeper
    decision = await gatekeeper_agent.evaluate(history)
    
    # 4. Print the final routing decision
    print("\n" + "="*40)
    print("🧠 GATEKEEPER DECISION")
    print("="*40)
    print(f"Is Ready:          {decision.is_ready}")
    print(f"Intended Action:   {decision.intended_action}")
    print(f"Missing Fields:    {decision.missing_fields}")
    print(f"Action Parameters: {decision.action_parameters}")
    print("="*40 + "\n")


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


@app.get("/webhook")
async def verify_webhook(
    mode: Optional[str] = Query(None, alias="hub.mode"),
    token: Optional[str] = Query(None, alias="hub.verify_token"),
    challenge: Optional[str] = Query(None, alias="hub.challenge")
):
    """Meta Webhook Verification Endpoint (GET)."""
    if mode == "subscribe" and token == config.WHATSAPP_VERIFY_TOKEN:
        print(" Verification successful!")
        return Response(content=challenge, media_type="text/plain")
    return Response(content="Verification failed", status_code=403)


@app.post("/webhook")
async def receive_whatsapp_event(request: Request, background_tasks: BackgroundTasks):
    payload = await request.json()

    try:
        value = payload["entry"][0]["changes"][0]["value"]

        if "messages" in value:
            client_msg = value["messages"][0]
            
            # Guard against media, audio, or reaction payloads
            if client_msg.get("type") == "text":
                phone = client_msg["from"]
                text = client_msg["text"]["body"]

                # 1. Push raw message to Redis buffer & reset the 10-second typing lock
                await redis_manager.stack_incoming_message(phone=phone, text=text)

                # 2. Spawn monitor task if one is not already watching this phone
                if phone not in redis_manager.active_monitors:
                    redis_manager.active_monitors.add(phone)
                    background_tasks.add_task(
                        redis_manager.monitor_typing_lock, 
                        phone, 
                        process_gatekeeper_flow
                    )
            
    except (KeyError, IndexError):
        pass

    # Instantly return 200 OK to Meta so it never times out
    return {"status": "success"}


@app.get("/demo/history/{phone}")
async def get_client_history(phone: str):
    """Demonstration Endpoint: Inspect accumulated Redis history for a phone number."""
    history = await redis_manager.get_conversation_history(phone)
    clean_phone = "".join(filter(str.isdigit, phone))
    ttl = await redis_manager.r.ttl(f"session:{clean_phone}")

    return {
        "phone": clean_phone,
        "ttl_seconds_remaining": ttl,
        "total_messages": len(history),
        "history": history
    }


@app.get("/demo/db-test/{phone}")
async def test_mongodb(phone: str):
    """Developer Endpoint: Test MongoDB inserting and fetching."""
    test_message = "Hello! This is a test message to check MongoDB."
    await db_manager.save_message_to_window(
        phone=phone, 
        role="user", 
        text=test_message
    )
    recent_history = await db_manager.get_recent_messages(phone=phone, limit=5)
    
    return {
        "status": "success",
        "message_inserted": test_message,
        "database_history": recent_history
    }