from typing import Optional
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Query, Response, BackgroundTasks
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
import requests, httpx

from config import config
from redis_manager import redis_manager
from services import process_gatekeeper_flow
from database import db_manager


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("Booting up: Setting up MongoDB Indexes...")
    await db_manager.setup_indexes()
    yield
    print("Shutting down...")
    db_manager.close()


app = FastAPI(title="Whatsapp Agent", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    """Serves the minimal Meta Onboarding Portal."""
    # This assumes your index.html file is in the exact same directory as main.py
    return FileResponse("index.html")
    

# @app.get("/")
# async def root():
#     return {
#         "status": True,
#         "message": "Whatsapp Agent is running."
#     }


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

            if client_msg.get("type") == "text":
                phone = client_msg["from"]
                text = client_msg["text"]["body"]

                await redis_manager.stack_incoming_message(phone=phone, text=text)

                if phone not in redis_manager.active_monitors:
                    redis_manager.active_monitors.add(phone)
                    background_tasks.add_task(
                        redis_manager.monitor_typing_lock, 
                        phone, 
                        process_gatekeeper_flow
                    )
            
    except (KeyError, IndexError):
        pass

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


@app.post("/api/connect-whatsapp")
async def connect_whatsapp_account(request: Request):
    """
    Receives the OAuth code from the minimal frontend and exchanges it 
    for a permanent access token via the Meta Graph API.
    """
    payload = await request.json()
    code = payload.get("code")
    
    if not code:
        return {"status": "error", "message": "No authorization code provided."}

    # 1. Exchange the code for a permanent access token
    token_url = f"https://graph.facebook.com/v20.0/oauth/access_token"
    token_payload = {
        "client_id": "2409051026256039",
        "client_secret": "ddddc907ac98c1a6cbe1b247158fa802",
        "code": code,
        "grant_type": "authorization_code",
        # redirect_uri must match exactly what you configured in the Meta dashboard!
        "redirect_uri": "http://localhost:8000/" 
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(token_url, data=token_payload)
        token_data = response.json()

        if "access_token" in token_data:
            access_token = token_data["access_token"]
            
            # 2. Grab the tenant's Phone Number ID (Optional: Fetch it via Graph API using the new token)
            # For simplicity, you can pass phone_number_id from the frontend, or make a GET request to 
            # https://graph.facebook.com/v20.0/me/accounts to map it automatically.
            phone_number_id = "extracted_phone_number_id" 
            
            # 3. Save directly to your MongoDB 'tenants' collection
            await db_manager.db.tenants.update_one(
                {"phone_number_id": phone_number_id},
                {
                    "$set": {
                        "access_token": access_token,
                        "client_name": "New Agent", # Update this dynamically based on their portal login
                        "status": "active"
                    }
                },
                upsert=True
            )
            
            return {"status": "success", "message": "Tenant connected perfectly."}
            
        else:
            print(f"Meta Token Exchange Error: {token_data}")
            return {"status": "error", "message": "Failed to exchange token with Meta."}