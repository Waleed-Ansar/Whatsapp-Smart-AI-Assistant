import os
import pathlib
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

app = FastAPI()

# Credentials (Use environment variables in production)
APP_ID = os.getenv("META_APP_ID", "2409051026256039")
APP_SECRET = os.getenv("META_APP_SECRET", "ddddc907ac98c1a6cbe1b247158fa802")

# Pydantic schema for automated payload validation
class LinkWhatsAppPayload(BaseModel):
    agent_id: str
    code: str
    waba_id: str | None = None
    phone_number_id: str | None = None

@app.get("/", response_class=HTMLResponse)
async def serve_onboarding_page():
    html_path = pathlib.Path("index.html")
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="index.html not found")
    return HTMLResponse(content=html_path.read_text())

@app.post("/api/whatsapp/link")
async def link_agent_whatsapp(payload: LinkWhatsAppPayload):
    async with httpx.AsyncClient() as client:
        # 1. Exchange authorization code for Access Token
        token_res = await client.get(
            "https://graph.facebook.com/v20.0/oauth/access_token",
            params={
                "client_id": APP_ID,
                "client_secret": APP_SECRET,
                "code": payload.code
            }
        )
        token_data = token_res.json()
        access_token = token_data.get("access_token")
        
        if not access_token:
            raise HTTPException(status_code=400, detail=token_data)

        waba_id = payload.waba_id
        phone_id = payload.phone_number_id
        headers = {"Authorization": f"Bearer {access_token}"}

        # 2. Strategy A: Direct WABA Lookup
        if not waba_id:
            res = await client.get("https://graph.facebook.com/v20.0/me/whatsapp_business_accounts", headers=headers)
            waba_list = res.json().get("data", [])
            if waba_list:
                waba_id = waba_list[0].get("id")

        # 3. Strategy B: Client Shared WABA Lookup (Common for Embedded Signup)
        if not waba_id:
            res = await client.get("https://graph.facebook.com/v20.0/me/client_whatsapp_business_accounts", headers=headers)
            waba_list = res.json().get("data", [])
            if waba_list:
                waba_id = waba_list[0].get("id")

        # 4. Strategy C: Inspect Token Permissions for Target WABA ID
        if not waba_id:
            debug_res = await client.get(
                "https://graph.facebook.com/v20.0/debug_token",
                params={"input_token": access_token, "access_token": f"{APP_ID}|{APP_SECRET}"}
            )
            debug_data = debug_res.json().get("data", {})
            for scope in debug_data.get("granular_scopes", []):
                if scope.get("scope") == "whatsapp_business_management":
                    target_ids = scope.get("target_ids", [])
                    if target_ids:
                        waba_id = target_ids[0]
                        break

        # 5. Retrieve Phone Number ID using resolved WABA ID
        if waba_id and not phone_id:
            phone_res = await client.get(f"https://graph.facebook.com/v20.0/{waba_id}/phone_numbers", headers=headers)
            phone_list = phone_res.json().get("data", [])
            if phone_list:
                phone_id = phone_list[0].get("id")

        print(f"[LINK DEBUG] WABA ID: {waba_id} | Phone ID: {phone_id}")

        # 6. Auto-subscribe WABA to Webhooks
        if waba_id:
            await client.post(f"https://graph.facebook.com/v20.0/{waba_id}/subscribed_apps", headers=headers)

        return {
            "status": "linked", 
            "agent_id": payload.agent_id,
            "phone_number_id": phone_id,
            "waba_id": waba_id
        }