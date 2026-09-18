# from typing import Optional
# from contextlib import asynccontextmanager
# from datetime import datetime
# import httpx
# from fastapi import FastAPI, Request, Query, Response, BackgroundTasks
# from fastapi.responses import FileResponse
# from fastapi.middleware.cors import CORSMiddleware

# from config import config
# from redis_manager import redis_manager
# from services import process_gatekeeper_flow
# from database import db_manager


# @asynccontextmanager
# async def lifespan(app: FastAPI):
#     print("Booting up: Setting up MongoDB Indexes...")
#     await db_manager.setup_indexes()
#     yield
#     print("Shutting down...")
#     db_manager.close()


# app = FastAPI(title="Whatsapp Agent", lifespan=lifespan)

# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=["*"],
#     allow_credentials=True,
#     allow_methods=["*"],
#     allow_headers=["*"],
# )


# @app.get("/")
# async def root():
#     """Serves the minimal Meta Onboarding Portal."""
#     return FileResponse("index.html")


# @app.get("/health")
# async def health_check():
#     """Non-blocking health check against the MCP server."""
#     headers = {"Authorization": f"Bearer {config.MCP_SERVER_API_KEY}"}
    
#     async with httpx.AsyncClient() as client:
#         try:
#             response = await client.get(config.SERVER_HEALTH_CHECK_URL, headers=headers, timeout=5.0)
#             return {
#                 "status": response.status_code,
#                 "message": response.json()
#             }
#         except Exception as e:
#             return {"status": 500, "error": str(e)}


# @app.get("/webhook")
# async def verify_webhook(
#     mode: Optional[str] = Query(None, alias="hub.mode"),
#     token: Optional[str] = Query(None, alias="hub.verify_token"),
#     challenge: Optional[str] = Query(None, alias="hub.challenge")
# ):
#     """Meta Webhook Verification Endpoint (GET)."""
#     print(f"[LOG EVENT AT {datetime.now()}] EVENT: GET WEBHOOK")
#     if mode == "subscribe" and token == config.WHATSAPP_VERIFY_TOKEN:
#         print("Verification successful!")
#         return Response(content=challenge, media_type="text/plain")
#     return Response(content="Verification failed", status_code=403)


# @app.post("/webhook")
# async def receive_whatsapp_event(request: Request, background_tasks: BackgroundTasks):
#     """Meta Webhook Receiver Endpoint (POST)."""
#     payload = await request.json()

#     try:
#         entry = payload.get("entry", [])[0]
#         changes = entry.get("changes", [])[0]
#         value = changes.get("value", {})

#         if "messages" in value:
#             client_msg = value["messages"][0]
#             msg_id = client_msg.get("id")

#             # Deduplication: Drop if Meta retries an already processed message ID
#             if await redis_manager.r.get(f"processed:{msg_id}"):
#                 return {"status": "already_processed"}
#             await redis_manager.r.set(f"processed:{msg_id}", "1", ex=300)

#             if client_msg.get("type") == "text":
#                 phone = client_msg["from"]
#                 text = client_msg["text"]["body"]

#                 # Cache incoming text by ID for 24h so future swipe-replies can read it
#                 await redis_manager.r.set(f"msg:{msg_id}", text, ex=86400)

#                 # Check if this message is a swipe-to-reply quoting an earlier message
#                 if "context" in client_msg:
#                     quoted_msg_id = client_msg["context"].get("id")
#                     quoted_text = await redis_manager.r.get(f"msg:{quoted_msg_id}")
#                     if quoted_text:
#                         text = f"[In reply to: \"{quoted_text}\"]\n{text}"

#                 # Stack user message and monitor typing lock for debounce
#                 await redis_manager.stack_incoming_message(phone=phone, text=text)

#                 if phone not in redis_manager.active_monitors:
#                     redis_manager.active_monitors.add(phone)
#                     background_tasks.add_task(
#                         redis_manager.monitor_typing_lock, 
#                         phone, 
#                         process_gatekeeper_flow
#                     )

#     except (KeyError, IndexError, Exception) as e:
#         print(f"Webhook processing error: {e}")

#     # Always return HTTP 200 immediately to prevent Meta timeout retries
#     return {"status": "success"}


# @app.get("/demo/history/{phone}")
# async def get_client_history(phone: str):
#     """Inspect accumulated Redis history for a phone number."""
#     history = await redis_manager.get_conversation_history(phone)
#     clean_phone = "".join(filter(str.isdigit, phone))
#     ttl = await redis_manager.r.ttl(f"session:{clean_phone}")

#     return {
#         "phone": clean_phone,
#         "ttl_seconds_remaining": ttl,
#         "total_messages": len(history),
#         "history": history
#     }


# @app.get("/demo/db-test/{phone}")
# async def test_mongodb(phone: str):
#     """Test MongoDB message insert and query."""
#     test_message = "Hello! This is a test message to check MongoDB."
#     await db_manager.save_message_to_window(
#         phone=phone, 
#         role="user", 
#         text=test_message
#     )
#     recent_history = await db_manager.get_recent_messages(phone=phone, limit=5)
    
#     return {
#         "status": "success",
#         "message_inserted": test_message,
#         "database_history": recent_history
#     }


# @app.post("/api/connect-whatsapp")
# async def connect_whatsapp_account(request: Request):
#     """
#     Exchanges OAuth code from frontend for an access token and links
#     the tenant in MongoDB.
#     """
#     payload = await request.json()
#     code = payload.get("code")
#     phone_number_id = payload.get("phone_number_id")
#     waba_id = payload.get("waba_id")
#     client_name = payload.get("client_name", "New Agent")
    
#     if not code:
#         return {"status": "error", "message": "No authorization code provided."}

#     token_url = "https://graph.facebook.com/v21.0/oauth/access_token"
#     token_params = {
#         "client_id": config.META_APP_ID,
#         "client_secret": config.META_APP_SECRET,
#         "code": code,
#         "grant_type": "authorization_code",
#         "redirect_uri": config.META_REDIRECT_URI 
#     }

#     async with httpx.AsyncClient() as client:
#         response = await client.get(token_url, params=token_params)
#         token_data = response.json()

#         if "access_token" in token_data:
#             access_token = token_data["access_token"]
            
#             # Save tenant details to MongoDB
#             tenant_filter = {"phone_number_id": phone_number_id} if phone_number_id else {"client_name": client_name}
            
#             await db_manager.db.tenants.update_one(
#                 tenant_filter,
#                 {
#                     "$set": {
#                         "access_token": access_token,
#                         "phone_number_id": phone_number_id,
#                         "waba_id": waba_id,
#                         "client_name": client_name,
#                         "status": "active",
#                         "updated_at": datetime.utcnow()
#                     }
#                 },
#                 upsert=True
#             )
            
#             return {"status": "success", "message": "Tenant connected successfully."}
            
#         print(f"Meta Token Exchange Error: {token_data}")
#         return {
#             "status": "error", 
#             "message": "Failed to exchange token with Meta.",
#             "details": token_data
#         }


from typing import Optional
from contextlib import asynccontextmanager
from datetime import datetime
import httpx
from fastapi import FastAPI, Request, Query, Response, BackgroundTasks
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from config import config
from redis_manager import redis_manager
from services import process_gatekeeper_flow
from database import db_manager


async def resolve_quoted_message(wamid: str) -> Optional[str]:
    """Retrieves quoted message content from Redis (hot) or MongoDB (cold fallback)."""
    if not wamid:
        return None

    # 1. Hot Cache (Redis)
    cached = await redis_manager.r.get(f"msg:{wamid}")
    if cached:
        return cached if isinstance(cached, str) else cached.decode("utf-8")

    # 2. Cold Storage Fallback (MongoDB)
    db_text = await db_manager.get_message_by_wamid(wamid)
    if db_text:
        await redis_manager.r.set(f"msg:{wamid}", db_text, ex=172800)
        return db_text

    return None


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
    return FileResponse("index.html")


@app.get("/health")
async def health_check():
    headers = {"Authorization": f"Bearer {config.MCP_SERVER_API_KEY}"}
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(config.SERVER_HEALTH_CHECK_URL, headers=headers, timeout=5.0)
            return {
                "status": response.status_code,
                "message": response.json()
            }
        except Exception as e:
            return {"status": 500, "error": str(e)}


@app.get("/webhook")
async def verify_webhook(
    mode: Optional[str] = Query(None, alias="hub.mode"),
    token: Optional[str] = Query(None, alias="hub.verify_token"),
    challenge: Optional[str] = Query(None, alias="hub.challenge")
):
    print(f"[LOG EVENT AT {datetime.now()}] EVENT: GET WEBHOOK")
    if mode == "subscribe" and token == config.WHATSAPP_VERIFY_TOKEN:
        print("Verification successful!")
        return Response(content=challenge, media_type="text/plain")
    return Response(content="Verification failed", status_code=403)


@app.post("/webhook")
async def receive_whatsapp_event(request: Request, background_tasks: BackgroundTasks):
    payload = await request.json()

    try:
        entry = payload.get("entry", [])[0]
        changes = entry.get("changes", [])[0]
        value = changes.get("value", {})

        if "messages" in value:
            client_msg = value["messages"][0]
            msg_id = client_msg.get("id")

            # Deduplication: Drop retried webhook deliveries from Meta
            if await redis_manager.r.get(f"processed:{msg_id}"):
                return {"status": "already_processed"}
            await redis_manager.r.set(f"processed:{msg_id}", "1", ex=300)

            if client_msg.get("type") == "text":
                phone = client_msg["from"]
                text = client_msg["text"]["body"]

                # 1. Resolve Swipe-to-Reply Context (both user and bot quotes)
                context = client_msg.get("context")
                if context and "id" in context:
                    quoted_id = context["id"]
                    quoted_text = await resolve_quoted_message(quoted_id)
                    if quoted_text:
                        print(f"[REPLY DETECTED] Swiped message: '{quoted_text[:50]}...'")
                        text = f"[In reply to: \"{quoted_text}\"]\n{text}"
                    else:
                        print(f"[REPLY DETECTED] Quoted ID {quoted_id} not found in cache/DB.")

                # 2. Cache incoming user message in Redis (48h)
                await redis_manager.r.set(f"msg:{msg_id}", text, ex=172800)

                # 3. Store in MongoDB with its wamid
                await db_manager.save_message_to_window(
                    phone=phone,
                    role="user",
                    text=text,
                    wamid=msg_id
                )

                # 4. Push to typing lock buffer
                await redis_manager.stack_incoming_message(phone=phone, text=text)

                if phone not in redis_manager.active_monitors:
                    redis_manager.active_monitors.add(phone)
                    background_tasks.add_task(
                        redis_manager.monitor_typing_lock, 
                        phone, 
                        process_gatekeeper_flow
                    )

    except Exception as e:
        print(f"[ERROR] Webhook exception: {e}")

    # Acknowledge Meta immediately with HTTP 200
    return {"status": "success"}