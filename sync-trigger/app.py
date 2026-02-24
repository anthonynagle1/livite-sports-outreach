"""
Tiny web service that triggers the CRM cron runner on demand.
Deploy to Render (free tier). Put the /sync URL in Notion as a button.

When Meire clicks the link in Notion, this triggers the GitHub Actions
workflow immediately instead of waiting for the 15-minute cycle.
"""

import os
import requests
from flask import Flask, redirect

app = Flask(__name__)

GITHUB_TOKEN = os.environ.get("GITHUB_PAT")
REPO = "anthonynagle1/livite-sports-outreach"
WORKFLOW = "cron-runner.yml"


@app.route("/")
def home():
    return """
    <html>
    <head><title>Livite CRM Sync</title></head>
    <body style="font-family: sans-serif; text-align: center; padding: 60px;">
        <h1>Livite CRM Sync</h1>
        <p>Click the button to run the email pipeline now.</p>
        <a href="/sync" style="
            display: inline-block;
            padding: 16px 32px;
            background: #2ecc71;
            color: white;
            text-decoration: none;
            border-radius: 8px;
            font-size: 18px;
        ">Sync Now</a>
    </body>
    </html>
    """


@app.route("/sync")
def sync():
    if not GITHUB_TOKEN:
        return "Error: GITHUB_PAT not configured", 500

    # Trigger the GitHub Actions workflow
    response = requests.post(
        f"https://api.github.com/repos/{REPO}/actions/workflows/{WORKFLOW}/dispatches",
        headers={
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json",
        },
        json={"ref": "main"},
    )

    if response.status_code == 204:
        return """
        <html>
        <head><title>Sync Triggered</title></head>
        <body style="font-family: sans-serif; text-align: center; padding: 60px;">
            <h1 style="color: #2ecc71;">Sync Triggered!</h1>
            <p>The CRM pipeline is running now. Drafts will be created and approved emails will be sent.</p>
            <p>You can close this tab.</p>
        </body>
        </html>
        """
    else:
        return f"Error triggering workflow: {response.status_code} - {response.text}", 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
