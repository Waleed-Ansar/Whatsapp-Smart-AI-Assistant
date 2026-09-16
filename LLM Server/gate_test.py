import asyncio
import json
from gatekeeper import gatekeeper_agent, IntentDecision

async def run_test():
    print("=" * 70)
    print("1. Fetching Tool Schemas from MCP Server...")
    print("=" * 70)
    
    schemas = await gatekeeper_agent._get_tool_schemas()
    print("Discovered MCP Tool Requirements:")
    print(schemas)
    print("\n" + "=" * 70)

    # -------------------------------------------------------------
    # Test Case 1: Incomplete Fragment
    # -------------------------------------------------------------
    print("2. Simulating Incomplete Thought...")
    incomplete_history = [
        {"role": "user", "content": "Hi there"},
        {"role": "user", "content": "Can you provide me details of property, in gujranwala"}
    ]
    
    decision_1: IntentDecision = await gatekeeper_agent.evaluate(incomplete_history)
    print("Outcome:")
    print(f"  - Is Ready:          {decision_1.is_ready}")
    print(f"  - Intended Tool:     {decision_1.intended_action}")
    print(f"  - Required Fields:   {decision_1.required_fields}")
    print(f"  - Missing Fields:    {decision_1.missing_fields}")
    print(f"  - Action Parameters: {json.dumps(decision_1.action_parameters, indent=2)}")
    
    if not decision_1.is_ready:
        print("  -> SUCCESS: Gatekeeper trapped the incomplete message.")
    else:
        print("  -> FAILURE: Gatekeeper fired prematurely.")

    print("\n" + "=" * 70)

    # -------------------------------------------------------------
    # Test Case 2: Complete Thought with Parameters
    # -------------------------------------------------------------
    print("3. Simulating Complete Actionable Request...")
    complete_history = [
        {"role": "user", "content": "Hi there"},
        {"role": "user", "content": "property in Wapda town, Gujranwala in district Daska"}
    ]

    decision_2: IntentDecision = await gatekeeper_agent.evaluate(complete_history)
    print("Outcome:")
    print(f"  - Is Ready:          {decision_2.is_ready}")
    print(f"  - Intended Tool:     {decision_2.intended_action}")
    print(f"  - Required Fields:   {decision_2.required_fields}")
    print(f"  - Missing Fields:    {decision_2.missing_fields}")
    print(f"  - Action Parameters: {json.dumps(decision_2.action_parameters, indent=2)}")
    
    if decision_2.is_ready:
        print("  -> SUCCESS: Gatekeeper extracted parameters and approved execution!")
    else:
        print("  -> NOTICE: Gatekeeper felt fields were missing.")
        
    print("=" * 70)

if __name__ == "__main__":
    asyncio.run(run_test())