import json
import requests

from gatekeeper import gatekeeper_agent
from database import db_manager
from llm import llm_server
from config import config


async def send_message(message):
    url = f"https://graph.facebook.com/v21.0/{config.PHONE_NUMBER_ID}/messages"

    headers = {
        "Authorization": f"Bearer {config.WA_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": config.RECIPIENT_PHONE,
        "type": "text",
        "text": {
            "preview_url": False,
            "body": message
        }
    }
    
    response = requests.post(url, headers=headers, json=payload)
    
    return response

async def process_gatekeeper_flow(phone: str, aggregated_text: str):
    print(f"\n[10-SECOND LOCK CLEARED] Processing for {phone}: '{aggregated_text}'")

    await db_manager.save_message_to_window(phone, role="user", text=aggregated_text)

    history = await db_manager.get_recent_messages(phone, limit=5)

    decision = await gatekeeper_agent.evaluate(history)
    # print(decision.is_ready)
    
    if decision.missing_fields == [] and decision.intended_action != "":
        decision.is_ready = True

    print("\n" + "="*40)
    print("🧠 GATEKEEPER DECISION")
    print("="*40)
    print(f"Is Ready:          {decision.is_ready}")
    print(f"Intended Action:   {decision.intended_action}")
    print(f"Missing Fields:    {decision.missing_fields}")
    print(f"Action Parameters: {decision.action_parameters}")
    print("="*40 + "\n")
    
    if decision.is_ready == True:
        response = await llm_server.serve(decision, chat_id=phone)
        data = json.loads(response)
    
        await send_message(json.dumps(data, indent=3))
        print(data)