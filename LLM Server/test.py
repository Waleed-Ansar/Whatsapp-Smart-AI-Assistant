# import requests

# APP_ID = "3410883035757449"
# APP_SECRET = "8595b402fefa186588d247c738cc8021"  # From App settings > Basic
# # Paste a freshly generated code (codes expire and are single-use)
# CODE = "AQI6cPO0eXW079VSxsPqM1P2mYEEuuiP2_kwLY1DdKjhF3A4gUxF8JnnHr6b0dlfv0s7TWMF2qwtFQ6PD1IWXaqjFrx87GtO_8n_7MS6v07YUseQz-lPD67aZ_VK3mIOBehKpSxWcPv26YPGadchIgm---GGh5wESkM78v4ZyMkVEIftLzGNqRrtZB7vWh1htY2HW25X0J-dtyxI7di15wJoZjiLllw6xzmTakHOTHPc9msl21lvbQQZcZ9O4GPtrcqutdv97T5qv9UGHLnYUwaIMK_CmCAffwM4GQPOXcLXsthgG0VeeCNcaazFAW7zFbambUtkBDJ-0-jq7xLAX2-m5VUZrFbD9tPTy5yv53MrZg6f0hhrQtD1YNh5hqFYT9S5NGKZAdoNtzBiP8tvZfocbkMyBwA8Dk_HicaOZLWr7Q"

# # MUST exactly match the URI used in the browser dialog and your dashboard
# EXACT_REDIRECT_URI = "https://unlocking-unclothed-employer.ngrok-free.dev/"

# url = "https://graph.facebook.com/v21.0/oauth/access_token"

# params = {
#     "client_id": APP_ID,
#     "client_secret": APP_SECRET,
#     "code": CODE,
#     "redirect_uri": EXACT_REDIRECT_URI
# }

# response = requests.get(url, params=params)
# print(response.json())


import requests

ACCESS_TOKEN = "EAAweLhe3e4kBSrNu55ZBcvjivZBvNhtM4kkoqZC8iKeNJrIkZCmVi1Oj1iqQKvZBJrPMrSaUOlZCzBk3gTvbrZCzE4FdZBM2ZAd3uGp1n6wFN3iw8fLeZCJZBJRcwRHVZAesuvwxsoaNkTnrZC8mAJqf6exj2iZAWnqrdZB4t2VPSAl5zx0V41hUuswCiEmLpNob9O48oyqKfDO7BdPZCGhjtASoTpj9wWzZCgaHZBIxWbIsR1kdxCfa7tCcSZBBIpgv86vfKRhIc6BPYVknH3I8BrfZAcIZAd3ZCZCNpYP4JZCceGyxGujmB1gjH9QJcCEZBGgV9mmlCg8A0DdzZC7iT7SosZD"
PHONE_NUMBER_ID = "1373050325881747"  # From session logging payload
RECIPIENT_PHONE = "+92-319-0477882"   # Format: 15551234567 (with country code, no +)

url = f"https://graph.facebook.com/v21.0/{PHONE_NUMBER_ID}/messages"

headers = {
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "Content-Type": "application/json"
}

payload = {
    "messaging_product": "whatsapp",
    "recipient_type": "individual",
    "to": RECIPIENT_PHONE,
    "type": "text",
    "text": {
        "preview_url": False,
        "body": "Yes I am."
    }
}

response = requests.post(url, headers=headers, json=payload)
print("Message Send Status:", response.json())

# import requests

# ACCESS_TOKEN = "EAAweLhe3e4kBSrNu55ZBcvjivZBvNhtM4kkoqZC8iKeNJrIkZCmVi1Oj1iqQKvZBJrPMrSaUOlZCzBk3gTvbrZCzE4FdZBM2ZAd3uGp1n6wFN3iw8fLeZCJZBJRcwRHVZAesuvwxsoaNkTnrZC8mAJqf6exj2iZAWnqrdZB4t2VPSAl5zx0V41hUuswCiEmLpNob9O48oyqKfDO7BdPZCGhjtASoTpj9wWzZCgaHZBIxWbIsR1kdxCfa7tCcSZBBIpgv86vfKRhIc6BPYVknH3I8BrfZAcIZAd3ZCZCNpYP4JZCceGyxGujmB1gjH9QJcCEZBGgV9mmlCg8A0DdzZC7iT7SosZD"  # Your token
# BUSINESS_ID = "1709732443425499"

# # Query owned and shared WABAs using the business ID directly
# for endpoint in ["owned_whatsapp_business_accounts", "client_whatsapp_business_accounts"]:
#     url = f"https://graph.facebook.com/v21.0/{BUSINESS_ID}/{endpoint}"
#     params = {
#         "access_token": ACCESS_TOKEN,
#         "fields": "id,name,currency,timezone_id,phone_numbers{id,display_phone_number,verified_name}"
#     }

#     res = requests.get(url, params=params).json()
#     print(res)
    
#     if "data" in res and res["data"]:
#         for item in res["data"]:
#             print(f"\n--- WABA Found ({endpoint}) ---")
#             print(f"WABA Name: {item.get('name')}")
#             print(f"WABA ID:   {item.get('id')}")
#             for phone in item.get("phone_numbers", {}).get("data", []):
#                 print(f"  -> Phone Number ID: {phone.get('id')}")
#                 print(f"  -> Display Number:  {phone.get('display_phone_number')}")

# import requests

# WABA_ID = "1709732443425499"
# ACCESS_TOKEN = "EAAweLhe3e4kBSrNu55ZBcvjivZBvNhtM4kkoqZC8iKeNJrIkZCmVi1Oj1iqQKvZBJrPMrSaUOlZCzBk3gTvbrZCzE4FdZBM2ZAd3uGp1n6wFN3iw8fLeZCJZBJRcwRHVZAesuvwxsoaNkTnrZC8mAJqf6exj2iZAWnqrdZB4t2VPSAl5zx0V41hUuswCiEmLpNob9O48oyqKfDO7BdPZCGhjtASoTpj9wWzZCgaHZBIxWbIsR1kdxCfa7tCcSZBBIpgv86vfKRhIc6BPYVknH3I8BrfZAcIZAd3ZCZCNpYP4JZCceGyxGujmB1gjH9QJcCEZBGgV9mmlCg8A0DdzZC7iT7SosZD"  # Your User or System User Token

# url = f"https://graph.facebook.com/v21.0/{WABA_ID}/subscribed_apps"
# headers = {
#     "Authorization": f"Bearer {ACCESS_TOKEN}"
# }

# response = requests.get(url, headers=headers)
# print("Status Code:", response.status_code)
# print("Active Subscriptions:\n", response.json())