import requests

# Use the same token you used to send messages successfully
ACCESS_TOKEN = "YOUR_ACCESS_TOKEN"

# ⚠️ IMPORTANT: Use the WABA ID, NOT the Phone Number ID ⚠️
WABA_ID = "YOUR_WHATSAPP_BUSINESS_ACCOUNT_ID" 

url = f"https://graph.facebook.com/v20.0/{WABA_ID}/subscribed_apps"

headers = {
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "Content-Type": "application/json"
}

# Making a POST request here forces Meta to link the WABA to your Webhook App
response = requests.post(url, headers=headers)

print(f"Status Code: {response.status_code}")
print(f"Response: {response.json()}")