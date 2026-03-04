"""
One-time script to generate a Gmail OAuth refresh token.

Run this locally, sign in with anthony@livite.com in the browser,
and it will print the new GOOGLE_REFRESH_TOKEN to paste into .env.

Usage:
    python3 tools/refresh_gmail_token.py
"""
import os
import json
from dotenv import load_dotenv
load_dotenv()

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    'https://www.googleapis.com/auth/gmail.send',
    'https://www.googleapis.com/auth/gmail.readonly',
]

client_config = {
    "installed": {
        "client_id": os.getenv("GOOGLE_CLIENT_ID"),
        "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost"],
    }
}

print("Opening browser — sign in with anthony@livite.com")
print("(Make sure to pick the right account if multiple are listed)\n")

flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
creds = flow.run_local_server(port=0, access_type='offline', prompt='consent')

print("\n=== New Refresh Token ===")
print(f"GOOGLE_REFRESH_TOKEN={creds.refresh_token}")
print("\nPaste this into your .env file (replace the old GOOGLE_REFRESH_TOKEN line).")
print("Also update this value in Render environment variables.")
