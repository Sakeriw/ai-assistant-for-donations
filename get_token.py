import requests
import os
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
CODE = os.getenv("OAUTH_CODE")

if not CLIENT_ID or not CLIENT_SECRET or not CODE:
    raise Exception("Переменные не найдены в .env")

response = requests.post(
    "https://www.donationalerts.com/oauth/token",
    data={
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": "http://localhost",
        "code": CODE
    }
)

print(response.json())
