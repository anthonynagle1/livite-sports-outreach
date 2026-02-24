"""
Livite CRM Cron Service — runs on Render free tier.

Endpoints:
  /        — Status page with manual sync button
  /cron    — Runs the CRM pipeline (hit by external pinger every 2 min)
  /sync    — Manual trigger (same as /cron but with redirect UI)
  /health  — Health check for uptime monitors

Setup:
  1. Deploy to Render as a web service
  2. Set environment variables in Render dashboard (copy from .env)
  3. Set GMAIL_TOKEN_JSON and GOOGLE_CREDENTIALS_JSON as env vars
  4. Use cron-job.org (free) to hit /cron every 2 minutes
"""

import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime

from flask import Flask, jsonify, request

app = Flask(__name__)

# Track last run
last_run = {"time": None, "status": None, "output": None}
is_running = False
run_lock = threading.Lock()


def setup_credentials():
    """Write credential files from environment variables."""
    token_json = os.environ.get("GMAIL_TOKEN_JSON")
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")

    if token_json:
        with open("token.json", "w") as f:
            f.write(token_json)

    if creds_json:
        with open("credentials.json", "w") as f:
            f.write(creds_json)

    # Create .tmp directory
    os.makedirs(".tmp", exist_ok=True)


def run_cron():
    """Execute the CRM cron runner."""
    global is_running, last_run

    with run_lock:
        if is_running:
            return {"status": "already_running", "last_run": last_run["time"]}
        is_running = True

    try:
        setup_credentials()

        result = subprocess.run(
            [sys.executable, "tools/notion_cron_runner.py"],
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute timeout
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        )

        last_run["time"] = datetime.utcnow().isoformat()
        last_run["status"] = "success" if result.returncode == 0 else "error"
        # Keep last 2000 chars of output
        last_run["output"] = (result.stderr or "")[-2000:]

        return {
            "status": last_run["status"],
            "time": last_run["time"],
            "returncode": result.returncode,
        }

    except subprocess.TimeoutExpired:
        last_run["time"] = datetime.utcnow().isoformat()
        last_run["status"] = "timeout"
        return {"status": "timeout"}

    except Exception as e:
        last_run["time"] = datetime.utcnow().isoformat()
        last_run["status"] = f"error: {str(e)}"
        return {"status": "error", "message": str(e)}

    finally:
        is_running = False


@app.route("/")
def home():
    status_color = "#2ecc71" if last_run["status"] == "success" else "#e74c3c"
    last_time = last_run["time"] or "Never"
    last_status = last_run["status"] or "Not run yet"

    return f"""
    <html>
    <head><title>Livite CRM</title></head>
    <body style="font-family: sans-serif; text-align: center; padding: 60px;">
        <h1>Livite CRM Pipeline</h1>
        <p>Last run: <strong>{last_time}</strong></p>
        <p>Status: <span style="color: {status_color}; font-weight: bold;">{last_status}</span></p>
        <br>
        <a href="/sync" style="
            display: inline-block;
            padding: 16px 32px;
            background: #2ecc71;
            color: white;
            text-decoration: none;
            border-radius: 8px;
            font-size: 18px;
        ">Run Now</a>
    </body>
    </html>
    """


@app.route("/cron")
def cron():
    """Endpoint for external cron pinger (cron-job.org, UptimeRobot, etc.)."""
    result = run_cron()
    return jsonify(result)


@app.route("/sync")
def sync():
    """Manual trigger with UI feedback."""
    result = run_cron()

    if result["status"] == "already_running":
        msg = "Pipeline is already running. Check back in a minute."
        color = "#f39c12"
    elif result["status"] == "success":
        msg = "Pipeline completed successfully!"
        color = "#2ecc71"
    else:
        msg = f"Pipeline finished with status: {result['status']}"
        color = "#e74c3c"

    return f"""
    <html>
    <head><title>Sync Result</title></head>
    <body style="font-family: sans-serif; text-align: center; padding: 60px;">
        <h1 style="color: {color};">{msg}</h1>
        <p>You can close this tab or <a href="/">go back</a>.</p>
    </body>
    </html>
    """


@app.route("/health")
def health():
    """Health check endpoint."""
    return jsonify({
        "status": "healthy",
        "last_run": last_run["time"],
        "last_status": last_run["status"],
        "is_running": is_running,
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
