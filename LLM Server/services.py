import json

from gatekeeper import gatekeeper_agent
from database import db_manager
from llm import llm_server


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
    
        print(json.dumps(response, indent=3))