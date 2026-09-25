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
from services import services
from database import db_manager
from knowledge import knowledge_manager
from llm import llm_server
from reply_client import auto_reply_agent


async def resolve_quoted_message(
    wamid: str
) -> Optional[str]:
    """Retrieves quoted message content from Redis (hot) or MongoDB (cold fallback)."""

    if not wamid:
        return None

    cached = await redis_manager.r.get(
        f"msg:{wamid}"
    )

    if cached:
        return (
            cached
            if isinstance(cached, str)
            else cached.decode("utf-8")
        )

    db_text = await db_manager.get_message_by_wamid(
        wamid
    )

    if db_text:

        await redis_manager.r.set(
            f"msg:{wamid}",
            db_text,
            ex=172800
        )

        return db_text

    return None


@asynccontextmanager
async def lifespan(app: FastAPI):

    print(
        "Booting up: Setting up MongoDB Indexes..."
    )

    await db_manager.setup_indexes()

    knowledge_manager.load()

    redis_url = config.REDIS_URL

    print(
        "[LANGGRAPH] Connecting to Redis..."
    )

    # Auto Reply Agent still uses LangGraph + Redis memory.
    async with AsyncRedisSaver.from_conn_string(
        redis_url
    ) as checkpointer:

        await checkpointer.setup()

        print(
            "[LANGGRAPH] Redis checkpointer ready"
        )

        # ---------------------------------------------------------
        # AUTO REPLY AGENT -- KEPT FOR TESTING
        # ---------------------------------------------------------
        await auto_reply_agent.initialize(
            checkpointer=checkpointer
        )

        print(
            "[AUTO REPLY] Agent initialized"
        )

        # ---------------------------------------------------------
        # MAIN SINGLE TOOL-CALLING AGENT
        # ---------------------------------------------------------
        await llm_server.initialize()

        print(
            "[MAIN AGENT] Ready"
        )

        yield

    print(
        "Shutting down..."
    )

    db_manager.close()


async def run_auto_reply(
    phone: str,
    client_text: str,
):
    """
    Testing conversational agent.

    This is independent from the main tool-calling agent.
    It responds conversationally to every inbound client text message.
    """

    try:

        reply = await auto_reply_agent.respond(
            phone=phone,
            client_message=client_text
        )

        if not reply:
            return

        # services.send_whatsapp_message() already stores the outgoing
        # WhatsApp message in MongoDB, so do not save it a second time here.
        await services.send_whatsapp_message(
            phone=phone,
            message=reply
        )

        print(
            f"[AUTO REPLY] Reply sent to {phone}"
        )

    except Exception as e:

        print(
            f"[AUTO REPLY] Error: {e}"
        )


app = FastAPI(
    title="Whatsapp Agent",
    lifespan=lifespan
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():

    return FileResponse(
        "index.html"
    )


@app.get("/health")
async def health_check():

    headers = {
        "Authorization":
            f"Bearer {config.MCP_SERVER_API_KEY}"
    }

    async with httpx.AsyncClient() as client:

        try:

            response = await client.get(
                config.SERVER_HEALTH_CHECK_URL,
                headers=headers,
                timeout=5.0
            )

            return {
                "status": response.status_code,
                "message": response.json()
            }

        except Exception as e:

            return {
                "status": 500,
                "error": str(e)
            }


@app.get("/webhook")
async def verify_webhook(
    mode: Optional[str] = Query(
        None,
        alias="hub.mode"
    ),
    token: Optional[str] = Query(
        None,
        alias="hub.verify_token"
    ),
    challenge: Optional[str] = Query(
        None,
        alias="hub.challenge"
    )
):

    print(
        f"[LOG EVENT AT {datetime.now()}] "
        f"EVENT: GET WEBHOOK"
    )

    if (
        mode == "subscribe"
        and token == config.WHATSAPP_VERIFY_TOKEN
    ):

        print(
            "Verification successful!"
        )

        return Response(
            content=challenge,
            media_type="text/plain"
        )

    return Response(
        content="Verification failed",
        status_code=403
    )


@app.post("/webhook")
async def receive_whatsapp_event(
    request: Request,
    background_tasks: BackgroundTasks
):

    payload = await request.json()

    try:

        entry = payload.get(
            "entry",
            []
        )[0]

        changes = entry.get(
            "changes",
            []
        )[0]

        value = changes.get(
            "value",
            {}
        )

        if "messages" not in value:
            return {
                "status": "success"
            }

        client_msg = value["messages"][0]

        msg_id = client_msg.get(
            "id"
        )

        if not msg_id:
            return {
                "status": "success"
            }

        if await redis_manager.r.get(
            f"processed:{msg_id}"
        ):

            return {
                "status": "already_processed"
            }

        await redis_manager.r.set(
            f"processed:{msg_id}",
            "1",
            ex=300
        )

        message_type = client_msg.get(
            "type"
        )

        phone = client_msg.get(
            "from"
        )

        if message_type == "text":

            text = client_msg["text"]["body"]

            context = client_msg.get(
                "context"
            )

            if (
                context
                and "id" in context
            ):

                quoted_id = context["id"]

                quoted_text = (
                    await resolve_quoted_message(
                        quoted_id
                    )
                )

                if quoted_text:

                    print(
                        "[REPLY DETECTED] "
                        f"Swiped message: "
                        f"'{quoted_text[:50]}...'"
                    )

                    text = (
                        f'[In reply to: "{quoted_text}"]\n'
                        f"{text}"
                    )

                else:

                    print(
                        "[REPLY DETECTED] "
                        f"Quoted ID {quoted_id} "
                        "not found in cache/DB."
                    )

            await redis_manager.r.set(
                f"msg:{msg_id}",
                text,
                ex=172800
            )

            await db_manager.save_message_to_window(
                phone=phone,
                role="user",
                text=text,
                wamid=msg_id
            )

            # =====================================================
            # AUTO REPLY AGENT -- TESTING / CONVERSATIONAL REPLIES
            # =====================================================
            background_tasks.add_task(
                run_auto_reply,
                phone,
                text
            )

            # =====================================================
            # MAIN SINGLE AGENT -- INTENT + TOOL EXECUTION
            # =====================================================
            background_tasks.add_task(
                services.process_agent_flow,
                phone,
                text
            )

        elif message_type == "audio":

            audio_data = client_msg.get(
                "audio",
                {}
            )

            media_id = audio_data.get(
                "id"
            )

            mime_type = audio_data.get(
                "mime_type",
                "audio/ogg"
            )

            print(
                f"[VOICE MESSAGE] "
                f"Received from {phone}"
            )

            print(
                f"[VOICE MESSAGE] "
                f"Media ID: {media_id}"
            )

            print(
                f"[VOICE MESSAGE] "
                f"MIME type: {mime_type}"
            )

            if not media_id:

                print(
                    "[VOICE MESSAGE] "
                    "No media ID found."
                )

                return {
                    "status": "success"
                }

            background_tasks.add_task(
                services.process_voice_message,
                phone,
                media_id,
                msg_id
            )

        return {
            "status": "success"
        }

    except Exception as e:

        print(
            f"[ERROR] Webhook exception: {e}"
        )

        return {
            "status": "success"
        }
