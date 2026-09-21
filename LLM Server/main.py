from typing import Optional
from contextlib import asynccontextmanager
from datetime import datetime
import httpx
from fastapi import FastAPI, Request, Query, Response, BackgroundTasks
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.redis.aio import AsyncRedisSaver

from config import config
from redis_manager import redis_manager
from services import process_gatekeeper_flow
from database import db_manager
from gatekeeper import gatekeeper_agent


async def resolve_quoted_message(wamid: str) -> Optional[str]:
    """Retrieves quoted message content from Redis (hot) or MongoDB (cold fallback)."""
    if not wamid:
        return None

    cached = await redis_manager.r.get(f"msg:{wamid}")
    if cached:
        return cached if isinstance(cached, str) else cached.decode("utf-8")

    db_text = await db_manager.get_message_by_wamid(wamid)
    if db_text:
        await redis_manager.r.set(f"msg:{wamid}", db_text, ex=172800)
        return db_text

    return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("Booting up: Setting up MongoDB Indexes...")
    await db_manager.setup_indexes()

    if config.REDIS_USERNAME and config.REDIS_PASSWORD:
        redis_url = (
            f"redis://{config.REDIS_USERNAME}:{config.REDIS_PASSWORD}"
            f"@{config.REDIS_HOST}:{config.REDIS_PORT}"
        )
    elif config.REDIS_PASSWORD:
        redis_url = (
            f"redis://:{config.REDIS_PASSWORD}"
            f"@{config.REDIS_HOST}:{config.REDIS_PORT}"
        )
    else:
        redis_url = (
            f"redis://{config.REDIS_HOST}:{config.REDIS_PORT}"
        )

    print("[LANGGRAPH] Connecting to Redis...")

    async with AsyncRedisSaver.from_conn_string(redis_url) as checkpointer:

        print("[LANGGRAPH] Redis checkpointer created")

        await checkpointer.setup()

        print("[LANGGRAPH] Redis checkpointer setup complete")

        await gatekeeper_agent.initialize(checkpointer)

        print("[LANGGRAPH] Gatekeeper initialized")

        yield

    print("Shutting down...")


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

            if await redis_manager.r.get(f"processed:{msg_id}"):
                return {"status": "already_processed"}
            await redis_manager.r.set(f"processed:{msg_id}", "1", ex=300)

            if client_msg.get("type") == "text":
                phone = client_msg["from"]
                text = client_msg["text"]["body"]

                context = client_msg.get("context")
                if context and "id" in context:
                    quoted_id = context["id"]
                    quoted_text = await resolve_quoted_message(quoted_id)
                    if quoted_text:
                        print(f"[REPLY DETECTED] Swiped message: '{quoted_text[:50]}...'")
                        text = f"[In reply to: \"{quoted_text}\"]\n{text}"
                    else:
                        print(f"[REPLY DETECTED] Quoted ID {quoted_id} not found in cache/DB.")

                await redis_manager.r.set(f"msg:{msg_id}", text, ex=172800)

                await db_manager.save_message_to_window(
                    phone=phone,
                    role="user",
                    text=text,
                    wamid=msg_id
                )

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

    return {"status": "success"}