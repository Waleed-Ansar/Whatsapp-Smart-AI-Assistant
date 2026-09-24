import requests
from config import config


def send_whatsapp_message(phone: str, message: str) -> dict:

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

        response = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=15.0
        )


        return response

print(config.WA_ACCESS_TOKEN)
print(send_whatsapp_message(config.RECIPIENT_PHONE, "Hello, this is a test message."))