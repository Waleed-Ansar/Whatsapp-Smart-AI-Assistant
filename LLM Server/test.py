# import requests
# from config import config


# def send_whatsapp_message(phone: str, message: str) -> dict:

#         url = (
#             f"https://graph.facebook.com/v21.0/"
#             f"{config.PHONE_NUMBER_ID}/messages"
#         )

#         headers = {
#             "Authorization":
#                 f"Bearer {config.WA_ACCESS_TOKEN}",
#             "Content-Type":
#                 "application/json"
#         }

#         payload = {
#             "messaging_product": "whatsapp",
#             "recipient_type": "individual",
#             "to": phone,
#             "type": "text",
#             "text": {
#                 "preview_url": True,
#                 "body": message
#             }
#         }

#         response = requests.post(
#             url,
#             headers=headers,
#             json=payload,
#             timeout=15.0
#         )


#         return response


# print(send_whatsapp_message(config.RECIPIENT_PHONE, "Hello, this is a test message."))

import requests

url = "http://devrtd.rtdhuttons.com/Home/ExportToPdf"

# URL query parameters
params = {
    "pdfIds": "15772,15762,15761",
    "showLinks": "true",
    "cookieValue": "test123",
    "reportName": "RTDMarketAnalysisReport",
    "loginID": "sw0CftHRj10c6A_AUiCvDw",
    "bedroomType": "3",
    "IsMARPRro": "true",
    "IsChineseLang": "false",
    "lang": "en",
}

# Request headers
headers = {
    "Content-Type": "application/x-www-form-urlencoded",
}

# Cookies
cookies = {
    "ASP.NET_SessionId": "edboj0hzwmx2ukcjuwidw0da",
    "dlc": "test123",
}

# Form body payload (corresponds to --data-urlencode)
data = {
    "pdfIds": "101,102,103",
    "showLinks": "true",
    "cookieValue": "test123",
    "reportName": "RTDMarketAnalysisReport",
    "loginID": "ENCRYPTED_USER_ID",
    "clientName": "John Tan",
    "bedroomType": "3",
    "IsMARPRro": "true",
    "IsChineseLang": "false",
    "lang": "en",
    "preparedForClientName": "John Tan",
}

# Execute request
response = requests.request(
    method="GET",
    url=url,
    params=params,
    headers=headers,
    cookies=cookies,
    data=data,
    allow_redirects=True,  # mimics --location
)

# Check response status
print(f"Status Code: {response.status_code}")

# If the endpoint returns a PDF binary, save it to disk
if response.status_code == 200:
    with open("ExportedReport.pdf", "wb") as f:
        f.write(response.content)
    print("PDF saved successfully.")
else:
    print("Response text:", response.text)