import json
import httpx

from gatekeeper import gatekeeper_agent
from database import db_manager
from llm import llm_server
from config import config
from redis_manager import redis_manager

async def send_whatsapp_message(phone: str, message: str) -> dict:
    url = f"https://graph.facebook.com/v21.0/{config.PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {config.WA_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": phone,
        "type": "text",
        "text": {"preview_url": True, "body": message}
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers, json=payload, timeout=15.0)
        res_data = response.json()

        if "messages" in res_data and len(res_data["messages"]) > 0:
            bot_wamid = res_data["messages"][0]["id"]
            await redis_manager.r.set(f"msg:{bot_wamid}", message, ex=172800)
            await db_manager.save_message_to_window(phone, "assistant", message, bot_wamid)

        return res_data

async def process_gatekeeper_flow(
    phone: str,
    aggregated_text: str
):
    print(
        f"\n[COPILOT OBSERVER] "
        f"New message from {phone}: '{aggregated_text}'"
    )

    agent_id = config.PHONE_NUMBER_ID # agent_id_can_be_number_of_agent

    decision = await gatekeeper_agent.evaluate(
        agent_id=agent_id,
        client_phone=phone,
        latest_message=aggregated_text
    )

    print("\n" + "=" * 40)
    print("🧠 RTD GATEKEEPER DECISION")
    print("=" * 40)
    print(f"Is Ready:          {decision.is_ready}")
    print(f"Intended Action:   {decision.intended_action}")
    print(f"Missing Fields:    {decision.missing_fields}")
    print(f"Action Parameters: {decision.action_parameters}")
    print("=" * 40 + "\n")

    if (
        not decision.is_ready
        or not decision.intended_action
    ):
        print(
            "[COPILOT SILENT] "
            "No report action triggered."
        )
        return

    report_output = await llm_server.serve(
        decision,
        chat_id=phone
    )

    try:
        data = json.loads(report_output)
        formatted_message = json.dumps(
            data,
            indent=2
        )

    except Exception:
        formatted_message = str(report_output)

    await send_whatsapp_message(
        phone=phone,
        message=formatted_message
    )

    print(
        f"[REPORT DISPATCHED] "
        f"RTD report sent to {phone}"
    )