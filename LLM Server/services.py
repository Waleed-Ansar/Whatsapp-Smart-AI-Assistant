import os
import json
import asyncio
import httpx

from gatekeeper import gatekeeper_agent
from database import db_manager
from llm import llm_server
from config import config
from redis_manager import redis_manager
from transcribe import transcribe


AUDIO_DIR = "audio"

os.makedirs(
    AUDIO_DIR,
    exist_ok=True
)

class Services:
    def __init__(self):
        self.token = "toekn"

    async def send_whatsapp_message(self, phone: str, message: str) -> dict:

        url = (
            f"https://graph.facebook.com/v21.0/"
            f"{config.PHONE_NUMBER_ID}/messages"
        )

        headers = {
            "Authorization":
                f"Bearer {config.WA_ACCESS_TOKEN}",
            "Content-Type":
                "application/json"
        }

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": phone,
            "type": "text",
            "text": {
                "preview_url": True,
                "body": message
            }
        }

        async with httpx.AsyncClient() as client:

            response = await client.post(
                url,
                headers=headers,
                json=payload,
                timeout=15.0
            )

            res_data = response.json()

            if (
                "messages" in res_data
                and len(res_data["messages"]) > 0
            ):

                bot_wamid = (
                    res_data["messages"][0]["id"]
                )

                await redis_manager.r.set(
                    f"msg:{bot_wamid}",
                    message,
                    ex=172800
                )

                await db_manager.save_message_to_window(
                    phone,
                    "assistant",
                    message,
                    bot_wamid
                )

            return res_data


    async def get_whatsapp_media_url(self, 
        media_id: str
    ) -> str:

        url = (
            f"https://graph.facebook.com/v21.0/"
            f"{media_id}"
        )

        headers = {
            "Authorization":
                f"Bearer {config.WA_ACCESS_TOKEN}"
        }

        async with httpx.AsyncClient() as client:

            response = await client.get(
                url,
                headers=headers,
                timeout=15.0
            )

            response.raise_for_status()

            data = response.json()

            media_url = data.get(
                "url"
            )

            if not media_url:

                raise RuntimeError(
                    "WhatsApp did not return a media URL."
                )

            return media_url


    async def download_whatsapp_audio(self, media_id: str, msg_id: str) -> str:

        media_url = await self.get_whatsapp_media_url(
            media_id
        )

        headers = {
            "Authorization":
                f"Bearer {config.WA_ACCESS_TOKEN}"
        }

        file_path = os.path.join(
            AUDIO_DIR,
            f"{msg_id}.ogg"
        )

        async with httpx.AsyncClient() as client:

            response = await client.get(
                media_url,
                headers=headers,
                timeout=30.0
            )

            response.raise_for_status()

            audio_bytes = response.content

        with open(
            file_path,
            "wb"
        ) as audio_file:

            audio_file.write(
                audio_bytes
            )

        print(
            f"[WHATSAPP AUDIO] "
            f"Downloaded {len(audio_bytes)} bytes "
            f"→ {file_path}"
        )

        return file_path


    async def process_voice_message(self, phone: str, media_id: str, msg_id: str):
        print(
            f"\n[VOICE MESSAGE] "
            f"Received voice message from {phone}"
        )

        print(
            f"[VOICE MESSAGE] "
            f"Media ID: {media_id}"
        )

        audio_path = None

        try:
            audio_path = await self.download_whatsapp_audio(
                media_id=media_id,
                msg_id=msg_id
            )

            transcript = await transcribe.transcribe_audio(
                audio_path
            )

            if not transcript:

                print(
                    "[STT] Empty transcript. "
                    "Nothing to process."
                )

                return

            await redis_manager.r.set(
                f"msg:{msg_id}",
                transcript,
                ex=172800
            )

            await db_manager.save_message_to_window(
                phone=phone,
                role="user",
                text=transcript,
                wamid=msg_id
            )

            await redis_manager.stack_incoming_message(
                phone=phone,
                text=transcript
            )

            print(
                "[VOICE MESSAGE] "
                "Transcript added to Redis message stack."
            )

            if phone not in redis_manager.active_monitors:

                redis_manager.active_monitors.add(
                    phone
                )

                asyncio.create_task(
                    redis_manager.monitor_typing_lock(
                        phone,
                        self.process_gatekeeper_flow
                    )
                )

                print(
                    "[VOICE MESSAGE] "
                    f"Gatekeeper monitor started for {phone}"
                )

            else:

                print(
                    "[VOICE MESSAGE] "
                    f"Gatekeeper monitor already active "
                    f"for {phone}"
                )

            print(
                "[VOICE MESSAGE] "
                "Transcription sent to gatekeeper pipeline."
            )

        except Exception as e:

            print(
                "[VOICE MESSAGE ERROR] "
                f"{e}"
            )

        finally:
            if audio_path and os.path.exists(
                audio_path
            ):

                try:

                    os.remove(
                        audio_path
                    )

                    print(
                        "[VOICE MESSAGE] "
                        f"Temporary audio deleted: "
                        f"{audio_path}"
                    )

                except Exception as e:

                    print(
                        "[VOICE MESSAGE] "
                        f"Could not delete temporary audio: "
                        f"{e}"
                    )


    async def process_gatekeeper_flow(self, phone: str, aggregated_text: str):

        print(
            f"\n[COPILOT OBSERVER] "
            f"New message from {phone}: "
            f"'{aggregated_text}'"
        )

        agent_id = config.PHONE_NUMBER_ID

        decision = await gatekeeper_agent.evaluate(
            agent_id=agent_id,
            client_phone=phone,
            latest_message=aggregated_text
        )

        print(
            "\n" + "=" * 40
        )

        print(
            "🧠 RTD GATEKEEPER DECISION"
        )

        print(
            "=" * 40
        )

        print(
            f"Is Ready:          "
            f"{decision.is_ready}"
        )

        print(
            f"Intended Action:   "
            f"{decision.intended_action}"
        )

        print(
            f"Missing Fields:    "
            f"{decision.missing_fields}"
        )

        print(
            f"Action Parameters: "
            f"{decision.action_parameters}"
        )

        print(
            "=" * 40 + "\n"
        )

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

            data = json.loads(
                report_output
            )

            formatted_message = json.dumps(
                data,
                indent=2
            )

        except Exception:

            formatted_message = str(
                report_output
            )

        await self.send_whatsapp_message(
            phone=phone,
            message=formatted_message
        )

        print(
            f"[REPORT DISPATCHED] "
            f"RTD report sent to {phone}"
        )

services = Services()