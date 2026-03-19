#!/usr/bin/env python3
"""
One-time script to generate Gmail OAuth token for catering@livite.com.

Run this locally, sign in with catering@livite.com in the browser.
Saves token to catering_token.json.

Usage:
    python3 tools/auth_catering_gmail.py
"""
import os
import json

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    'https://www.googleapis.com/auth/gmail.send',
    'https://www.googleapis.com/auth/gmail.readonly',
]

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), '..')
CREDS_PATH = os.path.join(PROJECT_ROOT, 'credentials.json')
TOKEN_PATH = os.path.join(PROJECT_ROOT, 'catering_token.json')

if not os.path.exists(CREDS_PATH):
    print(f"Error: {CREDS_PATH} not found")
    exit(1)

print("Opening browser — sign in with catering@livite.com")
print("(Make sure to pick the catering account, NOT anthony@)\n")

flow = InstalledAppFlow.from_client_secrets_file(CREDS_PATH, SCOPES)
creds = flow.run_local_server(
    port=0,
    access_type='offline',
    prompt='consent',
    login_hint='catering@livite.com',
)

# Save token file
token_data = {
    "token": creds.token,
    "refresh_token": creds.refresh_token,
    "token_uri": creds.token_uri,
    "client_id": creds.client_id,
    "client_secret": creds.client_secret,
    "scopes": list(creds.scopes or SCOPES),
}
with open(TOKEN_PATH, 'w') as f:
    json.dump(token_data, f, indent=2)

print(f"\nToken saved to {os.path.abspath(TOKEN_PATH)}")
print(f"\n=== Add to .env ===")
print(f"CATERING_GOOGLE_REFRESH_TOKEN={creds.refresh_token}")
