"""
Livite Unified Hub — runs on Render free tier.

Endpoints:
  /              — Unified hub dashboard (Sales + Email status)
  /crm           — CRM pipeline status with manual sync button
  /cron          — Runs the CRM pipeline (hit by external pinger every 2 min)
  /crm-cron      — Alias for /cron
  /sync          — Manual trigger (same as /cron but with redirect UI)
  /api/hub-status — JSON status for all services (auto-refresh)
  /health        — Health check for uptime monitors
  /logs          — View last cron output for debugging

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
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import requests as http_requests
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

        last_run["time"] = datetime.now(timezone.utc).isoformat()
        last_run["status"] = "success" if result.returncode == 0 else "error"
        # Keep last 5000 chars of output
        last_run["output"] = (result.stderr or "")[-5000:]

        return {
            "status": last_run["status"],
            "time": last_run["time"],
            "returncode": result.returncode,
        }

    except subprocess.TimeoutExpired:
        last_run["time"] = datetime.now(timezone.utc).isoformat()
        last_run["status"] = "timeout"
        return {"status": "timeout"}

    except Exception as e:
        last_run["time"] = datetime.now(timezone.utc).isoformat()
        last_run["status"] = f"error: {str(e)}"
        return {"status": "error", "message": str(e)}

    finally:
        is_running = False


# ---------------------------------------------------------------------------
# Hub: Service config & health fetching
# ---------------------------------------------------------------------------

SERVICE_CONFIG = {
    "sales": {
        "name": "Sales Analysis",
        "desc": "Daily P&L, forecasting, scheduling, and restaurant BI",
        "health_path": "/health",
        "env_key": "HUB_SALES_URL",
        "default_url": "https://livite-dashboard.onrender.com",
        "dashboard_url": "https://livite-dashboard.onrender.com",
        "internal": False,
        "icon": '<svg viewBox="0 0 24 24"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>',
    },
    "email": {
        "name": "Email & Delivery Ops",
        "desc": "Email triage, Telegram bots, Mustafa delivery sync",
        "health_path": "/status",
        "env_key": "HUB_EMAIL_URL",
        "default_url": "https://web-production-62f22.up.railway.app",
        "dashboard_url": None,
        "internal": False,
        "icon": '<svg viewBox="0 0 24 24"><path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/><polyline points="22,6 12,13 2,6"/></svg>',
    },
}


def _fetch_service_health(url, timeout=2.5):
    """Fetch JSON from a service URL with timeout."""
    try:
        resp = http_requests.get(url, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        data["_ok"] = True
        return data
    except http_requests.exceptions.Timeout:
        return {"_ok": False, "_error": "timeout"}
    except http_requests.exceptions.ConnectionError:
        return {"_ok": False, "_error": "unreachable"}
    except Exception as e:
        return {"_ok": False, "_error": str(e)[:80]}


def _fetch_all_services():
    """Fetch health from all services in parallel."""
    results = {}

    def fetch_one(svc_id, cfg):
        base = os.environ.get(cfg["env_key"], cfg["default_url"])
        url = base.rstrip("/") + cfg["health_path"]
        return svc_id, _fetch_service_health(url)

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(fetch_one, k, v): k for k, v in SERVICE_CONFIG.items()}
        for future in as_completed(futures):
            svc_id, data = future.result()
            results[svc_id] = data

    return results


def _derive_service_state(svc_id, data):
    """Convert raw health data to display state."""
    if not data.get("_ok"):
        return {
            "status": "Offline",
            "color": "error",
            "metrics": [("Error", data.get("_error", "Unknown"))],
        }

    if svc_id == "sales":
        is_ok = data.get("status") == "ok"
        return {
            "status": "Online" if is_ok else "Degraded",
            "color": "success" if is_ok else "warning",
            "metrics": [
                ("Status", "Operational" if is_ok else "Degraded"),
                ("Chat AI", "Enabled" if data.get("chat_enabled") else "Disabled"),
            ],
        }

    elif svc_id == "email":
        is_ok = data.get("status") == "ok"
        return {
            "status": "Online" if is_ok else "Degraded",
            "color": "success" if is_ok else "warning",
            "metrics": [
                ("Pending", str(data.get("pending_emails", "?"))),
                ("Processed", str(data.get("processed_emails", "?"))),
                ("Version", str(data.get("version", "?"))),
            ],
        }

    return {"status": "Unknown", "color": "warning", "metrics": []}


# ---------------------------------------------------------------------------
# Shared design system — warm cream / earthy
# ---------------------------------------------------------------------------

_GOOGLE_FONTS = """<link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=DM+Sans:ital,opsz,wght@0,9..40,300;0,9..40,400;0,9..40,500;0,9..40,600;0,9..40,700&family=DM+Serif+Display&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">"""


def _base_styles():
    """Shared CSS foundation — warm cream theme."""
    return """
        :root {
            --bg: #F5EDDC;
            --bg-card: #FFFEF9;
            --bg-card-hover: #FFFDF5;
            --bg-inset: rgba(0, 0, 0, 0.03);
            --accent: #B45309;
            --accent-light: #D97706;
            --accent-dim: rgba(180, 83, 9, 0.08);
            --accent-border: rgba(180, 83, 9, 0.15);
            --success: #15803d;
            --success-bg: rgba(21, 128, 61, 0.08);
            --error: #dc2626;
            --error-bg: rgba(220, 38, 38, 0.08);
            --warning: #ca8a04;
            --warning-bg: rgba(202, 138, 4, 0.08);
            --text: #292524;
            --text-secondary: #57534e;
            --text-muted: #a8a29e;
            --border: rgba(0, 0, 0, 0.08);
            --border-strong: rgba(0, 0, 0, 0.12);
            --shadow-sm: 0 1px 2px rgba(0,0,0,0.04);
            --shadow-md: 0 4px 12px rgba(0,0,0,0.06);
            --shadow-lg: 0 8px 24px rgba(0,0,0,0.08);
        }

        * { margin: 0; padding: 0; box-sizing: border-box; }

        body {
            font-family: 'DM Sans', -apple-system, sans-serif;
            background: var(--bg);
            color: var(--text);
            min-height: 100vh;
            -webkit-font-smoothing: antialiased;
        }

        .shell { position: relative; }

        /* Top nav */
        .topbar {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 14px 32px;
            border-bottom: 1px solid var(--border);
            background: rgba(245, 237, 220, 0.85);
            backdrop-filter: blur(16px);
            -webkit-backdrop-filter: blur(16px);
            position: sticky;
            top: 0;
            z-index: 100;
        }

        .topbar-brand {
            display: flex;
            align-items: center;
            gap: 10px;
            text-decoration: none;
        }

        .topbar-logo {
            width: 28px;
            height: 28px;
            border-radius: 8px;
            background: var(--accent);
            display: flex;
            align-items: center;
            justify-content: center;
            color: white;
            font-family: 'DM Serif Display', serif;
            font-size: 15px;
            font-weight: 400;
        }

        .topbar-title {
            font-family: 'DM Serif Display', serif;
            font-size: 18px;
            font-weight: 400;
            color: var(--text);
            letter-spacing: 0.3px;
        }

        .topbar-nav {
            display: flex;
            gap: 4px;
        }

        .topbar-nav a {
            font-size: 13px;
            font-weight: 500;
            color: var(--text-muted);
            text-decoration: none;
            padding: 6px 14px;
            border-radius: 8px;
            transition: all 0.2s ease;
        }

        .topbar-nav a:hover {
            color: var(--text);
            background: var(--bg-inset);
        }

        .topbar-nav a.active {
            color: var(--accent);
            background: var(--accent-dim);
        }

        /* Animations */
        @keyframes fadeUp {
            from { opacity: 0; transform: translateY(12px); }
            to { opacity: 1; transform: translateY(0); }
        }

        @keyframes scaleIn {
            from { opacity: 0; transform: scale(0.97); }
            to { opacity: 1; transform: scale(1); }
        }
    """


def _nav_html(active="hub"):
    """Shared navigation bar."""
    links = [
        ("hub", "/", "Hub"),
        ("crm", "/crm", "CRM"),
        ("logs", "/logs", "Logs"),
    ]
    nav_items = "".join(
        f'<a href="{href}" class="{"active" if key == active else ""}">{label}</a>'
        for key, href, label in links
    )
    return f"""
    <header class="topbar">
        <div class="topbar-brand">
            <div class="topbar-logo">L</div>
            <span class="topbar-title">Livite</span>
        </div>
        <nav class="topbar-nav">{nav_items}</nav>
    </header>
    """


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def hub():
    """Unified hub — shows health/status of Livite services."""
    raw = _fetch_all_services()
    services_html = ""
    for idx, (svc_id, cfg) in enumerate(SERVICE_CONFIG.items()):
        state = _derive_service_state(svc_id, raw.get(svc_id, {"_ok": False, "_error": "not fetched"}))
        color = state["color"]
        metrics_html = "".join(
            f'<div class="svc-metric"><span class="svc-metric-label">{label}</span>'
            f'<span class="svc-metric-value">{value}</span></div>'
            for label, value in state["metrics"]
        )
        link_url = cfg["dashboard_url"]
        link_target = ' target="_blank" rel="noopener"' if link_url and link_url.startswith("http") else ""
        link_html = (
            f'<a class="svc-link" href="{link_url}"{link_target}>'
            f'<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>'
            f"Open Dashboard</a>"
            if link_url
            else '<span class="svc-link-muted">Managed via Telegram</span>'
        )
        services_html += f'''
            <div class="service-card" id="svc-{svc_id}" style="animation-delay: {0.1 + idx * 0.1}s;">
                <div class="svc-header">
                    <div class="svc-icon">{cfg["icon"]}</div>
                    <div class="svc-status status-{color}">{state["status"]}</div>
                </div>
                <h2 class="svc-name">{cfg["name"]}</h2>
                <p class="svc-desc">{cfg["desc"]}</p>
                <div class="svc-metrics">{metrics_html}</div>
                {link_html}
            </div>'''

    now_str = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
    hour = datetime.now(timezone.utc).hour - 5
    if hour < 0:
        hour += 24
    greeting = "Good morning" if hour < 12 else ("Good afternoon" if hour < 17 else "Good evening")

    up_count = sum(1 for d in raw.values() if d.get("_ok"))
    total_count = len(SERVICE_CONFIG)
    all_up = up_count == total_count

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Livite — Dashboard</title>
    {_GOOGLE_FONTS}
    <style>
        {_base_styles()}

        .hub {{
            max-width: 780px;
            margin: 0 auto;
            padding: 48px 24px 80px;
        }}

        .hub-hero {{
            margin-bottom: 40px;
            animation: fadeUp 0.5s ease-out both;
        }}

        .hub-hero h1 {{
            font-family: 'DM Serif Display', serif;
            font-size: 32px;
            font-weight: 400;
            color: var(--text);
            margin-bottom: 8px;
        }}

        .hub-meta {{
            display: flex;
            align-items: center;
            gap: 16px;
        }}

        .systems-badge {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            font-size: 13px;
            font-weight: 500;
            color: {"var(--success)" if all_up else "var(--warning)"};
        }}

        .systems-badge::before {{
            content: '';
            width: 7px;
            height: 7px;
            border-radius: 50%;
            background: {"var(--success)" if all_up else "var(--warning)"};
        }}

        .refresh-time {{
            font-size: 13px;
            color: var(--text-muted);
        }}

        /* Service cards */
        .service-grid {{
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 16px;
        }}

        .service-card {{
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 14px;
            padding: 24px;
            transition: all 0.25s ease;
            animation: fadeUp 0.5s ease-out both;
            display: flex;
            flex-direction: column;
            box-shadow: var(--shadow-sm);
        }}

        .service-card:hover {{
            border-color: var(--border-strong);
            box-shadow: var(--shadow-md);
            transform: translateY(-2px);
        }}

        .svc-header {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 14px;
        }}

        .svc-icon {{
            width: 38px;
            height: 38px;
            border-radius: 10px;
            display: flex;
            align-items: center;
            justify-content: center;
            background: var(--accent-dim);
        }}

        .svc-icon svg {{
            width: 18px;
            height: 18px;
            stroke: var(--accent);
            fill: none;
            stroke-width: 2;
            stroke-linecap: round;
            stroke-linejoin: round;
        }}

        .svc-status {{
            font-size: 12px;
            font-weight: 600;
            padding: 4px 10px;
            border-radius: 100px;
        }}

        .svc-status.status-success {{
            color: var(--success);
            background: var(--success-bg);
        }}

        .svc-status.status-error {{
            color: var(--error);
            background: var(--error-bg);
        }}

        .svc-status.status-warning {{
            color: var(--warning);
            background: var(--warning-bg);
        }}

        .svc-name {{
            font-family: 'DM Serif Display', serif;
            font-size: 18px;
            font-weight: 400;
            margin-bottom: 4px;
            color: var(--text);
        }}

        .svc-desc {{
            font-size: 13px;
            color: var(--text-muted);
            margin-bottom: 18px;
            line-height: 1.5;
        }}

        .svc-metrics {{
            flex: 1;
            display: flex;
            flex-direction: column;
            gap: 6px;
            margin-bottom: 18px;
        }}

        .svc-metric {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 8px 12px;
            background: var(--bg-inset);
            border-radius: 8px;
        }}

        .svc-metric-label {{
            font-size: 12px;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.5px;
            font-weight: 500;
        }}

        .svc-metric-value {{
            font-family: 'JetBrains Mono', monospace;
            font-size: 13px;
            font-weight: 500;
            color: var(--text);
        }}

        .svc-link {{
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            padding: 10px 16px;
            font-size: 13px;
            font-weight: 600;
            color: var(--accent);
            text-decoration: none;
            border: 1px solid var(--accent-border);
            border-radius: 10px;
            background: var(--accent-dim);
            transition: all 0.2s ease;
        }}

        .svc-link:hover {{
            background: rgba(180, 83, 9, 0.12);
            box-shadow: var(--shadow-sm);
        }}

        .svc-link-muted {{
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 10px 16px;
            font-size: 13px;
            color: var(--text-muted);
            border: 1px solid var(--border);
            border-radius: 10px;
        }}

        @media (max-width: 640px) {{
            .service-grid {{ grid-template-columns: 1fr; }}
            .hub {{ padding: 32px 16px 60px; }}
            .hub-hero h1 {{ font-size: 26px; }}
            .topbar {{ padding: 12px 16px; }}
        }}
    </style>
</head>
<body>
<div class="shell">
    {_nav_html("hub")}

    <main class="hub">
        <section class="hub-hero">
            <h1>{greeting}</h1>
            <div class="hub-meta">
                <span class="systems-badge">{up_count}/{total_count} systems online</span>
                <span class="refresh-time" id="last-refresh">{now_str}</span>
            </div>
        </section>

        <div class="service-grid">
            {services_html}
        </div>
    </main>
</div>
<script>
(function() {{
    var REFRESH = 30000;
    var cMap = {{success:'status-success', error:'status-error', warning:'status-warning'}};

    function esc(t) {{
        var d = document.createElement('div');
        d.appendChild(document.createTextNode(t));
        return d.innerHTML;
    }}

    async function refresh() {{
        try {{
            var r = await fetch('/api/hub-status');
            if (!r.ok) return;
            var data = await r.json();
            for (var id in data) {{
                if (id.startsWith('_')) continue;
                var card = document.getElementById('svc-' + id);
                if (!card) continue;
                var svc = data[id];
                var pill = card.querySelector('.svc-status');
                if (pill) {{
                    pill.textContent = svc.status;
                    pill.className = 'svc-status ' + (cMap[svc.color] || 'status-warning');
                }}
                var mEl = card.querySelector('.svc-metrics');
                if (mEl && svc.metrics) {{
                    mEl.innerHTML = svc.metrics.map(function(m) {{
                        return '<div class="svc-metric"><span class="svc-metric-label">' + esc(m[0])
                            + '</span><span class="svc-metric-value">' + esc(m[1]) + '</span></div>';
                    }}).join('');
                }}
            }}
            var ts = document.getElementById('last-refresh');
            if (ts && data._timestamp) {{
                ts.textContent = data._timestamp.slice(11, 19) + ' UTC';
            }}
        }} catch(e) {{}}
    }}

    setInterval(refresh, REFRESH);
}})();
</script>
</body>
</html>"""


@app.route("/crm")
def crm_page():
    """CRM pipeline status page."""
    last_time = last_run["time"] or "Never"
    last_status = last_run["status"] or "Awaiting first run"
    is_success = last_run["status"] == "success"
    is_error = last_run["status"] and "error" in str(last_run["status"])

    if is_success:
        status_label = "Operational"
        status_color = "success"
    elif is_error:
        status_label = "Error"
        status_color = "error"
    else:
        status_label = "Idle"
        status_color = "muted"

    running_indicator = ""
    if is_running:
        status_label = "Running"
        status_color = "accent"
        running_indicator = '<div class="running-bar"><div class="running-fill"></div></div>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>CRM Pipeline — Livite</title>
    {_GOOGLE_FONTS}
    <style>
        {_base_styles()}

        .crm-page {{
            max-width: 560px;
            margin: 0 auto;
            padding: 60px 24px 80px;
            text-align: center;
        }}

        .crm-status {{
            display: inline-flex;
            align-items: center;
            gap: 8px;
            padding: 6px 16px;
            border-radius: 100px;
            font-size: 13px;
            font-weight: 600;
            margin-bottom: 24px;
            animation: fadeUp 0.5s ease-out both;
        }}

        .crm-status::before {{
            content: '';
            width: 7px;
            height: 7px;
            border-radius: 50%;
        }}

        .crm-status.st-success {{ color: var(--success); background: var(--success-bg); }}
        .crm-status.st-success::before {{ background: var(--success); }}
        .crm-status.st-error {{ color: var(--error); background: var(--error-bg); }}
        .crm-status.st-error::before {{ background: var(--error); }}
        .crm-status.st-muted {{ color: var(--text-muted); background: var(--bg-inset); }}
        .crm-status.st-muted::before {{ background: var(--text-muted); }}
        .crm-status.st-accent {{ color: var(--accent); background: var(--accent-dim); }}
        .crm-status.st-accent::before {{ background: var(--accent); }}

        .crm-page h1 {{
            font-family: 'DM Serif Display', serif;
            font-size: 30px;
            font-weight: 400;
            margin-bottom: 8px;
            animation: fadeUp 0.5s ease-out 0.05s both;
        }}

        .crm-page .subtitle {{
            font-size: 15px;
            color: var(--text-muted);
            margin-bottom: 32px;
            animation: fadeUp 0.5s ease-out 0.1s both;
        }}

        .stat-row {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 12px;
            margin-bottom: 32px;
            animation: fadeUp 0.5s ease-out 0.15s both;
        }}

        .stat-box {{
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 18px 16px;
            box-shadow: var(--shadow-sm);
        }}

        .stat-box .label {{
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            color: var(--text-muted);
            font-weight: 500;
            margin-bottom: 6px;
        }}

        .stat-box .value {{
            font-family: 'JetBrains Mono', monospace;
            font-size: 14px;
            font-weight: 500;
            color: var(--text);
            word-break: break-all;
        }}

        .run-btn {{
            display: inline-flex;
            align-items: center;
            gap: 8px;
            padding: 14px 32px;
            background: var(--accent);
            color: white;
            font-size: 14px;
            font-weight: 600;
            text-decoration: none;
            border-radius: 12px;
            transition: all 0.2s ease;
            box-shadow: 0 2px 8px rgba(180, 83, 9, 0.2);
            animation: fadeUp 0.5s ease-out 0.2s both;
        }}

        .run-btn:hover {{
            background: var(--accent-light);
            transform: translateY(-1px);
            box-shadow: 0 4px 16px rgba(180, 83, 9, 0.25);
        }}

        .run-btn svg {{
            width: 16px;
            height: 16px;
            fill: currentColor;
        }}

        .running-bar {{
            margin: 20px auto 0;
            height: 3px;
            background: var(--bg-inset);
            border-radius: 3px;
            overflow: hidden;
            max-width: 200px;
        }}

        .running-fill {{
            height: 100%;
            width: 35%;
            background: var(--accent);
            border-radius: 3px;
            animation: slide 1.5s ease-in-out infinite;
        }}

        @keyframes slide {{
            0% {{ transform: translateX(-100%); }}
            100% {{ transform: translateX(400%); }}
        }}

        @media (max-width: 480px) {{
            .stat-row {{ grid-template-columns: 1fr; }}
            .crm-page {{ padding: 40px 16px 60px; }}
        }}
    </style>
</head>
<body>
<div class="shell">
    {_nav_html("crm")}

    <main class="crm-page">
        <div class="crm-status st-{status_color}">{status_label}</div>
        <h1>CRM Pipeline</h1>
        <p class="subtitle">NCAA outreach automation</p>

        <div class="stat-row">
            <div class="stat-box">
                <div class="label">Last Run</div>
                <div class="value">{last_time[:16].replace("T", " ") if last_time != "Never" else "Never"}</div>
            </div>
            <div class="stat-box">
                <div class="label">Result</div>
                <div class="value">{last_status.replace("_", " ").title()}</div>
            </div>
        </div>

        <a href="/sync" class="run-btn">
            <svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>
            Run Pipeline
        </a>
        {running_indicator}
    </main>
</div>
</body>
</html>"""


@app.route("/cron")
@app.route("/crm-cron")
def cron():
    """Endpoint for external cron pinger (cron-job.org, UptimeRobot, etc.)."""
    result = run_cron()
    return jsonify(result)


@app.route("/sync")
def sync():
    """Manual trigger with UI feedback."""
    result = run_cron()

    if result["status"] == "already_running":
        msg = "Pipeline Already Running"
        detail = "A sync is already in progress. Check back in a moment."
        color = "warning"
    elif result["status"] == "success":
        msg = "Pipeline Complete"
        detail = "All stages executed successfully."
        color = "success"
    else:
        msg = "Pipeline Error"
        detail = f"Finished with status: {result['status']}"
        color = "error"

    import html as html_mod
    detail = html_mod.escape(detail)

    color_map = {
        "success": "var(--success)",
        "error": "var(--error)",
        "warning": "var(--warning)",
    }
    c = color_map.get(color, "var(--text-muted)")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Sync Result — Livite</title>
    {_GOOGLE_FONTS}
    <style>
        {_base_styles()}

        .result-page {{
            display: flex;
            align-items: center;
            justify-content: center;
            min-height: calc(100vh - 53px);
            padding: 24px;
        }}

        .result-card {{
            text-align: center;
            max-width: 400px;
            animation: scaleIn 0.4s ease-out both;
        }}

        .result-dot {{
            width: 48px;
            height: 48px;
            border-radius: 50%;
            margin: 0 auto 20px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 22px;
        }}

        .result-card h1 {{
            font-family: 'DM Serif Display', serif;
            font-size: 26px;
            font-weight: 400;
            margin-bottom: 8px;
        }}

        .result-card p {{
            font-size: 15px;
            color: var(--text-secondary);
            margin-bottom: 28px;
            line-height: 1.6;
        }}

        .back-link {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            font-size: 13px;
            font-weight: 600;
            color: var(--accent);
            text-decoration: none;
            padding: 10px 20px;
            border-radius: 10px;
            border: 1px solid var(--accent-border);
            background: var(--accent-dim);
            transition: all 0.2s ease;
        }}

        .back-link:hover {{
            background: rgba(180, 83, 9, 0.12);
        }}
    </style>
</head>
<body>
<div class="shell">
    {_nav_html("crm")}

    <main class="result-page">
        <div class="result-card">
            <div class="result-dot" style="background: {"var(--success-bg)" if color == "success" else ("var(--error-bg)" if color == "error" else "var(--warning-bg)")}; color: {c};">
                {"&#10003;" if color == "success" else ("&#10007;" if color == "error" else "&#8987;")}
            </div>
            <h1>{msg}</h1>
            <p>{detail}</p>
            <a href="/crm" class="back-link">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/></svg>
                Back to CRM
            </a>
        </div>
    </main>
</div>
</body>
</html>"""


@app.route("/api/hub-status")
def api_hub_status():
    """JSON endpoint for hub auto-refresh."""
    raw = _fetch_all_services()
    result = {}
    for svc_id, data in raw.items():
        state = _derive_service_state(svc_id, data)
        result[svc_id] = {
            "status": state["status"],
            "color": state["color"],
            "metrics": state["metrics"],
        }
    result["_timestamp"] = datetime.now(timezone.utc).isoformat()
    return jsonify(result)


@app.route("/health")
def health():
    """Health check endpoint."""
    return jsonify({
        "status": "healthy",
        "last_run": last_run["time"],
        "last_status": last_run["status"],
        "is_running": is_running,
    })


@app.route("/debug")
def debug():
    """Debug endpoint — show runtime info."""
    import platform
    info = {"python": platform.python_version()}
    try:
        from notion_client import Client
        c = Client(auth="test")
        info["notion_client_has_query"] = hasattr(c.databases, "query")
        info["databases_type"] = str(type(c.databases))
        info["databases_dir"] = [x for x in dir(c.databases) if not x.startswith("_")]
    except Exception as e:
        info["notion_error"] = str(e)
    return jsonify(info)


@app.route("/logs")
def logs():
    """Show last cron output for debugging."""
    output = last_run.get("output") or "No output yet"
    last_time = last_run["time"] or "Never"
    last_status = last_run["status"] or "Not run yet"

    import html as html_mod
    output_escaped = html_mod.escape(output)
    status_escaped = html_mod.escape(str(last_status))

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Logs — Livite</title>
    {_GOOGLE_FONTS}
    <style>
        {_base_styles()}

        .logs-page {{
            max-width: 860px;
            margin: 0 auto;
            padding: 36px 24px 80px;
        }}

        .logs-header {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 20px;
            animation: fadeUp 0.4s ease-out both;
            flex-wrap: wrap;
            gap: 12px;
        }}

        .logs-header h1 {{
            font-family: 'DM Serif Display', serif;
            font-size: 24px;
            font-weight: 400;
        }}

        .logs-meta {{
            display: flex;
            gap: 10px;
        }}

        .logs-meta span {{
            font-size: 12px;
            color: var(--text-muted);
            padding: 5px 12px;
            background: var(--bg-card);
            border-radius: 8px;
            border: 1px solid var(--border);
        }}

        .terminal {{
            background: #1e1e1e;
            border-radius: 12px;
            overflow: hidden;
            animation: fadeUp 0.4s ease-out 0.1s both;
            box-shadow: var(--shadow-lg);
        }}

        .terminal-bar {{
            display: flex;
            align-items: center;
            gap: 7px;
            padding: 12px 16px;
            background: #2d2d2d;
        }}

        .terminal-dot {{
            width: 10px;
            height: 10px;
            border-radius: 50%;
        }}

        .terminal-dot.r {{ background: #ff5f57; }}
        .terminal-dot.y {{ background: #febc2e; }}
        .terminal-dot.g {{ background: #28c840; }}

        .terminal-title {{
            font-family: 'JetBrains Mono', monospace;
            font-size: 11px;
            color: #999;
            margin-left: 8px;
        }}

        .terminal pre {{
            font-family: 'JetBrains Mono', monospace;
            font-size: 12px;
            line-height: 1.7;
            color: #d4d4d4;
            padding: 20px 24px;
            margin: 0;
            white-space: pre-wrap;
            word-break: break-word;
            max-height: 70vh;
            overflow-y: auto;
        }}

        .terminal pre::-webkit-scrollbar {{
            width: 6px;
        }}

        .terminal pre::-webkit-scrollbar-track {{
            background: transparent;
        }}

        .terminal pre::-webkit-scrollbar-thumb {{
            background: #555;
            border-radius: 3px;
        }}

        @media (max-width: 600px) {{
            .logs-page {{ padding: 24px 12px 60px; }}
            .logs-header {{ flex-direction: column; align-items: flex-start; }}
        }}
    </style>
</head>
<body>
<div class="shell">
    {_nav_html("logs")}

    <main class="logs-page">
        <div class="logs-header">
            <h1>Pipeline Logs</h1>
            <div class="logs-meta">
                <span>{last_time[:16].replace("T", " ") if last_time != "Never" else "Never"}</span>
                <span>{status_escaped}</span>
            </div>
        </div>

        <div class="terminal">
            <div class="terminal-bar">
                <span class="terminal-dot r"></span>
                <span class="terminal-dot y"></span>
                <span class="terminal-dot g"></span>
                <span class="terminal-title">notion_cron_runner.py</span>
            </div>
            <pre>{output_escaped}</pre>
        </div>
    </main>
</div>
</body>
</html>"""


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
