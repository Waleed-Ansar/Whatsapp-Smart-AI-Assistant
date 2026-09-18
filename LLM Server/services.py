# import json
# import requests

# from gatekeeper import gatekeeper_agent
# from database import db_manager
# from llm import llm_server
# from config import config


# async def send_message(message):
#     url = f"https://graph.facebook.com/v21.0/{config.PHONE_NUMBER_ID}/messages"

#     headers = {
#         "Authorization": f"Bearer {config.WA_ACCESS_TOKEN}",
#         "Content-Type": "application/json"
#     }

#     payload = {
#         "messaging_product": "whatsapp",
#         "recipient_type": "individual",
#         "to": config.RECIPIENT_PHONE,
#         "type": "text",
#         "text": {
#             "preview_url": False,
#             "body": message
#         }
#     }
    
#     response = requests.post(url, headers=headers, json=payload)
    
#     return response

# async def process_gatekeeper_flow(phone: str, aggregated_text: str):
#     print(f"\n[10-SECOND LOCK CLEARED] Processing for {phone}: '{aggregated_text}'")

#     await db_manager.save_message_to_window(phone, role="user", text=aggregated_text)

#     history = await db_manager.get_recent_messages(phone, limit=5)

#     decision = await gatekeeper_agent.evaluate(history)
#     # print(decision.is_ready)
    
#     if decision.missing_fields == [] and decision.intended_action != "":
#         decision.is_ready = True

#     print("\n" + "="*40)
#     print("🧠 GATEKEEPER DECISION")
#     print("="*40)
#     print(f"Is Ready:          {decision.is_ready}")
#     print(f"Intended Action:   {decision.intended_action}")
#     print(f"Missing Fields:    {decision.missing_fields}")
#     print(f"Action Parameters: {decision.action_parameters}")
#     print("="*40 + "\n")
    
#     if decision.is_ready == True:
#         response = await llm_server.serve(decision, chat_id=phone)
#         data = json.loads(response)
    
#         await send_message(json.dumps(data, indent=3))
#         print(data)


import json
import httpx

from gatekeeper import gatekeeper_agent
from database import db_manager
from llm import llm_server
from config import config
from redis_manager import redis_manager


async def send_whatsapp_message(phone: str, message: str) -> dict:
    """
    Sends outbound WhatsApp message, stores its wamid in Redis and MongoDB,
    allowing users to swipe on the bot's messages.
    """
    url = f"https://graph.facebook.com/v21.0/{config.PHONE_NUMBER_ID}/messages"

    headers = {
        "Authorization": f"Bearer {config.WA_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": phone,  # Dynamically routed to sender
        "type": "text",
        "text": {
            "preview_url": False,
            "body": message
        }
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers, json=payload, timeout=15.0)
        res_data = response.json()

        # Capture Meta's outbound message ID
        if "messages" in res_data and len(res_data["messages"]) > 0:
            bot_wamid = res_data["messages"][0]["id"]

            # 1. Cache bot message in Redis (48 hours)
            await redis_manager.r.set(f"msg:{bot_wamid}", message, ex=172800)

            # 2. Save bot message in MongoDB with its wamid
            await db_manager.save_message_to_window(
                phone=phone,
                role="assistant",
                text=message,
                wamid=bot_wamid
            )
            print(f"[CACHE] Stored outbound bot message {bot_wamid}")

        return res_data

# Alias for backwards compatibility
send_message = send_whatsapp_message


async def process_gatekeeper_flow(phone: str, aggregated_text: str):
    print(f"\n[DEBOUNCE CLEARED] Processing flow for {phone}: '{aggregated_text}'")

    # Fetch recent conversation window from MongoDB
    history = await db_manager.get_recent_messages(phone, limit=6)

    decision = await gatekeeper_agent.evaluate(history)

    # Clean check for readiness
    if not decision.missing_fields and decision.intended_action:
        decision.is_ready = True

    print("\n" + "="*40)
    print("🧠 GATEKEEPER DECISION")
    print("="*40)
    print(f"Is Ready:          {decision.is_ready}")
    print(f"Intended Action:   {decision.intended_action}")
    print(f"Missing Fields:    {decision.missing_fields}")
    print(f"Action Parameters: {decision.action_parameters}")
    print("="*40 + "\n")

    if decision.is_ready:
        response = await llm_server.serve(decision, chat_id=phone)
        try:
            data = json.loads(response)
            bot_text = json.dumps(data, indent=3)
        except Exception:
            bot_text = str(response)

        await send_whatsapp_message(phone=phone, message=bot_text)
        print(f"[DISPATCHED] Response sent to {phone}")
    elif decision.missing_fields and decision.intended_action:
        missing_str = ", ".join(decision.missing_fields)
        await send_whatsapp_message(
            phone=phone, 
            message=f"Please provide the following details: {missing_str}"
        )