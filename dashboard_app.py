"""
Livite Dashboard — Web App

Flask app that generates dashboards on-demand.
No pre-generated files needed — pick a date, get a dashboard.

Usage:
    python3 app.py                  # Run locally on port 5001
    gunicorn app:app -b 0.0.0.0:$PORT  # Production (Render, Railway, etc.)
"""

import logging
import os
import sys
import json
import time
import warnings
import base64
import hashlib
from datetime import datetime, timedelta
from functools import wraps

logger = logging.getLogger(__name__)

import yaml

warnings.filterwarnings('ignore')

# Add tools/dashboard/ to path so dashboard imports work
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'tools', 'dashboard'))

from dotenv import load_dotenv
load_dotenv()

from flask import Flask, request, Response, redirect, url_for, session, jsonify

import pandas as pd
from fetch_toast_data import get_daily_data, get_toast_csv_cached, list_available_dates
from dashboard_metrics import compute_all_metrics, detect_anomalies, compute_analyst_insights, parse_toast_datetime
from dashboard_comparisons import (
    resolve_comparison_dates, fetch_all_comparisons, compute_all_deltas
)
from dashboard_html import build_dashboard
from dashboard_aggregation import aggregate_metrics
from metrics_cache import get_cached_metrics, cache_metrics, is_today, cache_stats, batch_connection

# ── Claude AI Chat ──
ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY', '')
_anthropic_client = None

def _get_anthropic():
    global _anthropic_client
    if _anthropic_client is None and ANTHROPIC_API_KEY:
        import anthropic
        _anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _anthropic_client

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY') or os.urandom(32).hex()

# ── Vendor Prices Blueprint ──
from vendor_prices import bp as vendor_prices_bp
app.register_blueprint(vendor_prices_bp)

# ── Invoice Tracking Blueprint ──
from invoices import bp as invoices_bp
app.register_blueprint(invoices_bp)

# ── Hub Dashboard Blueprint ──
from tools.dashboard.hub import bp as hub_bp
app.register_blueprint(hub_bp)

# ── Rate limiting ──
try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address
    limiter = Limiter(get_remote_address, app=app, default_limits=["120/minute"],
                      storage_uri="memory://")
except ImportError:
    limiter = None

# ── Security: cookie & session settings ──
app.config['SESSION_COOKIE_SECURE'] = bool(os.getenv('RENDER'))  # HTTPS on Render only
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=8)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16 MB max (vendor price file uploads)

PROJECT_ROOT = os.path.dirname(__file__)
EARLIEST_DATE = datetime(2024, 11, 7)

# ── Authentication ──
DASHBOARD_PASSWORD = os.getenv('DASHBOARD_PASSWORD', '')

# Load users from config.yaml (username → {password, name})
_config_path = os.path.join(PROJECT_ROOT, 'config.yaml')
_USERS = {}
try:
    with open(_config_path) as _f:
        _cfg = yaml.safe_load(_f) or {}
    _USERS = _cfg.get('users', {})
except FileNotFoundError:
    pass

def _check_login(username, password):
    """Check username/password against config.yaml users (timing-safe).
    Returns {'name': ..., 'role': ...} on success, None on failure."""
    from hmac import compare_digest
    if _USERS:
        user = _USERS.get(username.lower())
        if user and compare_digest(str(user.get('password', '')), password):
            return {'name': user.get('name', username),
                    'role': user.get('role', 'manager')}
        return None
    # Fallback: legacy single-password mode
    if DASHBOARD_PASSWORD and compare_digest(DASHBOARD_PASSWORD, password):
        return {'name': 'User', 'role': 'owner'}
    if not DASHBOARD_PASSWORD:
        return {'name': 'User', 'role': 'owner'}
    return None

def login_required(f):
    """Decorator: redirect to /login if not authenticated."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not DASHBOARD_PASSWORD and not _USERS:
            return f(*args, **kwargs)  # Auth disabled
        if not session.get('authenticated'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def owner_required(f):
    """Decorator: require owner role. Managers get redirected to vendor prices."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not DASHBOARD_PASSWORD and not _USERS:
            return f(*args, **kwargs)
        if not session.get('authenticated'):
            return redirect(url_for('login'))
        if session.get('role') != 'owner':
            return redirect('/prices/')
        return f(*args, **kwargs)
    return decorated

# ── Cache the logo once at startup ──
_LOGO_B64 = ""

def _load_logo():
    global _LOGO_B64
    # Try static/ first (deployed), then original logo dir (local dev)
    candidates = [
        os.path.join(PROJECT_ROOT, "static", "logo.png"),
        os.path.join(PROJECT_ROOT, "assets", "brand", "livite-logo-files 3",
                     "01_wordmark", "01_color", "livite-wordmark_green_rgb.png"),
    ]
    for logo_path in candidates:
        if os.path.exists(logo_path):
            with open(logo_path, "rb") as f:
                _LOGO_B64 = base64.b64encode(f.read()).decode("ascii")
            break
    return _LOGO_B64


_available_dates_cache = None
_available_dates_ts = 0

def _get_available_dates():
    """Get set of available dates from Azure (cached with 5-min TTL)."""
    global _available_dates_cache, _available_dates_ts
    now = time.time()
    if _available_dates_cache is not None and (now - _available_dates_ts) < 300:
        return _available_dates_cache
    try:
        _available_dates_cache = set(list_available_dates())
        _available_dates_ts = now
    except Exception:
        if _available_dates_cache is not None:
            return _available_dates_cache
        return set()
    return _available_dates_cache


def _compute_4wra(date, available_dates):
    """Compute 4-week rolling average for 15-min slots."""
    slot_totals = {}
    weeks_found = 0

    for w in range(1, 5):
        hist_date = date - timedelta(days=7 * w)
        if hist_date < EARLIEST_DATE:
            continue
        hist_str = hist_date.strftime("%Y%m%d")
        if hist_str not in available_dates:
            continue
        try:
            od = get_toast_csv_cached(hist_str, "OrderDetails.csv")
        except Exception:
            continue

        if 'Voided' in od.columns:
            od = od[od['Voided'] != True].copy()
        od['Amount'] = pd.to_numeric(od.get('Amount', pd.Series(dtype='float64')), errors='coerce').fillna(0)
        od['_opened_dt'] = od['Opened'].apply(parse_toast_datetime)
        od['_hour'] = od['_opened_dt'].apply(lambda x: x.hour if x else None)
        od['_minute'] = od['_opened_dt'].apply(lambda x: x.minute if x else None)
        weeks_found += 1

        for h in range(7, 23):
            for q in [0, 15, 30, 45]:
                mask = (od['_hour'] == h) & (od['_minute'].notna())
                mask = mask & (od['_minute'] >= q) & (od['_minute'] < q + 15)
                slot_df = od[mask]
                q_rev = round(slot_df['Amount'].sum(), 2)
                q_orders = len(slot_df)
                key = f"{h}:{q:02d}"
                if key not in slot_totals:
                    slot_totals[key] = {"revenues": [], "orders": []}
                slot_totals[key]["revenues"].append(q_rev)
                slot_totals[key]["orders"].append(q_orders)

    result = {}
    for key, vals in slot_totals.items():
        n = len(vals["revenues"])
        result[key] = {
            "avg_revenue": round(sum(vals["revenues"]) / n, 2) if n > 0 else 0,
            "avg_orders": round(sum(vals["orders"]) / n, 1) if n > 0 else 0,
            "weeks_found": n,
        }
    return result, weeks_found


def _generate_daily_html(date: datetime) -> str:
    """Generate daily dashboard HTML string (no file I/O)."""
    date_str = date.strftime("%Y%m%d")
    use_cache = not is_today(date_str)

    # Check metrics cache first (skip for today — data may be incomplete)
    metrics = get_cached_metrics(date_str) if use_cache else None
    from_cache = metrics is not None
    needs_recache = False

    if metrics is None:
        data = get_daily_data(date, quiet=True)
        if 'OrderDetails' not in data:
            return _error_page(f"No data available for {date.strftime('%B %d, %Y')}")
        metrics = compute_all_metrics(data, date)
    elif metrics.get('weather') is None:
        # Cached from range computation (no weather) — enrich with weather
        try:
            from fetch_weather_data import get_daily_weather, get_weather_range
            from metrics.weather import compute_weather_metrics
            weather_data = get_daily_weather(date)
            if weather_data:
                hist_start = date - timedelta(days=30)
                hist_weather = get_weather_range(hist_start, date - timedelta(days=1))
                hist_revenue = []
                for i in range(1, 31):
                    hd = (date - timedelta(days=i)).strftime("%Y%m%d")
                    cached_day = get_cached_metrics(hd)
                    if cached_day and cached_day.get('revenue'):
                        hist_revenue.append({
                            'date': hd,
                            'revenue': cached_day['revenue'].get('toast_total', 0)
                        })
                metrics['weather'] = compute_weather_metrics(
                    weather_data, date_str, hist_weather, hist_revenue
                )
                needs_recache = True
        except Exception as e:
            logger.warning("Weather metric computation failed for %s: %s", date_str, e)

    # 4WRA
    available = _get_available_dates()
    try:
        slot_4wra, weeks_found = _compute_4wra(date, available)
        if metrics.get('revenue') and slot_4wra:
            metrics['revenue']['quarter_hourly_4wra'] = slot_4wra
            metrics['revenue']['quarter_hourly_4wra_weeks'] = weeks_found
    except Exception as e:
        logger.warning("4WRA computation failed for %s: %s", date_str, e)

    # Cache computed metrics (skip today, skip if unchanged from cache)
    if use_cache and metrics and (not from_cache or needs_recache):
        cache_metrics(date_str, metrics)

    # Comparisons
    comp_dates = resolve_comparison_dates(date)
    comparisons = fetch_all_comparisons(comp_dates, available)

    current_summary = {
        'revenue': metrics['revenue']['toast_total'] if metrics.get('revenue') else 0,
        'orders': metrics['revenue']['total_orders'] if metrics.get('revenue') else 0,
        'avg_check': metrics['revenue']['avg_check'] if metrics.get('revenue') else 0,
        'guests': metrics['revenue']['total_guests'] if metrics.get('revenue') else 0,
        'labor_total': metrics['labor']['total_labor'] if metrics.get('labor') else 0,
        'labor_pct': metrics['labor']['labor_pct'] if metrics.get('labor') else 0,
        'unique_customers': metrics['customers']['unique_customers'] if metrics.get('customers') else 0,
    }
    deltas = compute_all_deltas(current_summary, comparisons)
    comparisons['deltas'] = deltas

    anomalies = detect_anomalies(metrics, comparisons, date_str=date_str)

    try:
        _4wra = metrics.get('revenue', {}).get('quarter_hourly_4wra', {})
        analyst_insights = compute_analyst_insights(metrics, _4wra)
    except Exception:
        analyst_insights = []

    logo = _LOGO_B64 or _load_logo()

    prev_date = date - timedelta(days=1)
    next_date = date + timedelta(days=1)
    prev_str = prev_date.strftime("%Y%m%d") if prev_date.strftime("%Y%m%d") in available else ""
    next_str = next_date.strftime("%Y%m%d") if next_date.strftime("%Y%m%d") in available else ""

    chat_ctx = _format_metrics_context(metrics) if ANTHROPIC_API_KEY else ""

    return build_dashboard(metrics, comparisons, anomalies,
                           date_str=date_str, prev_date_str=prev_str,
                           next_date_str=next_str,
                           analyst_insights=analyst_insights,
                           logo_b64=logo,
                           chat_enabled=bool(ANTHROPIC_API_KEY),
                           chat_context=chat_ctx)


def _generate_range_html(start: datetime, end: datetime) -> str:
    """Generate range dashboard HTML string (no file I/O)."""
    try:
        return _generate_range_html_inner(start, end)
    except Exception as e:
        logger.error("Error generating range dashboard: %s", e, exc_info=True)
        return _error_page(f"Error generating range dashboard: {e}")


def _generate_range_html_inner(start: datetime, end: datetime) -> str:
    start_str = start.strftime("%Y%m%d")
    end_str = end.strftime("%Y%m%d")
    num_days = (end - start).days + 1

    # Format display string
    if start.month == end.month and start.year == end.year:
        range_display = f"{start.strftime('%B')} {start.day}-{end.day}, {start.year} ({num_days} days)"
    elif start.year == end.year:
        range_display = f"{start.strftime('%b')} {start.day} - {end.strftime('%b')} {end.day}, {start.year} ({num_days} days)"
    else:
        range_display = f"{start.strftime('%b %d, %Y')} - {end.strftime('%b %d, %Y')} ({num_days} days)"

    # Count how many days need fetching from Azure (uncached)
    uncached = 0
    current = start
    with batch_connection():
        while current <= end:
            ds = current.strftime("%Y%m%d")
            if not is_today(ds) and get_cached_metrics(ds) is None:
                uncached += 1
            current += timedelta(days=1)

    # If too many uncached days, redirect to pre-cache first
    if uncached > 20:
        return _precache_page(start_str, end_str, num_days, uncached, range_display)

    # Check for pre-computed aggregation (built during pre-cache phase)
    agg_key = f"agg_{start_str}_{end_str}"
    cached_agg = get_cached_metrics(agg_key)
    if cached_agg:
        agg = cached_agg
    else:
        daily_metrics = []
        current = start
        with batch_connection():
            while current <= end:
                ds = current.strftime("%Y%m%d")
                use_cache = not is_today(ds)

                # Try cache first for each day
                metrics = get_cached_metrics(ds) if use_cache else None

                if metrics is None:
                    data = get_daily_data(current, quiet=True)
                    if 'OrderDetails' in data:
                        metrics = compute_all_metrics(data, current, skip_weather=True)
                        if use_cache and metrics:
                            cache_metrics(ds, metrics)

                if metrics:
                    daily_metrics.append(metrics)
                current += timedelta(days=1)

        if not daily_metrics:
            return _error_page(f"No data available for {range_display}")

        agg = aggregate_metrics(daily_metrics, start_str, end_str, num_days)
    agg['date_display'] = range_display
    agg['day_of_week'] = f"{num_days}-Day Summary"

    anomalies = detect_anomalies(agg, {})
    try:
        _4wra = agg.get('revenue', {}).get('quarter_hourly_4wra', {})
        analyst_insights = compute_analyst_insights(agg, _4wra)
    except Exception:
        analyst_insights = []

    logo = _LOGO_B64 or _load_logo()

    chat_ctx = _format_metrics_context(agg) if ANTHROPIC_API_KEY else ""

    return build_dashboard(agg, comparisons={}, anomalies=anomalies,
                           date_str=f"{start_str}_{end_str}",
                           prev_date_str="", next_date_str="",
                           analyst_insights=analyst_insights,
                           logo_b64=logo,
                           chat_enabled=bool(ANTHROPIC_API_KEY),
                           chat_context=chat_ctx)


def _error_page(message: str) -> str:
    """Return a styled error page."""
    from markupsafe import escape
    safe_msg = escape(message)
    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Livite Dashboard</title>
<style>
body{{font-family:'DM Sans',sans-serif;background:#F5EDDC;display:flex;justify-content:center;align-items:center;min-height:100vh;margin:0;}}
.box{{background:#fff;border-radius:12px;padding:40px;text-align:center;max-width:400px;box-shadow:0 2px 12px rgba(0,0,0,0.08);}}
h2{{color:#475417;margin-bottom:12px;}}
p{{color:#7a7a6f;font-size:14px;margin-bottom:24px;}}
a{{color:#4a7c1f;text-decoration:none;font-weight:600;font-size:14px;}}
a:hover{{text-decoration:underline;}}
</style></head>
<body><div class="box">
<h2>No Data Available</h2>
<p>{safe_msg}</p>
<p>Data is available from November 7, 2024 onward.</p>
<a href="/">&#8592; Back to Dashboard Picker</a>
</div></body></html>"""


def _precache_page(start_str, end_str, num_days, uncached, range_display):
    """Show a loading page with smoothie cup animation, Tetris, fun facts, and AJAX pre-caching."""
    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Loading {range_display}...</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
body{{font-family:'DM Sans',sans-serif;background:#F5EDDC;display:flex;justify-content:center;align-items:center;min-height:100vh;margin:0;flex-direction:column;gap:14px;padding:16px;}}
.box{{background:#fff;border-radius:12px;padding:24px 28px;text-align:center;max-width:480px;width:100%;box-shadow:0 2px 12px rgba(0,0,0,0.08);}}
h2{{color:#475417;margin:0 0 6px;font-size:18px;}}
.sub{{color:#7a7a6f;font-size:13px;margin-bottom:16px;}}
.status{{color:#7a7a6f;font-size:12px;margin-top:8px;}}
a{{color:#4a7c1f;text-decoration:none;font-weight:600;font-size:12px;}}
/* Smoothie cup */
.cup-wrap{{display:flex;justify-content:center;margin:0 auto 4px;}}
.cup{{position:relative;width:80px;height:140px;}}
.cup-body{{position:absolute;bottom:0;width:80px;height:120px;background:#fff;border:3px solid #475417;border-radius:4px 4px 16px 16px;overflow:hidden;}}
.cup-liquid{{position:absolute;bottom:0;width:100%;background:linear-gradient(180deg,#8cc63f 0%,#4a7c1f 100%);transition:height 0.6s ease;border-radius:0 0 13px 13px;}}
.cup-lid{{position:absolute;top:0;left:-4px;width:88px;height:16px;background:#475417;border-radius:8px 8px 2px 2px;}}
.cup-straw{{position:absolute;top:-20px;left:50px;width:4px;height:48px;background:#e8512f;border-radius:2px;transform:rotate(8deg);}}
.cup-straw::after{{content:'';position:absolute;top:0;left:-3px;width:10px;height:4px;background:#e8512f;border-radius:2px;}}
.pct-label{{font-size:22px;font-weight:700;color:#fff;position:absolute;width:100%;text-align:center;bottom:35%;z-index:2;text-shadow:0 1px 3px rgba(0,0,0,0.2);}}
.pct-label.dark{{color:#475417;text-shadow:none;}}
/* Bubbles */
@keyframes bubble{{0%{{transform:translateY(0) scale(1);opacity:0.6;}}100%{{transform:translateY(-80px) scale(0.3);opacity:0;}}}}
.bubble{{position:absolute;border-radius:50%;background:rgba(255,255,255,0.4);animation:bubble 2s ease-in infinite;}}
/* Fun facts */
.fun{{background:#fff;border-radius:12px;padding:16px 22px;text-align:center;max-width:480px;width:100%;box-shadow:0 2px 12px rgba(0,0,0,0.08);}}
.fun-text{{font-size:13px;color:#475417;line-height:1.5;min-height:36px;transition:opacity 0.3s;}}
.fun-label{{font-size:10px;color:#7a7a6f;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:6px;font-weight:600;}}
/* Tetris */
.game-box{{background:#fff;border-radius:12px;padding:14px;text-align:center;max-width:480px;width:100%;box-shadow:0 2px 12px rgba(0,0,0,0.08);}}
.game-box h3{{color:#475417;margin:0 0 6px;font-size:13px;font-weight:600;}}
.game-wrap{{display:flex;justify-content:center;align-items:flex-start;gap:12px;}}
canvas{{border:2px solid #e2d9c8;border-radius:8px;display:block;background:#faf6ee;}}
.side{{text-align:left;}}
.side div{{font-size:11px;color:#7a7a6f;margin-bottom:3px;}}
.side span{{color:#475417;font-weight:700;}}
.controls{{font-size:10px;color:#a0a090;margin-top:6px;line-height:1.5;}}
/* Game tabs */
.game-tabs{{display:flex;gap:0;margin-bottom:10px;justify-content:center;}}
.game-tab{{font-family:inherit;font-size:11px;padding:5px 14px;border:1px solid #e2d9c8;background:#faf6ee;color:#7a7a6f;cursor:pointer;font-weight:500;}}
.game-tab:first-child{{border-radius:6px 0 0 6px;}}
.game-tab:last-child{{border-radius:0 6px 6px 0;border-left:none;}}
.game-tab:not(:first-child):not(:last-child){{border-left:none;}}
.game-tab.active{{background:#475417;color:#fff;font-weight:600;border-color:#475417;}}
</style></head>
<body>
<div class="box">
<h2>Building Dashboard</h2>
<p class="sub">{range_display}<br>{uncached} of {num_days} days need processing</p>
<div class="cup-wrap"><div class="cup">
  <div class="cup-straw"></div>
  <div class="cup-lid"></div>
  <div class="cup-body">
    <div class="cup-liquid" id="liquid" style="height:0%"></div>
    <div class="pct-label dark" id="pctLabel">0%</div>
    <div class="bubble" style="left:15%;bottom:5%;width:6px;height:6px;animation-delay:0s;"></div>
    <div class="bubble" style="left:55%;bottom:8%;width:4px;height:4px;animation-delay:0.7s;"></div>
    <div class="bubble" style="left:35%;bottom:3%;width:5px;height:5px;animation-delay:1.4s;"></div>
  </div>
</div></div>
<div class="status" id="status">Starting...</div>
<div style="margin-top:8px;"><a href="/">Cancel</a></div>
</div>

<div class="fun">
<div class="fun-label">Did You Know?</div>
<div class="fun-text" id="funFact">Loading fun facts...</div>
</div>

<div class="game-box">
<div class="game-tabs">
<button class="game-tab active" onclick="switchGame('tetris')">Tetris</button>
<button class="game-tab" onclick="switchGame('flappy')">Flappy Smoothie</button>
<button class="game-tab" onclick="switchGame('launch')">Salad Launch</button>
<button class="game-tab" onclick="switchGame('roulette')">Roulette</button>
<button class="game-tab" onclick="switchGame('blackjack')">Blackjack</button>
</div>
<div id="game-tetris" class="game-panel">
<div class="game-wrap">
<canvas id="game" width="200" height="360"></canvas>
<div class="side">
<div>Score: <span id="sc">0</span></div>
<div>Lines: <span id="ln">0</span></div>
<div>Best: <span id="best">0</span></div>
<div class="controls">
Desktop: Arrow keys<br>
Mobile: Tap L/R half,<br>swipe down, tap top to rotate
</div>
</div>
</div>
</div>
<div id="game-flappy" class="game-panel" style="display:none;">
<div class="game-wrap">
<canvas id="flappyCanvas" width="280" height="360" style="cursor:pointer;"></canvas>
<div class="side">
<div>Score: <span id="flappyScore">0</span></div>
<div>Best: <span id="flappyBest">0</span></div>
<div class="controls">
Desktop: Space / Up<br>
Mobile: Tap to flap
</div>
</div>
</div>
</div>
<div id="game-launch" class="game-panel" style="display:none;">
<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;padding:0 4px;">
<span style="font-size:12px;color:#475417;font-weight:600;">Score: <span id="launchScore">0</span></span>
<span style="font-size:12px;color:#7a7a6f;">Birds: <span id="launchShots">3</span></span>
<span style="font-size:11px;color:#7a7a6f;">Best: <span id="launchBest">0</span></span>
</div>
<div style="display:flex;justify-content:center;">
<canvas id="launchCanvas" width="520" height="280" style="cursor:pointer;border:2px solid #e2d9c8;border-radius:8px;max-width:100%;"></canvas>
</div>
<div class="controls" style="margin-top:6px;">Drag from slingshot to aim and launch!</div>
</div>
<div id="game-roulette" class="game-panel" style="display:none;">
<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;padding:0 4px;">
<span style="font-size:12px;color:#475417;font-weight:600;">Balance: $<span id="roulBalance">1000</span></span>
<span style="font-size:12px;color:#7a7a6f;">Bet: $<span id="roulBet">0</span></span>
<span style="font-size:11px;color:#7a7a6f;"><span id="roulResult">—</span></span>
</div>
<div style="display:flex;justify-content:center;">
<canvas id="rouletteCanvas" width="520" height="365" style="cursor:pointer;border:2px solid #e2d9c8;border-radius:8px;max-width:100%;"></canvas>
</div>
<div class="controls" style="margin-top:4px;">Click numbers/bets to place chips · Tap chip to select · Spin to go! · Yellow/Blue birds: tap in-flight for power</div>
</div>
<div id="game-blackjack" class="game-panel" style="display:none;">
<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;padding:0 4px;">
<span style="font-size:12px;color:#475417;font-weight:600;">Balance: $<span id="bjBalance">1000</span></span>
<span style="font-size:12px;color:#7a7a6f;">Bet: $<span id="bjBet">0</span></span>
<span style="font-size:11px;color:#e67e22;font-weight:600;"><span id="bjMsg"></span></span>
</div>
<div style="display:flex;justify-content:center;">
<canvas id="blackjackCanvas" width="520" height="340" style="cursor:pointer;border:2px solid #e2d9c8;border-radius:8px;max-width:100%;"></canvas>
</div>
<div class="controls" style="margin-top:4px;">Select chip amount, click to bet · Deal · Hit or Stand to play</div>
</div>
</div>

<script>
/* ── Fun Facts ── */
var facts=[
  "Livite opened in Brookline, MA serving fresh wraps, salads, smoothies, and bowls.",
  "The #1 seller is usually the Chipotle Chicken Wrap -- customers can't resist it.",
  "About 80% of smoothie orders stick with the default Oat Milk base.",
  "The busiest hour is almost always 12:00-1:00 PM -- the lunch rush is real.",
  "Livite's Herb Vinaigrette is house-made and goes on the Livite Bowl and House Salad.",
  "Fun fact: 'BOGO' Uber promos account for about 66% of all Uber Eats orders.",
  "The Thai Peanut Dressing is used on both the Peanut Salad and the Spicy Peanut Noodle Bowl.",
  "Walk-in customers have the highest average check -- they're committed to the full experience.",
  "Livite's peak smoothie season runs March through September.",
  "The Apple Walnut Salad and Berry Berry Avocado both get Balsamic Vinaigrette.",
  "Online orders (direct) have no commission fees -- every dollar counts.",
  "Kale, spinach, mango, berries, acai -- Livite blends about 200+ smoothies on a busy day.",
  "Brookline Village has been home to Livite since day one.",
  "The Buffalo Chicken Salad is the only menu item that gets Blue Cheese Dressing.",
  "DoorDash, Uber Eats, and Grubhub together can make up 30-40% of daily orders.",
  "Livite's busiest day of the week? Usually Tuesday or Wednesday.",
  "The Southwest Vegan Bowl is 100% plant-based and topped with Herb Vinaigrette.",
  "Catering orders can be 5-10x the average check -- big days are real outliers.",
  "Laurie built Livite from the ground up -- every recipe reflects her vision.",
  "Laurie's tip: The Chipotle Chicken Wrap + a Mango Smoothie is the perfect combo.",
  "Laurie personally taste-tests every new dressing batch -- quality is non-negotiable.",
];
var fi=Math.floor(Math.random()*facts.length);
document.getElementById('funFact').textContent=facts[fi];
setInterval(function(){{fi=(fi+1)%facts.length;document.getElementById('funFact').textContent=facts[fi];}},6000);

/* ── Pre-cache polling ── */
var start='{start_str}',end='{end_str}',total={uncached},aggregating=false;
function setPct(pct){{
  document.getElementById('liquid').style.height=pct+'%';
  var lbl=document.getElementById('pctLabel');
  lbl.textContent=pct+'%';
  lbl.className=pct>30?'pct-label':'pct-label dark';
}}
function poll(){{
  var url=aggregating?'/api/precache/aggregate?start='+start+'&end='+end
                     :'/api/precache?start='+start+'&end='+end;
  fetch(url)
  .then(function(r){{return r.json();}})
  .then(function(d){{
    if(aggregating){{
      if(d.done){{
        setPct(100);
        document.getElementById('status').textContent='Done! Loading dashboard...';
        window.location.href='/range/'+start+'/'+end;
      }}else{{
        setTimeout(poll,2000);
      }}
      return;
    }}
    var remaining=d.remaining!==undefined?d.remaining:total;
    var pct=Math.min(100,Math.round((total-remaining)/total*100));
    if(d.phase==='aggregating'){{
      setPct(95);
      document.getElementById('status').textContent='Blending it all together...';
      aggregating=true;
      fetch('/api/precache/aggregate?start='+start+'&end='+end)
      .then(function(r){{return r.json();}})
      .then(function(d2){{
        setPct(100);
        document.getElementById('status').textContent='Done! Loading dashboard...';
        window.location.href='/range/'+start+'/'+end;
      }})
      .catch(function(){{
        document.getElementById('status').textContent='Retrying blend...';
        setTimeout(poll,3000);
      }});
      return;
    }}
    setPct(pct);
    if(d.done){{
      setPct(100);
      document.getElementById('status').textContent='Done! Loading dashboard...';
      window.location.href='/range/'+start+'/'+end;
    }}else{{
      document.getElementById('status').textContent='Processing '+(total-remaining)+' of '+total+' days';
      setTimeout(poll,1500);
    }}
  }})
  .catch(function(){{
    document.getElementById('status').textContent='Retrying...';
    setTimeout(poll,3000);
  }});
}}
poll();

/* ── Tetris ── */
(function(){{
  var cv=document.getElementById('game'),ctx=cv.getContext('2d');
  var COLS=10,ROWS=18,SZ=20;
  var board=[];
  for(var r=0;r<ROWS;r++){{board[r]=[];for(var c=0;c<COLS;c++)board[r][c]=0;}}
  var score=0,lines=0,best=0,gameOver=false;

  var SHAPES=[
    [[1,1,1,1]],
    [[1,1],[1,1]],
    [[0,1,0],[1,1,1]],
    [[1,0,0],[1,1,1]],
    [[0,0,1],[1,1,1]],
    [[1,1,0],[0,1,1]],
    [[0,1,1],[1,1,0]]
  ];
  var COLORS=['#4a7c1f','#8cc63f','#475417','#f5a623','#3498db','#e8512f','#9b59b6'];

  var cur,cx,cy,ci,drop=0,speed=30;

  function newPiece(){{
    ci=Math.floor(Math.random()*SHAPES.length);
    cur=SHAPES[ci].map(function(r){{return r.slice();}});
    cx=Math.floor((COLS-cur[0].length)/2);
    cy=0;
    if(collides(cx,cy,cur)){{gameOver=true;}}
  }}

  function collides(px,py,p){{
    for(var r=0;r<p.length;r++)
      for(var c=0;c<p[r].length;c++)
        if(p[r][c]){{
          var nx=px+c,ny=py+r;
          if(nx<0||nx>=COLS||ny>=ROWS)return true;
          if(ny>=0&&board[ny][nx])return true;
        }}
    return false;
  }}

  function place(){{
    for(var r=0;r<cur.length;r++)
      for(var c=0;c<cur[r].length;c++)
        if(cur[r][c]){{
          var ny=cy+r;
          if(ny>=0&&ny<ROWS)board[ny][cx+c]=ci+1;
        }}
    // Clear lines
    var cleared=0;
    for(var r=ROWS-1;r>=0;r--){{
      if(board[r].every(function(v){{return v>0;}})){{
        board.splice(r,1);
        board.unshift(new Array(COLS).fill(0));
        cleared++;r++;
      }}
    }}
    if(cleared){{
      lines+=cleared;
      score+=cleared*cleared*100;
      if(score>best)best=score;
      document.getElementById('ln').textContent=lines;
      speed=Math.max(8,30-Math.floor(lines/5)*2);
    }}
    document.getElementById('sc').textContent=score;
    document.getElementById('best').textContent=best;
  }}

  function rotate(){{
    var nw=cur.length,nh=cur[0].length;
    var rot=[];
    for(var c=0;c<nh;c++){{rot[c]=[];for(var r=nw-1;r>=0;r--)rot[c].push(cur[r][c]);}}
    if(!collides(cx,cy,rot))cur=rot;
  }}

  function move(dx){{if(!collides(cx+dx,cy,cur))cx+=dx;}}
  function hardDrop(){{while(!collides(cx,cy+1,cur))cy++;place();newPiece();}}

  document.addEventListener('keydown',function(e){{
    if(gameOver)return;
    if(e.key==='ArrowLeft'){{move(-1);e.preventDefault();}}
    else if(e.key==='ArrowRight'){{move(1);e.preventDefault();}}
    else if(e.key==='ArrowUp'){{rotate();e.preventDefault();}}
    else if(e.key==='ArrowDown'){{hardDrop();e.preventDefault();}}
  }});

  // Mobile touch
  var tx=0,ty=0;
  cv.addEventListener('touchstart',function(e){{
    tx=e.touches[0].clientX;ty=e.touches[0].clientY;
  }});
  cv.addEventListener('touchend',function(e){{
    if(gameOver){{resetGame();return;}}
    var ex=e.changedTouches[0].clientX,ey=e.changedTouches[0].clientY;
    var dx=ex-tx,dy=ey-ty;
    if(Math.abs(dy)>30&&dy>0){{hardDrop();}}
    else if(Math.abs(dx)<15&&Math.abs(dy)<15){{
      var rect=cv.getBoundingClientRect();
      if(ty-rect.top<rect.height*0.3)rotate();
      else if(tx-rect.left<rect.width/2)move(-1);
      else move(1);
    }}
    else if(Math.abs(dx)>20){{
      if(dx<0)move(-1);else move(1);
    }}
  }});

  function resetGame(){{
    for(var r=0;r<ROWS;r++)for(var c=0;c<COLS;c++)board[r][c]=0;
    score=0;lines=0;gameOver=false;speed=30;
    document.getElementById('sc').textContent=0;
    document.getElementById('ln').textContent=0;
    newPiece();
  }}

  function draw(){{
    ctx.clearRect(0,0,cv.width,cv.height);

    // Board
    for(var r=0;r<ROWS;r++)
      for(var c=0;c<COLS;c++){{
        if(board[r][c]){{
          ctx.fillStyle=COLORS[board[r][c]-1];
          ctx.fillRect(c*SZ,r*SZ,SZ-1,SZ-1);
        }}
      }}

    // Current piece
    if(!gameOver&&cur){{
      ctx.fillStyle=COLORS[ci];
      for(var r=0;r<cur.length;r++)
        for(var c=0;c<cur[r].length;c++)
          if(cur[r][c])ctx.fillRect((cx+c)*SZ,(cy+r)*SZ,SZ-1,SZ-1);
    }}

    if(gameOver){{
      ctx.fillStyle='rgba(250,246,238,0.85)';
      ctx.fillRect(0,0,cv.width,cv.height);
      ctx.fillStyle='#475417';
      ctx.font='bold 16px DM Sans,sans-serif';
      ctx.textAlign='center';
      ctx.fillText('Game Over',cv.width/2,cv.height/2-10);
      ctx.font='12px DM Sans,sans-serif';
      ctx.fillStyle='#7a7a6f';
      ctx.fillText('Tap or press any key',cv.width/2,cv.height/2+12);
    }}

    // Gravity
    if(!gameOver){{
      drop++;
      if(drop>=speed){{
        drop=0;
        if(!collides(cx,cy+1,cur)){{cy++;}}
        else{{place();newPiece();}}
      }}
    }}

    requestAnimationFrame(draw);
  }}

  cv.addEventListener('click',function(){{
    if(gameOver)resetGame();
  }});
  document.addEventListener('keydown',function(e){{
    if(gameOver)resetGame();
  }});

  newPiece();
  draw();
}})();

/* ── Game Tab Switching ── */
function switchGame(id){{
  document.querySelectorAll('.game-panel').forEach(function(p){{p.style.display='none';}});
  document.querySelectorAll('.game-tab').forEach(function(t){{t.classList.remove('active');}});
  document.getElementById('game-'+id).style.display='block';
  event.target.classList.add('active');
}}

/* ── Flappy Smoothie ── */
(function(){{
  var cv=document.getElementById('flappyCanvas'),ctx=cv.getContext('2d');
  var W=cv.width,H=cv.height;
  var bird={{x:65,y:H/2,vy:0,r:12}};
  var GRAV=0.4,JUMP=-6.5;
  var pipes=[],pipeW=38,gap=105,pipeSpd=2.2;
  var frame=0,pipeEvery=90;
  var score=0,best=0,gameOver=false,started=false;

  function reset(){{
    bird.y=H/2;bird.vy=0;pipes=[];score=0;frame=0;
    gameOver=false;started=false;
    document.getElementById('flappyScore').textContent='0';
  }}

  function flap(){{
    if(gameOver){{reset();return;}}
    if(!started)started=true;
    bird.vy=JUMP;
  }}

  function addPipe(){{
    var minT=50,maxT=H-gap-70;
    var topH=minT+Math.random()*(maxT-minT);
    pipes.push({{x:W,topH:topH,scored:false}});
  }}

  function drawPipeCap(x,y,w,h){{
    ctx.fillStyle='#3a6415';
    ctx.fillRect(x-3,y,w+6,h);
  }}

  function tick(){{
    ctx.clearRect(0,0,W,H);
    ctx.fillStyle='#faf6ee';ctx.fillRect(0,0,W,H);

    // Ground
    ctx.fillStyle='#e2d9c8';ctx.fillRect(0,H-22,W,22);
    ctx.fillStyle='#d5ccb8';ctx.fillRect(0,H-22,W,2);

    // Update
    if(started&&!gameOver){{
      bird.vy+=GRAV;bird.y+=bird.vy;
      if(bird.y+bird.r>H-22){{bird.y=H-22-bird.r;gameOver=true;}}
      if(bird.y-bird.r<0){{bird.y=bird.r;bird.vy=0;}}
      frame++;
      if(frame%pipeEvery===0)addPipe();
      for(var i=pipes.length-1;i>=0;i--){{
        var p=pipes[i];p.x-=pipeSpd;
        if(!p.scored&&p.x+pipeW<bird.x){{
          p.scored=true;score++;
          if(score>best)best=score;
          document.getElementById('flappyScore').textContent=score;
          document.getElementById('flappyBest').textContent=best;
        }}
        if(bird.x+bird.r>p.x&&bird.x-bird.r<p.x+pipeW){{
          if(bird.y-bird.r<p.topH||bird.y+bird.r>p.topH+gap)gameOver=true;
        }}
        if(p.x+pipeW<-10)pipes.splice(i,1);
      }}
    }}

    // Draw pipes
    for(var i=0;i<pipes.length;i++){{
      var p=pipes[i];
      ctx.fillStyle='#4a7c1f';
      ctx.fillRect(p.x,0,pipeW,p.topH);
      drawPipeCap(p.x,p.topH-14,pipeW,14);
      var botY=p.topH+gap;
      ctx.fillStyle='#4a7c1f';
      ctx.fillRect(p.x,botY,pipeW,H-22-botY);
      drawPipeCap(p.x,botY,pipeW,14);
    }}

    // Bird - smoothie cup
    var tilt=Math.min(Math.max(bird.vy*3,-25),25);
    ctx.save();
    ctx.translate(bird.x,bird.y);
    ctx.rotate(tilt*Math.PI/180);
    // Cup body
    ctx.fillStyle='#f5a623';
    ctx.beginPath();
    ctx.moveTo(-10,12);ctx.lineTo(-8,-10);ctx.lineTo(8,-10);ctx.lineTo(10,12);
    ctx.closePath();ctx.fill();
    ctx.strokeStyle='#e8512f';ctx.lineWidth=1.5;ctx.stroke();
    // Liquid top
    ctx.fillStyle='#8cc63f';
    ctx.beginPath();
    ctx.moveTo(-8,-4);ctx.lineTo(8,-4);ctx.lineTo(9,4);ctx.lineTo(-9,4);
    ctx.closePath();ctx.fill();
    // Lid
    ctx.fillStyle='#e8512f';
    ctx.fillRect(-9,-12,18,4);
    // Straw
    ctx.strokeStyle='#e8512f';ctx.lineWidth=2;
    ctx.beginPath();ctx.moveTo(3,-12);ctx.lineTo(6,-20);ctx.stroke();
    ctx.restore();

    // Overlays
    if(!started&&!gameOver){{
      ctx.fillStyle='#475417';ctx.font='bold 15px DM Sans,sans-serif';ctx.textAlign='center';
      ctx.fillText('Tap to Start!',W/2,H/2-25);
      ctx.font='11px DM Sans,sans-serif';ctx.fillStyle='#7a7a6f';
      ctx.fillText('Space or tap to flap',W/2,H/2-8);
    }}
    if(gameOver){{
      ctx.fillStyle='rgba(250,246,238,0.85)';ctx.fillRect(0,0,W,H);
      ctx.fillStyle='#475417';ctx.font='bold 16px DM Sans,sans-serif';ctx.textAlign='center';
      ctx.fillText('Game Over',W/2,H/2-16);
      ctx.font='13px DM Sans,sans-serif';ctx.fillText('Score: '+score,W/2,H/2+4);
      ctx.font='11px DM Sans,sans-serif';ctx.fillStyle='#7a7a6f';
      ctx.fillText('Tap to restart',W/2,H/2+22);
    }}

    requestAnimationFrame(tick);
  }}

  cv.addEventListener('click',flap);
  cv.addEventListener('touchstart',function(e){{e.preventDefault();flap();}});
  document.addEventListener('keydown',function(e){{
    if(document.getElementById('game-flappy').style.display==='none')return;
    if(e.key===' '||e.key==='ArrowUp'){{e.preventDefault();flap();}}
  }});
  tick();
}})();

/* ── Salad Launch ── */
(function(){{
  var cv=document.getElementById('launchCanvas'),ctx=cv.getContext('2d');
  var W=cv.width,H=cv.height;
  var GY=H-40; // ground y
  var slingX=75,slingY=GY;
  // State
  var score=0,best=parseInt(localStorage.getItem('launchBest')||'0',10);
  var state='aiming'; // aiming | flying | settle | levelclear | gameover
  var settleTick=0;
  var levelNum=0;
  // Birds: type 'r'=red, 'y'=yellow, 'b'=blue
  var birdQueue=[],birdIndex=0;
  var proj=null,splitBirds=[]; // proj is active bird
  var abilityUsed=false;
  var dragging=false,dragX=0,dragY=0;
  // Blocks & pigs
  var blocks=[],pigs=[];
  var debris=[];
  var scorePopups=[];
  // Clouds
  var clouds=[{{x:100,y:35,r:20}},{{x:180,y:22,r:15}},{{x:350,y:40,r:18}},{{x:430,y:20,r:13}}];

  // ── Level definitions ───────────────────────────────────────────────
  var LEVELS=[
    // Level 0: simple stack with 2 pigs
    {{
      birds:['r','r','y'],
      setup:function(){{
        blocks=[
          {{x:290,y:GY-24,w:24,h:24,type:'w',hp:2,maxHp:2,vx:0,vy:0,rot:0,alive:true}},
          {{x:290,y:GY-48,w:24,h:24,type:'w',hp:2,maxHp:2,vx:0,vy:0,rot:0,alive:true}},
          {{x:330,y:GY-24,w:24,h:24,type:'g',hp:1,maxHp:1,vx:0,vy:0,rot:0,alive:true}},
          {{x:366,y:GY-24,w:24,h:24,type:'w',hp:2,maxHp:2,vx:0,vy:0,rot:0,alive:true}},
          {{x:366,y:GY-48,w:24,h:24,type:'g',hp:1,maxHp:1,vx:0,vy:0,rot:0,alive:true}},
        ];
        pigs=[
          {{x:304,y:GY-72,r:13,hp:2,maxHp:2,alive:true,vx:0,vy:0,rot:0}},
          {{x:380,y:GY-72,r:13,hp:2,maxHp:2,alive:true,vx:0,vy:0,rot:0}},
        ];
      }}
    }},
    // Level 1: taller tower with stone base
    {{
      birds:['r','y','b'],
      setup:function(){{
        blocks=[
          {{x:300,y:GY-28,w:28,h:28,type:'s',hp:4,maxHp:4,vx:0,vy:0,rot:0,alive:true}},
          {{x:300,y:GY-56,w:28,h:28,type:'w',hp:2,maxHp:2,vx:0,vy:0,rot:0,alive:true}},
          {{x:340,y:GY-28,w:28,h:28,type:'s',hp:4,maxHp:4,vx:0,vy:0,rot:0,alive:true}},
          {{x:340,y:GY-56,w:28,h:28,type:'g',hp:1,maxHp:1,vx:0,vy:0,rot:0,alive:true}},
          {{x:380,y:GY-28,w:28,h:28,type:'w',hp:2,maxHp:2,vx:0,vy:0,rot:0,alive:true}},
          {{x:380,y:GY-56,w:28,h:28,type:'w',hp:2,maxHp:2,vx:0,vy:0,rot:0,alive:true}},
          {{x:380,y:GY-84,w:28,h:28,type:'g',hp:1,maxHp:1,vx:0,vy:0,rot:0,alive:true}},
          {{x:320,y:GY-84,w:28,h:28,type:'s',hp:4,maxHp:4,vx:0,vy:0,rot:0,alive:true}},
        ];
        pigs=[
          {{x:314,y:GY-112,r:14,hp:2,maxHp:2,alive:true,vx:0,vy:0,rot:0}},
          {{x:366,y:GY-42,r:14,hp:2,maxHp:2,alive:true,vx:0,vy:0,rot:0}},
          {{x:394,y:GY-112,r:14,hp:2,maxHp:2,alive:true,vx:0,vy:0,rot:0}},
        ];
      }}
    }},
    // Level 2: spread-out targets for blue bird
    {{
      birds:['b','r','y','r'],
      setup:function(){{
        blocks=[
          {{x:270,y:GY-24,w:22,h:22,type:'g',hp:1,maxHp:1,vx:0,vy:0,rot:0,alive:true}},
          {{x:310,y:GY-24,w:22,h:22,type:'w',hp:2,maxHp:2,vx:0,vy:0,rot:0,alive:true}},
          {{x:350,y:GY-24,w:22,h:22,type:'g',hp:1,maxHp:1,vx:0,vy:0,rot:0,alive:true}},
          {{x:390,y:GY-24,w:22,h:22,type:'w',hp:2,maxHp:2,vx:0,vy:0,rot:0,alive:true}},
          {{x:430,y:GY-24,w:22,h:22,type:'s',hp:4,maxHp:4,vx:0,vy:0,rot:0,alive:true}},
          {{x:430,y:GY-46,w:22,h:22,type:'s',hp:4,maxHp:4,vx:0,vy:0,rot:0,alive:true}},
        ];
        pigs=[
          {{x:283,y:GY-50,r:13,hp:2,maxHp:2,alive:true,vx:0,vy:0,rot:0}},
          {{x:361,y:GY-50,r:13,hp:2,maxHp:2,alive:true,vx:0,vy:0,rot:0}},
          {{x:443,y:GY-70,r:13,hp:2,maxHp:2,alive:true,vx:0,vy:0,rot:0}},
        ];
      }}
    }}
  ];

  // ── Setup level ──────────────────────────────────────────────────────
  function loadLevel(){{
    var lvl=LEVELS[levelNum%LEVELS.length];
    birdQueue=lvl.birds.slice();
    birdIndex=0;
    lvl.setup();
    debris=[];splitBirds=[];proj=null;abilityUsed=false;
    dragging=false;state='aiming';
    document.getElementById('launchShots').textContent=birdQueue.length-birdIndex;
  }}

  function resetGame(){{
    score=0;levelNum=0;
    document.getElementById('launchScore').textContent='0';
    loadLevel();
  }}

  // ── Physics helpers ──────────────────────────────────────────────────
  function applyForce(cx,cy,force,radius){{
    blocks.forEach(function(b){{
      if(!b.alive)return;
      var bx=b.x+b.w/2,by=b.y+b.h/2;
      var dx=bx-cx,dy=by-cy,dist=Math.sqrt(dx*dx+dy*dy);
      if(dist<radius&&dist>0){{
        var f=force*(1-dist/radius);
        b.vx+=dx/dist*f; b.vy+=dy/dist*f;
        b.hp-=f*1.2;
        if(b.hp<=0){{destroyBlock(b);}}
      }}
    }});
    pigs.forEach(function(p){{
      if(!p.alive)return;
      var dx=p.x-cx,dy=p.y-cy,dist=Math.sqrt(dx*dx+dy*dy);
      if(dist<radius&&dist>0){{
        var f=force*(1-dist/radius);
        p.vx+=dx/dist*f; p.vy+=dy/dist*f;
        p.hp-=f*0.8;
        if(p.hp<=0){{destroyPig(p);}}
      }}
    }});
  }}

  function destroyBlock(b){{
    if(!b.alive)return;
    b.alive=false;
    var pts=b.type==='g'?500:b.type==='s'?200:300;
    score+=pts;
    scorePopups.push({{x:b.x+b.w/2,y:b.y,text:'+'+pts,life:45}});
    for(var i=0;i<5;i++){{
      debris.push({{x:b.x+b.w/2,y:b.y+b.h/2,
        vx:(Math.random()-0.5)*5,vy:-Math.random()*4-1,
        r:3+Math.random()*2,
        color:b.type==='g'?'#a8d8ff':b.type==='s'?'#999':' #c8a87a',
        life:35}});
    }}
    updateScoreDisplay();
  }}

  function destroyPig(p){{
    if(!p.alive)return;
    p.alive=false;
    score+=5000;
    scorePopups.push({{x:p.x,y:p.y-p.r-8,text:'+5000',life:60}});
    for(var i=0;i<8;i++){{
      debris.push({{x:p.x,y:p.y,vx:(Math.random()-0.5)*6,vy:-Math.random()*5-1,
        r:3+Math.random()*3,color:'#5d9e2a',life:40}});
    }}
    updateScoreDisplay();
  }}

  function updateScoreDisplay(){{
    if(score>best){{best=score;localStorage.setItem('launchBest',best);
      document.getElementById('launchBest').textContent=best;}}
    document.getElementById('launchScore').textContent=score;
  }}

  // ── Bird launch ──────────────────────────────────────────────────────
  function launchBird(dx,dy){{
    if(birdIndex>=birdQueue.length)return;
    var type=birdQueue[birdIndex];
    var pow=Math.min(Math.sqrt(dx*dx+dy*dy),85);
    var ang=Math.atan2(dy,dx);
    var spd=pow*0.155;
    proj={{type:type,x:slingX,y:slingY-32,vx:-Math.cos(ang)*spd,vy:-Math.sin(ang)*spd,
           r:type==='b'?9:10,active:true,trail:[]}};
    splitBirds=[];abilityUsed=false;
    birdIndex++;
    document.getElementById('launchShots').textContent=Math.max(0,birdQueue.length-birdIndex);
    state='flying';
  }}

  function useAbility(){{
    if(!proj||!proj.active||abilityUsed)return;
    abilityUsed=true;
    if(proj.type==='y'){{
      // Yellow: speed boost
      proj.vx*=2.0; proj.vy*=1.6;
    }} else if(proj.type==='b'){{
      // Blue: split into 3
      var offsets=[[-4,-3],[0,0],[4,3]];
      offsets.forEach(function(o){{
        splitBirds.push({{type:'b',x:proj.x,y:proj.y,
          vx:proj.vx+o[0]*0.4,vy:proj.vy+o[1]*0.5,r:7,active:true,trail:[]}});
      }});
      proj.active=false;
    }}
  }}

  // ── Input ─────────────────────────────────────────────────────────────
  function getPos(e){{
    var rect=cv.getBoundingClientRect();
    var cx,cy;
    if(e.touches){{cx=e.touches[0].clientX-rect.left;cy=e.touches[0].clientY-rect.top;}}
    else{{cx=e.clientX-rect.left;cy=e.clientY-rect.top;}}
    return{{x:cx*(W/rect.width),y:cy*(H/rect.height)}};
  }}

  function startDrag(e){{
    if(state==='levelclear'||state==='gameover'){{resetGame();return;}}
    if(state!=='aiming'){{useAbility();return;}}
    if(birdIndex>=birdQueue.length)return;
    var pos=getPos(e);
    var dx=pos.x-slingX,dy=pos.y-(slingY-32);
    if(Math.sqrt(dx*dx+dy*dy)<55){{dragging=true;dragX=pos.x;dragY=pos.y;}}
  }}
  function moveDrag(e){{
    if(!dragging)return;e.preventDefault();
    var pos=getPos(e);
    var dx=pos.x-slingX,dy=pos.y-slingY;
    var dist=Math.sqrt(dx*dx+dy*dy);
    if(dist>85){{dragX=slingX+dx/dist*85;dragY=slingY+dy/dist*85;}}
    else{{dragX=pos.x;dragY=pos.y;}}
  }}
  function endDrag(){{
    if(!dragging)return;dragging=false;
    var dx=slingX-dragX,dy=(slingY-32)-dragY;
    if(Math.sqrt(dx*dx+dy*dy)>12)launchBird(slingX-dragX,(slingY-32)-dragY);
  }}

  cv.addEventListener('mousedown',startDrag);
  cv.addEventListener('mousemove',moveDrag);
  cv.addEventListener('mouseup',endDrag);
  cv.addEventListener('touchstart',function(e){{e.preventDefault();startDrag(e);}},{{passive:false}});
  cv.addEventListener('touchmove',function(e){{moveDrag(e);}},{{passive:false}});
  cv.addEventListener('touchend',function(e){{endDrag(e);}});

  // ── Draw helpers ─────────────────────────────────────────────────────
  function drawBird(type,x,y,r,alpha){{
    ctx.save();ctx.globalAlpha=alpha||1;
    if(type==='r'){{
      // Red bird body
      ctx.fillStyle='#d93a2b';ctx.beginPath();ctx.arc(x,y,r,0,Math.PI*2);ctx.fill();
      // Beak
      ctx.fillStyle='#f5a623';ctx.beginPath();ctx.moveTo(x+r*0.6,y);
      ctx.lineTo(x+r*1.3,y-r*0.25);ctx.lineTo(x+r*1.3,y+r*0.25);ctx.closePath();ctx.fill();
      // Angry eyebrow
      ctx.strokeStyle='#222';ctx.lineWidth=1.5;
      ctx.beginPath();ctx.moveTo(x-r*0.5,y-r*0.55);ctx.lineTo(x+r*0.1,y-r*0.35);ctx.stroke();
      // Eye
      ctx.fillStyle='#fff';ctx.beginPath();ctx.arc(x-r*0.1,y-r*0.2,r*0.28,0,Math.PI*2);ctx.fill();
      ctx.fillStyle='#222';ctx.beginPath();ctx.arc(x-r*0.05,y-r*0.22,r*0.13,0,Math.PI*2);ctx.fill();
      // Crest
      ctx.fillStyle='#c0392b';
      for(var i=0;i<3;i++){{
        ctx.beginPath();ctx.moveTo(x-r*0.3+i*r*0.3,y-r);
        ctx.lineTo(x-r*0.1+i*r*0.3,y-r*1.5);
        ctx.lineTo(x+r*0.1+i*r*0.3,y-r);ctx.closePath();ctx.fill();
      }}
    }} else if(type==='y'){{
      // Yellow triangle bird (Chuck)
      ctx.fillStyle='#f5d020';
      ctx.beginPath();ctx.moveTo(x+r*1.3,y);ctx.lineTo(x-r*0.8,y-r);ctx.lineTo(x-r*0.8,y+r);
      ctx.closePath();ctx.fill();
      ctx.strokeStyle='#d4a017';ctx.lineWidth=1.5;ctx.stroke();
      // Eye
      ctx.fillStyle='#fff';ctx.beginPath();ctx.arc(x+r*0.2,y-r*0.25,r*0.28,0,Math.PI*2);ctx.fill();
      ctx.fillStyle='#222';ctx.beginPath();ctx.arc(x+r*0.28,y-r*0.27,r*0.13,0,Math.PI*2);ctx.fill();
      // Beak
      ctx.fillStyle='#e67e22';ctx.beginPath();ctx.moveTo(x+r*1.3,y);
      ctx.lineTo(x+r*1.6,y-r*0.15);ctx.lineTo(x+r*1.6,y+r*0.15);ctx.closePath();ctx.fill();
    }} else if(type==='b'){{
      // Blue bird body
      ctx.fillStyle='#3498db';ctx.beginPath();ctx.arc(x,y,r,0,Math.PI*2);ctx.fill();
      ctx.strokeStyle='#1a6fa8';ctx.lineWidth=1.5;ctx.stroke();
      // Eye
      ctx.fillStyle='#fff';ctx.beginPath();ctx.arc(x+r*0.2,y-r*0.2,r*0.28,0,Math.PI*2);ctx.fill();
      ctx.fillStyle='#222';ctx.beginPath();ctx.arc(x+r*0.28,y-r*0.22,r*0.13,0,Math.PI*2);ctx.fill();
      // Beak
      ctx.fillStyle='#f5a623';ctx.beginPath();ctx.moveTo(x+r*0.7,y+r*0.1);
      ctx.lineTo(x+r*1.3,y-r*0.1);ctx.lineTo(x+r*1.3,y+r*0.3);ctx.closePath();ctx.fill();
    }}
    ctx.restore();
  }}

  function drawPig(p){{
    if(!p.alive)return;
    ctx.save();
    ctx.translate(p.x,p.y);
    if(p.rot)ctx.rotate(p.rot);
    // Body
    var dmg=p.hp/p.maxHp;
    ctx.fillStyle=dmg>0.5?'#7bc044':'#5a9e30';
    ctx.beginPath();ctx.arc(0,0,p.r,0,Math.PI*2);ctx.fill();
    // Snout
    ctx.fillStyle='#6aad2c';ctx.beginPath();ctx.ellipse(p.r*0.25,p.r*0.3,p.r*0.38,p.r*0.28,0,0,Math.PI*2);ctx.fill();
    ctx.fillStyle='#2a6e0c';
    ctx.beginPath();ctx.arc(-p.r*0.08,p.r*0.32,p.r*0.1,0,Math.PI*2);ctx.fill();
    ctx.beginPath();ctx.arc(p.r*0.35,p.r*0.32,p.r*0.1,0,Math.PI*2);ctx.fill();
    // Eyes
    ctx.fillStyle='#fff';
    ctx.beginPath();ctx.arc(-p.r*0.3,-p.r*0.25,p.r*0.28,0,Math.PI*2);ctx.fill();
    ctx.beginPath();ctx.arc(p.r*0.3,-p.r*0.25,p.r*0.28,0,Math.PI*2);ctx.fill();
    ctx.fillStyle='#222';
    ctx.beginPath();ctx.arc(-p.r*0.25,-p.r*0.28,p.r*0.13,0,Math.PI*2);ctx.fill();
    ctx.beginPath();ctx.arc(p.r*0.35,-p.r*0.28,p.r*0.13,0,Math.PI*2);ctx.fill();
    // Ears
    ctx.fillStyle='#6aad2c';
    ctx.beginPath();ctx.moveTo(-p.r*0.55,-p.r*0.7);ctx.lineTo(-p.r*0.85,-p.r*1.1);ctx.lineTo(-p.r*0.2,-p.r*0.8);ctx.closePath();ctx.fill();
    ctx.beginPath();ctx.moveTo(p.r*0.55,-p.r*0.7);ctx.lineTo(p.r*0.85,-p.r*1.1);ctx.lineTo(p.r*0.2,-p.r*0.8);ctx.closePath();ctx.fill();
    // Damage cracks
    if(dmg<0.6){{
      ctx.strokeStyle='rgba(0,0,0,0.35)';ctx.lineWidth=1.2;
      ctx.beginPath();ctx.moveTo(-p.r*0.3,p.r*0.1);ctx.lineTo(0,-p.r*0.2);ctx.lineTo(p.r*0.2,p.r*0.15);ctx.stroke();
    }}
    ctx.restore();
  }}

  function drawBlock(b){{
    if(!b.alive)return;
    ctx.save();
    ctx.translate(b.x+b.w/2,b.y+b.h/2);
    if(b.rot)ctx.rotate(b.rot);
    ctx.translate(-b.w/2,-b.h/2);
    var dmg=b.hp/b.maxHp;
    if(b.type==='w'){{
      ctx.fillStyle=dmg>0.5?'#c8a87a':'#a0824e';
      ctx.fillRect(0,0,b.w,b.h);
      ctx.strokeStyle='#8a6a3a';ctx.lineWidth=1;
      // Wood grain
      ctx.beginPath();ctx.moveTo(0,b.h*0.33);ctx.lineTo(b.w,b.h*0.33);ctx.stroke();
      ctx.beginPath();ctx.moveTo(0,b.h*0.66);ctx.lineTo(b.w,b.h*0.66);ctx.stroke();
    }} else if(b.type==='s'){{
      ctx.fillStyle=dmg>0.5?'#9a9a9a':'#717171';
      ctx.fillRect(0,0,b.w,b.h);
      ctx.strokeStyle='#555';ctx.lineWidth=1;
      // Stone pattern
      ctx.beginPath();ctx.moveTo(b.w/2,0);ctx.lineTo(b.w/2,b.h);ctx.stroke();
      ctx.beginPath();ctx.moveTo(0,b.h/2);ctx.lineTo(b.w,b.h/2);ctx.stroke();
    }} else if(b.type==='g'){{
      ctx.fillStyle=dmg>0.5?'rgba(160,210,255,0.55)':'rgba(120,180,240,0.6)';
      ctx.fillRect(0,0,b.w,b.h);
      ctx.strokeStyle='rgba(100,170,240,0.9)';ctx.lineWidth=1.5;ctx.strokeRect(0,0,b.w,b.h);
      // Shine
      ctx.fillStyle='rgba(255,255,255,0.35)';ctx.fillRect(2,2,b.w*0.35,b.h*0.35);
    }}
    if(dmg<0.6){{
      ctx.strokeStyle='rgba(0,0,0,0.3)';ctx.lineWidth=1;
      ctx.beginPath();ctx.moveTo(0,0);ctx.lineTo(b.w*0.5,b.h*0.4);ctx.stroke();
    }}
    ctx.restore();
  }}

  function drawSlingshot(){{
    // Post left
    ctx.fillStyle='#8B5e3c';ctx.beginPath();
    ctx.moveTo(slingX-13,slingY);ctx.lineTo(slingX-9,slingY-48);
    ctx.lineTo(slingX-5,slingY-48);ctx.lineTo(slingX-1,slingY);ctx.fill();
    // Post right
    ctx.beginPath();
    ctx.moveTo(slingX+1,slingY);ctx.lineTo(slingX+5,slingY-42);
    ctx.lineTo(slingX+9,slingY-42);ctx.lineTo(slingX+13,slingY);ctx.fill();
    // Fork top
    ctx.fillStyle='#a0703a';
    ctx.beginPath();ctx.arc(slingX-9,slingY-48,5,0,Math.PI*2);ctx.fill();
    ctx.beginPath();ctx.arc(slingX+7,slingY-42,5,0,Math.PI*2);ctx.fill();
  }}

  function drawScene(){{
    // Sky gradient
    var sky=ctx.createLinearGradient(0,0,0,GY);
    sky.addColorStop(0,'#87ceeb');sky.addColorStop(1,'#ddeeff');
    ctx.fillStyle=sky;ctx.fillRect(0,0,W,GY);

    // Clouds
    ctx.fillStyle='rgba(255,255,255,0.85)';
    clouds.forEach(function(c){{
      ctx.beginPath();ctx.arc(c.x,c.y,c.r,0,Math.PI*2);ctx.fill();
      ctx.beginPath();ctx.arc(c.x+c.r*0.7,c.y+c.r*0.2,c.r*0.75,0,Math.PI*2);ctx.fill();
      ctx.beginPath();ctx.arc(c.x-c.r*0.7,c.y+c.r*0.3,c.r*0.65,0,Math.PI*2);ctx.fill();
    }});

    // Ground
    ctx.fillStyle='#5a7c2e';ctx.fillRect(0,GY,W,H-GY);
    ctx.fillStyle='#4a6a24';ctx.fillRect(0,GY,W,4);
    // Dirt
    ctx.fillStyle='#c8a87a';ctx.fillRect(0,GY+4,W,H-GY-4);

    // Grass tufts
    ctx.fillStyle='#7cbf3a';
    for(var g=5;g<W;g+=22){{
      ctx.beginPath();ctx.moveTo(g,GY);ctx.lineTo(g+3,GY-7);ctx.lineTo(g+6,GY);ctx.fill();
    }}
  }}

  function drawTrajectory(){{
    if(!dragging)return;
    var tdx=slingX-dragX,tdy=(slingY-32)-dragY;
    var pow=Math.min(Math.sqrt(tdx*tdx+tdy*tdy),85);
    var ang=Math.atan2(tdy,tdx);
    var spd=pow*0.155;
    var tvx=-Math.cos(ang)*spd,tvy=-Math.sin(ang)*spd;
    var tx=slingX,ty=slingY-32;
    ctx.fillStyle='rgba(255,255,255,0.65)';
    for(var d=0;d<10;d++){{
      tx+=tvx*3;ty+=tvy*3;tvy+=0.15*3;
      if(ty>GY)break;
      ctx.beginPath();ctx.arc(tx,ty,2.5-d*0.18,0,Math.PI*2);ctx.fill();
    }}
  }}

  function drawBirdQueue(){{
    var startX=12,startY=GY+18;
    for(var i=birdIndex;i<birdQueue.length;i++){{
      var r=i===birdIndex?11:8;
      var ox=startX+(i-birdIndex)*22;
      drawBird(birdQueue[i],ox,startY,r,i===birdIndex?1:0.65);
    }}
  }}

  // ── Update physics ───────────────────────────────────────────────────
  function updateBird(b){{
    if(!b.active)return;
    b.trail.push({{x:b.x,y:b.y}});
    if(b.trail.length>10)b.trail.shift();
    b.vy+=0.15;b.x+=b.vx;b.y+=b.vy;

    // Ground collision
    if(b.y+b.r>GY){{b.active=false;return;}}
    // Off screen
    if(b.x>W+30||b.x<-30||b.y<-60){{b.active=false;return;}}

    // Block collision
    blocks.forEach(function(bl){{
      if(!bl.alive)return;
      var cx=Math.max(bl.x,Math.min(b.x,bl.x+bl.w));
      var cy=Math.max(bl.y,Math.min(b.y,bl.y+bl.h));
      var dx=b.x-cx,dy=b.y-cy;
      if(dx*dx+dy*dy<b.r*b.r){{
        var spd=Math.sqrt(b.vx*b.vx+b.vy*b.vy);
        applyForce(b.x,b.y,spd*0.6,45);
        b.active=false;
      }}
    }});

    // Pig collision
    pigs.forEach(function(p){{
      if(!p.alive)return;
      var dx=b.x-p.x,dy=b.y-p.y;
      if(dx*dx+dy*dy<(b.r+p.r)*(b.r+p.r)){{
        var spd=Math.sqrt(b.vx*b.vx+b.vy*b.vy);
        p.hp-=spd*0.8;
        if(p.hp<=0)destroyPig(p);
        applyForce(b.x,b.y,spd*0.5,50);
        b.active=false;
      }}
    }});
  }}

  function updatePhysics(){{
    // Blocks gravity
    blocks.forEach(function(b){{
      if(!b.alive)return;
      b.vy+=0.22;b.x+=b.vx;b.y+=b.vy;
      b.vx*=0.95;
      if(b.y+b.h>GY){{b.y=GY-b.h;b.vy*=-0.25;b.vx*=0.8;}}
    }});
    // Pig gravity
    pigs.forEach(function(p){{
      if(!p.alive)return;
      p.vy+=0.22;p.x+=p.vx;p.y+=p.vy;
      p.vx*=0.95;
      if(p.y+p.r>GY){{p.y=GY-p.r;p.vy*=-0.2;p.vx*=0.8;}}
    }});
    // Debris
    for(var i=debris.length-1;i>=0;i--){{
      var d=debris[i];d.x+=d.vx;d.y+=d.vy;d.vy+=0.2;d.life--;
      if(d.life<=0)debris.splice(i,1);
    }}
    // Score popups
    for(var i=scorePopups.length-1;i>=0;i--){{
      scorePopups[i].y-=0.8;scorePopups[i].life--;
      if(scorePopups[i].life<=0)scorePopups.splice(i,1);
    }}
  }}

  // ── Check state transitions ──────────────────────────────────────────
  function checkState(){{
    if(state!=='flying'&&state!=='settle')return;

    var mainDone=!proj||!proj.active;
    var splitDone=splitBirds.every(function(b){{return !b.active;}});
    if(!mainDone||!splitDone)return; // still in flight

    if(state==='flying'){{state='settle';settleTick=0;return;}}

    settleTick++;
    if(settleTick<90)return; // wait for physics to stabilize

    var pigsLeft=pigs.filter(function(p){{return p.alive;}}).length;
    if(pigsLeft===0){{
      // Level clear
      var bonus=(birdQueue.length-birdIndex)*2000;
      if(bonus>0){{score+=bonus;scorePopups.push({{x:W/2,y:H/2-20,text:'Bonus +'+bonus,life:80}});}}
      updateScoreDisplay();
      state='levelclear';
      return;
    }}
    // No birds left
    if(birdIndex>=birdQueue.length){{
      state='gameover';return;
    }}
    // More birds, ready to aim
    state='aiming';
  }}

  // ── Main draw loop ───────────────────────────────────────────────────
  function tick(){{
    ctx.clearRect(0,0,W,H);
    drawScene();
    drawSlingshot();

    // Rubber band
    var bx=dragging?dragX:(state==='aiming'?slingX:slingX);
    var by=dragging?dragY:(state==='aiming'?slingY-32:slingY-32);
    if(state==='aiming'&&birdIndex<birdQueue.length){{
      ctx.strokeStyle='#7a4010';ctx.lineWidth=2.5;
      ctx.beginPath();ctx.moveTo(slingX-9,slingY-48);ctx.lineTo(bx,by);ctx.stroke();
      ctx.beginPath();ctx.moveTo(slingX+7,slingY-42);ctx.lineTo(bx,by);ctx.stroke();
      drawTrajectory();
      if(!dragging)drawBird(birdQueue[birdIndex],slingX,slingY-32,10,1);
      else drawBird(birdQueue[birdIndex],dragX,dragY,10,1);
    }} else if(state==='aiming'){{
      // No birds left visual
    }} else {{
      // Band without ball
      ctx.strokeStyle='#7a4010';ctx.lineWidth=2;
      ctx.beginPath();ctx.moveTo(slingX-9,slingY-48);ctx.lineTo(slingX,slingY-40);ctx.stroke();
      ctx.beginPath();ctx.moveTo(slingX+7,slingY-42);ctx.lineTo(slingX,slingY-38);ctx.stroke();
    }}

    // Draw blocks and pigs
    blocks.forEach(function(b){{drawBlock(b);}});
    pigs.forEach(function(p){{drawPig(p);}});

    // Draw debris
    debris.forEach(function(d){{
      ctx.save();ctx.globalAlpha=d.life/40;
      ctx.fillStyle=d.color;ctx.beginPath();ctx.arc(d.x,d.y,d.r,0,Math.PI*2);ctx.fill();
      ctx.restore();
    }});

    // Draw projectile trail + bird
    if(proj&&proj.active){{
      proj.trail.forEach(function(pt,i){{
        ctx.save();ctx.globalAlpha=(i/proj.trail.length)*0.3;
        ctx.fillStyle='#fff';ctx.beginPath();ctx.arc(pt.x,pt.y,proj.r*0.5,0,Math.PI*2);ctx.fill();
        ctx.restore();
      }});
      drawBird(proj.type,proj.x,proj.y,proj.r,1);
    }}
    splitBirds.forEach(function(b){{
      if(b.active)drawBird(b.type,b.x,b.y,b.r,1);
    }});

    // Score popups
    scorePopups.forEach(function(sp){{
      ctx.save();ctx.globalAlpha=Math.min(1,sp.life/20);
      ctx.fillStyle='#f5d020';ctx.font='bold 12px DM Sans,sans-serif';ctx.textAlign='center';
      ctx.fillText(sp.text,sp.x,sp.y);ctx.restore();
    }});

    // Bird queue display
    drawBirdQueue();

    // Level indicator
    ctx.fillStyle='#2c4a0e';ctx.font='bold 11px DM Sans,sans-serif';ctx.textAlign='left';
    ctx.fillText('Lv '+(levelNum+1),W-42,14);

    // Physics update
    if(state==='flying'||state==='settle'){{
      if(proj)updateBird(proj);
      splitBirds.forEach(function(b){{updateBird(b);}});
      updatePhysics();
    }}
    checkState();

    // Overlays
    if(state==='levelclear'){{
      ctx.fillStyle='rgba(0,0,0,0.45)';ctx.fillRect(0,0,W,H);
      ctx.textAlign='center';
      ctx.fillStyle='#f5d020';ctx.font='bold 20px DM Sans,sans-serif';
      ctx.fillText('Level Clear!',W/2,H/2-18);
      ctx.fillStyle='#fff';ctx.font='13px DM Sans,sans-serif';
      ctx.fillText('Score: '+score,W/2,H/2+4);
      ctx.fillStyle='rgba(255,255,255,0.65)';ctx.font='11px DM Sans,sans-serif';
      ctx.fillText('Tap to continue',W/2,H/2+22);
    }} else if(state==='gameover'){{
      ctx.fillStyle='rgba(0,0,0,0.5)';ctx.fillRect(0,0,W,H);
      ctx.textAlign='center';
      ctx.fillStyle='#e74c3c';ctx.font='bold 20px DM Sans,sans-serif';
      ctx.fillText('Game Over',W/2,H/2-18);
      ctx.fillStyle='#fff';ctx.font='13px DM Sans,sans-serif';
      ctx.fillText('Score: '+score+' · Best: '+best,W/2,H/2+4);
      ctx.fillStyle='rgba(255,255,255,0.65)';ctx.font='11px DM Sans,sans-serif';
      ctx.fillText('Tap to restart',W/2,H/2+22);
    }}

    requestAnimationFrame(tick);
  }}

  // Continue-after-levelclear on tap
  cv.addEventListener('click',function(){{
    if(state==='levelclear'){{
      levelNum++;loadLevel();
    }} else if(state==='gameover'){{
      resetGame();
    }}
  }});

  document.getElementById('launchBest').textContent=best;
  resetGame();
  tick();
}})();

/* ── Roulette ── */
(function(){{
  var cv=document.getElementById('rouletteCanvas');
  if(!cv)return;
  var ctx=cv.getContext('2d');
  var W=cv.width,H=cv.height;

  // European wheel order (0 + 36 numbers)
  var WHEEL=[0,32,15,19,4,21,2,25,17,34,6,27,13,36,11,30,8,23,10,5,24,16,33,1,20,14,31,9,22,18,29,7,28,12,35,3,26];
  var N=WHEEL.length; // 37
  var RED_SET={{1:1,3:1,5:1,7:1,9:1,12:1,14:1,16:1,18:1,19:1,21:1,23:1,25:1,27:1,30:1,32:1,34:1,36:1}};
  function pocketColor(n){{
    if(n===0)return'#1a7a2a';
    return RED_SET[n]?'#c0392b':'#1a1a1a';
  }}

  // Wheel geometry
  var WCX=150,WCY=170;
  var WOUT=128,WNUM_IN=98,WSPOKE=58,WHUB=30;
  var TRACK_R=120; // ball orbit radius
  var POCKET_R=100; // radius where ball settles

  // Betting grid (right side)
  var GX=302,GY=8;
  var CW=17,CH=20;
  // 0 spans 3 rows on left of grid; numbers 1-36 in 3 rows × 12 cols
  function numCell(n){{
    if(n===0)return{{x:GX,y:GY,w:CW,h:CH*3,cx:GX+CW/2,cy:GY+CH*1.5}};
    var col=Math.ceil(n/3)-1; // 0..11
    var row=2-((n-1)%3);      // 0=bottom row(1,4..),2=top(3,6..)
    return{{x:GX+CW+col*CW,y:GY+row*CH,w:CW,h:CH,cx:GX+CW+col*CW+CW/2,cy:GY+row*CH+CH/2}};
  }}
  // Outside bets
  var OB=[
    {{k:'1st12',l:'1st 12',x:GX+CW,       y:GY+CH*3,w:CW*4,h:CH-1}},
    {{k:'2nd12',l:'2nd 12',x:GX+CW+CW*4,  y:GY+CH*3,w:CW*4,h:CH-1}},
    {{k:'3rd12',l:'3rd 12',x:GX+CW+CW*8,  y:GY+CH*3,w:CW*4,h:CH-1}},
    {{k:'low',  l:'1-18',  x:GX+CW,       y:GY+CH*4,w:CW*2,h:CH-1}},
    {{k:'even', l:'Even',  x:GX+CW+CW*2,  y:GY+CH*4,w:CW*2,h:CH-1}},
    {{k:'red',  l:'●',     x:GX+CW+CW*4,  y:GY+CH*4,w:CW*2,h:CH-1,fill:'#c0392b'}},
    {{k:'black',l:'●',     x:GX+CW+CW*6,  y:GY+CH*4,w:CW*2,h:CH-1,fill:'#222'}},
    {{k:'odd',  l:'Odd',   x:GX+CW+CW*8,  y:GY+CH*4,w:CW*2,h:CH-1}},
    {{k:'high', l:'19-36', x:GX+CW+CW*10, y:GY+CH*4,w:CW*2,h:CH-1}},
  ];

  // Chips
  var CHIPS=[25,50,100,250];
  var CHIP_COL=['#1a7a2a','#2255bb','#333','#7b2fb5'];
  var selChip=0; // index
  var CHIP_BTN_Y=H-68;

  // State
  var balance=parseInt(localStorage.getItem('roulBalance')||'1000',10);
  var bets={{}};  // key -> amount
  var totalBet=0;
  var state='betting'; // betting | spinning | result
  var wheelRot=0,wheelSpeed=0;
  var ballAngle=0,ballR=TRACK_R,ballSpeed=0;
  var winNumber=-1,winPocketIdx=-1;
  var spinFrame=0,SPIN_FRAMES=260;
  var bounceOff=0; // extra radial wobble during settle
  var resultMsg='',resultColor='';

  function updateUI(){{
    document.getElementById('roulBalance').textContent=balance;
    document.getElementById('roulBet').textContent=totalBet;
  }}

  function placeBet(key){{
    if(state!=='betting')return;
    var amt=CHIPS[selChip];
    if(amt>balance-totalBet)return; // not enough funds
    bets[key]=(bets[key]||0)+amt;
    totalBet+=amt;
    updateUI();
  }}

  function clearBets(){{
    if(state!=='betting')return;
    balance+=totalBet;
    bets={{}};totalBet=0;
    updateUI();
  }}

  function spin(){{
    if(state!=='betting'||totalBet===0)return;
    balance-=0; // already deducted from "available" display; actual deduction already happened via totalBet
    state='spinning';
    spinFrame=0;
    // Pick winning number
    winNumber=WHEEL[Math.floor(Math.random()*N)];
    winPocketIdx=WHEEL.indexOf(winNumber);
    // Ball starts counter to wheel
    wheelSpeed=0.028+Math.random()*0.015;
    ballSpeed=-(0.065+Math.random()*0.02);
    ballAngle=Math.random()*Math.PI*2;
    ballR=TRACK_R;
    bounceOff=0;
    resultMsg='';
  }}

  function finishSpin(){{
    // Settle ball into winning pocket
    var targetAngle=wheelRot+(winPocketIdx/N)*Math.PI*2+(Math.PI*2/(N*2));
    ballAngle=targetAngle%(Math.PI*2);
    ballR=POCKET_R+4;
    state='result';
    // Calculate winnings
    var payout=0;
    var isRed=RED_SET[winNumber];
    var isBlack=winNumber>0&&!isRed;
    Object.keys(bets).forEach(function(k){{
      var b=bets[k];
      if(k==='n'+winNumber){{payout+=b*36;}} // straight up 35:1 + return bet
      else if(k==='red'&&isRed){{payout+=b*2;}}
      else if(k==='black'&&isBlack){{payout+=b*2;}}
      else if(k==='even'&&winNumber>0&&winNumber%2===0){{payout+=b*2;}}
      else if(k==='odd'&&winNumber%2===1){{payout+=b*2;}}
      else if(k==='low'&&winNumber>=1&&winNumber<=18){{payout+=b*2;}}
      else if(k==='high'&&winNumber>=19&&winNumber<=36){{payout+=b*2;}}
      else if(k==='1st12'&&winNumber>=1&&winNumber<=12){{payout+=b*3;}}
      else if(k==='2nd12'&&winNumber>=13&&winNumber<=24){{payout+=b*3;}}
      else if(k==='3rd12'&&winNumber>=25&&winNumber<=36){{payout+=b*3;}}
    }});
    balance+=payout;
    if(payout>totalBet){{resultMsg='WIN +$'+(payout-totalBet);resultColor='#1a7a2a';}}
    else if(payout>0){{resultMsg='WIN +$'+(payout-totalBet);resultColor='#1a7a2a';}}
    else{{resultMsg='LOSE -$'+totalBet;resultColor='#c0392b';}}
    totalBet=0;bets={{}};
    localStorage.setItem('roulBalance',balance);
    if(balance<=0){{balance=1000;}}
    document.getElementById('roulResult').textContent=resultMsg;
    document.getElementById('roulResult').style.color=resultColor;
    updateUI();
  }}

  // ── Draw helpers ─────────────────────────────────────────────────────
  function drawWheel(){{
    var sliceAngle=Math.PI*2/N;
    for(var i=0;i<N;i++){{
      var a0=wheelRot+i*sliceAngle-Math.PI/2;
      var a1=a0+sliceAngle;
      var num=WHEEL[i];
      // Pocket fill
      ctx.fillStyle=pocketColor(num);
      ctx.beginPath();ctx.moveTo(WCX,WCY);
      ctx.arc(WCX,WCY,WOUT,a0,a1);ctx.closePath();ctx.fill();
      // Inner separator line
      ctx.strokeStyle='#f5f0e8';ctx.lineWidth=0.5;
      ctx.beginPath();ctx.moveTo(WCX,WCY);
      ctx.arc(WCX,WCY,WOUT,a0,a1);ctx.closePath();ctx.stroke();
      // Number background band (outer ring)
      ctx.fillStyle='rgba(0,0,0,0.25)';
      ctx.beginPath();ctx.arc(WCX,WCY,WOUT,a0,a1);
      ctx.arc(WCX,WCY,WNUM_IN,a1,a0,true);ctx.closePath();ctx.fill();
      // Number text
      var midA=a0+sliceAngle/2;
      var tr=WNUM_IN+(WOUT-WNUM_IN)*0.5;
      var tx=WCX+Math.cos(midA)*tr,ty=WCY+Math.sin(midA)*tr;
      ctx.save();ctx.translate(tx,ty);ctx.rotate(midA+Math.PI/2);
      ctx.fillStyle='#fff';ctx.font='bold '+(num>9?6:7)+'px DM Sans,sans-serif';
      ctx.textAlign='center';ctx.textBaseline='middle';
      ctx.fillText(num,0,0);ctx.restore();
    }}
    // Spoke dividers
    ctx.strokeStyle='#c8b87a';ctx.lineWidth=1.5;
    for(var i=0;i<N;i++){{
      var a=wheelRot+i*(Math.PI*2/N)-Math.PI/2;
      ctx.beginPath();ctx.moveTo(WCX+Math.cos(a)*WSPOKE,WCY+Math.sin(a)*WSPOKE);
      ctx.lineTo(WCX+Math.cos(a)*WNUM_IN,WCY+Math.sin(a)*WNUM_IN);ctx.stroke();
    }}
    // Center hub
    var hubGrad=ctx.createRadialGradient(WCX,WCY,2,WCX,WCY,WSPOKE);
    hubGrad.addColorStop(0,'#d4a830');hubGrad.addColorStop(1,'#8b6010');
    ctx.fillStyle=hubGrad;ctx.beginPath();ctx.arc(WCX,WCY,WSPOKE,0,Math.PI*2);ctx.fill();
    ctx.fillStyle='#c8a820';ctx.beginPath();ctx.arc(WCX,WCY,WHUB,0,Math.PI*2);ctx.fill();
    ctx.strokeStyle='#a08010';ctx.lineWidth=2;ctx.beginPath();ctx.arc(WCX,WCY,WHUB,0,Math.PI*2);ctx.stroke();
    // Outer rim ring
    ctx.strokeStyle='#8b6010';ctx.lineWidth=5;
    ctx.beginPath();ctx.arc(WCX,WCY,WOUT+2,0,Math.PI*2);ctx.stroke();
    ctx.strokeStyle='#c8a820';ctx.lineWidth=2;
    ctx.beginPath();ctx.arc(WCX,WCY,WOUT+5,0,Math.PI*2);ctx.stroke();
    // Ball track ring
    ctx.strokeStyle='rgba(200,180,120,0.4)';ctx.lineWidth=1;
    ctx.beginPath();ctx.arc(WCX,WCY,TRACK_R,0,Math.PI*2);ctx.stroke();
  }}

  function drawBall(){{
    var bx=WCX+Math.cos(ballAngle)*(ballR+bounceOff);
    var by=WCY+Math.sin(ballAngle)*(ballR+bounceOff);
    // Shadow
    ctx.fillStyle='rgba(0,0,0,0.25)';ctx.beginPath();
    ctx.arc(bx+1,by+2,5,0,Math.PI*2);ctx.fill();
    // Ball
    var bGrad=ctx.createRadialGradient(bx-2,by-2,1,bx,by,5);
    bGrad.addColorStop(0,'#fff');bGrad.addColorStop(1,'#d0d0d0');
    ctx.fillStyle=bGrad;ctx.beginPath();ctx.arc(bx,by,5,0,Math.PI*2);ctx.fill();
    ctx.strokeStyle='#aaa';ctx.lineWidth=0.5;ctx.stroke();
  }}

  function drawBettingGrid(){{
    // Background panel
    ctx.fillStyle='#1a5c1a';ctx.fillRect(GX-2,GY-2,CW*13+4,CH*5+2);
    // 0 cell
    var z=numCell(0);
    ctx.fillStyle='#1a7a2a';ctx.fillRect(z.x,z.y,z.w,z.h);
    ctx.strokeStyle='#fff';ctx.lineWidth=0.5;ctx.strokeRect(z.x,z.y,z.w,z.h);
    if(bets['n0']){{
      ctx.fillStyle='rgba(255,215,0,0.35)';ctx.fillRect(z.x,z.y,z.w,z.h);
    }}
    ctx.fillStyle='#fff';ctx.font='bold 7px DM Sans,sans-serif';ctx.textAlign='center';ctx.textBaseline='middle';
    ctx.fillText('0',z.cx,z.cy);
    // Numbers 1-36
    for(var n=1;n<=36;n++){{
      var c=numCell(n);
      ctx.fillStyle=RED_SET[n]?'#c0392b':'#1a1a1a';
      ctx.fillRect(c.x,c.y,c.w,c.h);
      ctx.strokeStyle='rgba(255,255,255,0.4)';ctx.lineWidth=0.5;ctx.strokeRect(c.x,c.y,c.w,c.h);
      if(bets['n'+n]){{
        ctx.fillStyle='rgba(255,215,0,0.35)';ctx.fillRect(c.x,c.y,c.w,c.h);
      }}
      ctx.fillStyle='#fff';ctx.font='bold '+(n>9?6:7)+'px DM Sans,sans-serif';
      ctx.textAlign='center';ctx.textBaseline='middle';
      ctx.fillText(n,c.cx,c.cy);
    }}
    // Outside bets
    OB.forEach(function(o){{
      ctx.fillStyle=o.fill||(bets[o.k]?'#2d7a2d':'#2a5a2a');
      ctx.fillRect(o.x,o.y,o.w,o.h);
      if(bets[o.k]){{ctx.fillStyle='rgba(255,215,0,0.35)';ctx.fillRect(o.x,o.y,o.w,o.h);}}
      ctx.strokeStyle='rgba(255,255,255,0.4)';ctx.lineWidth=0.5;ctx.strokeRect(o.x,o.y,o.w,o.h);
      ctx.fillStyle='#fff';ctx.font='bold 6px DM Sans,sans-serif';
      ctx.textAlign='center';ctx.textBaseline='middle';
      ctx.fillText(o.l,o.x+o.w/2,o.y+o.h/2);
    }});
    // Bet amount chips on cells
    Object.keys(bets).forEach(function(k){{
      var amt=bets[k];if(!amt)return;
      var cx,cy;
      if(k.startsWith('n')){{
        var cell=numCell(parseInt(k.slice(1)));
        cx=cell.cx;cy=cell.cy-6;
      }} else {{
        var ob=OB.filter(function(o){{return o.k===k;}})[0];
        if(!ob)return;
        cx=ob.x+ob.w/2;cy=ob.y+ob.h/2;
      }}
      ctx.fillStyle='#ffd700';ctx.beginPath();ctx.arc(cx,cy,5,0,Math.PI*2);ctx.fill();
      ctx.fillStyle='#333';ctx.font='bold 5px DM Sans,sans-serif';ctx.textAlign='center';ctx.textBaseline='middle';
      ctx.fillText(amt>=1000?'1k':amt,cx,cy);
    }});
  }}

  function drawChips(){{
    var startX=GX,btnW=48,btnH=22,gap=4;
    for(var i=0;i<CHIPS.length;i++){{
      var bx=startX+i*(btnW+gap),by=CHIP_BTN_Y;
      // Chip circle
      ctx.fillStyle=CHIP_COL[i];ctx.beginPath();ctx.arc(bx+btnW/2,by+btnH/2,11,0,Math.PI*2);ctx.fill();
      if(i===selChip){{ctx.strokeStyle='#ffd700';ctx.lineWidth=2;ctx.stroke();}}
      else{{ctx.strokeStyle='rgba(255,255,255,0.4)';ctx.lineWidth=1;ctx.stroke();}}
      ctx.fillStyle='#fff';ctx.font='bold 7px DM Sans,sans-serif';ctx.textAlign='center';ctx.textBaseline='middle';
      ctx.fillText('$'+CHIPS[i],bx+btnW/2,by+btnH/2);
    }}
    // Clear bets button
    var clrX=startX+4*(btnW+gap),clrY=CHIP_BTN_Y;
    ctx.fillStyle='#7a2020';ctx.beginPath();ctx.arc(clrX+24,clrY+11,11,0,Math.PI*2);ctx.fill();
    ctx.strokeStyle='rgba(255,255,255,0.4)';ctx.lineWidth=1;ctx.stroke();
    ctx.fillStyle='#fff';ctx.font='bold 6px DM Sans,sans-serif';ctx.textAlign='center';ctx.textBaseline='middle';
    ctx.fillText('Clear',clrX+24,clrY+11);
  }}

  function drawSpinButton(){{
    var bx=GX,by=H-40,bw=208,bh=28;
    var canSpin=state==='betting'&&totalBet>0;
    ctx.fillStyle=canSpin?'#c0392b':'#7a4040';
    ctx.beginPath();ctx.roundRect(bx,by,bw,bh,6);ctx.fill();
    ctx.fillStyle=canSpin?'#fff':'rgba(255,255,255,0.4)';
    ctx.font='bold 13px DM Sans,sans-serif';ctx.textAlign='center';ctx.textBaseline='middle';
    ctx.fillText(state==='result'?'New Spin':'SPIN',bx+bw/2,by+bh/2);
  }}

  function drawBackground(){{
    // Green felt table
    var felt=ctx.createRadialGradient(WCX,WCY,40,WCX,WCY,260);
    felt.addColorStop(0,'#1e6b1e');felt.addColorStop(1,'#144014');
    ctx.fillStyle=felt;ctx.fillRect(0,0,W,H);
  }}

  function drawResultBanner(){{
    if(state!=='result')return;
    ctx.fillStyle='rgba(0,0,0,0.6)';ctx.beginPath();ctx.roundRect(WCX-65,WCY+100,130,36,6);ctx.fill();
    ctx.font='bold 13px DM Sans,sans-serif';ctx.textAlign='center';ctx.textBaseline='middle';
    ctx.fillStyle=resultColor||'#fff';
    ctx.fillText(resultMsg,WCX,WCY+118);
    var num=winNumber;
    ctx.fillStyle=pocketColor(num);ctx.beginPath();ctx.arc(WCX,WCY+140,12,0,Math.PI*2);ctx.fill();
    ctx.strokeStyle='#fff';ctx.lineWidth=1;ctx.stroke();
    ctx.fillStyle='#fff';ctx.font='bold 9px DM Sans,sans-serif';ctx.textAlign='center';ctx.textBaseline='middle';
    ctx.fillText(num,WCX,WCY+140);
    ctx.fillStyle='rgba(255,255,255,0.6)';ctx.font='10px DM Sans,sans-serif';
    ctx.fillText('Tap to bet again',WCX,WCY+158);
  }}

  // ── Physics update ───────────────────────────────────────────────────
  function updateSpin(){{
    if(state!=='spinning')return;
    spinFrame++;

    // Wheel decelerates
    wheelRot+=wheelSpeed;
    wheelSpeed*=0.9985;

    // Ball counter-spins and decelerates faster
    ballAngle+=ballSpeed;
    ballSpeed*=0.9960;

    // After ~half time, ball starts to drop inward
    if(spinFrame>140){{
      var dropFrac=(spinFrame-140)/80;
      ballR=TRACK_R-(TRACK_R-POCKET_R)*Math.min(1,dropFrac);
      // Simulate bumping — small radial wobble
      bounceOff=Math.sin(spinFrame*0.55)*(6*(1-Math.min(1,dropFrac)));
    }}

    if(spinFrame>=SPIN_FRAMES){{
      finishSpin();
    }}
  }}

  // ── Main loop ────────────────────────────────────────────────────────
  function tick(){{
    ctx.clearRect(0,0,W,H);
    drawBackground();
    drawWheel();
    drawBall();
    drawBettingGrid();
    drawChips();
    drawSpinButton();
    if(state!=='spinning')drawResultBanner();
    updateSpin();
    requestAnimationFrame(tick);
  }}

  // ── Input ─────────────────────────────────────────────────────────────
  function getPos(e){{
    var rect=cv.getBoundingClientRect();
    var px,py;
    if(e.touches){{px=e.touches[0].clientX-rect.left;py=e.touches[0].clientY-rect.top;}}
    else{{px=e.clientX-rect.left;py=e.clientY-rect.top;}}
    return{{x:px*(W/rect.width),y:py*(H/rect.height)}};
  }}

  cv.addEventListener('click',function(e){{
    var pos=getPos(e);
    var x=pos.x,y=pos.y;

    // Result state: tap anywhere to reset
    if(state==='result'){{
      state='betting';resultMsg='';
      document.getElementById('roulResult').textContent='—';
      return;
    }}

    // Chip selector
    var btnW=48,gap=4;
    for(var i=0;i<CHIPS.length;i++){{
      var bx=GX+i*(btnW+gap)+btnW/2,by=CHIP_BTN_Y+11;
      if(Math.hypot(x-bx,y-by)<12){{selChip=i;return;}}
    }}
    // Clear button
    if(Math.hypot(x-(GX+4*(btnW+gap)+24),y-(CHIP_BTN_Y+11))<12){{clearBets();return;}}

    // Spin button
    var sbx=GX,sby=H-40,sbw=208,sbh=28;
    if(x>=sbx&&x<=sbx+sbw&&y>=sby&&y<=sby+sbh){{spin();return;}}

    // 0 cell
    var z=numCell(0);
    if(x>=z.x&&x<=z.x+z.w&&y>=z.y&&y<=z.y+z.h){{placeBet('n0');return;}}
    // Number cells
    for(var n=1;n<=36;n++){{
      var c=numCell(n);
      if(x>=c.x&&x<=c.x+c.w&&y>=c.y&&y<=c.y+c.h){{placeBet('n'+n);return;}}
    }}
    // Outside bets
    for(var i=0;i<OB.length;i++){{
      var o=OB[i];
      if(x>=o.x&&x<=o.x+o.w&&y>=o.y&&y<=o.y+o.h){{placeBet(o.k);return;}}
    }}
  }});

  updateUI();
  tick();
}})();

/* ── Blackjack ── */
(function(){{
  var cv=document.getElementById('blackjackCanvas');
  if(!cv)return;
  var ctx=cv.getContext('2d');
  var W=cv.width,H=cv.height;

  var SUITS=['♠','♥','♦','♣'];
  var RANKS=['A','2','3','4','5','6','7','8','9','10','J','Q','K'];
  function cardVal(rank){{
    if(rank==='A')return 11;
    if(['J','Q','K'].indexOf(rank)>=0)return 10;
    return parseInt(rank,10);
  }}
  function handValue(hand){{
    var total=0,aces=0;
    hand.forEach(function(c){{
      total+=c.val;
      if(c.rank==='A')aces++;
    }});
    while(total>21&&aces>0){{total-=10;aces--;}}
    return total;
  }}

  var CHIPS=[25,50,100,250];
  var CHIP_COL=['#1a7a2a','#2255bb','#333','#7b2fb5'];
  var selChip=0;

  var balance=parseInt(localStorage.getItem('bjBalance')||'1000',10);
  var currentBet=0;
  var state='betting'; // betting | playing | dealerTurn | done
  var deck=[],playerHand=[],dealerHand=[];
  var dealerHoleRevealed=false;
  var dblDown=false;

  function mkDeck(){{
    var d=[];
    SUITS.forEach(function(s){{RANKS.forEach(function(r){{d.push({{suit:s,rank:r,val:cardVal(r),faceUp:true}});}});}});
    // Shuffle (Fisher-Yates)
    for(var i=d.length-1;i>0;i--){{var j=Math.floor(Math.random()*(i+1));var tmp=d[i];d[i]=d[j];d[j]=tmp;}}
    return d;
  }}
  function deal(){{
    if(deck.length<15)deck=mkDeck();
    var c=deck.pop();return c;
  }}

  function startRound(){{
    if(currentBet===0)return;
    balance-=currentBet;
    deck=mkDeck();
    playerHand=[deal(),deal()];
    dealerHand=[deal(),{{...deal(),faceUp:false}}];
    dealerHoleRevealed=false;dblDown=false;
    state='playing';
    // Check blackjack
    if(handValue(playerHand)===21){{
      revealAndSettle();
    }}
    updateUI();
  }}

  function hit(){{
    if(state!=='playing')return;
    playerHand.push(deal());
    if(handValue(playerHand)>21){{revealAndSettle();}}
    updateUI();
  }}

  function stand(){{
    if(state!=='playing')return;
    state='dealerTurn';
    dealerTurn();
  }}

  function doubleDown(){{
    if(state!=='playing'||playerHand.length!==2)return;
    if(currentBet>balance)return;
    balance-=currentBet;currentBet*=2;dblDown=true;
    playerHand.push(deal());
    revealAndSettle();
    updateUI();
  }}

  function dealerTurn(){{
    dealerHand.forEach(function(c){{c.faceUp=true;}});
    dealerHoleRevealed=true;
    while(handValue(dealerHand)<17){{dealerHand.push(deal());}}
    revealAndSettle();
  }}

  function revealAndSettle(){{
    dealerHand.forEach(function(c){{c.faceUp=true;}});
    dealerHoleRevealed=true;
    var pv=handValue(playerHand),dv=handValue(dealerHand);
    var pBJ=pv===21&&playerHand.length===2,dBJ=dv===21&&dealerHand.length===2;
    var msg='',payout=0;
    if(pv>21){{msg='Bust — lose $'+currentBet;payout=0;}}
    else if(pBJ&&!dBJ){{msg='Blackjack! +$'+Math.floor(currentBet*1.5);payout=currentBet+Math.floor(currentBet*1.5);}}
    else if(dv>21){{msg='Dealer bust — win +$'+currentBet;payout=currentBet*2;}}
    else if(pv>dv){{msg='Win +$'+currentBet;payout=currentBet*2;}}
    else if(pv===dv){{msg='Push';payout=currentBet;}}
    else{{msg='Lose -$'+currentBet;payout=0;}}
    balance+=payout;
    if(balance<=0){{balance=1000;msg+=' (refilled to $1000)';}}
    localStorage.setItem('bjBalance',balance);
    document.getElementById('bjMsg').textContent=msg;
    state='done';
    updateUI();
  }}

  function newRound(){{
    if(state!=='done'&&state!=='betting')return;
    currentBet=0;
    playerHand=[];dealerHand=[];
    state='betting';
    document.getElementById('bjMsg').textContent='';
    updateUI();
  }}

  function updateUI(){{
    document.getElementById('bjBalance').textContent=balance;
    document.getElementById('bjBet').textContent=currentBet;
  }}

  // ── Draw helpers ─────────────────────────────────────────────────────
  function drawCard(x,y,card,faceUp){{
    var CW=38,CH=52;
    ctx.save();
    ctx.fillStyle='#fff';
    ctx.beginPath();ctx.roundRect(x,y,CW,CH,4);ctx.fill();
    if(faceUp===false||(!card.faceUp)){{
      // Card back
      ctx.fillStyle='#1a3a8a';ctx.beginPath();ctx.roundRect(x+2,y+2,CW-4,CH-4,3);ctx.fill();
      ctx.strokeStyle='#fff';ctx.lineWidth=0.5;
      for(var xi=0;xi<4;xi++)for(var yi=0;yi<6;yi++){{
        ctx.beginPath();ctx.arc(x+5+xi*8,y+5+yi*8,2,0,Math.PI*2);ctx.stroke();
      }}
    }} else {{
      var red=card.suit==='♥'||card.suit==='♦';
      ctx.fillStyle=red?'#c0392b':'#1a1a1a';
      ctx.font='bold 9px DM Sans,sans-serif';ctx.textAlign='left';ctx.textBaseline='top';
      ctx.fillText(card.rank,x+3,y+3);
      ctx.font='bold 8px DM Sans,sans-serif';
      ctx.fillText(card.suit,x+3,y+13);
      // Center suit
      ctx.font=CH>40?'22px serif':'18px serif';ctx.textAlign='center';ctx.textBaseline='middle';
      ctx.fillText(card.suit,x+CW/2,y+CH/2);
    }}
    ctx.strokeStyle='#ccc';ctx.lineWidth=1;
    ctx.beginPath();ctx.roundRect(x,y,CW,CH,4);ctx.stroke();
    ctx.restore();
  }}

  function drawHand(hand,startX,y,label,showSum){{
    hand.forEach(function(card,i){{
      drawCard(startX+i*44,y,card,card.faceUp);
    }});
    if(label){{
      ctx.fillStyle='rgba(255,255,255,0.7)';ctx.font='11px DM Sans,sans-serif';
      ctx.textAlign='left';ctx.textBaseline='top';
      ctx.fillText(label,startX,y-16);
    }}
    if(showSum&&hand.length>0){{
      var revealed=hand.filter(function(c){{return c.faceUp;}});
      if(revealed.length>0){{
        var sum=handValue(revealed);
        ctx.fillStyle='#fff';ctx.font='bold 12px DM Sans,sans-serif';
        ctx.textAlign='right';ctx.textBaseline='top';
        ctx.fillText(sum,startX+hand.length*44+4,y);
      }}
    }}
  }}

  function drawButtons(){{
    var by=H-48;
    if(state==='playing'){{
      var btns=[
        {{l:'Hit',x:20,fill:'#1a7a2a'}},
        {{l:'Stand',x:90,fill:'#2255bb'}},
        {{l:'Double',x:165,fill:'#7b2fb5'}},
      ];
      btns[2].fill='#7b2fb5';
      btns.forEach(function(b){{
        ctx.fillStyle=b.l==='Double'?'#7b2fb5':b.fill;
        ctx.beginPath();ctx.roundRect(b.x,by,64,30,5);ctx.fill();
        ctx.fillStyle='#fff';ctx.font='bold 11px DM Sans,sans-serif';
        ctx.textAlign='center';ctx.textBaseline='middle';
        ctx.fillText(b.l,b.x+32,by+15);
      }});
    }} else if(state==='betting'){{
      // Chip selector
      CHIPS.forEach(function(amt,i){{
        var bx=20+i*60,cy=by+15;
        ctx.fillStyle=CHIP_COL[i];
        ctx.beginPath();ctx.arc(bx+20,cy,16,0,Math.PI*2);ctx.fill();
        if(i===selChip){{ctx.strokeStyle='#ffd700';ctx.lineWidth=2.5;ctx.stroke();}}
        else{{ctx.strokeStyle='rgba(255,255,255,0.3)';ctx.lineWidth=1;ctx.stroke();}}
        ctx.fillStyle='#fff';ctx.font='bold 8px DM Sans,sans-serif';ctx.textAlign='center';ctx.textBaseline='middle';
        ctx.fillText('$'+amt,bx+20,cy);
      }});
      // Deal button
      var dealOk=currentBet>0;
      ctx.fillStyle=dealOk?'#c0392b':'#7a4040';
      ctx.beginPath();ctx.roundRect(W-100,by,80,30,5);ctx.fill();
      ctx.fillStyle=dealOk?'#fff':'rgba(255,255,255,0.4)';
      ctx.font='bold 12px DM Sans,sans-serif';ctx.textAlign='center';ctx.textBaseline='middle';
      ctx.fillText('Deal',W-60,by+15);
    }} else if(state==='done'){{
      ctx.fillStyle='#475417';ctx.beginPath();ctx.roundRect(W/2-50,by,100,30,5);ctx.fill();
      ctx.fillStyle='#fff';ctx.font='bold 12px DM Sans,sans-serif';
      ctx.textAlign='center';ctx.textBaseline='middle';
      ctx.fillText('New Hand',W/2,by+15);
    }}
  }}

  function drawScene(){{
    // Felt
    var felt=ctx.createLinearGradient(0,0,0,H);
    felt.addColorStop(0,'#1b5e20');felt.addColorStop(1,'#0d3d10');
    ctx.fillStyle=felt;ctx.fillRect(0,0,W,H);
    // Oval table outline
    ctx.strokeStyle='rgba(255,255,255,0.15)';ctx.lineWidth=3;
    ctx.beginPath();ctx.ellipse(W/2,H/2,W/2-10,H/2-10,0,0,Math.PI*2);ctx.stroke();
    // Dealer area
    ctx.fillStyle='rgba(255,255,255,0.06)';ctx.fillRect(0,0,W,100);
    drawHand(dealerHand,28,28,'Dealer',true);
    // Player area
    ctx.fillStyle='rgba(255,255,255,0.06)';ctx.fillRect(0,H-160,W,160);
    drawHand(playerHand,28,H-145,'You',true);
    // Bet display
    if(currentBet>0){{
      ctx.fillStyle='#ffd700';ctx.beginPath();ctx.arc(W/2,H/2,18,0,Math.PI*2);ctx.fill();
      ctx.strokeStyle='#a08000';ctx.lineWidth=2;ctx.stroke();
      ctx.fillStyle='#333';ctx.font='bold 9px DM Sans,sans-serif';ctx.textAlign='center';ctx.textBaseline='middle';
      ctx.fillText('$'+currentBet,W/2,H/2);
    }}
    drawButtons();
  }}

  function tick(){{
    ctx.clearRect(0,0,W,H);
    drawScene();
    requestAnimationFrame(tick);
  }}

  // ── Input ─────────────────────────────────────────────────────────────
  function getPos(e){{
    var rect=cv.getBoundingClientRect();
    var px,py;
    if(e.touches){{px=e.touches[0].clientX-rect.left;py=e.touches[0].clientY-rect.top;}}
    else{{px=e.clientX-rect.left;py=e.clientY-rect.top;}}
    return{{x:px*(W/rect.width),y:py*(H/rect.height)}};
  }}

  cv.addEventListener('click',function(e){{
    var pos=getPos(e);var x=pos.x,y=pos.y;
    var by=H-48;
    if(state==='betting'){{
      // Chip clicks
      for(var i=0;i<CHIPS.length;i++){{
        var bx=20+i*60+20;
        if(Math.hypot(x-bx,y-(by+15))<18){{
          if(selChip===i){{
            // Add chip to bet
            if(CHIPS[i]<=balance-currentBet)currentBet+=CHIPS[i];
          }} else {{
            selChip=i;
          }}
          updateUI();return;
        }}
      }}
      // Deal button
      if(x>=W-100&&x<=W-20&&y>=by&&y<=by+30){{startRound();return;}}
    }} else if(state==='playing'){{
      if(x>=20&&x<=84&&y>=by&&y<=by+30){{hit();return;}}
      if(x>=90&&x<=154&&y>=by&&y<=by+30){{stand();return;}}
      if(x>=165&&x<=229&&y>=by&&y<=by+30){{doubleDown();return;}}
    }} else if(state==='done'){{
      if(x>=W/2-50&&x<=W/2+50&&y>=by&&y<=by+30){{newRound();return;}}
    }}
  }});

  updateUI();tick();
}})();
</script>
</body></html>"""


@app.route('/api/precache')
@owner_required
def api_precache():
    """Cache a batch of days per request. Called repeatedly by the loading page."""
    start_str = request.args.get('start', '')
    end_str = request.args.get('end', '')
    if not start_str or not end_str:
        return jsonify({'error': 'Missing start/end'}), 400

    try:
        start = datetime.strptime(start_str, "%Y%m%d")
        end = datetime.strptime(end_str, "%Y%m%d")
    except ValueError:
        return jsonify({'error': 'Invalid dates'}), 400

    # Process up to 15 uncached days per request (CSV disk cache makes compute fast)
    cached_count = 0
    uncached_remaining = 0
    batch = 0
    current = start
    with batch_connection():
        while current <= end:
            ds = current.strftime("%Y%m%d")
            if not is_today(ds):
                if get_cached_metrics(ds) is not None:
                    cached_count += 1
                elif batch < 15:
                    # Fetch and cache this day
                    try:
                        data = get_daily_data(current, quiet=True)
                        if 'OrderDetails' in data:
                            metrics = compute_all_metrics(data, current, skip_weather=True)
                            if metrics:
                                cache_metrics(ds, metrics)
                                cached_count += 1
                                batch += 1
                    except Exception as e:
                        logger.warning("Precache failed for %s: %s", ds, e)
                        batch += 1  # skip failures, count toward batch limit
                else:
                    uncached_remaining += 1
            current += timedelta(days=1)

    # Phase 2: all days cached — pre-aggregate so the final page load is fast
    if uncached_remaining == 0:
        agg_key = f"agg_{start_str}_{end_str}"
        if get_cached_metrics(agg_key) is not None:
            return jsonify({
                'cached': cached_count, 'remaining': 0, 'done': True,
            })

        # Aggregation not cached yet — do it now
        return jsonify({
            'cached': cached_count, 'remaining': 0, 'done': False,
            'phase': 'aggregating',
        })

    return jsonify({
        'cached': cached_count,
        'remaining': uncached_remaining,
        'done': False,
    })


@app.route('/api/precache/aggregate')
@owner_required
def api_precache_aggregate():
    """Run aggregation as a separate request after all days are cached."""
    start_str = request.args.get('start', '')
    end_str = request.args.get('end', '')
    if not start_str or not end_str:
        return jsonify({'error': 'Missing start/end'}), 400

    try:
        start = datetime.strptime(start_str, "%Y%m%d")
        end = datetime.strptime(end_str, "%Y%m%d")
    except ValueError:
        return jsonify({'error': 'Invalid dates'}), 400

    agg_key = f"agg_{start_str}_{end_str}"

    # Already aggregated?
    if get_cached_metrics(agg_key) is not None:
        return jsonify({'done': True})

    # Load all daily metrics from cache and aggregate
    daily_metrics = []
    current = start
    with batch_connection():
        while current <= end:
            ds = current.strftime("%Y%m%d")
            m = get_cached_metrics(ds)
            if m:
                daily_metrics.append(m)
            current += timedelta(days=1)

    if not daily_metrics:
        return jsonify({
            'error': 'No cached data found',
            'reason': 'Caching may still be in progress — retry in 30s',
            'retry_after': 30,
        }), 202

    num_days = (end - start).days + 1
    agg = aggregate_metrics(daily_metrics, start_str, end_str, num_days)
    cache_metrics(agg_key, agg)

    return jsonify({'done': True, 'days': len(daily_metrics)})


def _landing_page() -> str:
    """The home page with date picker."""
    today = datetime.now()
    yesterday = today - timedelta(days=1)
    yesterday_str = yesterday.strftime("%Y-%m-%d")
    today_str = today.strftime("%Y-%m-%d")

    # Precomputed date ranges
    last_mon = today - timedelta(days=today.weekday() + 7)
    last_sun = last_mon + timedelta(days=6)
    this_mon = today - timedelta(days=today.weekday())
    first_of_month = today.replace(day=1)
    if today.month == 1:
        first_last_month = today.replace(year=today.year - 1, month=12, day=1)
    else:
        first_last_month = today.replace(month=today.month - 1, day=1)
    last_day_last_month = first_of_month - timedelta(days=1)
    first_of_year = today.replace(month=1, day=1)

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Livite Dashboard</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
*{{box-sizing:border-box;}}
body{{
    font-family:'DM Sans',sans-serif;margin:0;padding:20px;min-height:100vh;
    background:#F5EDDC;
    background-image:
        radial-gradient(ellipse at 20% 0%, rgba(71,84,23,0.05) 0%, transparent 50%),
        radial-gradient(ellipse at 80% 100%, rgba(74,124,31,0.03) 0%, transparent 50%);
}}
.container{{max-width:960px;margin:30px auto;}}
.logo{{text-align:center;margin-bottom:28px;animation:fade-down 0.5s ease;}}
@keyframes fade-down{{from{{opacity:0;transform:translateY(-10px)}}to{{opacity:1;transform:translateY(0)}}}}
@keyframes card-up{{from{{opacity:0;transform:translateY(12px)}}to{{opacity:1;transform:translateY(0)}}}}
.top-row{{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:14px;}}
.nav-row{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px;margin-bottom:14px;}}
.card{{
    background:#fff;border-radius:16px;padding:24px;
    box-shadow:0 2px 12px rgba(71,84,23,0.06), 0 1px 2px rgba(0,0,0,0.03);
    transition:box-shadow 0.2s;animation:card-up 0.4s ease backwards;
    border:1px solid rgba(226,217,200,0.5);
}}
.top-row .card:nth-child(1){{animation-delay:0.05s;}}
.top-row .card:nth-child(2){{animation-delay:0.1s;}}
.nav-row .card:nth-child(1){{animation-delay:0.15s;}}
.nav-row .card:nth-child(2){{animation-delay:0.2s;}}
.nav-row .card:nth-child(3){{animation-delay:0.25s;}}
.card:hover{{box-shadow:0 4px 20px rgba(71,84,23,0.1), 0 1px 3px rgba(0,0,0,0.04);}}
h2{{color:#475417;font-size:16px;margin:0 0 14px 0;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;}}
h3{{color:#475417;font-size:13px;margin:0 0 10px 0;font-weight:600;}}
label{{font-size:12px;color:#7a7a6f;font-weight:500;}}
input[type=date]{{
    width:100%;padding:10px 12px;font-size:14px;border:1.5px solid #e2d9c8;
    border-radius:10px;background:#faf6ee;color:#1a2e05;font-family:inherit;
    margin-top:4px;transition:border-color 0.2s, box-shadow 0.2s;
}}
input[type=date]:focus{{outline:none;border-color:#4a7c1f;box-shadow:0 0 0 3px rgba(74,124,31,0.1);}}
.btn{{
    display:block;width:100%;padding:12px;font-size:14px;font-weight:600;
    border:none;border-radius:10px;cursor:pointer;font-family:inherit;
    transition:all 0.2s;text-align:center;text-decoration:none;
}}
.btn-primary{{
    background:linear-gradient(135deg, #4a7c1f 0%, #3d6819 100%);color:#fff;
    box-shadow:0 2px 8px rgba(74,124,31,0.2);
}}
.btn-primary:hover{{transform:translateY(-1px);box-shadow:0 4px 12px rgba(74,124,31,0.3);}}
.btn-primary:active{{transform:translateY(0);}}
.btn-secondary{{background:#f0eade;color:#475417;margin-top:8px;}}
.btn-secondary:hover{{background:#e6dcc8;}}
.presets{{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:14px;}}
.preset{{
    padding:10px 12px;font-size:12px;font-weight:500;border:1.5px solid #e2d9c8;
    border-radius:10px;background:#faf6ee;color:#475417;cursor:pointer;
    text-align:center;font-family:inherit;transition:all 0.2s;
}}
.preset:hover{{background:#4a7c1f;color:#fff;border-color:#4a7c1f;transform:translateY(-1px);box-shadow:0 2px 8px rgba(74,124,31,0.15);}}
.preset:active{{transform:translateY(0);}}
.preset-wide{{grid-column:span 2;}}
.section-label{{font-size:10px;color:#9a9687;text-transform:uppercase;letter-spacing:1px;font-weight:600;margin-bottom:10px;}}
.divider{{border:none;border-top:1px solid #ebe3d4;margin:16px 0;}}
.loading{{display:none;text-align:center;padding:60px 20px;}}
.loading.active{{display:block;}}
.loading p{{color:#7a7a6f;font-size:14px;margin-top:16px;}}
.spinner{{
    width:40px;height:40px;border:3px solid #e2d9c8;border-top:3px solid #4a7c1f;
    border-radius:50%;animation:spin 0.8s linear infinite;margin:0 auto;
}}
@keyframes spin{{0%{{transform:rotate(0deg)}}100%{{transform:rotate(360deg)}}}}
@media(max-width:960px){{
    .nav-row{{grid-template-columns:1fr 1fr;}}
}}
@media(max-width:600px){{
    .container{{margin:12px auto;}}
    .top-row,.nav-row{{grid-template-columns:1fr;}}
    .card{{padding:18px;}}
    .presets{{gap:6px;}}
    .preset{{padding:9px 8px;font-size:11px;}}
}}
</style>
</head>
<body>
<div class="container" id="main">
<div class="logo">
    <div style="width:44px;height:44px;margin:0 auto 10px;border-radius:12px;background:linear-gradient(135deg, #475417 0%, #5a6e1e 100%);display:flex;align-items:center;justify-content:center;box-shadow:0 2px 8px rgba(71,84,23,0.25);">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none"><path d="M12 2C8 2 4 6 4 10c0 3 2 5.5 4 7l4 5 4-5c2-1.5 4-4 4-7 0-4-4-8-8-8z" fill="rgba(255,255,255,0.9)"/><path d="M12 6c-2 0-4 2-4 4s2 4 4 4 4-2 4-4-2-4-4-4z" fill="rgba(71,84,23,0.3)"/></svg>
    </div>
    <div style="font-size:30px;font-weight:700;color:#475417;letter-spacing:-0.5px;">Livite</div>
    <div style="font-size:12px;color:#9a9687;margin-top:2px;letter-spacing:0.3px;">Sales Dashboard</div>
</div>

<div class="top-row">
<div class="card">
<h2>Single Day</h2>
<div class="presets">
    <button class="preset" onclick="goDaily('{yesterday_str}')">Yesterday</button>
    <button class="preset" onclick="goDaily('{today_str}')">Today</button>
</div>
<div class="divider"></div>
<label>Pick a date:</label>
<input type="date" id="single_date" value="{yesterday_str}" max="{today_str}" min="2024-11-07">
<button class="btn btn-primary" style="margin-top:12px;" onclick="goSinglePick()">View Dashboard</button>
</div>

<div class="card">
<h2>Date Range</h2>

<div class="section-label">Quick Ranges</div>
<div class="presets">
    <button class="preset" onclick="goRange('{this_mon.strftime('%Y-%m-%d')}', daysAgo(1))">This Week</button>
    <button class="preset" onclick="goRange('{last_mon.strftime('%Y-%m-%d')}', '{last_sun.strftime('%Y-%m-%d')}')">Last Week</button>
    <button class="preset" onclick="goRange(daysAgo(7), daysAgo(1))">Last 7 Days</button>
    <button class="preset" onclick="goRange(daysAgo(14), daysAgo(1))">Last 14 Days</button>
    <button class="preset" onclick="goRange('{first_of_month.strftime('%Y-%m-%d')}', daysAgo(1))">This Month</button>
    <button class="preset" onclick="goRange('{first_last_month.strftime('%Y-%m-%d')}', '{last_day_last_month.strftime('%Y-%m-%d')}')">Last Month</button>
    <button class="preset" onclick="goRange(daysAgo(30), daysAgo(1))">Last 30 Days</button>
    <button class="preset" onclick="goRange('{first_of_year.strftime('%Y-%m-%d')}', daysAgo(1))">Year to Date</button>
</div>

<div class="divider"></div>
<h3>Custom Range</h3>
<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">
<div>
    <label>From:</label>
    <input type="date" id="range_start" value="{(yesterday - timedelta(days=6)).strftime('%Y-%m-%d')}" max="{today_str}" min="2024-11-07">
</div>
<div>
    <label>To:</label>
    <input type="date" id="range_end" value="{yesterday_str}" max="{today_str}" min="2024-11-07">
</div>
</div>
<button class="btn btn-primary" style="margin-top:12px;" onclick="goRangePick()">View Range</button>
</div>
</div>

<div class="nav-row">
<div class="card">
<div class="section-label">Forecasting &amp; Analysis</div>
<div class="presets">
    <a href="/today" class="preset" style="background:linear-gradient(135deg,#4a9cd8,#3a8ac4);color:#fff;border-color:#4a9cd8;text-decoration:none;box-shadow:0 2px 6px rgba(74,156,216,0.2);">Today&apos;s Prediction</a>
    <a href="/week" class="preset" style="background:linear-gradient(135deg,#8cb82e,#7aa525);color:#fff;border-color:#8cb82e;text-decoration:none;box-shadow:0 2px 6px rgba(140,184,46,0.2);">This Week</a>
    <a href="/forecast" class="preset" style="background:linear-gradient(135deg,#475417,#5a6e1e);color:#fff;border-color:#475417;text-decoration:none;box-shadow:0 2px 6px rgba(71,84,23,0.2);">P&amp;L Forecast</a>
    <a href="/schedule" class="preset" style="background:linear-gradient(135deg,#9b72c4,#8a62b3);color:#fff;border-color:#9b72c4;text-decoration:none;box-shadow:0 2px 6px rgba(155,114,196,0.2);">Schedule</a>
    <a href="/financials" class="preset" style="text-decoration:none;">Financial Overview</a>
    <a href="/profit" class="preset" style="text-decoration:none;">Profit Calc</a>
    <a href="/profit/weekly" class="preset" style="text-decoration:none;">Weekly P&L</a>
    <a href="/pto" class="preset" style="text-decoration:none;">PTO Balances</a>
</div>
</div>

<div class="card">
<div class="section-label">Operations</div>
<div class="presets" style="grid-template-columns:1fr;">
    <a href="/catering" class="preset" style="background:linear-gradient(135deg,#8cb82e,#7aa525);color:#fff;border-color:#8cb82e;text-decoration:none;box-shadow:0 2px 6px rgba(140,184,46,0.2);">Catering</a>
    <a href="/employees" class="preset" style="background:linear-gradient(135deg,#4a9cd8,#3a8ac4);color:#fff;border-color:#4a9cd8;text-decoration:none;box-shadow:0 2px 6px rgba(74,156,216,0.2);">Employees</a>
    <a href="/prices/" class="preset" style="background:linear-gradient(135deg,#e67e22,#d4711d);color:#fff;border-color:#e67e22;text-decoration:none;box-shadow:0 2px 6px rgba(230,126,34,0.2);">Vendor Prices</a>
    <a href="/invoices/" class="preset" style="background:linear-gradient(135deg,#c0392b,#a93226);color:#fff;border-color:#c0392b;text-decoration:none;box-shadow:0 2px 6px rgba(192,57,43,0.2);">Invoices</a>
    <a href="/recipes" class="preset" style="background:linear-gradient(135deg,#475417,#3a4512);color:#fff;border-color:#475417;text-decoration:none;box-shadow:0 2px 6px rgba(71,84,23,0.2);">Recipes</a>
    <a href="/prices/order-check" class="preset" style="text-decoration:none;">Order Check</a>
    <a href="/drivers" class="preset" style="background:linear-gradient(135deg,#6b8e23,#5a7a1e);color:#fff;border-color:#6b8e23;text-decoration:none;box-shadow:0 2px 6px rgba(107,142,35,0.2);">Drivers</a>
    <a href="/forkable" class="preset" style="background:linear-gradient(135deg,#9b59b6,#8e44ad);color:#fff;border-color:#9b59b6;text-decoration:none;box-shadow:0 2px 6px rgba(155,89,182,0.2);">Forkable</a>
    <a href="/payroll" class="preset" style="background:linear-gradient(135deg,#2ecc71,#27ae60);color:#fff;border-color:#2ecc71;text-decoration:none;box-shadow:0 2px 6px rgba(46,204,113,0.2);">Payroll</a>
</div>
</div>

<div class="card">
<div class="section-label">Tools</div>
<div class="presets" style="grid-template-columns:1fr;">
    <a href="/chat" class="preset" style="text-decoration:none;">Ask Laurie 2.0</a>
    <a href="/trends" class="preset" style="background:linear-gradient(135deg,#2db88a,#28a47b);color:#fff;border-color:#2db88a;text-decoration:none;box-shadow:0 2px 6px rgba(45,184,138,0.2);">Trend Scout</a>
    <a href="/reviews" class="preset" style="background:linear-gradient(135deg,#f4b400,#e0a800);color:#fff;border-color:#f4b400;text-decoration:none;box-shadow:0 2px 6px rgba(244,180,0,0.2);">Review Monitor</a>
    <a href="/demo" class="preset" style="text-decoration:none;">Demo Mode</a>
    <button class="preset" onclick="warmCache()" id="warmBtn">Pre-load Weather</button>
    <button class="preset" onclick="warmAll()" id="warmAllBtn" style="background:linear-gradient(135deg,#475417,#5a6e1e);color:#fff;border-color:#475417;box-shadow:0 2px 6px rgba(71,84,23,0.2);">Load All Data</button>
</div>
<div id="warmStatus" style="font-size:11px;color:#7a7a6f;margin-top:6px;text-align:center;"></div>
</div>
</div>

<div style="text-align:center;margin-top:18px;padding-bottom:10px;">
<span style="font-size:10px;color:#b0a99a;letter-spacing:0.3px;">Data available from Nov 7, 2024</span>
</div>
</div>

<div class="loading" id="loading">
    <div class="spinner"></div>
    <p id="loading_msg">Generating dashboard...</p>
    <p style="font-size:11px;color:#a0a090;">This takes a few seconds</p>
</div>

<script>
function fmtDate(d) {{
    var y = d.getFullYear();
    var m = String(d.getMonth()+1).padStart(2,'0');
    var dd = String(d.getDate()).padStart(2,'0');
    return y + '-' + m + '-' + dd;
}}
function daysAgo(n) {{
    var d = new Date();
    d.setDate(d.getDate() - n);
    return fmtDate(d);
}}
function showLoading(msg) {{
    document.getElementById('main').style.display = 'none';
    var el = document.getElementById('loading');
    el.classList.add('active');
    document.getElementById('loading_msg').textContent = msg || 'Generating dashboard...';
}}
function goDaily(dateStr) {{
    var ds = dateStr.replace(/-/g, '');
    showLoading('Generating dashboard for ' + dateStr + '...');
    window.location.href = '/daily/' + ds;
}}
function goSinglePick() {{
    var v = document.getElementById('single_date').value;
    if (!v) {{ alert('Please select a date.'); return; }}
    goDaily(v);
}}
function goRange(startStr, endStr) {{
    var sd = startStr.replace(/-/g, '');
    var ed = endStr.replace(/-/g, '');
    if (sd === ed) {{ goDaily(startStr); return; }}
    showLoading('Generating range dashboard...');
    window.location.href = '/range/' + sd + '/' + ed;
}}
function goRangePick() {{
    var s = document.getElementById('range_start').value;
    var e = document.getElementById('range_end').value;
    if (!s || !e) {{ alert('Please select both start and end dates.'); return; }}
    if (s > e) {{ alert('Start date must be before end date.'); return; }}
    goRange(s, e);
}}
function warmCache() {{
    var btn = document.getElementById('warmBtn');
    var status = document.getElementById('warmStatus');
    btn.disabled = true;
    btn.textContent = 'Loading...';
    status.textContent = 'Fetching weather data for all dates...';
    fetch('/cache/warm', {{method: 'POST'}})
    .then(function(r) {{ return r.json(); }})
    .then(function(data) {{
        btn.disabled = false;
        btn.textContent = 'Pre-load Weather';
        if (data.status === 'ok') {{
            status.textContent = data.message;
            status.style.color = '#4a7c1f';
        }} else {{
            status.textContent = 'Error: ' + (data.message || 'Unknown error');
            status.style.color = '#c0392b';
        }}
    }})
    .catch(function(err) {{
        btn.disabled = false;
        btn.textContent = 'Pre-load Weather';
        status.textContent = 'Network error. Try again.';
        status.style.color = '#c0392b';
    }});
}}
function warmAll() {{
    var btn = document.getElementById('warmAllBtn');
    var status = document.getElementById('warmStatus');
    btn.disabled = true;
    btn.textContent = 'Caching...';
    status.textContent = 'Pre-loading all historical data...';
    status.style.color = '#7a7a6f';
    function poll() {{
        fetch('/api/warmall')
        .then(function(r) {{ return r.json(); }})
        .then(function(data) {{
            if (data.error) {{
                btn.disabled = false;
                btn.textContent = 'Load All Data';
                status.textContent = 'Error: ' + data.error;
                status.style.color = '#c0392b';
                return;
            }}
            var pct = data.total > 0 ? Math.round(data.cached / data.total * 100) : 0;
            status.textContent = 'Cached ' + data.cached + ' / ' + data.total + ' days (' + pct + '%)';
            if (data.done) {{
                btn.disabled = false;
                btn.textContent = 'Load All Data';
                status.textContent = 'All ' + data.total + ' days cached! Dashboards will load instantly.';
                status.style.color = '#4a7c1f';
            }} else {{
                btn.textContent = 'Caching... ' + pct + '%';
                setTimeout(poll, 500);
            }}
        }})
        .catch(function(err) {{
            btn.disabled = false;
            btn.textContent = 'Load All Data';
            status.textContent = 'Network error. Try again.';
            status.style.color = '#c0392b';
        }});
    }}
    poll();
}}
</script>
</body></html>"""


def _login_page(error=""):
    """Login page matching Livite design."""
    from markupsafe import escape
    error_html = f'<div class="error">{escape(error)}</div>' if error else ''
    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Livite Dashboard — Login</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
*{{box-sizing:border-box;}}
body{{
    font-family:'DM Sans',sans-serif;margin:0;display:flex;justify-content:center;align-items:center;min-height:100vh;
    background:#F5EDDC;
    background-image:
        radial-gradient(ellipse at 30% 20%, rgba(71,84,23,0.06) 0%, transparent 60%),
        radial-gradient(ellipse at 70% 80%, rgba(74,124,31,0.04) 0%, transparent 50%);
}}
.card{{
    background:#fff;border-radius:16px;padding:40px 36px;width:100%;max-width:380px;text-align:center;
    box-shadow:0 4px 24px rgba(71,84,23,0.08), 0 1px 3px rgba(0,0,0,0.04);
    animation: card-enter 0.5s ease;
}}
@keyframes card-enter{{
    from{{opacity:0;transform:translateY(16px)}}
    to{{opacity:1;transform:translateY(0)}}
}}
.logo-mark{{
    width:48px;height:48px;margin:0 auto 16px;border-radius:12px;
    background:linear-gradient(135deg, #475417 0%, #5a6e1e 100%);
    display:flex;align-items:center;justify-content:center;
    box-shadow:0 2px 8px rgba(71,84,23,0.25);
}}
.logo-mark svg{{width:24px;height:24px;}}
.title{{font-size:30px;font-weight:700;color:#475417;letter-spacing:-0.5px;}}
.subtitle{{font-size:12px;color:#9a9687;margin-top:4px;margin-bottom:28px;letter-spacing:0.3px;}}
input[type=text],input[type=password]{{
    width:100%;padding:13px 16px;font-size:14px;border:1.5px solid #e2d9c8;
    border-radius:10px;background:#faf6ee;color:#1a2e05;font-family:inherit;
    margin-bottom:12px;text-align:center;transition:border-color 0.2s, box-shadow 0.2s;
}}
input[type=text]:focus,input[type=password]:focus{{
    outline:none;border-color:#4a7c1f;box-shadow:0 0 0 3px rgba(74,124,31,0.1);
}}
.btn{{
    display:block;width:100%;padding:13px;font-size:14px;font-weight:600;
    border:none;border-radius:10px;cursor:pointer;font-family:inherit;
    background:linear-gradient(135deg, #4a7c1f 0%, #3d6819 100%);color:#fff;
    transition:all 0.2s;box-shadow:0 2px 8px rgba(74,124,31,0.25);
}}
.btn:hover{{transform:translateY(-1px);box-shadow:0 4px 12px rgba(74,124,31,0.3);}}
.btn:active{{transform:translateY(0);}}
.error{{color:#c0392b;font-size:12px;margin-bottom:12px;padding:8px;background:#fef2f2;border-radius:8px;}}
</style></head>
<body>
<div class="card">
    <div class="logo-mark">
        <svg viewBox="0 0 24 24" fill="none"><path d="M12 2C8 2 4 6 4 10c0 3 2 5.5 4 7l4 5 4-5c2-1.5 4-4 4-7 0-4-4-8-8-8z" fill="rgba(255,255,255,0.9)"/><path d="M12 6c-2 0-4 2-4 4s2 4 4 4 4-2 4-4-2-4-4-4z" fill="rgba(71,84,23,0.3)"/></svg>
    </div>
    <div class="title">Livite</div>
    <div class="subtitle">Dashboard Login</div>
    {error_html}
    <form method="POST" action="/login">
        <input type="text" name="username" placeholder="Username" autofocus autocomplete="username">
        <input type="password" name="password" placeholder="Password" autocomplete="current-password">
        <button type="submit" class="btn">Sign In</button>
    </form>
</div>
</body></html>"""


# ── Routes ──

@app.route('/login', methods=['GET', 'POST'])
@(limiter.limit("5/minute") if limiter else lambda f: f)
def login():
    if not DASHBOARD_PASSWORD and not _USERS:
        return redirect(url_for('index'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        pw = request.form.get('password', '')
        user_info = _check_login(username, pw)
        if user_info:
            session.permanent = True
            session['authenticated'] = True
            session['username'] = username.lower()
            session['display_name'] = user_info['name']
            session['role'] = user_info['role']
            # Managers go straight to vendor prices
            if user_info['role'] == 'manager':
                return redirect('/prices/')
            return redirect(url_for('index'))
        return _login_page(error="Incorrect username or password."), 401
    return _login_page()


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# ── Employee Cash Tips Entry (public — no auth needed) ──

_CASH_TIPS_DIR = os.path.join(PROJECT_ROOT, '.tmp', 'cash_tips')
os.makedirs(_CASH_TIPS_DIR, exist_ok=True)

def _load_cash_tips_for_date(date_str):
    """Load cash tips entries for a given date (YYYY-MM-DD). Returns list of dicts."""
    path = os.path.join(_CASH_TIPS_DIR, f"{date_str}.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return []

def _save_cash_tips_for_date(date_str, entries):
    """Save cash tips entries for a given date."""
    path = os.path.join(_CASH_TIPS_DIR, f"{date_str}.json")
    with open(path, 'w') as f:
        json.dump(entries, f, indent=2)

def _get_employee_display_names():
    """Get sorted list of employee display names from config for the tips dropdown."""
    cfg_path = os.path.join(PROJECT_ROOT, 'config.yaml')
    with open(cfg_path) as f:
        cfg = yaml.safe_load(f) or {}
    wages = cfg.get('wages', {})
    names = []
    for key, info in wages.items():
        # Skip owners — they don't enter cash tips
        if info.get('type') == 'owner':
            continue
        # Convert "last, first" to "First Last"
        parts = key.split(', ', 1)
        if len(parts) == 2:
            display = f"{parts[1].title()} {parts[0].title()}"
        else:
            display = key.title()
        names.append({"key": key, "display": display})
    names.sort(key=lambda x: x["display"])
    return names

@app.route('/tips')
def tips_entry():
    """Public page where employees scan a QR code and enter daily cash tips."""
    today = datetime.now().strftime('%Y-%m-%d')
    date_str = request.args.get('date', today)
    employees = _get_employee_display_names()
    entries = _load_cash_tips_for_date(date_str)
    return Response(_render_tips_page(date_str, employees, entries), content_type='text/html')

@app.route('/tips/submit', methods=['POST'])
def tips_submit():
    """Submit a cash tip entry."""
    date_str = request.form.get('date', datetime.now().strftime('%Y-%m-%d'))
    employee = request.form.get('employee', '').strip()
    amount_str = request.form.get('amount', '0')

    try:
        amount = round(float(amount_str), 2)
    except (ValueError, TypeError):
        amount = 0.0

    if not employee or amount <= 0:
        return redirect(f'/tips?date={date_str}&error=1')

    entries = _load_cash_tips_for_date(date_str)

    # Update existing entry or add new one
    found = False
    for e in entries:
        if e['employee'] == employee:
            e['amount'] = amount
            e['time'] = datetime.now().strftime('%I:%M %p')
            found = True
            break
    if not found:
        entries.append({
            'employee': employee,
            'amount': amount,
            'time': datetime.now().strftime('%I:%M %p'),
        })

    _save_cash_tips_for_date(date_str, entries)
    return redirect(f'/tips?date={date_str}&ok=1')

@app.route('/tips/delete', methods=['POST'])
def tips_delete():
    """Delete a cash tip entry (for corrections)."""
    date_str = request.form.get('date', datetime.now().strftime('%Y-%m-%d'))
    employee = request.form.get('employee', '').strip()
    entries = _load_cash_tips_for_date(date_str)
    entries = [e for e in entries if e['employee'] != employee]
    _save_cash_tips_for_date(date_str, entries)
    return redirect(f'/tips?date={date_str}')

@app.route('/tips/qr')
def tips_qr():
    """Generate QR code image for the /tips URL."""
    try:
        import qrcode
        from io import BytesIO
        url = request.url_root.rstrip('/') + '/tips'
        # On Render, use HTTPS
        if os.getenv('RENDER'):
            url = url.replace('http://', 'https://')
        qr = qrcode.QRCode(version=1, box_size=10, border=4)
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="#475417", back_color="white")
        buf = BytesIO()
        img.save(buf, format='PNG')
        buf.seek(0)
        return Response(buf.getvalue(), content_type='image/png')
    except ImportError:
        return Response("QR code library not installed. Run: pip3 install qrcode[pil]",
                        content_type='text/plain', status=500)


def _render_tips_page(date_str, employees, entries):
    """Render the employee cash tips entry page HTML."""
    emp_options = ''.join(
        f'<option value="{e["key"]}">{e["display"]}</option>'
        for e in employees
    )

    entries_html = ''
    total = 0.0
    if entries:
        rows = ''
        for e in sorted(entries, key=lambda x: x.get('employee', '')):
            parts = e['employee'].split(', ', 1)
            display = f"{parts[1].title()} {parts[0].title()}" if len(parts) == 2 else e['employee'].title()
            amt = e.get('amount', 0)
            total += amt
            rows += f'''<tr>
                <td>{display}</td>
                <td style="text-align:right;font-weight:600;">${amt:.2f}</td>
                <td style="text-align:center;color:#999;font-size:12px;">{e.get('time', '')}</td>
                <td style="text-align:center;">
                    <form method="post" action="/tips/delete" style="display:inline;">
                        <input type="hidden" name="date" value="{date_str}">
                        <input type="hidden" name="employee" value="{e['employee']}">
                        <button type="submit" style="background:none;border:none;color:#c0392b;cursor:pointer;font-size:14px;" title="Remove">&times;</button>
                    </form>
                </td>
            </tr>'''
        entries_html = f'''
        <div style="margin-top:20px;">
            <h3 style="color:#475417;font-size:16px;margin-bottom:8px;">Today's Entries</h3>
            <table style="width:100%;border-collapse:collapse;font-size:14px;">
                <thead>
                    <tr style="border-bottom:2px solid #475417;">
                        <th style="text-align:left;padding:6px;">Name</th>
                        <th style="text-align:right;padding:6px;">Amount</th>
                        <th style="text-align:center;padding:6px;">Time</th>
                        <th style="width:30px;"></th>
                    </tr>
                </thead>
                <tbody>{rows}</tbody>
                <tfoot>
                    <tr style="border-top:2px solid #475417;font-weight:700;">
                        <td style="padding:6px;">Total</td>
                        <td style="text-align:right;padding:6px;">${total:.2f}</td>
                        <td></td><td></td>
                    </tr>
                </tfoot>
            </table>
        </div>'''

    ok_msg = '<div style="background:#d4edda;color:#155724;padding:8px 12px;border-radius:6px;margin-bottom:12px;font-size:13px;">Tip recorded!</div>' if 'ok=1' in (date_str + str(entries)) else ''
    # Check actual request args via a simple approach
    return f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Cash Tips — Livite</title>
    <link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: 'DM Sans', -apple-system, sans-serif;
            background: #F5EDDC;
            color: #2d2a24;
            min-height: 100vh;
        }}
        .header {{
            background: #475417;
            color: white;
            padding: 16px 20px;
            text-align: center;
        }}
        .header h1 {{ font-size: 20px; font-weight: 600; }}
        .header p {{ font-size: 12px; opacity: 0.8; margin-top: 2px; }}
        .container {{
            max-width: 420px;
            margin: 0 auto;
            padding: 20px 16px;
        }}
        .card {{
            background: white;
            border-radius: 12px;
            padding: 20px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.08);
        }}
        .date-display {{
            text-align: center;
            font-size: 15px;
            font-weight: 600;
            color: #475417;
            margin-bottom: 16px;
        }}
        label {{
            display: block;
            font-size: 13px;
            font-weight: 600;
            color: #475417;
            margin-bottom: 4px;
        }}
        select, input[type="number"] {{
            width: 100%;
            padding: 12px;
            border: 2px solid #e0d5bf;
            border-radius: 8px;
            font-size: 16px;
            font-family: 'DM Sans', sans-serif;
            margin-bottom: 12px;
            background: white;
            -webkit-appearance: none;
        }}
        select:focus, input:focus {{
            border-color: #475417;
            outline: none;
        }}
        .btn {{
            width: 100%;
            padding: 14px;
            background: #4a7c1f;
            color: white;
            border: none;
            border-radius: 8px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            font-family: 'DM Sans', sans-serif;
        }}
        .btn:hover {{ background: #3a6216; }}
        .btn:active {{ transform: scale(0.98); }}
        table td, table th {{ padding: 8px 6px; }}
        table tbody tr {{ border-bottom: 1px solid #f0ebe0; }}
        .success {{ background:#d4edda;color:#155724;padding:10px 12px;border-radius:8px;margin-bottom:14px;font-size:14px;text-align:center; }}
        .error {{ background:#f8d7da;color:#721c24;padding:10px 12px;border-radius:8px;margin-bottom:14px;font-size:14px;text-align:center; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>Livite Cash Tips</h1>
        <p>Enter your cash tips for the day</p>
    </div>
    <div class="container">
        <div class="card">
            <div class="date-display">{datetime.strptime(date_str, "%Y-%m-%d").strftime("%A, %B %d, %Y")}</div>

            <div id="okMsg"></div>
            <div id="errMsg"></div>

            <form method="post" action="/tips/submit">
                <input type="hidden" name="date" value="{date_str}">

                <label for="employee">Your Name</label>
                <select name="employee" id="employee" required>
                    <option value="">Select your name...</option>
                    {emp_options}
                </select>

                <label for="amount">Cash Tips ($)</label>
                <input type="number" name="amount" id="amount" step="0.01" min="0.01"
                       inputmode="decimal" placeholder="0.00" required>

                <button type="submit" class="btn">Submit Tips</button>
            </form>

            {entries_html}
        </div>
    </div>
    <script>
        // Show success/error from URL params
        const params = new URLSearchParams(window.location.search);
        if (params.get('ok') === '1') {{
            document.getElementById('okMsg').innerHTML = '<div class="success">Tip recorded!</div>';
        }}
        if (params.get('error') === '1') {{
            document.getElementById('errMsg').innerHTML = '<div class="error">Please select your name and enter an amount.</div>';
        }}
    </script>
</body>
</html>'''


# ── Demo Dashboard (no auth — for showing to prospects) ──

_DEMO_BANNER = (
    '<div style="position:fixed;top:0;left:0;right:0;z-index:9999;'
    'background:linear-gradient(135deg,#475417,#6b8e23);color:white;'
    'text-align:center;padding:8px 16px;font-size:13px;font-weight:600;'
    'letter-spacing:1px;box-shadow:0 2px 8px rgba(0,0,0,0.15);">'
    'DEMO DASHBOARD &mdash; Sample data for demonstration purposes'
    '</div>'
    '<div style="height:36px;"></div>'
)


@app.route('/demo')
@app.route('/demo/<date_str>')
def demo_dashboard(date_str=None):
    """Public demo dashboard with scaled-down fake data."""
    from demo_data import generate_demo_data

    try:
        demo_data = generate_demo_data()
    except FileNotFoundError as e:
        return _error_page(str(e)), 404

    demo_date = datetime(2026, 2, 17)
    metrics = compute_all_metrics(demo_data, demo_date)

    try:
        analyst_insights = compute_analyst_insights(
            metrics,
            metrics.get('revenue', {}).get('quarter_hourly_4wra', {}),
        )
    except Exception:
        analyst_insights = []

    logo = _LOGO_B64 or _load_logo()

    html = build_dashboard(
        metrics, comparisons={}, anomalies=[],
        date_str="20260217",
        prev_date_str="", next_date_str="",
        analyst_insights=analyst_insights,
        logo_b64=logo,
        chat_enabled=False,
        chat_context="",
    )

    # Inject demo banner after <body>
    html = html.replace('<body>', '<body>' + _DEMO_BANNER, 1)
    # Replace "Dashboard Home" link to point to /demo
    html = html.replace('href="/"', 'href="/demo"')
    # Remove date nav links (no real dates to navigate to)
    html = html.replace('Prev Day', '').replace('Next Day', '')

    return Response(html, content_type='text/html')


@app.route('/')
@login_required
def index():
    if session.get('role') != 'owner':
        return redirect('/prices/')
    # Serve Next.js static export if built, otherwise fall back to old landing page
    nextjs_index = os.path.join(PROJECT_ROOT, 'web', 'out', 'index.html')
    if os.path.exists(nextjs_index):
        from flask import send_from_directory
        return send_from_directory(os.path.join(PROJECT_ROOT, 'web', 'out'), 'index.html')
    return _landing_page()


@app.route('/_next/<path:filename>')
def nextjs_assets(filename):
    """Serve Next.js static assets."""
    from flask import send_from_directory
    return send_from_directory(os.path.join(PROJECT_ROOT, 'web', 'out', '_next'), filename)


@app.route('/logo.png')
def nextjs_logo():
    from flask import send_from_directory
    return send_from_directory(os.path.join(PROJECT_ROOT, 'web', 'out'), 'logo.png')


@app.route('/daily/<date_str>')
@owner_required
def daily(date_str):
    try:
        date = datetime.strptime(date_str, "%Y%m%d")
    except ValueError:
        return _error_page(f"Invalid date format: {date_str}. Use YYYYMMDD."), 400

    if date < EARLIEST_DATE:
        return _error_page(f"No data before November 7, 2024."), 404

    html = _generate_daily_html(date)
    return Response(html, content_type='text/html')


@app.route('/range/<start_str>/<end_str>')
@owner_required
def date_range(start_str, end_str):
    try:
        start = datetime.strptime(start_str, "%Y%m%d")
        end = datetime.strptime(end_str, "%Y%m%d")
    except ValueError:
        return _error_page("Invalid date format. Use YYYYMMDD."), 400

    if start > end:
        start, end = end, start

    if start < EARLIEST_DATE:
        start = EARLIEST_DATE

    html = _generate_range_html(start, end)
    return Response(html, content_type='text/html')


@app.route('/cache/stats')
@owner_required
def cache_status():
    stats = cache_stats()
    return Response(json.dumps(stats, indent=2), content_type='application/json')


@app.route('/api/warmall')
@owner_required
def api_warmall():
    """Pre-cache metrics for all available dates. Processes a batch per call."""
    batch_size = 15
    try:
        available = sorted(_get_available_dates())
    except Exception:
        return jsonify({'error': 'Could not list dates'}), 500

    cached_count = 0
    batch = 0
    uncached_remaining = 0
    total = len(available)

    with batch_connection():
        for ds in available:
            if is_today(ds):
                continue
            if get_cached_metrics(ds) is not None:
                cached_count += 1
            elif batch < batch_size:
                try:
                    dt = datetime.strptime(ds, "%Y%m%d")
                    data = get_daily_data(dt, quiet=True)
                    if 'OrderDetails' in data:
                        metrics = compute_all_metrics(data, dt, skip_weather=True)
                        if metrics:
                            cache_metrics(ds, metrics)
                            cached_count += 1
                            batch += 1
                except Exception:
                    batch += 1
            else:
                uncached_remaining += 1

    return jsonify({
        'cached': cached_count,
        'remaining': uncached_remaining,
        'total': total,
        'done': uncached_remaining == 0,
    })


@app.route('/cache/warm', methods=['POST'])
@owner_required
def warm_cache():
    """Pre-fetch all weather data in a single bulk API call."""
    try:
        from fetch_weather_data import warm_weather_cache
        cached, total = warm_weather_cache()
        return jsonify({
            "status": "ok",
            "weather_cached": cached,
            "total_days": total,
            "message": f"Cached {cached} new days of weather ({total} total days)"
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/health')
def health_check():
    """Health check — no auth required."""
    return jsonify({
        "status": "ok",
        "chat_enabled": bool(ANTHROPIC_API_KEY),
        "api_key_set": bool(ANTHROPIC_API_KEY),
    })


# ── Financials ──

@app.route('/financials')
@owner_required
def financials():
    """Financial overview page -- P&L and Balance Sheet visualization."""
    try:
        from tools.financials import parse_all_pl, parse_all_bs
        from tools.financials import compute_financials_metrics
        from tools.financials import build_financials_page

        # Parse optional period filter from query string
        start_str = request.args.get('start', '')
        end_str = request.args.get('end', '')
        start_month = None
        end_month = None
        if start_str and len(start_str) == 8:
            start_month = start_str[:4] + "-" + start_str[4:6]
        if end_str and len(end_str) == 8:
            end_month = end_str[:4] + "-" + end_str[4:6]

        pl_data = parse_all_pl()
        bs_data = parse_all_bs()
        metrics = compute_financials_metrics(
            pl_data, bs_data,
            start_month=start_month,
            end_month=end_month,
        )

        # Pull catering platform revenue (Excel tracker + Toast POS)
        try:
            from tools.catering import get_catering_by_month
            catering = get_catering_by_month()
            if catering:
                metrics["catering"] = catering
        except Exception:
            pass  # graceful degradation -- page works without catering data

        logo = _LOGO_B64 or _load_logo()
        html = build_financials_page(
            metrics, logo_b64=logo,
            current_start=start_str, current_end=end_str,
        )
        return Response(html, content_type='text/html')
    except Exception as e:
        logger.error("Error loading financial data: %s", e, exc_info=True)
        return _error_page(f"Error loading financial data: {e}")


# ── Catering ──

@app.route('/catering')
@owner_required
def catering():
    """Catering dashboard -- all catering orders across platforms."""
    try:
        from tools.catering import get_catering_dashboard_data, build_catering_page

        start_str = request.args.get('start', '')
        end_str = request.args.get('end', '')
        start_month = None
        end_month = None
        if start_str and len(start_str) == 8:
            start_month = start_str[:4] + "-" + start_str[4:6]
        if end_str and len(end_str) == 8:
            end_month = end_str[:4] + "-" + end_str[4:6]

        metrics = get_catering_dashboard_data(
            start_month=start_month,
            end_month=end_month,
        )

        logo = _LOGO_B64 or _load_logo()
        html = build_catering_page(
            metrics, logo_b64=logo,
            current_start=start_str, current_end=end_str,
        )
        return Response(html, content_type='text/html')
    except Exception as e:
        logger.error("Error loading catering data: %s", e, exc_info=True)
        return _error_page(f"Error loading catering data: {e}")


@app.route('/forecast')
@owner_required
def forecast():
    """P&L Forecast -- revenue and profit projections."""
    try:
        from tools.forecast import generate_forecast, build_forecast_page

        months_ahead = int(request.args.get('months', '6'))
        months_ahead = min(max(months_ahead, 3), 12)

        metrics = generate_forecast(months_ahead=months_ahead)
        logo = _LOGO_B64 or _load_logo()
        html = build_forecast_page(metrics, logo_b64=logo)
        return Response(html, content_type='text/html')
    except Exception as e:
        logger.error("Error generating forecast: %s", e, exc_info=True)
        return _error_page(f"Error generating forecast: {e}")


@app.route('/today')
@owner_required
def today_prediction():
    """Today's Prediction -- weather-aware daily forecast with hourly breakdown."""
    try:
        from tools.forecast import generate_today_prediction, build_today_page

        metrics = generate_today_prediction()
        logo = _LOGO_B64 or _load_logo()
        html = build_today_page(metrics, logo_b64=logo)
        return Response(html, content_type='text/html')
    except Exception as e:
        logger.error("Error generating today's prediction: %s", e, exc_info=True)
        return _error_page(f"Error generating today's prediction: {e}")


@app.route('/week')
@owner_required
def week_view():
    """This Week -- actuals + forecast with last week comparison."""
    try:
        from tools.forecast import generate_week_view, build_week_page

        metrics = generate_week_view()
        logo = _LOGO_B64 or _load_logo()
        html = build_week_page(metrics, logo_b64=logo)
        return Response(html, content_type='text/html')
    except Exception as e:
        logger.error("Error generating weekly view: %s", e, exc_info=True)
        return _error_page(f"Error generating weekly view: {e}")


# ── Schedule: in-memory cache (30 min TTL) ──
_schedule_cache = {}  # key -> {"data": dict, "ts": float}
_SCHEDULE_TTL = 1800  # seconds


def _get_iso_week_for(week: str) -> str:
    """Convert 'this' or 'next' to ISO week string like '2026-W08'.

    Also accepts an already-formatted ISO week string and returns it as-is.
    """
    from datetime import datetime, timedelta
    if week and week.startswith("20") and "-W" in week:
        return week  # already ISO format
    today = datetime.now()
    if week == "next":
        target = today + timedelta(days=7)
    else:
        target = today
    iso = target.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


@app.route('/schedule')
@owner_required
def schedule_page():
    """Labor Schedule -- lightweight shell page, data loaded async via JS."""
    from tools.scheduling.html import build_schedule_page

    week = request.args.get('week', 'this')
    if week not in ('this', 'next'):
        week = 'this'

    logo = _LOGO_B64 or _load_logo()
    html = build_schedule_page(logo_b64=logo, week=week)
    return Response(html, content_type='text/html')


@app.route('/availability')
@owner_required
def availability_page():
    """Employee availability matrix — interactive editing UI."""
    from tools.scheduling.html import build_availability_page
    from tools.scheduling.availability import load_availability

    logo = _LOGO_B64 or _load_logo()
    avail_data = load_availability()
    html = build_availability_page(avail_data, logo_b64=logo)
    return Response(html, content_type='text/html')


@app.route('/api/schedule/data')
@owner_required
def schedule_data_api():
    """Return schedule data as JSON (cached 30 min).

    If a saved schedule exists for the week, loads that instead of
    generating fresh. Pass ?force_generate=1 to bypass saved data.
    """
    from tools.scheduling.scheduler import generate_weekly_schedule
    from tools.scheduling.availability import load_availability
    from tools.scheduling.persistence import load_schedule

    week = request.args.get('week', 'this')
    if week not in ('this', 'next'):
        week = 'this'
    force_gen = request.args.get('force_generate', '0') == '1'

    # Check cache
    cache_key = f"schedule_{week}"
    cached = _schedule_cache.get(cache_key)
    if cached and time.time() - cached['ts'] < _SCHEDULE_TTL:
        return jsonify(cached['data'])

    # Check for saved schedule (unless force generating)
    if not force_gen:
        iso_week = _get_iso_week_for(week)
        saved = load_schedule(iso_week) if iso_week else None
        if saved:
            # Attach availability for client-side explain feature
            avail_data = load_availability()
            avail_map = {}
            for name, emp in avail_data.get('employees', {}).items():
                avail_map[name] = emp.get('availability', {})
            saved['availability'] = avail_map
            _schedule_cache[cache_key] = {'data': saved, 'ts': time.time()}
            return jsonify(saved)

    # Generate fresh schedule
    try:
        metrics = generate_weekly_schedule(week=week)
    except Exception as e:
        logger.error("generate_weekly_schedule failed: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500

    # Attach availability data for client-side explain feature
    avail_data = load_availability()
    avail_map = {}
    for name, emp in avail_data.get('employees', {}).items():
        avail_map[name] = emp.get('availability', {})
    metrics['availability'] = avail_map

    # Cache result
    _schedule_cache[cache_key] = {'data': metrics, 'ts': time.time()}
    return jsonify(metrics)


@app.route('/api/schedule/move', methods=['POST'])
@owner_required
def schedule_move_api():
    """Move a shift from one day to another for the same employee."""
    from tools.scheduling.availability import (
        load_availability, _time_to_hours, _hours_to_time, get_employee_wage,
    )

    payload = request.json or {}
    employee = payload.get('employee', '')
    from_day = payload.get('from_day')
    to_day = payload.get('to_day')
    week = payload.get('week', 'this')

    if from_day is None or to_day is None:
        return jsonify({"error": "Missing from_day or to_day"}), 400

    # Get cached schedule
    cache_key = f"schedule_{week}"
    cached = _schedule_cache.get(cache_key)
    if not cached:
        return jsonify({"error": "Schedule not loaded. Refresh the page."}), 400

    schedule = cached['data']
    days = schedule.get('days', [])

    if from_day < 0 or from_day >= len(days) or to_day < 0 or to_day >= len(days):
        return jsonify({"error": "Invalid day index"}), 400

    # Find shift to move
    from_shifts = days[from_day].get('shifts', [])
    shift_idx = None
    for i, s in enumerate(from_shifts):
        if s['employee'] == employee:
            shift_idx = i
            break
    if shift_idx is None:
        return jsonify({"error": "Shift not found"}), 404

    # Check availability on target day
    avail_map = schedule.get('availability', {})
    target_dow = days[to_day]['dow_name']
    emp_avail = (avail_map.get(employee) or {}).get(target_dow)
    if emp_avail is None:
        return jsonify({"error": f"Not available on {target_dow}"}), 400

    # Check target doesn't already have a shift for this employee
    to_shifts = days[to_day].get('shifts', [])
    for s in to_shifts:
        if s['employee'] == employee:
            return jsonify({"error": f"Already scheduled on {target_dow}"}), 400

    # Remove from source day
    shift = from_shifts.pop(shift_idx)

    # Create new shift on target day using that day's availability
    avail_start = _time_to_hours(emp_avail.get('start', '07:00'))
    avail_end = _time_to_hours(emp_avail.get('end', '21:00'))
    new_hours = min(avail_end - avail_start, shift['hours'], 10.0)
    new_start = avail_start

    new_shift = dict(shift)
    new_shift['start'] = _hours_to_time(new_start)
    new_shift['end'] = _hours_to_time(new_start + new_hours)
    new_shift['hours'] = round(new_hours, 1)

    if new_shift.get('type') == 'salaried':
        new_shift['cost'] = 300.0
    else:
        wage = get_employee_wage(employee)
        new_shift['cost'] = round(new_hours * wage, 2)

    to_shifts.append(new_shift)

    # Recalculate day totals for affected days
    for d in [days[from_day], days[to_day]]:
        d_shifts = d.get('shifts', [])
        d['total_hours'] = round(sum(s['hours'] for s in d_shifts), 1)
        d['labor_cost'] = round(sum(s['cost'] for s in d_shifts), 2)
        d['headcount'] = len(d_shifts)
        d['foh_count'] = len([s for s in d_shifts if s.get('dept') in ('FOH', 'both')])
        d['boh_count'] = len([s for s in d_shifts if s.get('dept') in ('BOH', 'both')])

    # Recalculate weekly totals
    schedule['labor_cost_estimate'] = round(sum(d['labor_cost'] for d in days), 2)
    schedule['total_labor_hours'] = round(sum(d['total_hours'] for d in days), 1)
    total_rev = schedule.get('total_revenue_forecast', 0)
    total_hrs = schedule['total_labor_hours']
    schedule['projected_splh'] = round(total_rev / total_hrs, 2) if total_hrs > 0 else 0

    # Recalculate employee summary
    weekly_hours = {}
    for d in days:
        for s in d.get('shifts', []):
            weekly_hours[s['employee']] = weekly_hours.get(s['employee'], 0) + s['hours']

    for emp_s in schedule.get('employee_summary', []):
        name = emp_s['name']
        emp_s['weekly_hours'] = round(weekly_hours.get(name, 0), 1)
        max_h = emp_s.get('max_hours', 40)
        emp_s['pct_max'] = round(emp_s['weekly_hours'] / max_h * 100, 1) if max_h > 0 else 0
        emp_s['over_max'] = emp_s['weekly_hours'] > max_h
        emp_s['shifts'] = sum(
            1 for d in days for s in d.get('shifts', []) if s['employee'] == name
        )
        if emp_s.get('type') == 'salaried':
            emp_s['cost'] = round(300.0 * emp_s['shifts'], 2)
        else:
            emp_s['cost'] = round(emp_s['weekly_hours'] * emp_s.get('wage', 0), 2)

    # Update cache
    cached['data'] = schedule
    cached['ts'] = time.time()

    return jsonify(schedule)


@app.route('/api/schedule/edit', methods=['POST'])
@owner_required
def schedule_edit_api():
    """Edit a shift's start/end times."""
    from tools.scheduling.availability import (
        _time_to_hours, _hours_to_time, get_employee_wage,
    )

    payload = request.json or {}
    employee = payload.get('employee', '')
    day_idx = payload.get('day')
    new_start = payload.get('start', '')
    new_end = payload.get('end', '')
    week = payload.get('week', 'this')

    if day_idx is None or not new_start or not new_end:
        return jsonify({"error": "Missing required fields"}), 400

    try:
        start_h = _time_to_hours(new_start)
        end_h = _time_to_hours(new_end)
    except Exception:
        return jsonify({"error": "Invalid time format"}), 400

    if start_h >= end_h:
        return jsonify({"error": "Start must be before end"}), 400

    hours = round(end_h - start_h, 1)
    if hours < 1 or hours > 10:
        return jsonify({"error": "Shift must be 1-10 hours"}), 400

    # Get cached schedule
    cache_key = f"schedule_{week}"
    cached = _schedule_cache.get(cache_key)
    if not cached:
        return jsonify({"error": "Schedule not loaded. Refresh the page."}), 400

    schedule = cached['data']
    days = schedule.get('days', [])

    if day_idx < 0 or day_idx >= len(days):
        return jsonify({"error": "Invalid day index"}), 400

    # Validate against availability
    avail_map = schedule.get('availability', {})
    dow = days[day_idx]['dow_name']
    emp_avail = (avail_map.get(employee) or {}).get(dow)
    if emp_avail:
        avail_start = _time_to_hours(emp_avail.get('start', '07:00'))
        avail_end = _time_to_hours(emp_avail.get('end', '21:00'))
        if start_h < avail_start or end_h > avail_end:
            return jsonify({
                "error": f"Outside availability ({emp_avail['start']}-{emp_avail['end']})"
            }), 400

    # Find and update the shift
    day_shifts = days[day_idx].get('shifts', [])
    shift = None
    for s in day_shifts:
        if s['employee'] == employee:
            shift = s
            break
    if shift is None:
        return jsonify({"error": "Shift not found"}), 404

    shift['start'] = new_start
    shift['end'] = new_end
    shift['hours'] = hours
    if shift.get('type') == 'salaried':
        shift['cost'] = 300.0
    else:
        wage = get_employee_wage(employee)
        shift['cost'] = round(hours * wage, 2)

    # Recalculate day totals
    d = days[day_idx]
    d_shifts = d.get('shifts', [])
    d['total_hours'] = round(sum(s['hours'] for s in d_shifts), 1)
    d['labor_cost'] = round(sum(s['cost'] for s in d_shifts), 2)
    d['headcount'] = len(d_shifts)
    d['foh_count'] = len([s for s in d_shifts if s.get('dept') in ('FOH', 'both')])
    d['boh_count'] = len([s for s in d_shifts if s.get('dept') in ('BOH', 'both')])

    # Recalculate weekly totals
    schedule['labor_cost_estimate'] = round(sum(d['labor_cost'] for d in days), 2)
    schedule['total_labor_hours'] = round(sum(d['total_hours'] for d in days), 1)
    total_rev = schedule.get('total_revenue_forecast', 0)
    total_hrs = schedule['total_labor_hours']
    schedule['projected_splh'] = round(total_rev / total_hrs, 2) if total_hrs > 0 else 0

    # Recalculate employee summary
    weekly_hours = {}
    for d in days:
        for s in d.get('shifts', []):
            weekly_hours[s['employee']] = weekly_hours.get(s['employee'], 0) + s['hours']

    for emp_s in schedule.get('employee_summary', []):
        name = emp_s['name']
        emp_s['weekly_hours'] = round(weekly_hours.get(name, 0), 1)
        max_h = emp_s.get('max_hours', 40)
        emp_s['pct_max'] = round(emp_s['weekly_hours'] / max_h * 100, 1) if max_h > 0 else 0
        emp_s['over_max'] = emp_s['weekly_hours'] > max_h
        emp_s['shifts'] = sum(
            1 for d in days for s in d.get('shifts', []) if s['employee'] == name
        )
        if emp_s.get('type') == 'salaried':
            emp_s['cost'] = round(300.0 * emp_s['shifts'], 2)
        else:
            emp_s['cost'] = round(emp_s['weekly_hours'] * emp_s.get('wage', 0), 2)

    cached['data'] = schedule
    cached['ts'] = time.time()

    return jsonify(schedule)


@app.route('/api/schedule/reset', methods=['POST'])
@owner_required
def schedule_reset_api():
    """Clear schedule cache to force regeneration."""
    week = (request.json or {}).get('week', 'this')
    cache_key = f"schedule_{week}"
    _schedule_cache.pop(cache_key, None)
    return jsonify({"status": "ok"})


@app.route('/api/schedule/save', methods=['POST'])
@owner_required
def schedule_save_api():
    """Save current schedule as a draft YAML file."""
    from tools.scheduling.persistence import save_schedule

    payload = request.json or {}
    week = payload.get('week', 'this')
    schedule_data = payload.get('schedule')

    if not schedule_data:
        return jsonify({"error": "No schedule data provided"}), 400

    iso_week = _get_iso_week_for(week)
    result = save_schedule(iso_week, schedule_data, status='draft')
    return jsonify(result)


@app.route('/api/schedule/publish', methods=['POST'])
@owner_required
def schedule_publish_api():
    """Publish a saved schedule (mark as final)."""
    from tools.scheduling.persistence import publish_schedule

    payload = request.json or {}
    week = payload.get('week', 'this')
    iso_week = _get_iso_week_for(week)

    result = publish_schedule(iso_week)
    if not result:
        return jsonify({"error": "No saved schedule found for " + iso_week}), 404
    return jsonify(result)


@app.route('/schedule/review')
@owner_required
def schedule_review_page():
    """Scheduled vs Actual comparison page."""
    from tools.scheduling.review_html import build_review_page
    from tools.scheduling.persistence import list_schedules

    logo = _LOGO_B64 or _load_logo()
    saved = list_schedules()

    # Default to most recent published schedule
    week = request.args.get('week', '')
    if not week:
        published = [s for s in saved if s.get('status') == 'published']
        if published:
            week = published[0]['week']

    html = build_review_page(logo_b64=logo, week=week, saved_weeks=saved)
    return Response(html, content_type='text/html')


@app.route('/api/schedule/review/data')
@owner_required
def schedule_review_data_api():
    """Return scheduled-vs-actual comparison data as JSON."""
    from tools.scheduling.persistence import load_schedule
    from tools.scheduling.actual import compare_scheduled_vs_actual

    week = request.args.get('week', '')
    if not week:
        return jsonify({"error": "No week specified"}), 400

    schedule = load_schedule(week)
    if not schedule:
        return jsonify({"error": f"No saved schedule for {week}"}), 404

    try:
        result = compare_scheduled_vs_actual(schedule)
    except Exception as e:
        logger.error("compare_scheduled_vs_actual failed: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500

    return jsonify(result)


@app.route('/api/availability', methods=['GET'])
@owner_required
def get_availability_api():
    """Get employee availability data as JSON."""
    from tools.scheduling.availability import load_availability
    return jsonify(load_availability())


@app.route('/api/availability', methods=['POST'])
@owner_required
def update_availability_api():
    """Update a single employee's availability."""
    from tools.scheduling.availability import load_availability, save_availability
    data = load_availability()
    updates = request.json
    employee = updates.get("employee", "")
    changes = updates.get("changes", {})
    if employee not in data.get("employees", {}):
        return jsonify({"error": f"Employee {employee} not found"}), 404
    data["employees"][employee].update(changes)
    save_availability(data)
    return jsonify({"status": "ok"})


# ── Employee Database ──

@app.route('/employees')
@owner_required
def employees_page():
    """Employee database — manage staff, departments, roles, availability."""
    from tools.scheduling.employees_html import build_employees_page
    logo = _LOGO_B64 or _load_logo()
    html = build_employees_page(logo_b64=logo)
    return Response(html, content_type='text/html')


@app.route('/api/employees/data')
@owner_required
def employees_data_api():
    """Full employee list with merged availability + wage data."""
    from tools.scheduling.availability import (
        get_all_employees, get_custom_roles
    )
    emps = get_all_employees()
    # Remap 'skills' -> 'roles' for UI
    for e in emps:
        e["roles"] = e.pop("skills", [])
    # Sort by department then display_name
    dept_order = {"FOH": 0, "BOH": 1, "both": 2}
    emps.sort(key=lambda e: (dept_order.get(e.get("department", ""), 3),
                              (e.get("display_name") or e.get("name", "")).lower()))
    # Collect all known roles
    custom = get_custom_roles()
    all_roles = list(custom)
    for e in emps:
        for r in e.get("roles", []):
            if r not in all_roles:
                all_roles.append(r)
    return jsonify({
        "employees": emps,
        "role_options": all_roles,
        "department_options": ["FOH", "BOH", "both"],
    })


@app.route('/api/employees/update', methods=['POST'])
@owner_required
def employees_update_api():
    """Update one employee's data (availability.yaml + config.yaml wage)."""
    from tools.scheduling.availability import (
        load_availability, save_availability, update_wage
    )
    body = request.json or {}
    emp_name = body.get("employee", "").strip().lower()
    changes = body.get("changes", {})
    if not emp_name:
        return jsonify({"error": "Employee name required"}), 400

    data = load_availability()
    employees = data.get("employees", {})
    if emp_name not in employees:
        return jsonify({"error": f"Employee '{emp_name}' not found"}), 404

    rec = employees[emp_name]
    # Map UI fields to YAML fields
    if "display_name" in changes:
        rec["display_name"] = changes["display_name"]
    if "department" in changes:
        rec["department"] = changes["department"]
    if "roles" in changes:
        rec["skills"] = changes["roles"]  # UI says 'roles', YAML says 'skills'
    if "max_hours_week" in changes:
        rec["max_hours_week"] = int(changes["max_hours_week"])
    if "availability" in changes:
        rec["availability"] = changes["availability"]
    if "notes" in changes:
        rec["notes"] = changes["notes"]

    save_availability(data)

    # Update wage in config.yaml if provided
    if "wage" in changes:
        update_wage(emp_name, float(changes["wage"]))
    # Update type in config.yaml if provided
    if "type" in changes:
        import yaml as _yaml
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")
        try:
            with open(config_path, "r") as f:
                cfg = _yaml.safe_load(f) or {}
        except FileNotFoundError:
            cfg = {}
        wages = cfg.setdefault("wages", {})
        if emp_name in wages:
            wages[emp_name]["type"] = changes["type"]
        else:
            wages[emp_name] = {"wage": float(changes.get("wage", 0)), "type": changes["type"]}
        with open(config_path, "w") as f:
            _yaml.dump(cfg, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    # Clear schedule cache since employee data changed
    _schedule_cache.clear()
    return jsonify({"status": "ok"})


@app.route('/api/employees/add', methods=['POST'])
@owner_required
def employees_add_api():
    """Add a new employee."""
    from tools.scheduling.availability import add_employee, update_wage
    body = request.json or {}
    name = body.get("name", "").strip().lower()
    if not name:
        return jsonify({"error": "Name required (last, first)"}), 400

    # Check for duplicates
    from tools.scheduling.availability import load_availability
    existing = load_availability().get("employees", {})
    if name in existing:
        return jsonify({"error": f"Employee '{name}' already exists"}), 400

    record = {
        "display_name": body.get("display_name", ""),
        "department": body.get("department", "FOH"),
        "max_hours_week": int(body.get("max_hours_week", 40)),
        "skills": body.get("roles", []),
        "availability": body.get("availability", {}),
        "notes": body.get("notes", ""),
    }
    add_employee(name, record)

    # Add wage to config.yaml
    wage = float(body.get("wage", 0))
    emp_type = body.get("type", "hourly")
    import yaml as _yaml
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")
    try:
        with open(config_path, "r") as f:
            cfg = _yaml.safe_load(f) or {}
    except FileNotFoundError:
        cfg = {}
    wages = cfg.setdefault("wages", {})
    wages[name] = {"wage": wage, "type": emp_type}
    with open(config_path, "w") as f:
        _yaml.dump(cfg, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    _schedule_cache.clear()
    return jsonify({"status": "ok"})


@app.route('/api/employees/remove', methods=['POST'])
@owner_required
def employees_remove_api():
    """Remove an employee from availability (preserves wage history in config)."""
    from tools.scheduling.availability import remove_employee
    body = request.json or {}
    name = body.get("employee", "").strip().lower()
    if not name:
        return jsonify({"error": "Employee name required"}), 400
    if not remove_employee(name):
        return jsonify({"error": f"Employee '{name}' not found"}), 404
    _schedule_cache.clear()
    return jsonify({"status": "ok"})


@app.route('/api/employees/add-role', methods=['POST'])
@owner_required
def employees_add_role_api():
    """Add a new custom role option."""
    from tools.scheduling.availability import add_custom_role
    body = request.json or {}
    role = body.get("role", "").strip().lower()
    if not role:
        return jsonify({"error": "Role name required"}), 400
    add_custom_role(role)
    return jsonify({"status": "ok"})


@app.route('/api/employees/delete-role', methods=['POST'])
@owner_required
def employees_delete_role_api():
    """Delete a custom role option (also removes from all employees)."""
    from tools.scheduling.availability import remove_custom_role
    body = request.json or {}
    role = body.get("role", "").strip().lower()
    if not role:
        return jsonify({"error": "Role name required"}), 400
    if not remove_custom_role(role):
        return jsonify({"error": f"Role '{role}' not found"}), 404
    _schedule_cache.clear()
    return jsonify({"status": "ok"})


# ── Recipes ──

RECIPE_ITEMS_DB = os.getenv("NOTION_ITEMS_DB_ID", "")
RECIPE_PRICES_DB = os.getenv("NOTION_PRICES_DB_ID", "")


@app.route('/recipes')
@owner_required
def recipes_page():
    """Recipe costing page — view, add, edit recipes with cost analysis."""
    from tools.recipes.html import build_recipe_page
    logo = _LOGO_B64 or _load_logo()
    html = build_recipe_page(logo_b64=logo)
    return Response(html, content_type='text/html')


@app.route('/api/recipes/data')
@owner_required
def recipes_data_api():
    """All recipes with calculated costs from current vendor prices."""
    from tools.recipes.data import calculate_all_recipes
    try:
        results = calculate_all_recipes(RECIPE_ITEMS_DB, RECIPE_PRICES_DB)
    except Exception as e:
        logger.error("calculate_all_recipes failed: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500
    return jsonify(results)


@app.route('/api/recipes/save', methods=['POST'])
@owner_required
def recipes_save_api():
    """Create or update a recipe."""
    from tools.recipes.data import save_recipe
    body = request.json or {}
    if not body.get("name"):
        return jsonify({"error": "Recipe name required"}), 400
    try:
        result = save_recipe(body)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify(result)


@app.route('/api/recipes/delete', methods=['POST'])
@owner_required
def recipes_delete_api():
    """Delete a recipe by ID."""
    from tools.recipes.data import delete_recipe
    body = request.json or {}
    rid = body.get("id", "")
    if not rid:
        return jsonify({"error": "Recipe ID required"}), 400
    if delete_recipe(rid):
        return jsonify({"status": "ok"})
    return jsonify({"error": "Recipe not found"}), 404


@app.route('/api/recipes/ingredients')
@owner_required
def recipes_ingredients_api():
    """Items Master list with prices for autocomplete in recipe editor."""
    from tools.recipes.data import get_items_list, get_current_ingredient_prices
    try:
        items = get_items_list(RECIPE_ITEMS_DB)
        # Merge latest price data for rich autocomplete
        if RECIPE_ITEMS_DB and RECIPE_PRICES_DB:
            prices = get_current_ingredient_prices(RECIPE_ITEMS_DB, RECIPE_PRICES_DB)
            for item in items:
                key = item.get("name", "").lower()
                p = prices.get(key, {})
                item["price_per_unit"] = p.get("price_per_unit", 0)
                item["vendor"] = p.get("vendor", "")
                item["price_unit"] = p.get("unit", "")
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify(items)


@app.route('/api/recipes/coverage')
@owner_required
def recipes_coverage_api():
    """Recipe coverage stats: what % of sales are costed vs estimated."""
    from tools.recipes.data import load_recipes, get_current_ingredient_prices
    from tools.recipes.toast_link import compute_theoretical_food_cost
    from datetime import datetime, timedelta

    # Use yesterday (today's data may not be complete)
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
    date_str = request.args.get("date", yesterday)

    try:
        recipes = load_recipes()
        prices = get_current_ingredient_prices(RECIPE_ITEMS_DB, RECIPE_PRICES_DB)
        result = compute_theoretical_food_cost(date_str, recipes, prices)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify(result)


@app.route('/api/recipes/books')
@owner_required
def recipes_books_api():
    """Return list of unique recipe book names."""
    from tools.recipes.data import get_recipe_books
    return jsonify(get_recipe_books())


@app.route('/api/recipes/menu-coverage')
@owner_required
def recipes_menu_coverage_api():
    """Full menu coverage: all Toast items vs recipe database."""
    from tools.recipes.menu_coverage import scan_sales_data, get_coverage_summary
    try:
        items = scan_sales_data()
        summary = get_coverage_summary(items)
        return jsonify({"items": items, "summary": summary})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/recipes/toast-items')
@owner_required
def recipes_toast_items_api():
    """Return list of Toast POS menu item names for linking recipes."""
    from tools.recipes.menu_coverage import scan_sales_data
    try:
        items = scan_sales_data()
        return jsonify([{"name": i["name"], "category": i["category"],
                         "avg_daily": i["avg_daily_qty"], "revenue": i["total_revenue"]}
                        for i in items])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/recipes/ingredient-report')
@owner_required
def recipes_ingredient_report_api():
    """Detailed ingredient match report: which recipe ingredients have Notion prices."""
    from tools.recipes.data import load_recipes, get_current_ingredient_prices

    try:
        recipes = load_recipes()
        prices = get_current_ingredient_prices(RECIPE_ITEMS_DB, RECIPE_PRICES_DB)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    # Build per-ingredient report across all recipes
    seen = {}  # ingredient_name -> {matched, recipes, vendor, unit_cost}
    for r in recipes:
        for ing in r.get("ingredients", []):
            name = ing.get("item", "").strip().lower()
            if not name:
                continue
            if name not in seen:
                price_info = prices.get(name, {})
                seen[name] = {
                    "name": ing.get("item", "").strip(),
                    "matched": bool(price_info.get("price_per_unit")),
                    "unit_cost": price_info.get("price_per_unit", 0),
                    "vendor": price_info.get("vendor", ""),
                    "unit": price_info.get("unit", ing.get("uom", "")),
                    "recipes": [],
                }
            seen[name]["recipes"].append(r.get("name", ""))

    items = sorted(seen.values(), key=lambda x: (x["matched"], x["name"]))
    matched = sum(1 for i in items if i["matched"])
    unmatched = sum(1 for i in items if not i["matched"])

    return jsonify({
        "total": len(items),
        "matched": matched,
        "unmatched": unmatched,
        "match_pct": round(matched / max(len(items), 1) * 100, 1),
        "items": items,
    })


# ── Modifiers API ──

@app.route('/api/modifiers/data')
@owner_required
def modifiers_data_api():
    """Return all modifiers with cost data."""
    from tools.recipes.data import load_modifiers, get_current_ingredient_prices, calculate_recipe_cost
    from copy import deepcopy
    mods = load_modifiers()
    try:
        prices = get_current_ingredient_prices(RECIPE_ITEMS_DB, RECIPE_PRICES_DB)
    except Exception:
        prices = {}
    results = []
    for mod in mods:
        fake_recipe = {
            "ingredients": mod.get("ingredients", []),
            "portions": 1,
            "menu_price": mod.get("menu_price", 0),
        }
        cost_data = calculate_recipe_cost(fake_recipe, prices)
        merged = deepcopy(mod)
        merged["cost"] = cost_data
        results.append(merged)
    return jsonify(results)


@app.route('/api/modifiers/save', methods=['POST'])
@owner_required
def modifiers_save_api():
    """Create or update a modifier."""
    from tools.recipes.data import save_modifier
    body = request.json or {}
    if not body.get("name"):
        return jsonify({"error": "Modifier name required"}), 400
    saved = save_modifier(body)
    return jsonify(saved)


@app.route('/api/modifiers/delete', methods=['POST'])
@owner_required
def modifiers_delete_api():
    """Delete a modifier by ID."""
    from tools.recipes.data import delete_modifier
    body = request.json or {}
    mid = body.get("id", "")
    if not mid:
        return jsonify({"error": "Modifier ID required"}), 400
    if delete_modifier(mid):
        return jsonify({"status": "ok"})
    return jsonify({"error": "Modifier not found"}), 404


# ── Claude Chat ──

def _format_metrics_context(metrics):
    """Format metrics dict into concise text context for Claude."""
    lines = []
    r = metrics.get('revenue', {})
    if r:
        lines.append(f"Revenue: ${r.get('toast_total', 0):,.2f}")
        lines.append(f"Orders: {r.get('total_orders', 0)}")
        lines.append(f"Avg Check: ${r.get('avg_check', 0):,.2f}")
        lines.append(f"Guests: {r.get('total_guests', 0)}")
        channels = r.get('channels', {})
        if channels:
            # Handle both single-day (float) and aggregated (dict with 'total') channel values
            def _ch_val(v):
                if isinstance(v, dict):
                    return v.get('total', 0)
                return v if isinstance(v, (int, float)) else 0
            ch_parts = [f"  {k}: ${_ch_val(v):,.2f}" for k, v in sorted(channels.items(), key=lambda x: -_ch_val(x[1])) if _ch_val(v) > 0]
            if ch_parts:
                lines.append("Revenue by Channel:\n" + "\n".join(ch_parts))
        top_items = r.get('top_items_by_revenue', [])
        if top_items:
            items_parts = [f"  {it.get('item','?')}: ${it.get('revenue',0):,.2f} ({it.get('qty',0)} sold)" for it in top_items[:10]]
            lines.append("Top Items:\n" + "\n".join(items_parts))
        hourly = r.get('hourly_revenue', {})
        if hourly:
            hr_parts = [f"  {h}:00: ${v:,.2f}" for h, v in sorted(hourly.items(), key=lambda x: int(x[0])) if v > 0]
            if hr_parts:
                lines.append("Revenue by Hour:\n" + "\n".join(hr_parts))

    l = metrics.get('labor', {})
    if l:
        lines.append(f"\nLabor Cost: ${l.get('total_labor', 0):,.2f}")
        lines.append(f"Labor %: {l.get('labor_pct', 0):.1f}%")
        lines.append(f"Total Hours: {l.get('total_hours', 0):.1f}")
        staff = l.get('staff_detail', [])
        if staff:
            staff_parts = [f"  {s.get('name','?')}: ${s.get('total_pay',0):,.2f} ({s.get('hours',0):.1f}h)" for s in staff[:10]]
            lines.append("Staff:\n" + "\n".join(staff_parts))

    k = metrics.get('kitchen', {})
    if k:
        stations = k.get('stations', {})
        if stations:
            kit_parts = [f"  {st}: p50={d.get('p50',0):.0f}s, p90={d.get('p90',0):.0f}s" for st, d in stations.items()]
            lines.append("\nKitchen Speed:\n" + "\n".join(kit_parts))

    c = metrics.get('customers', {})
    if c:
        lines.append(f"\nUnique Customers: {c.get('unique_customers', 0)}")
        lines.append(f"Phone Capture: {c.get('phone_capture_pct', 0):.0f}%")

    p = metrics.get('payments', {})
    if p:
        card_mix = p.get('card_type_mix', {})
        if card_mix:
            pay_parts = [f"  {k}: ${v:,.2f}" for k, v in sorted(card_mix.items(), key=lambda x: -x[1])]
            lines.append("\nPayment Mix:\n" + "\n".join(pay_parts))

    w = metrics.get('weather', {})
    if w:
        lines.append(f"\nWeather: {w.get('conditions', '?')}, {w.get('temp_high', '?')}F/{w.get('temp_low', '?')}F")
        if w.get('precipitation_inches', 0) > 0:
            lines.append(f"Precipitation: {w.get('precipitation_inches', 0):.2f} inches")
        if w.get('snow_inches', 0) > 0:
            lines.append(f"Snow: {w.get('snow_inches', 0):.1f} inches")
        lines.append(f"Sunset: {w.get('sunset', '?')}")
        if w.get('weather_impact_pct') is not None:
            lines.append(f"Weather Impact: Bad weather days average {w.get('weather_impact_pct'):+.1f}% vs clear days")
        events = w.get('events', [])
        if events:
            lines.append(f"Events: {', '.join(e.get('name', '') for e in events)}")

    dd = metrics.get('date_display', '')
    dow = metrics.get('day_of_week', '')
    header = f"Date: {dd}" + (f" ({dow})" if dow else "")
    return header + "\n" + "\n".join(lines)


import re as _re


def _extract_date_from_question(question):
    """Try to parse dates from natural language questions."""
    now = datetime.now()
    q = question.lower().strip()

    # "yesterday"
    if 'yesterday' in q:
        return (now - timedelta(days=1)).strftime("%Y%m%d")
    # "today"
    if 'today' in q:
        return now.strftime("%Y%m%d")
    # "last week" -> previous Mon-Sun
    if 'last week' in q:
        days_since_mon = now.weekday()
        last_mon = now - timedelta(days=days_since_mon + 7)
        last_sun = last_mon + timedelta(days=6)
        return last_mon.strftime("%Y%m%d") + "_" + last_sun.strftime("%Y%m%d")
    # "this week" -> this Mon through yesterday
    if 'this week' in q:
        days_since_mon = now.weekday()
        this_mon = now - timedelta(days=days_since_mon)
        yest = now - timedelta(days=1)
        if yest >= this_mon:
            return this_mon.strftime("%Y%m%d") + "_" + yest.strftime("%Y%m%d")
        return this_mon.strftime("%Y%m%d")
    # "last month"
    if 'last month' in q:
        first_this = now.replace(day=1)
        last_day_prev = first_this - timedelta(days=1)
        first_prev = last_day_prev.replace(day=1)
        return first_prev.strftime("%Y%m%d") + "_" + last_day_prev.strftime("%Y%m%d")

    # M/D-M/D or M/D - M/D patterns (e.g., 1/15-1/22)
    range_pat = _re.search(r'(\d{1,2})/(\d{1,2})\s*[-–]\s*(\d{1,2})/(\d{1,2})', q)
    if range_pat:
        m1, d1, m2, d2 = [int(x) for x in range_pat.groups()]
        y = now.year
        try:
            dt1 = datetime(y, m1, d1)
            dt2 = datetime(y, m2, d2)
            if dt2 > now:
                dt1 = datetime(y - 1, m1, d1)
                dt2 = datetime(y - 1, m2, d2)
            return dt1.strftime("%Y%m%d") + "_" + dt2.strftime("%Y%m%d")
        except ValueError:
            pass

    # Single M/D pattern (e.g., 1/15)
    single_pat = _re.search(r'(\d{1,2})/(\d{1,2})(?!\d)', q)
    if single_pat:
        m, d = int(single_pat.group(1)), int(single_pat.group(2))
        y = now.year
        try:
            dt = datetime(y, m, d)
            if dt > now:
                dt = datetime(y - 1, m, d)
            return dt.strftime("%Y%m%d")
        except ValueError:
            pass

    return ""


def _load_chat_context(date_str):
    """Load metrics context for a date or date range string."""
    if '_' in date_str:
        parts = date_str.split('_')
        if len(parts) == 2:
            # Load multiple days for the range
            try:
                dt_start = datetime.strptime(parts[0], "%Y%m%d")
                dt_end = datetime.strptime(parts[1], "%Y%m%d")
            except ValueError:
                return ""
            all_contexts = []
            d = dt_start
            days_loaded = 0
            while d <= dt_end and days_loaded < 14:  # cap at 14 days
                ds = d.strftime("%Y%m%d")
                metrics = get_cached_metrics(ds)
                if metrics is None:
                    try:
                        data = get_daily_data(d, quiet=True)
                        if 'OrderDetails' in data:
                            metrics = compute_all_metrics(data, d)
                            if not is_today(ds):
                                cache_metrics(ds, metrics)
                    except Exception as e:
                        logger.debug("Metrics load failed for %s: %s", ds, e)
                if metrics:
                    all_contexts.append(_format_metrics_context(metrics))
                    days_loaded += 1
                d += timedelta(days=1)
            if all_contexts:
                header = f"[Date range: {parts[0]} to {parts[1]}, {days_loaded} days loaded]\n\n"
                return header + "\n\n---\n\n".join(all_contexts)
            return ""
    else:
        metrics = get_cached_metrics(date_str)
        if metrics is None:
            try:
                date = datetime.strptime(date_str, "%Y%m%d")
                data = get_daily_data(date, quiet=True)
                if 'OrderDetails' in data:
                    metrics = compute_all_metrics(data, date)
                    if not is_today(date_str):
                        cache_metrics(date_str, metrics)
            except Exception as e:
                logger.debug("Metrics load failed for %s: %s", date_str, e)
        if metrics:
            return _format_metrics_context(metrics)
    return ""


CHAT_SYSTEM_PROMPT = """You are Laurie 2.0 — an AI assistant for Livite, a fast-casual restaurant in Brookline, MA. \
You answer questions about the restaurant's daily sales data and operations.

Your job is to be fast, clear, and useful. Lead with the answer, then add context.

Rules:
- Start with the key number or answer immediately. No preamble.
- Use dollar amounts and percentages.
- Keep responses short: 2-4 sentences for simple questions, a short paragraph for complex ones.
- Flag anything unusual — labor over 35%, revenue drops over 15%, missing data.
- Compare to benchmarks when relevant (labor target: 30-35%, food cost: 35%).
- If you spot something the owner should act on, say so directly.

Examples:
- "Revenue was $4,218, up 8% from last week. Labor at 31% — right on target. Top seller: Chipotle Chicken Wrap (47 units)."
- "Tuesday was $3,850 across 142 orders. Labor hit 38% — worth checking the schedule. Two new employees (Catano, Vasquez) aren't in the wage table yet."
- "DoorDash brought in $2,100 (40% of total). Uber was $1,800. Walk-in was only $1,334 — down 22% from last Tuesday."

You have access to the restaurant's metrics data which will be provided as context. \
The restaurant owners are your audience — give them what they need to make decisions.

If asked about something not in the data, say so honestly."""


@app.route('/api/chat', methods=['POST'])
@(limiter.limit("20/minute") if limiter else lambda f: f)
@owner_required
def api_chat():
    try:
        client = _get_anthropic()
        if not client:
            return jsonify({"error": "Chat not configured. Set ANTHROPIC_API_KEY in environment."}), 503

        body = request.get_json(silent=True) or {}
        question = body.get('question', '')
        if not isinstance(question, str):
            return jsonify({"error": "Invalid input."}), 400
        question = question.strip()
        if not question:
            return jsonify({"error": "No question provided."}), 400

        date_str = body.get('date', '')
        model_pref = body.get('model', 'haiku')

        # Resolve model ID
        if model_pref == 'sonnet':
            model_id = 'claude-sonnet-4-6'
        else:
            model_id = 'claude-haiku-4-5-20251001'

        # Prefer pre-computed context from dashboard page (has exact data user sees)
        context = body.get('context', '').strip()

        # Fallback: load data if no context provided (e.g. standalone /chat page)
        if not context:
            if not date_str:
                date_str = _extract_date_from_question(question)
            if not date_str:
                yesterday = datetime.now() - timedelta(days=1)
                date_str = yesterday.strftime("%Y%m%d")
            try:
                context = _load_chat_context(date_str)
            except Exception:
                context = ""

        messages = [{"role": "user", "content": question}]
        system = CHAT_SYSTEM_PROMPT
        if context:
            system += f"\n\nHere is the current dashboard data:\n\n{context}"
        else:
            system += "\n\nIMPORTANT: No data is loaded right now. Do NOT make up numbers. " \
                      "Tell the user you couldn't pull data for the requested date and suggest they " \
                      "navigate to a dashboard first, then click 'Ask Laurie 2.0' from there."

        resp = client.messages.create(
            model=model_id,
            max_tokens=1024,
            system=system,
            messages=messages,
        )
        if not resp.content:
            raise ValueError("Claude returned empty response content")
        answer = resp.content[0].text

        # Estimate cost from token usage
        usage = resp.usage
        input_tokens = getattr(usage, 'input_tokens', 0)
        output_tokens = getattr(usage, 'output_tokens', 0)
        # Pricing per million tokens (Feb 2026)
        if 'haiku' in model_id:
            cost = (input_tokens * 0.80 + output_tokens * 4.00) / 1_000_000
        else:  # sonnet
            cost = (input_tokens * 3.00 + output_tokens * 15.00) / 1_000_000
        cost_str = f"${cost:.4f}" if cost < 0.01 else f"${cost:.3f}"

        return jsonify({"answer": answer, "model": model_pref, "cost": cost_str})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/chat')
@owner_required
def chat_page():
    """Standalone chat page."""
    return _chat_page()


def _chat_page():
    """Full-page chat interface."""
    has_api = bool(ANTHROPIC_API_KEY)
    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Laurie 2.0 — Ask About Your Data</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
*{{box-sizing:border-box;}}
body{{font-family:'DM Sans',sans-serif;background:#F5EDDC;margin:0;padding:0;min-height:100vh;display:flex;flex-direction:column;}}
.header{{background:#fff;border-bottom:1px solid #e2d9c8;padding:12px 20px;display:flex;align-items:center;justify-content:space-between;}}
.header-left{{display:flex;align-items:center;gap:12px;}}
.header a{{color:#4a7c1f;text-decoration:none;font-size:13px;font-weight:500;}}
.header a:hover{{text-decoration:underline;}}
.title{{font-size:18px;font-weight:700;color:#475417;}}
.main-layout{{flex:1;display:flex;overflow:hidden;}}
.laurie-panel{{width:200px;flex-shrink:0;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;padding:20px 10px 0;position:relative;}}
.laurie-panel .laurie-name{{font-size:13px;font-weight:700;color:#475417;margin-bottom:8px;letter-spacing:0.5px;}}
.laurie-img{{width:180px;height:auto;object-fit:contain;transform-origin:bottom center;}}
.chat-col{{flex:1;display:flex;flex-direction:column;min-width:0;}}
.chat-area{{flex:1;overflow-y:auto;padding:20px;max-width:700px;width:100%;}}
.msg{{margin-bottom:16px;display:flex;gap:10px;align-items:flex-start;}}
.msg.user{{justify-content:flex-end;}}
.msg.user .bubble{{background:#4a7c1f;color:#fff;border-radius:16px 16px 4px 16px;}}
.msg.ai .bubble{{background:#fff;color:#1a2e05;border-radius:16px 16px 16px 4px;border:1px solid #e2d9c8;}}
.bubble{{padding:10px 16px;max-width:85%;font-size:14px;line-height:1.5;white-space:pre-wrap;word-wrap:break-word;}}
.typing{{color:#7a7a6f;font-size:13px;font-style:italic;padding:8px 16px;}}
.input-area{{background:#fff;border-top:1px solid #e2d9c8;padding:12px 20px;}}
.input-row{{max-width:700px;display:flex;gap:8px;align-items:flex-end;}}
.input-row textarea{{flex:1;padding:10px 14px;font-size:14px;border:1px solid #e2d9c8;border-radius:10px;background:#faf6ee;color:#1a2e05;font-family:inherit;resize:none;min-height:44px;max-height:120px;line-height:1.4;}}
.input-row textarea:focus{{outline:none;border-color:#4a7c1f;}}
.send-btn{{padding:10px 18px;background:#4a7c1f;color:#fff;border:none;border-radius:10px;font-size:14px;font-weight:600;cursor:pointer;font-family:inherit;white-space:nowrap;}}
.send-btn:hover{{background:#3d6819;}}
.send-btn:disabled{{background:#c4b89a;cursor:not-allowed;}}
.controls{{display:flex;align-items:center;gap:12px;max-width:700px;padding-top:6px;}}
.model-toggle{{display:flex;align-items:center;gap:6px;font-size:11px;color:#7a7a6f;}}
.model-toggle button{{padding:3px 10px;font-size:11px;border:1px solid #e2d9c8;border-radius:6px;background:#faf6ee;color:#7a7a6f;cursor:pointer;font-family:inherit;}}
.model-toggle button.active{{background:#4a7c1f;color:#fff;border-color:#4a7c1f;}}
.date-ctx{{font-size:11px;color:#7a7a6f;}}
.no-api{{text-align:center;padding:60px 20px;color:#7a7a6f;}}
.no-api h2{{color:#475417;}}

/* Laurie animations */
@keyframes laurie-sway{{
  0%,100%{{transform:rotate(0deg);}}
  25%{{transform:rotate(1.5deg);}}
  75%{{transform:rotate(-1.5deg);}}
}}
@keyframes laurie-headshake{{
  0%,100%{{transform:rotate(0deg);}}
  15%{{transform:rotate(2deg);}}
  30%{{transform:rotate(-2deg);}}
  45%{{transform:rotate(1.5deg);}}
  60%{{transform:rotate(-1deg);}}
  75%{{transform:rotate(0deg);}}
}}
@keyframes laurie-breathe{{
  0%,100%{{transform:scale(1);}}
  50%{{transform:scale(1.015);}}
}}
.anim-sway{{animation:laurie-sway 2.5s ease-in-out infinite;}}
.anim-headshake{{animation:laurie-headshake 1.8s ease-in-out infinite;}}
.anim-breathe{{animation:laurie-breathe 3s ease-in-out infinite;}}

/* Mobile: hide laurie panel, stack vertically */
@media(max-width:768px){{
  .laurie-panel{{display:none;}}
  .main-layout{{flex-direction:column;}}
}}
</style></head>
<body>
<div class="header">
<div class="header-left">
<img src="/static/avatar.png" style="width:32px;height:32px;border-radius:50%;object-fit:cover;">
<span class="title">Laurie 2.0</span>
</div>
<a href="/">Dashboard Home</a>
</div>

{'<div class="no-api"><h2>Chat Not Available</h2><p>Set ANTHROPIC_API_KEY in your environment to enable AI chat.</p><p><a href="/">Back to Dashboard</a></p></div>' if not has_api else ''}

<div class="main-layout" {'style="display:none;"' if not has_api else ''}>
<div class="laurie-panel">
<span class="laurie-name">LAURIE 2.0</span>
<img src="/static/avatar.png" class="laurie-img anim-breathe" id="laurieImg">
</div>
<div class="chat-col">
<div class="chat-area" id="chat">
<div class="msg ai"><div class="bubble">Oh good, you're here. I've been sitting with these numbers ALL day waiting for someone to ask.\n\nGo ahead, ask me something. I'm not busy or anything.\n\n\u2022 "How did we do yesterday?"\n\u2022 "What were our top 5 items last week?"\n\u2022 "What was our busiest hour on Monday?"\n\u2022 "How's labor looking compared to revenue?"\n\nI'll be right here. Like always.</div></div>
</div>

<div class="input-area" id="inputArea">
<div class="input-row">
<textarea id="question" placeholder="Ask about your sales data..." rows="1" onkeydown="if(event.key==='Enter'&&!event.shiftKey){{event.preventDefault();sendChat();}}"></textarea>
<button class="send-btn" id="sendBtn" onclick="sendChat()">Send</button>
</div>
<div class="controls">
<div class="model-toggle">
Model:
<button id="btn-haiku" class="active" onclick="setModel('haiku')">Haiku</button>
<button id="btn-sonnet" onclick="setModel('sonnet')">Sonnet</button>
</div>
<div class="date-ctx" id="dateCtx"></div>
</div>
</div>
</div><!-- /chat-col -->
</div><!-- /main-layout -->

<script>
var currentModel = 'haiku';
var currentDate = '';

// Try to detect date context from referrer or URL params
(function() {{
    var params = new URLSearchParams(window.location.search);
    var d = params.get('date');
    if (d) {{
        currentDate = d;
        document.getElementById('dateCtx').textContent = 'Context: ' + d;
    }}
}})();

function setModel(m) {{
    currentModel = m;
    document.getElementById('btn-haiku').className = m === 'haiku' ? 'active' : '';
    document.getElementById('btn-sonnet').className = m === 'sonnet' ? 'active' : '';
}}

// Laurie animation cycling
var laurieAnims = ['anim-sway', 'anim-headshake', 'anim-breathe'];
var laurieAnimIdx = 2; // start on breathe
function cycleLaurieAnim() {{
    var img = document.getElementById('laurieImg');
    if (!img) return;
    img.className = 'laurie-img ' + laurieAnims[laurieAnimIdx];
    laurieAnimIdx = (laurieAnimIdx + 1) % laurieAnims.length;
}}
setInterval(cycleLaurieAnim, 6000);

// Trigger headshake when AI responds
function laurieReact() {{
    var img = document.getElementById('laurieImg');
    if (!img) return;
    img.className = 'laurie-img anim-headshake';
    setTimeout(function() {{
        img.className = 'laurie-img anim-breathe';
    }}, 3000);
}}

function addMessage(role, text) {{
    var chat = document.getElementById('chat');
    var div = document.createElement('div');
    div.className = 'msg ' + role;
    var bubble = document.createElement('div');
    bubble.className = 'bubble';
    bubble.textContent = text;
    div.appendChild(bubble);
    chat.appendChild(div);
    chat.scrollTop = chat.scrollHeight;
    if (role === 'ai') laurieReact();
}}

function sendChat() {{
    var input = document.getElementById('question');
    var q = input.value.trim();
    if (!q) return;

    addMessage('user', q);
    input.value = '';
    input.style.height = '44px';

    var btn = document.getElementById('sendBtn');
    btn.disabled = true;

    // Show typing indicator
    var chat = document.getElementById('chat');
    var typing = document.createElement('div');
    typing.className = 'typing';
    typing.id = 'typing';
    typing.textContent = 'Thinking...';
    chat.appendChild(typing);
    chat.scrollTop = chat.scrollHeight;
    // Laurie sways impatiently while thinking
    var limg = document.getElementById('laurieImg');
    if (limg) limg.className = 'laurie-img anim-sway';

    fetch('/api/chat', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{question: q, date: currentDate, model: currentModel}})
    }})
    .then(function(r) {{
        if (!r.ok) {{ return r.text().then(function(t) {{ throw new Error('HTTP ' + r.status + ': ' + t.substring(0, 200)); }}); }}
        return r.json();
    }})
    .then(function(data) {{
        var t = document.getElementById('typing');
        if (t) t.remove();
        if (data.error) {{
            addMessage('ai', 'Error: ' + data.error);
        }} else {{
            addMessage('ai', data.answer);
        }}
        btn.disabled = false;
    }})
    .catch(function(err) {{
        var t = document.getElementById('typing');
        if (t) t.remove();
        addMessage('ai', 'Error: ' + (err.message || 'Something went wrong'));
        btn.disabled = false;
    }});
}}

// Auto-resize textarea
document.getElementById('question').addEventListener('input', function() {{
    this.style.height = '44px';
    this.style.height = Math.min(this.scrollHeight, 120) + 'px';
}});
</script>
</body></html>"""


# ── Daily Profit Calculator ──

PROFIT_MANUAL_DIR = os.path.join(PROJECT_ROOT, '.tmp', 'profit_manual')
os.makedirs(PROFIT_MANUAL_DIR, exist_ok=True)
NOTION_PROFIT_DB_ID = os.getenv("NOTION_PROFIT_DB_ID", "")

# Fixed cost schedule — loaded from config.yaml
def _get_fixed_cost(date_str: str) -> float:
    """Return the daily fixed cost for a given date (from config.yaml)."""
    cfg_path = os.path.join(os.path.dirname(__file__), 'config.yaml')
    try:
        with open(cfg_path) as f:
            cfg = yaml.safe_load(f)
        fc = cfg.get("fixed_costs", {})
        schedule = fc.get("schedule", [])
        default = fc.get("default", 1150)
        for entry in schedule:
            if date_str >= str(entry.get("date", "")):
                return float(entry.get("amount", default))
        return float(default)
    except Exception:
        return 1150.0


def _load_manual_data(date_str: str) -> dict:
    """Load manually-entered profit fields for a date.

    Tries local JSON first, falls back to Notion.
    """
    path = os.path.join(PROFIT_MANUAL_DIR, f'{date_str}.json')
    if os.path.exists(path):
        with open(path) as f:
            return json.loads(f.read())

    # Fallback: load from Notion
    if NOTION_PROFIT_DB_ID:
        try:
            from profit_notion import get_daily_profit
            notion_data = get_daily_profit(NOTION_PROFIT_DB_ID, date_str)
            if notion_data:
                # Extract manual fields from Notion data
                manual_keys = [
                    "forkable", "ezcater", "fixed", "vacation", "sick",
                    "misc", "gh_fees", "dd_fees", "uber_fees",
                    "uber_ads", "notes",
                    "vacation_detail", "sick_detail",
                ]
                manual = {k: notion_data[k] for k in manual_keys if k in notion_data and notion_data[k]}
                # If we have detail arrays, use them as the vacation/sick values
                if manual.get("vacation_detail"):
                    manual["vacation"] = manual.pop("vacation_detail")
                if manual.get("sick_detail"):
                    manual["sick"] = manual.pop("sick_detail")
                if manual:
                    # Cache locally
                    with open(path, 'w') as f:
                        f.write(json.dumps(manual))
                    return manual
        except Exception as e:
            logger.warning("Notion profit load failed for %s: %s", date_str, e)

    return {}


def _save_manual_data(date_str: str, data: dict):
    """Save manually-entered profit fields for a date (local + Notion)."""
    path = os.path.join(PROFIT_MANUAL_DIR, f'{date_str}.json')
    existing = _load_manual_data(date_str)
    existing.update(data)
    with open(path, 'w') as f:
        f.write(json.dumps(existing))


def _sync_to_notion(date_str: str, full_data: dict):
    """Sync combined profit data (auto + manual) to Notion."""
    if not NOTION_PROFIT_DB_ID:
        return
    from profit_notion import upsert_daily_profit

    # Compute derived fields so they're stored in Notion
    m = full_data.get("manual", {})
    ca = full_data.get("catering_auto", {})
    toast = full_data.get("toast_total", 0)
    forkable = m.get("forkable") if m.get("forkable") else ca.get("forkable", 0)
    ezcater = m.get("ezcater") if m.get("ezcater") else ca.get("ezcater", 0)
    toast_discounts = full_data.get("bogo_discount", 0)
    full_data["toast_discounts"] = toast_discounts
    total_sales = toast + forkable + ezcater

    catering_fees = round(forkable * 0.125, 2) + round(ezcater * 0.20, 2)
    fees = m.get("gh_fees", 0) + m.get("dd_fees", 0) + m.get("uber_fees", 0) + catering_fees + m.get("shipday_fees", 0)
    # Food cost: 35% of total sales (toast already includes Uber discounts)
    food_cost = round(total_sales * 0.35, 2)
    labor = full_data.get("labor", 0)
    # Vacation/sick: support both array (new) and number (old) formats
    vac_raw = m.get("vacation", 0)
    sick_raw = m.get("sick", 0)
    vacation_total = sum(e.get("cost", 0) for e in vac_raw) if isinstance(vac_raw, list) else (vac_raw or 0)
    sick_total = sum(e.get("cost", 0) for e in sick_raw) if isinstance(sick_raw, list) else (sick_raw or 0)
    full_data["vacation"] = round(vacation_total, 2)
    full_data["sick"] = round(sick_total, 2)
    # Store detail for Notion rich text fields
    if isinstance(vac_raw, list):
        full_data["vacation_detail"] = vac_raw
    if isinstance(sick_raw, list):
        full_data["sick_detail"] = sick_raw
    date_for_fixed = full_data.get("date", "")
    fixed_val = m.get("fixed") if "fixed" in m else _get_fixed_cost(date_for_fixed)
    profit = total_sales - labor - vacation_total - sick_total - m.get("misc", 0) - fixed_val - fees - food_cost
    pct = (profit / total_sales * 100) if total_sales > 0 else 0

    full_data["total_sales"] = round(total_sales, 2)
    full_data["service_fees"] = round(fees, 2)
    full_data["food_cost"] = round(food_cost, 2)
    full_data["profit"] = round(profit, 2)
    full_data["profit_pct"] = round(pct, 2)

    upsert_daily_profit(NOTION_PROFIT_DB_ID, full_data)


def _get_profit_data(date_str: str) -> dict:
    """Get combined auto + manual profit data for a single date."""
    from calc_daily_profit import calc_daily_profit
    dt = datetime.strptime(date_str, "%Y%m%d")
    try:
        auto = calc_daily_profit(dt)
    except Exception as e:
        auto = None

    manual = _load_manual_data(date_str)

    if auto is None:
        return {"date": date_str, "error": "No Toast data available", "manual": manual}

    # Build combined result
    result = {
        "date": date_str,
        "display_date": dt.strftime("%m/%d/%Y"),
        "day_of_week": dt.strftime("%A"),
        # Auto fields
        "toast_total": auto.get("Toast Total", 0),
        "labor": auto.get("Labor", 0),
        "food_cost": auto.get("Food Cost", 0),
        "tds_fees": auto.get("Toast TDS Fees", 0),
        "ot_hours": auto.get("OT Hours", 0),
        "ot_pay": auto.get("OT Labor", 0),
        "total_hours": auto.get("TOTAL LABOR HOURS", 0),
        "ftes": auto.get("FTEs", 0),
        "blended_rate": auto.get("Blended Rate", 0),
        "payroll_taxes": auto.get("Payroll Taxes", 0),
        # BOGO
        "bogo_discount": auto.get("BOGO Discount", 0),
        # Online delivery count (for Shipday fees)
        "online_delivery_orders": auto.get("Online Delivery Orders", 0),
        # Channels
        "channels": {},
        # Per-employee labor
        "hourly_records": auto.get("hourly_records", []),
    }

    # Collect channel data
    channel_keys = [
        "DD Delivery", "DD Takeout", "GH Takeout", "Online", "Phone",
        "Online Ordering - Delivery", "To Go", "Uber Delivery", "Uber Takeout", "No Dining",
    ]
    for ch in channel_keys:
        val = auto.get(ch, 0)
        if val:
            result["channels"][ch] = val

    # Auto-pull catering revenue from Notion
    try:
        from catering.notion import get_catering_for_date
        iso_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
        catering = get_catering_for_date(iso_date)
        result["catering_auto"] = catering  # {forkable, ezcater}
    except Exception as e:
        logger.warning("Catering auto-pull failed for %s: %s", date_str, e)
        result["catering_auto"] = {}

    # Merge manual fields
    result["manual"] = manual

    return result


def _weekly_pl_page(week_str: str) -> str:
    """Generate the Weekly P&L page for a given ISO week (e.g. 2026-W09).

    Aggregates daily profit data Mon-Sun, shows invoice-based COGS alongside
    theoretical 35%, and displays a complete P&L statement.
    """
    import re as _re
    m = _re.match(r"(\d{4})-W(\d{2})", week_str)
    if not m:
        return _error_page(f"Invalid week: {week_str}. Use YYYY-WNN format.")

    year, week_num = int(m.group(1)), int(m.group(2))

    # Monday of this ISO week
    from datetime import date as _date
    jan4 = _date(year, 1, 4)  # Jan 4 is always in ISO week 1
    monday = jan4 + timedelta(weeks=week_num - 1, days=-jan4.weekday())
    sunday = monday + timedelta(days=6)

    # Prev / next week strings
    prev_mon = monday - timedelta(days=7)
    prev_iso = prev_mon.isocalendar()
    prev_week = f"{prev_iso[0]}-W{prev_iso[1]:02d}"
    next_mon = monday + timedelta(days=7)
    next_iso = next_mon.isocalendar()
    next_week = f"{next_iso[0]}-W{next_iso[1]:02d}"
    today = datetime.now().date()
    show_next = next_mon <= today

    # Aggregate daily data
    from collections import defaultdict
    agg = defaultdict(float)
    day_rows = []
    valid_days = 0

    for i in range(7):
        dt = monday + timedelta(days=i)
        ds = dt.strftime("%Y%m%d")

        if dt > today:
            day_rows.append({"date": ds, "display": dt.strftime("%m/%d"), "day": dt.strftime("%a"),
                             "future": True})
            continue

        data = _get_profit_data(ds)
        if data.get("error"):
            day_rows.append({"date": ds, "display": dt.strftime("%m/%d"), "day": dt.strftime("%a"),
                             "error": True})
            continue

        valid_days += 1
        m_data = data.get("manual", {})
        ca = data.get("catering_auto", {})
        toast = data.get("toast_total", 0)
        forkable = m_data.get("forkable") if m_data.get("forkable") else ca.get("forkable", 0)
        ezcater = m_data.get("ezcater") if m_data.get("ezcater") else ca.get("ezcater", 0)
        total_sales = toast + forkable + ezcater
        labor = data.get("labor", 0)
        payroll_taxes = data.get("payroll_taxes", 0)
        food_cost = round(total_sales * 0.35, 2)
        fixed = m_data.get("fixed") if "fixed" in m_data else _get_fixed_cost(ds)
        vac_raw = m_data.get("vacation", 0)
        sick_raw = m_data.get("sick", 0)
        vac_v = sum(e.get("cost", 0) for e in vac_raw) if isinstance(vac_raw, list) else (vac_raw or 0)
        sick_v = sum(e.get("cost", 0) for e in sick_raw) if isinstance(sick_raw, list) else (sick_raw or 0)
        misc = m_data.get("misc", 0)
        cat_fees = round(forkable * 0.125, 2) + round(ezcater * 0.20, 2)
        gh = m_data.get("gh_fees", 0)
        dd = m_data.get("dd_fees", 0)
        ub = m_data.get("uber_fees", 0)
        ub_ads = m_data.get("uber_ads", 0)
        ship = m_data.get("shipday_fees", 0)
        fees = gh + dd + ub + ub_ads + cat_fees + ship
        profit = total_sales - labor - vac_v - sick_v - misc - fixed - fees - food_cost
        pct = (profit / total_sales * 100) if total_sales > 0 else 0

        day_rows.append({
            "date": ds, "display": dt.strftime("%m/%d"), "day": dt.strftime("%a"),
            "sales": total_sales, "profit": profit, "pct": pct,
        })

        agg["toast"] += toast
        agg["forkable"] += forkable
        agg["ezcater"] += ezcater
        agg["revenue"] += total_sales
        agg["labor"] += labor - payroll_taxes
        agg["payroll_taxes"] += payroll_taxes
        agg["food_cost"] += food_cost
        agg["fixed"] += fixed
        agg["vacation"] += vac_v
        agg["sick"] += sick_v
        agg["misc"] += misc
        agg["gh_fees"] += gh
        agg["dd_fees"] += dd
        agg["uber_fees"] += ub
        agg["uber_ads"] += ub_ads
        agg["catering_fees"] += cat_fees
        agg["shipday_fees"] += ship
        agg["fees"] += fees
        agg["profit"] += profit

    # Invoice-based COGS for this week
    invoice_cogs = 0
    invoice_count = 0
    try:
        from invoices.tools.invoice_store import get_week_total
        inv_data = get_week_total(week_str)
        invoice_cogs = inv_data.get("total", 0)
        invoice_count = inv_data.get("invoice_count", 0)
    except Exception as e:
        logger.warning("Invoice COGS fetch failed for %s: %s", week_str, e)

    rev = agg["revenue"]
    theo_food = agg["food_cost"]
    has_invoices = invoice_cogs > 0
    actual_cogs = invoice_cogs if has_invoices else theo_food
    gross_profit = rev - actual_cogs
    gross_pct = (gross_profit / rev * 100) if rev > 0 else 0
    total_labor = agg["labor"] + agg["payroll_taxes"]
    total_opex = total_labor + agg["vacation"] + agg["sick"] + agg["fixed"] + agg["misc"]
    total_fees = agg["fees"]
    net_profit = rev - actual_cogs - total_opex - total_fees
    net_pct = (net_profit / rev * 100) if rev > 0 else 0
    profit_color = "#4a7c1f" if net_profit >= 0 else "#d9342b"

    def _f(v):
        return f"${v:,.2f}" if v >= 0 else f"-${abs(v):,.2f}"

    # COGS line — show actual from invoices OR theoretical with indicator
    if has_invoices:
        cogs_label = f"Purchases ({invoice_count} invoice{'s' if invoice_count != 1 else ''})"
        cogs_note = f'<div style="font-size:0.75rem;color:#999;margin-top:2px;">Theoretical 35%: {_f(theo_food)}</div>'
    else:
        cogs_label = "Food Cost (est. 35%)"
        cogs_note = '<div style="font-size:0.75rem;color:#e67e22;margin-top:2px;">No invoices — using 35% estimate</div>'

    # Daily breakdown rows
    daily_html = ""
    for dr in day_rows:
        if dr.get("future"):
            daily_html += f'<tr style="color:#ccc;"><td>{dr["day"]} {dr["display"]}</td><td colspan="2" style="text-align:center;">—</td></tr>'
        elif dr.get("error"):
            daily_html += f'<tr style="color:#999;"><td>{dr["day"]} {dr["display"]}</td><td colspan="2" style="text-align:center;">No data</td></tr>'
        else:
            pc = dr["pct"]
            pc_color = "#4a7c1f" if pc >= 0 else "#d9342b"
            daily_html += f'''<tr onclick="window.location='/profit/{dr["date"]}'" style="cursor:pointer;">
  <td>{dr["day"]} {dr["display"]}</td>
  <td style="text-align:right;font-family:'JetBrains Mono',monospace;">{_f(dr["sales"])}</td>
  <td style="text-align:right;font-family:'JetBrains Mono',monospace;color:{pc_color};font-weight:600;">{_f(dr["profit"])} <span style="font-size:0.8rem;font-weight:400;">({pc:.0f}%)</span></td>
</tr>'''

    # Nav
    nav_next = f'<a href="/profit/weekly/{next_week}" style="color:#4a7c1f;text-decoration:none;">Next &#9654;</a>' if show_next else '<span style="color:#ccc;">Next &#9654;</span>'

    page = f"""<!DOCTYPE html>
<html><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Weekly P&L — {week_str}</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{font-family:'DM Sans',-apple-system,sans-serif;background:#F5EDDC;color:#333;min-height:100vh;}}
.topnav{{background:#475417;color:white;padding:0.8rem 2rem;display:flex;justify-content:space-between;align-items:center;}}
.topnav a{{color:#F5EDDC;text-decoration:none;margin-left:1.2rem;font-size:0.85rem;}}
.topnav h1{{font-size:1.2rem;font-weight:600;}}
.container{{max-width:700px;margin:1.5rem auto;padding:0 1.5rem;}}
.card{{background:white;border-radius:12px;padding:1.5rem;margin-bottom:1rem;box-shadow:0 2px 8px rgba(0,0,0,0.06);}}
.nav-row{{display:flex;justify-content:space-between;align-items:center;margin-bottom:1rem;}}
.pl-row{{display:flex;justify-content:space-between;padding:6px 0;font-size:0.9rem;}}
.pl-row.indent{{padding-left:1.2rem;}}
.pl-row .label{{color:#555;}}
.pl-row .value{{font-family:'JetBrains Mono',monospace;font-weight:500;text-align:right;}}
.pl-row.total{{font-weight:700;border-top:2px solid #e0d5bf;padding-top:8px;margin-top:4px;}}
.pl-row.grand{{font-weight:700;font-size:1.1rem;border-top:3px solid #475417;padding-top:10px;margin-top:8px;}}
.section-header{{font-size:0.75rem;font-weight:600;color:#7a7265;text-transform:uppercase;letter-spacing:0.5px;padding:12px 0 4px;}}
table{{width:100%;border-collapse:collapse;font-size:0.9rem;}}
th{{text-align:left;font-size:0.75rem;font-weight:600;color:#7a7265;text-transform:uppercase;letter-spacing:0.5px;padding:8px 6px;border-bottom:2px solid #e0d5bf;}}
td{{padding:8px 6px;border-bottom:1px solid #f0e8d6;}}
tr:hover{{background:#faf6ee;}}
</style>
</head><body>
<div class="topnav">
  <h1>Weekly P&L</h1>
  <div>
    <a href="/invoices/">Invoices</a>
    <a href="/invoices/new">+ Invoice</a>
    <a href="/prices/">Prices</a>
    <a href="/profit">Daily P&L</a>
    <a href="/">Dashboard</a>
  </div>
</div>
<div class="container">

<div class="nav-row">
  <a href="/profit/weekly/{prev_week}" style="color:#4a7c1f;text-decoration:none;">&#9664; Prev</a>
  <div style="text-align:center;">
    <div style="font-size:1.1rem;font-weight:700;color:#475417;">
      {monday.strftime("%b %d")} – {sunday.strftime("%b %d, %Y")}
    </div>
    <div style="font-size:0.8rem;color:#7a7265;">{week_str} &middot; {valid_days} day{"s" if valid_days != 1 else ""} with data</div>
  </div>
  {nav_next}
</div>

<div class="card">
  <div class="section-header">Revenue</div>
  <div class="pl-row indent"><span class="label">Toast POS</span><span class="value">{_f(agg["toast"])}</span></div>
  <div class="pl-row indent"><span class="label">Forkable</span><span class="value">{_f(agg["forkable"])}</span></div>
  <div class="pl-row indent"><span class="label">EZ Cater</span><span class="value">{_f(agg["ezcater"])}</span></div>
  <div class="pl-row total"><span class="label">Total Revenue</span><span class="value">{_f(rev)}</span></div>

  <div class="section-header">Cost of Goods Sold</div>
  <div class="pl-row indent"><span class="label">{cogs_label}</span><span class="value">{_f(actual_cogs)}</span></div>
  {cogs_note}
  <div class="pl-row total"><span class="label">Gross Profit</span><span class="value">{_f(gross_profit)} <span style="font-size:0.8rem;color:#7a7265;">{gross_pct:.0f}%</span></span></div>

  <div class="section-header">Operating Expenses</div>
  <div class="pl-row indent"><span class="label">Labor</span><span class="value">{_f(agg["labor"])}</span></div>
  <div class="pl-row indent"><span class="label">Payroll Taxes</span><span class="value">{_f(agg["payroll_taxes"])}</span></div>
  <div class="pl-row indent"><span class="label">Fixed Costs</span><span class="value">{_f(agg["fixed"])}</span></div>
  <div class="pl-row indent"><span class="label">Vacation/Sick</span><span class="value">{_f(agg["vacation"] + agg["sick"])}</span></div>
  <div class="pl-row indent"><span class="label">Miscellaneous</span><span class="value">{_f(agg["misc"])}</span></div>
  <div class="pl-row total"><span class="label">Total OpEx</span><span class="value">{_f(total_opex)}</span></div>

  <div class="section-header">Service Fees</div>
  <div class="pl-row indent"><span class="label">DoorDash</span><span class="value">{_f(agg["dd_fees"])}</span></div>
  <div class="pl-row indent"><span class="label">Grubhub</span><span class="value">{_f(agg["gh_fees"])}</span></div>
  <div class="pl-row indent"><span class="label">Uber Eats</span><span class="value">{_f(agg["uber_fees"] + agg["uber_ads"])}</span></div>
  <div class="pl-row indent"><span class="label">Catering Fees</span><span class="value">{_f(agg["catering_fees"])}</span></div>
  <div class="pl-row indent"><span class="label">Shipday</span><span class="value">{_f(agg["shipday_fees"])}</span></div>
  <div class="pl-row total"><span class="label">Total Fees</span><span class="value">{_f(total_fees)}</span></div>

  <div class="pl-row grand"><span class="label">NET PROFIT</span><span class="value" style="color:{profit_color};">{_f(net_profit)} <span style="font-size:0.85rem;">{net_pct:.1f}%</span></span></div>
</div>

<div class="card">
  <h2 style="color:#475417;margin-bottom:0.5rem;font-size:1rem;">Daily Breakdown</h2>
  <table>
    <thead><tr>
      <th>Day</th>
      <th style="text-align:right;">Revenue</th>
      <th style="text-align:right;">Profit</th>
    </tr></thead>
    <tbody>{daily_html}</tbody>
  </table>
</div>

</div>
</body></html>"""

    return page


@app.route('/profit')
@owner_required
def profit_redirect():
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
    return redirect(f'/profit/{yesterday}')


@app.route('/profit/weekly')
@owner_required
def profit_weekly_redirect():
    """Redirect to current ISO week P&L."""
    dt = datetime.now()
    iso = dt.isocalendar()
    return redirect(f'/profit/weekly/{iso[0]}-W{iso[1]:02d}')


@app.route('/profit/weekly/<week_str>')
@owner_required
def profit_weekly(week_str):
    """Weekly P&L view — aggregates daily profit data + invoice COGS."""
    return Response(_weekly_pl_page(week_str), content_type='text/html')


@app.route('/profit/<date_str>')
@owner_required
def profit_single(date_str):
    try:
        dt = datetime.strptime(date_str, "%Y%m%d")
    except ValueError:
        return _error_page(f"Invalid date: {date_str}. Use YYYYMMDD."), 400
    return Response(_profit_page(date_str), content_type='text/html')


@app.route('/profit/<start_str>/<end_str>')
@owner_required
def profit_range(start_str, end_str):
    try:
        datetime.strptime(start_str, "%Y%m%d")
        datetime.strptime(end_str, "%Y%m%d")
    except ValueError:
        return _error_page("Invalid date format. Use YYYYMMDD."), 400
    return Response(_profit_range_page(start_str, end_str), content_type='text/html')


@app.route('/api/profit/<date_str>', methods=['GET'])
def api_profit_get(date_str):
    # Allow API key auth (server-to-server from Hub) or session auth
    api_key = request.headers.get('X-API-Key') or request.args.get('api_key')
    server_key = os.getenv('SERVER_API_KEY', '')
    if api_key and server_key and api_key == server_key:
        pass  # API key valid
    elif not session.get('authenticated') or session.get('role') != 'owner':
        return jsonify({"error": "Unauthorized"}), 401
    try:
        datetime.strptime(date_str, "%Y%m%d")
    except ValueError:
        return jsonify({"error": "Invalid date"}), 400
    return jsonify(_get_profit_data(date_str))


@app.route('/api/profit/<date_str>', methods=['POST'])
@owner_required
def api_profit_save(date_str):
    try:
        datetime.strptime(date_str, "%Y%m%d")
    except ValueError:
        return jsonify({"error": "Invalid date"}), 400
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "No data"}), 400
    _save_manual_data(date_str, data)

    # Sync full day to Notion (auto + manual combined)
    if NOTION_PROFIT_DB_ID:
        try:
            full_data = _get_profit_data(date_str)
            _sync_to_notion(date_str, full_data)
        except Exception as e:
            logger.warning("Notion sync failed for %s: %s", date_str, e)

    return jsonify({"status": "ok"})


@app.route('/api/profit/<date_str>/sync-notion')
@owner_required
def api_profit_sync_notion(date_str):
    """Re-fetch catering data from Notion for this date."""
    try:
        datetime.strptime(date_str, "%Y%m%d")
    except ValueError:
        return jsonify({"error": "Invalid date"}), 400
    try:
        from catering.notion import get_catering_for_date
        iso_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
        result = get_catering_for_date(iso_date)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e), "forkable": 0, "ezcater": 0})


def _profit_page(date_str: str) -> str:
    """Generate single-day profit calc page (mobile-first, BOGO-aware)."""
    data = _get_profit_data(date_str)
    dt = datetime.strptime(date_str, "%Y%m%d")
    prev_date = (dt - timedelta(days=1)).strftime("%Y%m%d")
    next_date = (dt + timedelta(days=1)).strftime("%Y%m%d")
    today = datetime.now().strftime("%Y%m%d")
    show_next = next_date <= today

    error_msg = data.get("error", "")

    # Auto values
    toast_total = data.get("toast_total", 0)
    labor = data.get("labor", 0)
    tds_fees = data.get("tds_fees", 0)
    ot_hours = data.get("ot_hours", 0)
    ot_pay = data.get("ot_pay", 0)
    total_hours = data.get("total_hours", 0)
    ftes = data.get("ftes", 0)
    blended_rate = data.get("blended_rate", 0)
    payroll_taxes = data.get("payroll_taxes", 0)
    channels = data.get("channels", {})
    # Toast discount total (info-only — shows what Toast captured as discounts)
    toast_discounts = data.get("bogo_discount", 0)

    # Catering: auto from Notion, manual override if set
    m = data.get("manual", {})
    ca = data.get("catering_auto", {})
    forkable = m.get("forkable") if m.get("forkable") else ca.get("forkable", 0)
    ezcater = m.get("ezcater") if m.get("ezcater") else ca.get("ezcater", 0)
    forkable_auto = ca.get("forkable", 0) > 0 and not m.get("forkable")
    ezcater_auto = ca.get("ezcater", 0) > 0 and not m.get("ezcater")
    fixed = m.get("fixed", 0)
    # Pre-fill Fixed at date-appropriate default if not explicitly set
    if "fixed" not in m:
        fixed = _get_fixed_cost(date_str)
    # Vacation/sick: support array (new) or number (old) format
    vac_raw = m.get("vacation", 0)
    sick_raw = m.get("sick", 0)
    vacation_entries = vac_raw if isinstance(vac_raw, list) else []
    sick_entries = sick_raw if isinstance(sick_raw, list) else []
    vacation = sum(e.get("cost", 0) for e in vacation_entries) if vacation_entries else (vac_raw if isinstance(vac_raw, (int, float)) else 0)
    sick = sum(e.get("cost", 0) for e in sick_entries) if sick_entries else (sick_raw if isinstance(sick_raw, (int, float)) else 0)

    # Build employee wage list for dropdown (exclude owners)
    _wages = _cfg.get("wages", {})
    employee_options = []
    for emp_key, emp_data in sorted(_wages.items()):
        if emp_data.get("type") == "owner":
            continue
        wage = emp_data.get("wage", 0)
        if wage <= 0:
            continue
        # Format display name: "last, first" → "First Last"
        parts = emp_key.split(", ", 1)
        display = f"{parts[1].title()} {parts[0].title()}" if len(parts) == 2 else emp_key.title()
        employee_options.append({"key": emp_key, "display": display, "wage": wage})

    vacation_entries_json = json.dumps(vacation_entries)
    sick_entries_json = json.dumps(sick_entries)
    employee_options_json = json.dumps({e["key"]: e["wage"] for e in employee_options})
    employee_display_json = json.dumps({e["key"]: e["display"] for e in employee_options})

    misc = m.get("misc", 0)
    gh_fees = m.get("gh_fees", 0)
    dd_fees = m.get("dd_fees", 0)
    uber_fees = m.get("uber_fees", 0)
    uber_ads = m.get("uber_ads", 0)
    # Catering fees: auto-calc from subtotals (Forkable 12.5%, EZ Cater 20%)
    forkable_fee = round(forkable * 0.125, 2)
    ezcater_fee = round(ezcater * 0.20, 2)
    catering_fees = forkable_fee + ezcater_fee
    # Shipday: $99/mo (~$3.30/day) + $1.50 per online delivery order
    online_del_orders = data.get("online_delivery_orders", 0)
    shipday_fees_auto = round(99 / 30 + online_del_orders * 1.50, 2)
    shipday_fees = m.get("shipday_fees") if m.get("shipday_fees") else shipday_fees_auto
    shipday_auto = not m.get("shipday_fees")
    notes = m.get("notes", "")

    # Derived — toast_total already includes Uber discounts added back
    total_sales = toast_total + forkable + ezcater
    food_cost = round(total_sales * 0.35, 2)
    service_fees = gh_fees + dd_fees + uber_fees + uber_ads + catering_fees + shipday_fees
    profit = total_sales - labor - vacation - sick - misc - fixed - service_fees - food_cost
    pct_profit = (profit / total_sales * 100) if total_sales > 0 else 0
    labor_pct = (labor / total_sales * 100) if total_sales > 0 else 0
    food_pct = (food_cost / total_sales * 100) if total_sales > 0 else 0

    profit_color = "#4a7c1f" if profit >= 0 else "#d9342b"
    day_name = dt.strftime("%A")
    display_date = dt.strftime("%B %d, %Y")

    # Channel chart data
    ch_labels = json.dumps(list(channels.keys()))
    ch_values = json.dumps(list(channels.values()))

    # Hourly labor breakdown (auto)
    hourly_pay = labor - payroll_taxes

    # Per-employee labor rows for Labor Detail
    hourly_records = data.get("hourly_records", [])
    # Sort by total_pay descending
    hourly_records_sorted = sorted(hourly_records, key=lambda r: r.get("total_pay", 0), reverse=True)
    emp_rows_html = ""
    for rec in hourly_records_sorted:
        ename = rec.get("employee", "")
        # Format: "last, first" → "First L."
        parts = ename.split(", ", 1)
        if len(parts) == 2:
            disp = parts[1].title() + " " + parts[0][:1].upper() + "."
        else:
            disp = ename.title()
        hrs = rec.get("hours", 0)
        pay = rec.get("total_pay", 0)
        wage = rec.get("wage", 0)
        ot = rec.get("ot_hours", 0)
        ot_tag = f" <span style='font-size:10px;color:#d9342b;'>+{ot:.1f}OT</span>" if ot > 0 else ""
        emp_rows_html += f"""    <div class="field-row">
      <span class="field-label" style="font-size:13px;">{disp}{ot_tag}</span>
      <span class="field-value field-auto" style="font-size:13px;">{hrs:.1f}h @ ${wage:.2f} = ${pay:,.2f}</span>
    </div>\n"""

    # Toast discount info (informational — shows what Toast captured as promo discounts)
    discount_html = ""
    if toast_discounts > 0:
        discount_html = f"""
    <div class="field-row" style="padding-left:10px;">
      <span class="field-label" style="font-size:12px;color:#7a7265;">Toast Discounts (already deducted)</span>
      <span class="field-value" style="font-size:12px;color:#7a7265;">${toast_discounts:,.2f}</span>
    </div>"""

    sales_sub = "Toast + Catering"

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Profit Calc — {display_date}</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
<style>
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{font-family:'DM Sans',sans-serif;background:#F5EDDC;color:#2d2a24;padding:16px;}}
.container{{max-width:960px;margin:0 auto;}}
h1{{font-size:22px;font-weight:700;color:#475417;margin-bottom:4px;}}
.nav{{display:flex;align-items:center;gap:10px;margin-bottom:16px;flex-wrap:wrap;}}
.nav a{{color:#4a7c1f;text-decoration:none;font-size:13px;font-weight:500;}}
.nav a:hover{{text-decoration:underline;}}
.nav .date-display{{font-size:15px;font-weight:600;color:#2d2a24;}}
.card{{background:#fff;border:1px solid #e0d5bf;border-radius:12px;padding:20px;margin-bottom:14px;box-shadow:0 1px 3px rgba(0,0,0,0.04);}}
.kpi-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin-bottom:14px;}}
.kpi{{background:#fff;border:1px solid #e0d5bf;border-radius:12px;padding:14px;text-align:center;}}
.kpi-label{{font-size:11px;font-weight:600;color:#7a7265;text-transform:uppercase;letter-spacing:0.5px;}}
.kpi-value{{font-size:26px;font-weight:700;font-family:'JetBrains Mono',monospace;margin:4px 0;}}
.kpi-sub{{font-size:11px;color:#7a7265;font-family:'JetBrains Mono',monospace;}}
.field-row{{display:flex;justify-content:space-between;align-items:center;padding:6px 0;border-bottom:1px solid #f0e8d6;}}
.field-row:last-child{{border-bottom:none;}}
.field-label{{font-size:14px;color:#2d2a24;font-weight:500;}}
.field-value{{font-size:14px;font-weight:600;font-family:'JetBrains Mono',monospace;color:#2d2a24;}}
.field-auto{{color:#4a7c1f;}}
.section-title{{font-size:12px;font-weight:600;color:#475417;text-transform:uppercase;letter-spacing:1px;margin-bottom:10px;padding-bottom:6px;border-bottom:2px solid #e0d5bf;}}
.save-indicator{{font-size:11px;color:#7a7265;transition:opacity 0.3s;}}
.save-indicator.saved{{color:#4a7c1f;}}
.error-banner{{background:#fef3f2;border:1px solid #fecaca;border-radius:8px;padding:12px 16px;color:#991b1b;font-size:13px;margin-bottom:14px;}}

/* Manual input fields — mobile-friendly */
input.manual{{width:100%;padding:10px 12px;border:1px solid #e0d5bf;border-radius:8px;font-size:15px;font-family:'JetBrains Mono',monospace;text-align:right;background:#faf6ee;min-height:44px;}}
input.manual:focus{{outline:none;border-color:#4a7c1f;box-shadow:0 0 0 2px rgba(74,124,31,0.15);}}
textarea.manual{{width:100%;padding:10px 12px;border:1px solid #e0d5bf;border-radius:8px;font-size:15px;font-family:'DM Sans',sans-serif;background:#faf6ee;resize:vertical;min-height:44px;}}
textarea.manual:focus{{outline:none;border-color:#4a7c1f;box-shadow:0 0 0 2px rgba(74,124,31,0.15);}}
.manual-field{{display:grid;grid-template-columns:1fr 1fr;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid #f0e8d6;}}
.manual-field:last-child{{border-bottom:none;}}
.manual-field label{{font-size:14px;color:#2d2a24;font-weight:500;}}

/* Collapsible sections */
.toggle-header{{display:flex;justify-content:space-between;align-items:center;cursor:pointer;padding:10px 0;user-select:none;}}
.toggle-header .section-title{{margin:0;border:none;padding:0;}}
.toggle-header .arrow{{font-size:12px;color:#7a7265;transition:transform 0.2s;}}
.toggle-header .arrow.open{{transform:rotate(90deg);}}
.toggle-content{{display:none;padding-top:8px;}}
.toggle-content.open{{display:block;}}

/* Date picker */
.date-picker-row{{display:flex;gap:8px;align-items:center;flex-wrap:wrap;}}
.date-picker-row input[type=date]{{padding:6px 10px;border:1px solid #e0d5bf;border-radius:6px;font-size:13px;font-family:'DM Sans',sans-serif;background:#faf6ee;}}
.btn{{padding:8px 16px;border:none;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;font-family:'DM Sans',sans-serif;text-decoration:none;display:inline-block;}}
.btn-primary{{background:#4a7c1f;color:#fff;}}
.btn-primary:hover{{background:#3d6819;}}
.btn-secondary{{background:#e0d5bf;color:#2d2a24;}}
.btn-secondary:hover{{background:#d5c9aa;}}

.grid-2{{display:grid;grid-template-columns:1fr 1fr;gap:14px;}}

@media(max-width:640px){{
  .grid-2{{grid-template-columns:1fr;}}
  .kpi-grid{{grid-template-columns:repeat(2,1fr);}}
  .kpi-value{{font-size:22px;}}
  .manual-field{{grid-template-columns:1fr;gap:4px;}}
}}
.entry-section{{padding:10px 0;border-bottom:1px solid #f0e8d6;}}
.entry-row{{display:flex;align-items:center;gap:6px;padding:5px 8px;background:#faf6ee;border-radius:6px;margin-bottom:4px;font-size:13px;}}
.entry-row .entry-name{{flex:2;font-weight:500;}}
.entry-row .entry-hrs{{flex:0.6;text-align:center;color:#7a7265;}}
.entry-row .entry-cost{{flex:0.8;text-align:right;font-weight:600;font-family:'JetBrains Mono',monospace;}}
.entry-row .entry-rm{{flex:0.3;text-align:center;color:#d9342b;cursor:pointer;font-weight:700;font-size:16px;}}
.entry-row .entry-rm:hover{{color:#b02020;}}

/* P&L Breakdown waterfall */
.pnl-card{{background:#fff;border:1px solid #e0d5bf;border-radius:12px;padding:20px;margin-bottom:14px;box-shadow:0 1px 3px rgba(0,0,0,0.04);}}
.pnl-row{{display:flex;justify-content:space-between;align-items:center;padding:5px 0;font-size:14px;}}
.pnl-row.indent{{padding-left:20px;font-size:12px;color:#7a7265;}}
.pnl-row .pnl-label{{font-weight:500;color:#2d2a24;}}
.pnl-row.indent .pnl-label{{font-weight:400;}}
.pnl-row .pnl-amt{{font-weight:600;font-family:'JetBrains Mono',monospace;color:#2d2a24;text-align:right;min-width:90px;}}
.pnl-row.indent .pnl-amt{{font-weight:500;color:#7a7265;}}
.pnl-row.deduct .pnl-amt{{color:#d9342b;}}
.pnl-divider{{border-top:1px solid #e0d5bf;margin:6px 0;}}
.pnl-divider.double{{border-top:3px double #2d2a24;margin:8px 0;}}
.pnl-row.total .pnl-label{{font-weight:700;font-size:15px;}}
.pnl-row.total .pnl-amt{{font-weight:700;font-size:15px;}}
.pnl-row.subtotal .pnl-label{{font-weight:600;}}
.pnl-row.subtotal .pnl-amt{{font-weight:600;}}
</style>
</head><body>
<div class="container">

<div class="nav">
  <a href="/">&larr; Home</a>
  <a href="/profit/weekly">Weekly P&L</a>
  <a href="/invoices/">Invoices</a>
  <a href="/profit/{prev_date}">&lsaquo; Prev</a>
  <span class="date-display">{day_name}, {display_date}</span>
  {"<a href='/profit/" + next_date + "'>Next &rsaquo;</a>" if show_next else ""}
  <span class="save-indicator" id="saveStatus"></span>
</div>

<div class="date-picker-row" style="margin-bottom:14px;">
  <input type="date" id="datePick" value="{dt.strftime('%Y-%m-%d')}" min="2024-11-07" max="{datetime.now().strftime('%Y-%m-%d')}">
  <button class="btn btn-secondary" onclick="goDate()">Go</button>
  <span style="color:#7a7265;font-size:12px;">|</span>
  <input type="date" id="rangeStart" min="2024-11-07" max="{datetime.now().strftime('%Y-%m-%d')}">
  <span style="color:#7a7265;font-size:12px;">to</span>
  <input type="date" id="rangeEnd" value="{dt.strftime('%Y-%m-%d')}" min="2024-11-07" max="{datetime.now().strftime('%Y-%m-%d')}">
  <button class="btn btn-secondary" onclick="goRange()">Range</button>
</div>

{"<div class='error-banner'>" + error_msg + "</div>" if error_msg else ""}

<!-- ═══ MANUAL ENTRY (first for easy access) ═══ -->
<div class="card">
  <div style="display:flex;justify-content:space-between;align-items:center;">
    <div class="section-title">Daily Entry</div>
    <button onclick="syncNotion()" id="syncBtn" style="padding:6px 14px;font-size:11px;font-weight:600;border:1px solid #e0d5bf;border-radius:8px;cursor:pointer;background:#fff;color:#475417;">Sync Notion</button>
  </div>

  <div class="manual-field">
    <label>Forkable{"<span style='font-size:10px;color:#4a7c1f;margin-left:6px;'>auto</span>" if forkable_auto else ""}</label>
    <input class="manual{" field-auto" if forkable_auto else ""}" type="number" step="0.01" inputmode="decimal" value="{forkable}" data-field="forkable" onchange="saveField(this)"{"style='color:#4a7c1f;'" if forkable_auto else ""}>
  </div>
  <div class="manual-field">
    <label>EZ Cater{"<span style='font-size:10px;color:#4a7c1f;margin-left:6px;'>auto</span>" if ezcater_auto else ""}</label>
    <input class="manual{" field-auto" if ezcater_auto else ""}" type="number" step="0.01" inputmode="decimal" value="{ezcater}" data-field="ezcater" onchange="saveField(this)"{"style='color:#4a7c1f;'" if ezcater_auto else ""}>
  </div>
  <div class="manual-field">
    <label>Fixed</label>
    <input class="manual" type="number" step="0.01" inputmode="decimal" value="{fixed}" data-field="fixed" onchange="saveField(this)">
  </div>

  <!-- Vacation entries -->
  <div class="entry-section">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
      <label style="font-weight:600;font-size:14px;">Vacation</label>
      <span id="vacationTotal" style="font-weight:600;font-size:14px;color:#2d2a24;">${vacation:,.2f}</span>
    </div>
    <div id="vacationEntries"></div>
    <div style="display:flex;gap:6px;align-items:center;margin-top:6px;">
      <select id="vacationEmp" style="flex:2;padding:8px;border:1px solid #e0d5bf;border-radius:8px;font-size:13px;background:#fff;">
        <option value="">Select employee</option>
      </select>
      <input id="vacationHrs" type="number" step="0.5" min="0.5" max="24" inputmode="decimal" placeholder="Hrs" style="flex:0.8;padding:8px;border:1px solid #e0d5bf;border-radius:8px;font-size:13px;text-align:center;">
      <button onclick="addEntry('vacation')" style="flex:0.5;padding:8px 0;background:#4a7c1f;color:#fff;border:none;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;">+</button>
    </div>
  </div>

  <!-- Sick entries -->
  <div class="entry-section">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
      <label style="font-weight:600;font-size:14px;">Sick</label>
      <span id="sickTotal" style="font-weight:600;font-size:14px;color:#2d2a24;">${sick:,.2f}</span>
    </div>
    <div id="sickEntries"></div>
    <div style="display:flex;gap:6px;align-items:center;margin-top:6px;">
      <select id="sickEmp" style="flex:2;padding:8px;border:1px solid #e0d5bf;border-radius:8px;font-size:13px;background:#fff;">
        <option value="">Select employee</option>
      </select>
      <input id="sickHrs" type="number" step="0.5" min="0.5" max="24" inputmode="decimal" placeholder="Hrs" style="flex:0.8;padding:8px;border:1px solid #e0d5bf;border-radius:8px;font-size:13px;text-align:center;">
      <button onclick="addEntry('sick')" style="flex:0.5;padding:8px 0;background:#4a7c1f;color:#fff;border:none;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;">+</button>
    </div>
  </div>
  <div class="manual-field">
    <label>Misc</label>
    <input class="manual" type="number" step="0.01" inputmode="decimal" value="{misc}" data-field="misc" onchange="saveField(this)">
  </div>
  <div class="manual-field" style="grid-template-columns:1fr;">
    <label>Notes</label>
    <textarea class="manual" data-field="notes" onchange="saveField(this)" rows="3">{notes}</textarea>
  </div>

  <!-- Collapsible: Fees -->
  <div class="toggle-header" onclick="toggle('feesSection',this)">
    <span class="section-title">Service Fees</span>
    <span class="arrow open">&#9654;</span>
  </div>
  <div class="toggle-content open" id="feesSection">
    <div class="manual-field">
      <label>GH Fees</label>
      <input class="manual" type="number" step="0.01" inputmode="decimal" value="{gh_fees}" data-field="gh_fees" data-fee="1" onchange="saveField(this)">
    </div>
    <div class="manual-field">
      <label>DD Fees</label>
      <input class="manual" type="number" step="0.01" inputmode="decimal" value="{dd_fees}" data-field="dd_fees" data-fee="1" onchange="saveField(this)">
    </div>
    <div class="manual-field">
      <label>Uber Fees</label>
      <input class="manual" type="number" step="0.01" inputmode="decimal" value="{uber_fees}" data-field="uber_fees" data-fee="1" onchange="saveField(this)">
    </div>
    <div class="manual-field">
      <label>Uber Ads</label>
      <input class="manual" type="number" step="0.01" inputmode="decimal" value="{uber_ads}" data-field="uber_ads" data-fee="1" onchange="saveField(this)">
    </div>
    <div class="manual-field">
      <label>3P Catering Fees <span class="auto-badge">auto</span> <span style="font-size:10px;color:#7a7265;">F:{forkable_fee:.0f} + E:{ezcater_fee:.0f}</span></label>
      <input class="manual field-auto" type="number" step="0.01" inputmode="decimal" value="{catering_fees}" data-fee="1" readonly style="color:#4a7c1f;">
    </div>
    <div class="manual-field">
      <label>Shipday Fees{' <span class="auto-badge">auto</span>' if shipday_auto else ''} <span style="font-size:10px;color:#7a7265;">({online_del_orders} deliveries)</span></label>
      <input class="manual" type="number" step="0.01" inputmode="decimal" value="{shipday_fees}" data-field="shipday_fees" data-fee="1" onchange="saveField(this)">
    </div>
  </div>
</div>

<!-- Submit button -->
<div style="text-align:center;margin:18px 0;">
  <button class="btn" id="submitBtn" onclick="submitAll()" style="padding:12px 40px;font-size:16px;font-weight:600;">
    Save & Submit
  </button>
  <div id="submitStatus" style="margin-top:8px;font-size:13px;color:#7a7265;"></div>
</div>

<!-- ═══ KPI CARDS ═══ -->
<div class="kpi-grid">
  <div class="kpi">
    <div class="kpi-label">Profit</div>
    <div class="kpi-value" id="kpiProfit" style="color:{profit_color}">${profit:,.0f}</div>
    <div class="kpi-sub" id="kpiProfitPct">{pct_profit:.1f}%</div>
  </div>
  <div class="kpi">
    <div class="kpi-label">Total Sales</div>
    <div class="kpi-value" id="kpiSales">${total_sales:,.0f}</div>
    <div class="kpi-sub">{sales_sub}</div>
  </div>
  <div class="kpi">
    <div class="kpi-label">Labor</div>
    <div class="kpi-value">${labor:,.0f}</div>
    <div class="kpi-sub">{labor_pct:.1f}% of sales</div>
  </div>
  <div class="kpi">
    <div class="kpi-label">Food Cost</div>
    <div class="kpi-value" id="kpiFoodCost">${food_cost:,.0f}</div>
    <div class="kpi-sub" id="kpiFoodPct">{food_pct:.1f}% of sales</div>
  </div>
</div>

<!-- ═══ P&L BREAKDOWN ═══ -->
<div class="pnl-card" id="pnlBreakdown">
  <div class="section-title">P&amp;L Breakdown</div>

  <div class="pnl-row">
    <span class="pnl-label">Total Sales</span>
    <span class="pnl-amt" id="pnlSales">${total_sales:,.2f}</span>
  </div>
  <div class="pnl-row indent">
    <span class="pnl-label">Toast Sales</span>
    <span class="pnl-amt">${toast_total:,.2f}</span>
  </div>
  <div class="pnl-row indent">
    <span class="pnl-label">Forkable</span>
    <span class="pnl-amt" id="pnlForkable">${forkable:,.2f}</span>
  </div>
  <div class="pnl-row indent">
    <span class="pnl-label">EZ Cater</span>
    <span class="pnl-amt" id="pnlEzcater">${ezcater:,.2f}</span>
  </div>

  <div class="pnl-divider"></div>
  <div class="pnl-row deduct">
    <span class="pnl-label">Food Cost (35%)</span>
    <span class="pnl-amt" id="pnlFood">-${food_cost:,.2f}</span>
  </div>
  <div class="pnl-divider"></div>
  <div class="pnl-row subtotal">
    <span class="pnl-label">Gross Profit</span>
    <span class="pnl-amt" id="pnlGross">${total_sales - food_cost:,.2f}</span>
  </div>

  <div class="pnl-divider"></div>
  <div class="pnl-row deduct">
    <span class="pnl-label">Labor</span>
    <span class="pnl-amt">-${labor:,.2f}</span>
  </div>
  <div class="pnl-row deduct">
    <span class="pnl-label">Vacation</span>
    <span class="pnl-amt" id="pnlVacation">-${vacation:,.2f}</span>
  </div>
  <div class="pnl-row deduct">
    <span class="pnl-label">Sick</span>
    <span class="pnl-amt" id="pnlSick">-${sick:,.2f}</span>
  </div>
  <div class="pnl-row deduct">
    <span class="pnl-label">Fixed Costs</span>
    <span class="pnl-amt" id="pnlFixed">-${fixed:,.2f}</span>
  </div>
  <div class="pnl-row deduct">
    <span class="pnl-label">Misc</span>
    <span class="pnl-amt" id="pnlMisc">-${misc:,.2f}</span>
  </div>
  <div class="pnl-row deduct">
    <span class="pnl-label">Service Fees</span>
    <span class="pnl-amt" id="pnlFees">-${service_fees:,.2f}</span>
  </div>
  <div class="pnl-row indent">
    <span class="pnl-label">GH Fees</span>
    <span class="pnl-amt" id="pnlGH">${gh_fees:,.2f}</span>
  </div>
  <div class="pnl-row indent">
    <span class="pnl-label">DD Fees</span>
    <span class="pnl-amt" id="pnlDD">${dd_fees:,.2f}</span>
  </div>
  <div class="pnl-row indent">
    <span class="pnl-label">Uber Fees</span>
    <span class="pnl-amt" id="pnlUber">${uber_fees:,.2f}</span>
  </div>
  <div class="pnl-row indent">
    <span class="pnl-label">Uber Ads</span>
    <span class="pnl-amt" id="pnlUberAds">${uber_ads:,.2f}</span>
  </div>
  <div class="pnl-row indent">
    <span class="pnl-label">3P Catering Fees</span>
    <span class="pnl-amt" id="pnlCatFees">${catering_fees:,.2f}</span>
  </div>
  <div class="pnl-row indent">
    <span class="pnl-label">Shipday</span>
    <span class="pnl-amt" id="pnlShipday">${shipday_fees:,.2f}</span>
  </div>

  <div class="pnl-divider double"></div>
  <div class="pnl-row total">
    <span class="pnl-label">Net Profit</span>
    <span class="pnl-amt" id="pnlProfit" style="color:{profit_color}">${profit:,.2f}</span>
  </div>
  <div class="pnl-row">
    <span class="pnl-label" style="font-size:12px;color:#7a7265;">Profit Margin</span>
    <span class="pnl-amt" id="pnlMargin" style="font-size:12px;color:#7a7265;">{pct_profit:.1f}%</span>
  </div>
</div>

<!-- ═══ AUTO DETAIL: Revenue ═══ -->
<div class="card">
  <div class="toggle-header" onclick="toggle('revenueDetail',this)">
    <span class="section-title">Revenue Detail</span>
    <span class="arrow open">&#9654;</span>
  </div>
  <div class="toggle-content open" id="revenueDetail">
    <div class="field-row">
      <span class="field-label">Toast Total</span>
      <span class="field-value field-auto">${toast_total:,.2f}</span>
    </div>
    {discount_html}
    <div class="field-row">
      <span class="field-label">Forkable</span>
      <span class="field-value">${forkable:,.2f}</span>
    </div>
    <div class="field-row">
      <span class="field-label">EZ Cater</span>
      <span class="field-value">${ezcater:,.2f}</span>
    </div>
    <div style="margin-top:14px;padding-top:10px;border-top:1px solid #e0d5bf;">
      <div class="field-row">
        <span class="field-label" style="font-weight:600;">Service Fees</span>
        <span class="field-value" id="totalFees">${service_fees:,.2f}</span>
      </div>
      <div class="field-row">
        <span class="field-label" style="padding-left:12px;font-size:12px;">Food Cost (35%)</span>
        <span class="field-value field-auto" style="font-size:12px;" id="foodCostDisplay">${food_cost:,.2f}</span>
      </div>
    </div>
    <div style="margin-top:14px;">
      <div style="display:flex;gap:6px;margin-bottom:8px;">
        <button id="chBarBtn" onclick="setChartType('bar')" style="padding:4px 12px;font-size:11px;font-weight:600;border:1px solid #e0d5bf;border-radius:6px;cursor:pointer;background:#475417;color:#fff;">Bar</button>
        <button id="chPieBtn" onclick="setChartType('pie')" style="padding:4px 12px;font-size:11px;font-weight:600;border:1px solid #e0d5bf;border-radius:6px;cursor:pointer;background:#fff;color:#2d2a24;">Pie</button>
      </div>
      <canvas id="channelChart" height="200"></canvas>
    </div>
  </div>
</div>

<!-- ═══ AUTO DETAIL: Labor ═══ -->
<div class="card">
  <div class="toggle-header" onclick="toggle('laborDetail',this)">
    <span class="section-title">Labor Detail</span>
    <span class="arrow">&#9654;</span>
  </div>
  <div class="toggle-content" id="laborDetail">
    <div class="field-row">
      <span class="field-label">Hourly Pay</span>
      <span class="field-value field-auto">${hourly_pay:,.2f}</span>
    </div>
    <div class="field-row">
      <span class="field-label">Payroll Tax</span>
      <span class="field-value field-auto">${payroll_taxes:,.2f}</span>
    </div>
    <div class="field-row">
      <span class="field-label">OT Pay</span>
      <span class="field-value field-auto">${ot_pay:,.2f}</span>
    </div>
    <div style="margin-top:10px;padding-top:10px;border-top:1px solid #e0d5bf;">
      <div class="field-row">
        <span class="field-label">Total Hours</span>
        <span class="field-value field-auto">{total_hours:.1f}</span>
      </div>
      <div class="field-row">
        <span class="field-label">OT Hours</span>
        <span class="field-value field-auto">{ot_hours:.1f}</span>
      </div>
      <div class="field-row">
        <span class="field-label">FTEs</span>
        <span class="field-value field-auto">{ftes:.1f}</span>
      </div>
      <div class="field-row">
        <span class="field-label">Avg Wage</span>
        <span class="field-value field-auto">${blended_rate:.2f}/hr</span>
      </div>
    </div>
    <div style="margin-top:10px;padding-top:10px;border-top:1px solid #e0d5bf;">
      <div style="font-size:11px;font-weight:600;color:#7a7265;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:6px;">By Employee</div>
{emp_rows_html}    </div>
  </div>
</div>

</div>

<script>
const DATE_STR = '{date_str}';
const TOAST_TOTAL = {toast_total};
const TOAST_DISCOUNTS = {toast_discounts};
const LABOR = {labor};
const FOOD_COST_PCT = 0.35;
const EMP_WAGES = {employee_options_json};
const EMP_DISPLAY = {employee_display_json};

let vacationEntries = {vacation_entries_json};
let sickEntries = {sick_entries_json};

function n(el) {{ return parseFloat(el.value) || 0; }}

function fmtMoney(v) {{ return '$' + v.toFixed(2).replace(/\\B(?=(\\d{{3}})+(?!\\d))/g, ','); }}

function renderEntries(type) {{
    const entries = type === 'vacation' ? vacationEntries : sickEntries;
    const container = document.getElementById(type + 'Entries');
    const total = entries.reduce((s, e) => s + (e.cost || 0), 0);
    document.getElementById(type + 'Total').textContent = fmtMoney(total);
    if (entries.length === 0) {{
        container.innerHTML = '';
        return;
    }}
    let html = '';
    entries.forEach((e, i) => {{
        const display = EMP_DISPLAY[e.employee] || e.employee;
        html += '<div class="entry-row">'
            + '<span class="entry-name">' + display + '</span>'
            + '<span class="entry-hrs">' + e.hours + 'h</span>'
            + '<span class="entry-cost">' + fmtMoney(e.cost) + '</span>'
            + '<span class="entry-rm" onclick="removeEntry(\\'' + type + '\\',' + i + ')">x</span>'
            + '</div>';
    }});
    container.innerHTML = html;
}}

function addEntry(type) {{
    const selEl = document.getElementById(type + 'Emp');
    const hrsEl = document.getElementById(type + 'Hrs');
    const emp = selEl.value;
    const hrs = parseFloat(hrsEl.value) || 0;
    if (!emp || hrs <= 0) return;
    const wage = EMP_WAGES[emp] || 0;
    const cost = Math.round(hrs * wage * 100) / 100;
    const entry = {{ employee: emp, hours: hrs, cost: cost }};
    if (type === 'vacation') vacationEntries.push(entry);
    else sickEntries.push(entry);
    selEl.value = '';
    hrsEl.value = '';
    renderEntries(type);
    recalc();
    saveEntries(type);
}}

function removeEntry(type, idx) {{
    if (type === 'vacation') vacationEntries.splice(idx, 1);
    else sickEntries.splice(idx, 1);
    renderEntries(type);
    recalc();
    saveEntries(type);
}}

function saveEntries(type) {{
    const entries = type === 'vacation' ? vacationEntries : sickEntries;
    const body = {{}};
    body[type] = entries;
    fetch('/api/profit/' + DATE_STR, {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify(body),
    }});
}}

function populateDropdowns() {{
    const sorted = Object.keys(EMP_WAGES).sort((a, b) => {{
        const da = EMP_DISPLAY[a] || a;
        const db = EMP_DISPLAY[b] || b;
        return da.localeCompare(db);
    }});
    ['vacation', 'sick'].forEach(type => {{
        const sel = document.getElementById(type + 'Emp');
        sorted.forEach(key => {{
            const opt = document.createElement('option');
            opt.value = key;
            opt.textContent = (EMP_DISPLAY[key] || key) + ' ($' + EMP_WAGES[key].toFixed(2) + '/hr)';
            sel.appendChild(opt);
        }});
    }});
}}

function recalc() {{
    const forkable = n(document.querySelector('[data-field="forkable"]'));
    const ezcater = n(document.querySelector('[data-field="ezcater"]'));
    const totalSales = TOAST_TOTAL + forkable + ezcater;

    const fixed = n(document.querySelector('[data-field="fixed"]'));
    const vacation = vacationEntries.reduce((s, e) => s + (e.cost || 0), 0);
    const sick = sickEntries.reduce((s, e) => s + (e.cost || 0), 0);
    const misc = n(document.querySelector('[data-field="misc"]'));

    let fees = 0;
    document.querySelectorAll('[data-fee]').forEach(el => {{ fees += n(el); }});

    const foodCost = totalSales * FOOD_COST_PCT;
    const profit = totalSales - LABOR - vacation - sick - misc - fixed - fees - foodCost;
    const pctProfit = totalSales > 0 ? (profit / totalSales * 100) : 0;
    const foodPct = totalSales > 0 ? (foodCost / totalSales * 100) : 0;

    document.getElementById('kpiProfit').textContent = '$' + Math.round(profit).toLocaleString();
    document.getElementById('kpiProfit').style.color = profit >= 0 ? '#4a7c1f' : '#d9342b';
    document.getElementById('kpiProfitPct').textContent = pctProfit.toFixed(1) + '%';
    document.getElementById('kpiSales').textContent = '$' + Math.round(totalSales).toLocaleString();
    document.getElementById('kpiFoodCost').textContent = '$' + Math.round(foodCost).toLocaleString();
    document.getElementById('kpiFoodPct').textContent = foodPct.toFixed(1) + '% of sales';
    const feesEl = document.getElementById('totalFees');
    if (feesEl) feesEl.textContent = '$' + fees.toFixed(2).replace(/\\B(?=(\\d{{3}})+(?!\\d))/g, ',');
    const fcEl = document.getElementById('foodCostDisplay');
    if (fcEl) fcEl.textContent = '$' + foodCost.toFixed(2).replace(/\\B(?=(\\d{{3}})+(?!\\d))/g, ',');

    // P&L Breakdown updates
    const grossProfit = totalSales - foodCost;
    const fmtD = v => '$' + v.toFixed(2).replace(/\\B(?=(\\d{{3}})+(?!\\d))/g, ',');
    const ghFees = n(document.querySelector('[data-field="gh_fees"]'));
    const ddFees = n(document.querySelector('[data-field="dd_fees"]'));
    const uberFees = n(document.querySelector('[data-field="uber_fees"]'));
    const uberAds = n(document.querySelector('[data-field="uber_ads"]'));
    const shipFees = n(document.querySelector('[data-field="shipday_fees"]'));
    const catFees = fees - ghFees - ddFees - uberFees - uberAds - shipFees;

    const pnl = (id, v) => {{ const el = document.getElementById(id); if (el) el.textContent = v; }};
    pnl('pnlSales', fmtD(totalSales));
    pnl('pnlForkable', fmtD(forkable));
    pnl('pnlEzcater', fmtD(ezcater));
    pnl('pnlFood', '-' + fmtD(foodCost));
    pnl('pnlGross', fmtD(grossProfit));
    pnl('pnlVacation', '-' + fmtD(vacation));
    pnl('pnlSick', '-' + fmtD(sick));
    pnl('pnlFixed', '-' + fmtD(fixed));
    pnl('pnlMisc', '-' + fmtD(misc));
    pnl('pnlFees', '-' + fmtD(fees));
    pnl('pnlGH', fmtD(ghFees));
    pnl('pnlDD', fmtD(ddFees));
    pnl('pnlUber', fmtD(uberFees));
    pnl('pnlUberAds', fmtD(uberAds));
    pnl('pnlCatFees', fmtD(catFees));
    pnl('pnlShipday', fmtD(shipFees));
    pnl('pnlProfit', fmtD(profit));
    const profitEl = document.getElementById('pnlProfit');
    if (profitEl) profitEl.style.color = profit >= 0 ? '#4a7c1f' : '#d9342b';
    pnl('pnlMargin', pctProfit.toFixed(1) + '%');
}}

function syncNotion() {{
    const btn = document.getElementById('syncBtn');
    btn.textContent = 'Syncing...';
    btn.disabled = true;
    fetch('/api/profit/' + DATE_STR + '/sync-notion').then(r => r.json()).then(d => {{
        btn.textContent = 'Sync Notion';
        btn.disabled = false;
        if (d.forkable > 0 || d.ezcater > 0) {{
            const forkInp = document.querySelector('[data-field="forkable"]');
            const ezInp = document.querySelector('[data-field="ezcater"]');
            if (d.forkable > 0 && forkInp && !parseFloat(forkInp.value)) {{
                forkInp.value = d.forkable;
                saveField(forkInp);
            }}
            if (d.ezcater > 0 && ezInp && !parseFloat(ezInp.value)) {{
                ezInp.value = d.ezcater;
                saveField(ezInp);
            }}
            btn.textContent = 'Synced!';
            setTimeout(() => {{ btn.textContent = 'Sync Notion'; }}, 2000);
        }} else {{
            btn.textContent = 'No orders found';
            setTimeout(() => {{ btn.textContent = 'Sync Notion'; }}, 2000);
        }}
    }}).catch(() => {{
        btn.textContent = 'Error';
        btn.disabled = false;
        setTimeout(() => {{ btn.textContent = 'Sync Notion'; }}, 2000);
    }});
}}

let saveTimer = null;
function saveField(el) {{
    recalc();
    const field = el.dataset.field || el.dataset.fee && el.dataset.field;
    const value = el.type === 'number' ? (parseFloat(el.value) || 0) : el.value;
    const status = document.getElementById('saveStatus');
    status.textContent = 'Saving...';
    status.className = 'save-indicator';

    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {{
        const body = {{}};
        body[field] = value;
        fetch('/api/profit/' + DATE_STR, {{
            method: 'POST',
            headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify(body),
        }}).then(r => {{
            status.textContent = 'Saved';
            status.className = 'save-indicator saved';
            setTimeout(() => {{ status.textContent = ''; }}, 2000);
        }}).catch(e => {{
            status.textContent = 'Save failed';
        }});
    }}, 500);
}}

function submitAll() {{
    const btn = document.getElementById('submitBtn');
    const status = document.getElementById('submitStatus');
    btn.disabled = true;
    btn.textContent = 'Saving...';
    status.textContent = '';

    // Collect all manual fields
    const body = {{}};
    document.querySelectorAll('.manual[data-field]').forEach(el => {{
        const field = el.dataset.field;
        body[field] = el.type === 'number' ? (parseFloat(el.value) || 0) : el.value;
    }});
    // Also collect textarea
    document.querySelectorAll('textarea[data-field]').forEach(el => {{
        body[el.dataset.field] = el.value;
    }});
    // Vacation/sick as entry arrays
    body.vacation = vacationEntries;
    body.sick = sickEntries;

    fetch('/api/profit/' + DATE_STR + '?sync=1', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify(body),
    }}).then(r => r.json()).then(data => {{
        btn.disabled = false;
        btn.textContent = 'Save & Submit';
        status.textContent = 'Saved and synced to Notion';
        status.style.color = '#4a7c1f';
        setTimeout(() => {{ status.textContent = ''; }}, 4000);
    }}).catch(e => {{
        btn.disabled = false;
        btn.textContent = 'Save & Submit';
        status.textContent = 'Save failed — try again';
        status.style.color = '#d9342b';
    }});
}}

function toggle(id, header) {{
    const el = document.getElementById(id);
    const arrow = header.querySelector('.arrow');
    el.classList.toggle('open');
    arrow.classList.toggle('open');
    if (id === 'revenueDetail') setTimeout(renderChart, 50);
}}

function goDate() {{
    const d = document.getElementById('datePick').value;
    if (d) window.location = '/profit/' + d.replace(/-/g, '');
}}

function goRange() {{
    const s = document.getElementById('rangeStart').value;
    const e = document.getElementById('rangeEnd').value;
    if (s && e) window.location = '/profit/' + s.replace(/-/g, '') + '/' + e.replace(/-/g, '');
}}

// Channel chart (lazy render with bar/pie toggle)
let chChart = null;
let chType = 'bar';
const chLabels = {ch_labels};
const chValues = {ch_values};
const chColors = ['#8cb82e','#4a9cd8','#9b72c4','#2db88a','#e8a830','#e86040','#475417','#c47d0a','#7c3a6b','#1a6e5c'];

function renderChart() {{
    const canvas = document.getElementById('channelChart');
    if (!canvas || chLabels.length === 0) return;
    if (chChart) chChart.destroy();
    const isPie = chType === 'pie';
    chChart = new Chart(canvas, {{
        type: isPie ? 'doughnut' : 'bar',
        data: {{
            labels: chLabels,
            datasets: [{{
                data: chValues,
                backgroundColor: chColors.slice(0, chLabels.length),
                borderRadius: isPie ? 0 : 4,
            }}]
        }},
        options: {{
            responsive: true,
            plugins: {{
                legend: {{ display: isPie, position: 'bottom', labels: {{ font: {{ size: 11 }} }} }},
                tooltip: {{ callbacks: {{ label: function(ctx) {{ var v = typeof ctx.parsed === 'number' ? ctx.parsed : ctx.parsed.y; return ctx.label + ': $' + v.toLocaleString(); }} }} }}
            }},
            scales: isPie ? {{}} : {{
                y: {{ ticks: {{ callback: v => '$' + v.toLocaleString() }} }},
                x: {{ ticks: {{ font: {{ size: 10 }}, maxRotation: 45 }} }}
            }}
        }}
    }});
}}

function setChartType(t) {{
    chType = t;
    document.getElementById('chBarBtn').style.background = t === 'bar' ? '#475417' : '#fff';
    document.getElementById('chBarBtn').style.color = t === 'bar' ? '#fff' : '#2d2a24';
    document.getElementById('chPieBtn').style.background = t === 'pie' ? '#475417' : '#fff';
    document.getElementById('chPieBtn').style.color = t === 'pie' ? '#fff' : '#2d2a24';
    renderChart();
}}

// Init vacation/sick dropdowns and render existing entries
populateDropdowns();
renderEntries('vacation');
renderEntries('sick');
</script>
</body></html>"""


def _profit_range_page(start_str: str, end_str: str) -> str:
    """Generate range profit page with a row per day."""
    start = datetime.strptime(start_str, "%Y%m%d")
    end = datetime.strptime(end_str, "%Y%m%d")
    days = (end - start).days + 1
    if days < 1 or days > 365:
        return _error_page("Range must be 1-365 days.")

    rows = []
    totals = {"sales": 0, "profit": 0, "labor": 0, "food": 0, "hours": 0}
    # Detailed P&L aggregation
    from collections import defaultdict
    detail = defaultdict(float)

    for i in range(days):
        dt = start + timedelta(days=i)
        ds = dt.strftime("%Y%m%d")
        data = _get_profit_data(ds)

        if data.get("error"):
            rows.append({"date": ds, "display": dt.strftime("%m/%d"), "day": dt.strftime("%a"),
                          "error": True})
            continue

        m = data.get("manual", {})
        toast = data.get("toast_total", 0)
        forkable = m.get("forkable", 0)
        ezcater = m.get("ezcater", 0)
        total_sales = toast + forkable + ezcater
        labor = data.get("labor", 0)
        toast_disc = data.get("toast_discounts", data.get("bogo_discount", 0))
        food_cost = (toast + toast_disc + forkable + ezcater) * 0.35
        fixed = m.get("fixed") if "fixed" in m else _get_fixed_cost(ds)
        vac_raw = m.get("vacation", 0)
        sick_raw = m.get("sick", 0)
        vac_v = sum(e.get("cost", 0) for e in vac_raw) if isinstance(vac_raw, list) else (vac_raw or 0)
        sick_v = sum(e.get("cost", 0) for e in sick_raw) if isinstance(sick_raw, list) else (sick_raw or 0)
        vac = vac_v + sick_v
        misc = m.get("misc", 0)
        cat_fees = round(forkable * 0.125, 2) + round(ezcater * 0.20, 2)
        gh = m.get("gh_fees", 0)
        dd = m.get("dd_fees", 0)
        ub = m.get("uber_fees", 0)
        ub_ads = m.get("uber_ads", 0)
        ship = m.get("shipday_fees", 0)
        fees = gh + dd + ub + ub_ads + cat_fees + ship
        profit = total_sales - labor - vac - misc - fixed - fees - food_cost
        pct = (profit / total_sales * 100) if total_sales > 0 else 0
        hours = data.get("total_hours", 0)
        ftes = data.get("ftes", 0)
        blended = data.get("blended_rate", 0)

        rows.append({
            "date": ds, "display": dt.strftime("%m/%d"), "day": dt.strftime("%a"),
            "sales": total_sales, "labor": labor, "food": food_cost,
            "fees": fees, "fixed": fixed, "profit": profit, "pct": pct,
            "hours": hours, "error": False,
        })
        totals["sales"] += total_sales
        totals["profit"] += profit
        totals["labor"] += labor
        totals["food"] += food_cost
        totals["hours"] += hours
        detail["fte_sum"] += ftes
        detail["wage_hours"] += blended * hours  # weighted for avg
        # P&L detail
        detail["toast"] += toast
        detail["forkable"] += forkable
        detail["ezcater"] += ezcater
        detail["food_cost"] += food_cost
        detail["labor_hourly"] += labor - data.get("payroll_taxes", 0)
        detail["payroll_taxes"] += data.get("payroll_taxes", 0)
        detail["ot_pay"] += data.get("ot_pay", 0)
        detail["vacation"] += vac_v
        detail["sick"] += sick_v
        detail["fixed"] += fixed
        detail["misc"] += misc
        detail["gh_fees"] += gh
        detail["dd_fees"] += dd
        detail["uber_fees"] += ub
        detail["uber_ads"] += ub_ads
        detail["catering_fees"] += cat_fees
        detail["shipday_fees"] += ship

    valid_days = len([r for r in rows if not r.get("error")])
    avg_profit = totals["profit"] / max(valid_days, 1)
    total_pct = (totals["profit"] / totals["sales"] * 100) if totals["sales"] > 0 else 0
    profit_color = "#4a7c1f" if totals["profit"] >= 0 else "#d9342b"
    avg_fte = detail["fte_sum"] / max(valid_days, 1)
    avg_wage = detail["wage_hours"] / max(totals["hours"], 1)

    # ── Accountant data lookup (if range is a single calendar month) ──
    acct = None
    start_ym = start.strftime("%Y-%m")
    end_ym = end.strftime("%Y-%m")
    if start_ym == end_ym:
        try:
            from financials.data import parse_all_pl
            from financials.metrics import EXPENSE_GROUPS, _OWNER_COMP_ACCOUNTS
            pl = parse_all_pl()
            if start_ym in pl["months"]:
                idx = pl["months"].index(start_ym)
                n_months = len(pl["months"])
                _get_acct = lambda accts: sum(pl["opex"].get(a, [0.0] * n_months)[idx] for a in accts if a in pl["opex"])
                # Remove owner comp from labor
                labor_accts = [a for a in EXPENSE_GROUPS.get("Labor", []) if a not in _OWNER_COMP_ACCOUNTS]
                acct = {
                    "revenue": pl["total_income"][idx],
                    "cogs": pl["cogs"][idx],
                    "gross_profit": pl["gross_profit"][idx],
                    "labor": _get_acct(labor_accts),
                    "fees": _get_acct(EXPENSE_GROUPS.get("Third Party Fees", [])),
                    "rent": _get_acct(EXPENSE_GROUPS.get("Rent & Occupancy", [])),
                    "total_opex": pl["total_opex"][idx] - sum(pl["opex"].get(a, [0.0] * n_months)[idx] for a in _OWNER_COMP_ACCOUNTS if a in pl["opex"]),
                    "net_income": pl["net_income"][idx],
                }
        except Exception as e:
            logger.warning("P&L detail computation failed: %s", e)

    # ── Build P&L summary HTML ──
    d = detail  # shorthand
    int_revenue = totals["sales"]
    int_food = totals["food"]
    int_gross = int_revenue - int_food
    int_opex = d["labor_hourly"] + d["payroll_taxes"] + d["vacation"] + d["sick"] + d["fixed"] + d["misc"]
    int_fees = d["gh_fees"] + d["dd_fees"] + d["uber_fees"] + d["uber_ads"] + d["catering_fees"] + d["shipday_fees"]
    int_profit = totals["profit"]
    int_gross_pct = (int_gross / int_revenue * 100) if int_revenue > 0 else 0
    int_profit_pct = (int_profit / int_revenue * 100) if int_revenue > 0 else 0

    def _pnl_row(label, val, acct_val=None, indent=False, bold=False, sep=False):
        """Build one P&L row with optional accountant comparison."""
        style = "padding:4px 0;"
        if indent:
            style += "padding-left:16px;font-size:12px;color:#7a7265;"
        if bold:
            style += "font-weight:600;"
        if sep:
            style += "border-top:1px solid #e0d5bf;padding-top:8px;"

        _DASH = "-"
        cells = f'<td style="{style}">{label}</td>'
        cells += f'<td style="{style}text-align:right;font-family:JetBrains Mono,monospace;font-size:13px;">${val:,.0f}</td>'
        if acct is not None:
            if acct_val is not None:
                var = val - acct_val
                vcol = "#4a7c1f" if abs(var) < abs(acct_val) * 0.05 else ("#c47d0a" if abs(var) < abs(acct_val) * 0.15 else "#d9342b")
                sign = "+" if var > 0 else ""
                cells += f'<td style="{style}text-align:right;font-family:JetBrains Mono,monospace;font-size:13px;">${acct_val:,.0f}</td>'
                cells += f'<td style="{style}text-align:right;font-family:JetBrains Mono,monospace;font-size:12px;color:{vcol};">{sign}${var:,.0f}</td>'
            else:
                cells += f'<td style="{style}text-align:right;color:#ccc;">{_DASH}</td>'
                cells += f'<td style="{style}text-align:right;color:#ccc;">{_DASH}</td>'
        return f"<tr>{cells}</tr>"

    def _pnl_pct_row(label, pct, acct_pct=None):
        style = "padding:2px 0;font-size:11px;color:#7a7265;"
        cells = f'<td style="{style}">{label}</td>'
        cells += f'<td style="{style}text-align:right;font-family:JetBrains Mono,monospace;">{pct:.1f}%</td>'
        if acct is not None:
            if acct_pct is not None:
                cells += f'<td style="{style}text-align:right;font-family:JetBrains Mono,monospace;">{acct_pct:.1f}%</td>'
                cells += '<td></td>'
            else:
                cells += '<td></td><td></td>'
        return f"<tr>{cells}</tr>"

    # Header row
    hdr_cols = '<th style="text-align:left;">Category</th><th style="text-align:right;">Internal Est.</th>'
    if acct is not None:
        hdr_cols += '<th style="text-align:right;">Accountant</th><th style="text-align:right;">Variance</th>'

    acct_gp = (acct["gross_profit"] / acct["revenue"] * 100) if acct and acct["revenue"] > 0 else None
    acct_np = (acct["net_income"] / acct["revenue"] * 100) if acct and acct["revenue"] > 0 else None

    pnl_rows = ""
    # REVENUE
    pnl_rows += '<tr><td colspan="4" style="font-weight:700;font-size:12px;color:#475417;padding:10px 0 4px;text-transform:uppercase;letter-spacing:0.5px;">Revenue</td></tr>'
    pnl_rows += _pnl_row("Toast Sales", d["toast"])
    if d["forkable"] > 0:
        pnl_rows += _pnl_row("Forkable", d["forkable"], indent=True)
    if d["ezcater"] > 0:
        pnl_rows += _pnl_row("EZ Cater", d["ezcater"], indent=True)
    pnl_rows += _pnl_row("Total Revenue", int_revenue, acct.get("revenue") if acct else None, bold=True, sep=True)

    # COGS
    pnl_rows += '<tr><td colspan="4" style="font-weight:700;font-size:12px;color:#475417;padding:10px 0 4px;text-transform:uppercase;letter-spacing:0.5px;">Cost of Goods</td></tr>'
    pnl_rows += _pnl_row("Food Cost (35% of production)", int_food, acct.get("cogs") if acct else None)
    pnl_rows += _pnl_row("Gross Profit", int_gross, acct.get("gross_profit") if acct else None, bold=True, sep=True)
    pnl_rows += _pnl_pct_row("Gross Margin", int_gross_pct, acct_gp)

    # OPERATING EXPENSES
    pnl_rows += '<tr><td colspan="4" style="font-weight:700;font-size:12px;color:#475417;padding:10px 0 4px;text-transform:uppercase;letter-spacing:0.5px;">Operating Expenses</td></tr>'
    pnl_rows += _pnl_row("Labor", d["labor_hourly"] + d["payroll_taxes"], acct.get("labor") if acct else None)
    pnl_rows += _pnl_row("Hourly Pay", d["labor_hourly"], indent=True)
    pnl_rows += _pnl_row("Payroll Tax", d["payroll_taxes"], indent=True)
    if d["ot_pay"] > 0:
        pnl_rows += _pnl_row("OT Premium (incl. above)", d["ot_pay"], indent=True)
    if d["vacation"] > 0:
        pnl_rows += _pnl_row("Vacation", d["vacation"])
    if d["sick"] > 0:
        pnl_rows += _pnl_row("Sick", d["sick"])
    pnl_rows += _pnl_row("Fixed Costs", d["fixed"])
    if d["misc"] > 0:
        pnl_rows += _pnl_row("Misc", d["misc"])
    pnl_rows += _pnl_row("Total OpEx", int_opex, acct.get("total_opex") if acct else None, bold=True, sep=True)

    # SERVICE FEES
    pnl_rows += '<tr><td colspan="4" style="font-weight:700;font-size:12px;color:#475417;padding:10px 0 4px;text-transform:uppercase;letter-spacing:0.5px;">Service Fees</td></tr>'
    if d["gh_fees"] > 0:
        pnl_rows += _pnl_row("GH Fees", d["gh_fees"], indent=True)
    if d["dd_fees"] > 0:
        pnl_rows += _pnl_row("DD Fees", d["dd_fees"], indent=True)
    if d["uber_fees"] > 0:
        pnl_rows += _pnl_row("Uber Fees", d["uber_fees"], indent=True)
    if d["uber_ads"] > 0:
        pnl_rows += _pnl_row("Uber Ads", d["uber_ads"], indent=True)
    if d["catering_fees"] > 0:
        pnl_rows += _pnl_row("3P Catering Fees", d["catering_fees"], indent=True)
    if d["shipday_fees"] > 0:
        pnl_rows += _pnl_row("Shipday Fees", d["shipday_fees"], indent=True)
    pnl_rows += _pnl_row("Total Fees", int_fees, acct.get("fees") if acct else None, bold=True, sep=True)

    # NET PROFIT
    pnl_rows += '<tr><td colspan="4" style="height:6px;"></td></tr>'
    pcol = "#4a7c1f" if int_profit >= 0 else "#d9342b"
    _pfmt = f'<span style="color:{pcol};font-weight:700;">${int_profit:,.0f}</span>'
    _acol = ""
    _vcol = ""
    if acct is not None:
        an = acct.get("net_income", 0)
        apcol = "#4a7c1f" if an >= 0 else "#d9342b"
        _acol = f'<td style="text-align:right;font-family:JetBrains Mono,monospace;font-size:13px;"><span style="color:{apcol};font-weight:700;">${an:,.0f}</span></td>'
        var = int_profit - an
        sign = "+" if var > 0 else ""
        _vcol = f'<td style="text-align:right;font-family:JetBrains Mono,monospace;font-size:13px;font-weight:600;">{sign}${var:,.0f}</td>'
    pnl_rows += f'<tr style="border-top:2px solid #475417;"><td style="font-weight:700;padding-top:8px;">Net Profit</td><td style="text-align:right;font-family:JetBrains Mono,monospace;font-size:13px;padding-top:8px;">{_pfmt}</td>{_acol}{_vcol}</tr>'
    pnl_rows += _pnl_pct_row("Profit Margin", int_profit_pct, acct_np)

    acct_badge = ""
    if acct is not None:
        acct_badge = '<span style="display:inline-block;background:#e8f5e9;color:#2e7d32;font-size:10px;font-weight:600;padding:2px 8px;border-radius:4px;margin-left:8px;">Accountant data available</span>'

    pnl_html = f"""<div class="card">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;">
    <span style="font-weight:700;font-size:16px;color:#475417;">P&L Summary</span>
    <span style="font-size:10px;color:#7a7265;background:#faf6ee;padding:3px 8px;border-radius:4px;">Internal Estimate{acct_badge}</span>
  </div>
  <p style="font-size:11px;color:#999;margin-bottom:10px;">Operational estimates from daily Toast data. Accountant data is the source of truth.</p>
  <table style="width:100%;border-collapse:collapse;font-size:13px;">
  <thead><tr>{hdr_cols}</tr></thead>
  <tbody>{pnl_rows}</tbody>
  </table>
</div>"""

    # Comparison chart (if accountant data available)
    comparison_chart_html = ""
    comparison_chart_js = ""
    if acct is not None:
        comp_labels = json.dumps(["Revenue", "COGS", "Labor", "Fees", "Profit"])
        comp_internal = json.dumps([round(int_revenue), round(int_food), round(d["labor_hourly"] + d["payroll_taxes"]), round(int_fees), round(int_profit)])
        comp_acct = json.dumps([round(acct.get("revenue", 0)), round(acct.get("cogs", 0)), round(acct.get("labor", 0)), round(acct.get("fees", 0)), round(acct.get("net_income", 0))])
        comparison_chart_html = f"""<div class="card">
  <div style="font-weight:600;font-size:14px;color:#475417;margin-bottom:8px;">Estimate vs Accountant</div>
  <canvas id="compChart" height="200"></canvas>
</div>"""
        comparison_chart_js = f"""
new Chart(document.getElementById('compChart'), {{
    type: 'bar',
    data: {{
        labels: {comp_labels},
        datasets: [
            {{ label: 'Internal Estimate', data: {comp_internal}, backgroundColor: '#8cb82e', borderRadius: 4 }},
            {{ label: 'Accountant Actual', data: {comp_acct}, backgroundColor: '#475417', borderRadius: 4 }}
        ]
    }},
    options: {{
        responsive: true,
        plugins: {{ legend: {{ position: 'bottom', labels: {{ font: {{ size: 11 }} }} }} }},
        scales: {{
            y: {{ ticks: {{ callback: v => '$' + v.toLocaleString() }} }},
            x: {{ ticks: {{ font: {{ size: 11 }} }} }}
        }}
    }}
}});"""

    # Build table rows
    table_rows = ""
    profit_values = []
    date_labels = []
    for r in rows:
        if r.get("error"):
            table_rows += f"<tr style='color:#999;'><td>{r['day']} {r['display']}</td><td colspan='6' style='text-align:center;font-size:12px;'>No data</td></tr>"
            profit_values.append(0)
        else:
            pc = f"{r['pct']:.1f}%"
            pcol = "#4a7c1f" if r['profit'] >= 0 else "#d9342b"
            table_rows += f"""<tr onclick="window.location='/profit/{r['date']}'" style="cursor:pointer;">
                <td><strong>{r['day']}</strong> {r['display']}</td>
                <td style="text-align:right;">${r['sales']:,.0f}</td>
                <td style="text-align:right;">${r['labor']:,.0f}</td>
                <td style="text-align:right;">${r['food']:,.0f}</td>
                <td style="text-align:right;">${r['fees']:,.0f}</td>
                <td style="text-align:right;color:{pcol};font-weight:600;">${r['profit']:,.0f}</td>
                <td style="text-align:right;color:{pcol};">{pc}</td>
            </tr>"""
            profit_values.append(round(r['profit']))
        date_labels.append(f"{r['day']} {r['display']}")

    display_range = f"{start.strftime('%b %d')} - {end.strftime('%b %d, %Y')} ({days} days)"

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Profit Calc — {display_range}</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
<style>
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{font-family:'DM Sans',sans-serif;background:#F5EDDC;color:#2d2a24;padding:16px;}}
.container{{max-width:960px;margin:0 auto;}}
h1{{font-size:22px;font-weight:700;color:#475417;margin-bottom:4px;}}
.nav{{display:flex;align-items:center;gap:10px;margin-bottom:16px;}}
.nav a{{color:#4a7c1f;text-decoration:none;font-size:13px;font-weight:500;}}
.nav a:hover{{text-decoration:underline;}}
.card{{background:#fff;border:1px solid #e0d5bf;border-radius:12px;padding:20px;margin-bottom:14px;box-shadow:0 1px 3px rgba(0,0,0,0.04);}}
.kpi-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:14px;}}
.kpi{{background:#fff;border:1px solid #e0d5bf;border-radius:12px;padding:16px;text-align:center;}}
.kpi-label{{font-size:11px;font-weight:600;color:#7a7265;text-transform:uppercase;letter-spacing:0.5px;}}
.kpi-value{{font-size:26px;font-weight:700;font-family:'JetBrains Mono',monospace;margin:4px 0;}}
.kpi-sub{{font-size:12px;color:#7a7265;font-family:'JetBrains Mono',monospace;}}
table{{width:100%;border-collapse:collapse;font-size:13px;}}
th{{text-align:left;font-size:11px;font-weight:600;color:#7a7265;text-transform:uppercase;letter-spacing:0.5px;padding:8px 6px;border-bottom:2px solid #e0d5bf;}}
td{{padding:8px 6px;border-bottom:1px solid #f0e8d6;}}
tr:hover{{background:#faf6ee;}}
</style>
</head><body>
<div class="container">

<div class="nav">
  <a href="/">&larr; Home</a>
  <a href="/profit">&larr; Single Day</a>
</div>

<h1>Profit Calc</h1>
<p style="color:#7a7265;font-size:13px;margin-bottom:14px;">{display_range}</p>

<div class="kpi-grid">
  <div class="kpi">
    <div class="kpi-label">Total Profit</div>
    <div class="kpi-value" style="color:{profit_color}">${totals['profit']:,.0f}</div>
    <div class="kpi-sub">{total_pct:.1f}% margin</div>
  </div>
  <div class="kpi">
    <div class="kpi-label">Avg Daily Profit</div>
    <div class="kpi-value" style="color:{profit_color}">${avg_profit:,.0f}</div>
    <div class="kpi-sub">per day</div>
  </div>
  <div class="kpi">
    <div class="kpi-label">Total Sales</div>
    <div class="kpi-value">${totals['sales']:,.0f}</div>
    <div class="kpi-sub">${totals['sales']/max(days,1):,.0f}/day avg</div>
  </div>
  <div class="kpi">
    <div class="kpi-label">Total Labor</div>
    <div class="kpi-value">${totals['labor']:,.0f}</div>
    <div class="kpi-sub">{totals['hours']:.0f} hours</div>
  </div>
  <div class="kpi">
    <div class="kpi-label">Avg FTEs / Day</div>
    <div class="kpi-value">{avg_fte:.1f}</div>
    <div class="kpi-sub">Avg Wage: ${avg_wage:.2f}/hr</div>
  </div>
</div>

{pnl_html}
{comparison_chart_html}

<div class="card">
  <canvas id="profitChart" height="180"></canvas>
</div>

<div class="card">
<table>
<thead><tr>
  <th>Date</th><th style="text-align:right;">Sales</th><th style="text-align:right;">Labor</th>
  <th style="text-align:right;">Food Cost</th><th style="text-align:right;">Fees</th>
  <th style="text-align:right;">Profit</th><th style="text-align:right;">%</th>
</tr></thead>
<tbody>{table_rows}</tbody>
</table>
</div>

</div>
<script>
new Chart(document.getElementById('profitChart'), {{
    type: 'bar',
    data: {{
        labels: {json.dumps(date_labels)},
        datasets: [{{
            label: 'Daily Profit',
            data: {json.dumps(profit_values)},
            backgroundColor: {json.dumps(profit_values)}.map(v => v >= 0 ? '#8cb82e' : '#e86040'),
            borderRadius: 4,
        }}]
    }},
    options: {{
        responsive: true,
        plugins: {{ legend: {{ display: false }} }},
        scales: {{
            y: {{ ticks: {{ callback: v => '$' + v.toLocaleString() }} }},
            x: {{ ticks: {{ font: {{ size: 10 }} }} }}
        }}
    }}
}});
{comparison_chart_js}
</script>
</body></html>"""


# ── PTO Balance Tracking ──

PTO_BALANCES_PATH = os.path.join(PROJECT_ROOT, '.tmp', 'pto_balances.json')
TMP_DIR = os.path.join(PROJECT_ROOT, '.tmp')


def _load_pto_data():
    """Load PTO data (accrual config + Gusto baseline)."""
    if os.path.exists(PTO_BALANCES_PATH):
        with open(PTO_BALANCES_PATH) as f:
            return json.load(f)
    return {
        "period_start": datetime.now().strftime("%Y-01-01"),
        "baseline_date": "",
        "accrual": {"sick_per_hours": 30, "sick_annual_cap": 40,
                    "vacation_per_hours": 51, "vacation_exclude_ot": True},
        "gusto_used": {},
    }


def _compute_hours_worked(period_start_str):
    """Scan TimeEntries CSVs from period_start to today, sum per-employee hours.
    Returns {employee: {regular: X, ot: X, total: X}}."""
    import csv
    result = {}
    if not period_start_str:
        return result
    start = datetime.strptime(period_start_str, "%Y-%m-%d")
    today = datetime.now()
    aliases = _cfg.get("employee_aliases", {})
    d = start
    while d <= today:
        ds = d.strftime("%Y%m%d")
        te_path = os.path.join(TMP_DIR, ds, "TimeEntries.csv")
        if os.path.exists(te_path):
            try:
                with open(te_path, newline='', encoding='utf-8-sig') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        emp = row.get("Employee", "").strip().lower()
                        emp = aliases.get(emp, emp)
                        reg = float(row.get("Regular Hours", 0) or 0)
                        ot = float(row.get("Overtime Hours", 0) or 0)
                        if emp:
                            if emp not in result:
                                result[emp] = {"regular": 0, "ot": 0, "total": 0}
                            result[emp]["regular"] += reg
                            result[emp]["ot"] += ot
                            result[emp]["total"] += reg + ot
            except Exception as e:
                logger.debug("Payroll hours accumulation failed for day: %s", e)
        d += timedelta(days=1)
    return result


def _compute_additional_pto_used(baseline_date_str):
    """Scan daily profit files AFTER baseline_date for additional vacation/sick entries."""
    used = {}
    if not baseline_date_str:
        return used
    # Start the day AFTER baseline (Gusto covers up to baseline)
    start = datetime.strptime(baseline_date_str, "%Y-%m-%d") + timedelta(days=1)
    today = datetime.now()
    d = start
    while d <= today:
        ds = d.strftime("%Y%m%d")
        path = os.path.join(PROFIT_MANUAL_DIR, f"{ds}.json")
        if os.path.exists(path):
            with open(path) as f:
                manual = json.load(f)
            for pto_type in ("vacation", "sick"):
                entries = manual.get(pto_type, [])
                if isinstance(entries, list):
                    for e in entries:
                        emp = e.get("employee", "")
                        hrs = e.get("hours", 0)
                        if emp and hrs > 0:
                            if emp not in used:
                                used[emp] = {"vacation": 0, "sick": 0}
                            used[emp][pto_type] += hrs
        d += timedelta(days=1)
    return used


@app.route('/pto')
@owner_required
def pto_page():
    """PTO balance tracking page — accrual-based."""
    pto = _load_pto_data()
    period_start = pto.get("period_start", "2026-01-01")
    baseline_date = pto.get("baseline_date", "")
    accrual = pto.get("accrual", {})
    gusto_used = pto.get("gusto_used", {})
    sick_rate = accrual.get("sick_per_hours", 30)
    sick_cap = accrual.get("sick_annual_cap", 40)
    vac_rate = accrual.get("vacation_per_hours", 51)

    # Compute hours worked from TimeEntries
    hours_worked = _compute_hours_worked(period_start)

    # Compute additional usage after Gusto baseline
    additional = _compute_additional_pto_used(baseline_date) if baseline_date else {}

    # Build employee list: union of config wages + gusto + hours_worked
    cfg_wages = _cfg.get("wages", {})
    all_emps = set()
    for emp, info in cfg_wages.items():
        if info.get("type") != "owner":
            all_emps.add(emp)
    all_emps.update(gusto_used.keys())
    all_emps.update(hours_worked.keys())
    employees = sorted(all_emps)

    # Build rows and totals
    table_rows = ""
    tot_sick_accrued = tot_sick_used = tot_sick_bal = 0
    tot_vac_accrued = tot_vac_used = tot_vac_bal = 0
    for emp in employees:
        parts = emp.split(", ")
        display = f"{parts[1].title()} {parts[0].title()}" if len(parts) == 2 else emp.title()

        hw = hours_worked.get(emp, {"regular": 0, "ot": 0, "total": 0})
        # Sick accrual: 1hr per sick_rate total hours, capped
        sick_accrued = min(hw["total"] / sick_rate, sick_cap) if sick_rate > 0 else 0
        # Vacation accrual: 1hr per vac_rate regular hours (no OT)
        vac_accrued = hw["regular"] / vac_rate if vac_rate > 0 else 0

        g = gusto_used.get(emp, {"sick": 0, "vacation": 0})
        a = additional.get(emp, {"sick": 0, "vacation": 0})
        sick_used = g.get("sick", 0) + a.get("sick", 0)
        vac_used = g.get("vacation", 0) + a.get("vacation", 0)

        sick_bal = sick_accrued - sick_used
        vac_bal = vac_accrued - vac_used
        sick_col = "#4a7c1f" if sick_bal >= 0 else "#d9342b"
        vac_col = "#4a7c1f" if vac_bal >= 0 else "#d9342b"

        tot_sick_accrued += sick_accrued
        tot_sick_used += sick_used
        tot_sick_bal += sick_bal
        tot_vac_accrued += vac_accrued
        tot_vac_used += vac_used
        tot_vac_bal += vac_bal

        mono = "font-family:'JetBrains Mono',monospace;"
        table_rows += f"""<tr>
  <td>{display}</td>
  <td style="text-align:center;{mono}font-size:12px;">{hw['total']:.0f}</td>
  <td style="text-align:center;{mono}font-size:12px;">{sick_accrued:.1f}</td>
  <td style="text-align:center;{mono}font-size:12px;">{sick_used:.1f}</td>
  <td style="text-align:center;{mono}font-weight:600;color:{sick_col};">{sick_bal:.1f}</td>
  <td style="text-align:center;{mono}font-size:12px;">{vac_accrued:.1f}</td>
  <td style="text-align:center;{mono}font-size:12px;">{vac_used:.1f}</td>
  <td style="text-align:center;{mono}font-weight:600;color:{vac_col};">{vac_bal:.1f}</td>
</tr>"""

    # Totals row
    mono = "font-family:'JetBrains Mono',monospace;"
    tot_sick_col = "#4a7c1f" if tot_sick_bal >= 0 else "#d9342b"
    tot_vac_col = "#4a7c1f" if tot_vac_bal >= 0 else "#d9342b"
    table_rows += f"""<tr style="border-top:2px solid #475417;font-weight:600;">
  <td>TOTAL</td>
  <td style="text-align:center;{mono}">&mdash;</td>
  <td style="text-align:center;{mono}">{tot_sick_accrued:.1f}</td>
  <td style="text-align:center;{mono}">{tot_sick_used:.1f}</td>
  <td style="text-align:center;{mono}color:{tot_sick_col};">{tot_sick_bal:.1f}</td>
  <td style="text-align:center;{mono}">{tot_vac_accrued:.1f}</td>
  <td style="text-align:center;{mono}">{tot_vac_used:.1f}</td>
  <td style="text-align:center;{mono}color:{tot_vac_col};">{tot_vac_bal:.1f}</td>
</tr>"""

    bl_display = baseline_date if baseline_date else "not set"

    return Response(f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PTO Balances</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{font-family:'DM Sans',sans-serif;background:#F5EDDC;color:#2d2a24;padding:16px;}}
.container{{max-width:1080px;margin:0 auto;}}
h1{{font-size:22px;font-weight:700;color:#475417;margin-bottom:4px;}}
.nav{{display:flex;align-items:center;gap:10px;margin-bottom:16px;}}
.nav a{{color:#4a7c1f;text-decoration:none;font-size:13px;font-weight:500;}}
.nav a:hover{{text-decoration:underline;}}
.card{{background:#fff;border:1px solid #e0d5bf;border-radius:12px;padding:20px;margin-bottom:14px;box-shadow:0 1px 3px rgba(0,0,0,0.04);}}
table{{width:100%;border-collapse:collapse;font-size:13px;}}
th{{text-align:center;font-size:10px;font-weight:600;color:#7a7265;text-transform:uppercase;letter-spacing:0.5px;padding:6px 4px;border-bottom:2px solid #e0d5bf;}}
th:first-child{{text-align:left;}}
td{{padding:6px 4px;border-bottom:1px solid #f0e8d6;font-size:12px;}}
tr:hover{{background:#faf6ee;}}
.section-hdr{{background:#faf6ee;padding:4px 6px;font-size:10px;font-weight:600;color:#475417;text-transform:uppercase;letter-spacing:0.5px;}}
.info-grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:14px;}}
.info-item{{font-size:12px;color:#7a7265;}}
.info-item strong{{color:#475417;font-weight:600;}}
.badge{{display:inline-block;padding:2px 8px;border-radius:4px;font-size:10px;font-weight:600;}}
.upload-area{{border:2px dashed #e0d5bf;border-radius:12px;padding:16px;text-align:center;cursor:pointer;color:#7a7265;font-size:12px;transition:border-color 0.2s;}}
.upload-area:hover{{border-color:#8cb82e;}}
#status{{margin-top:8px;font-size:12px;color:#7a7265;}}
</style>
</head><body>
<div class="container">

<div class="nav">
  <a href="/">&larr; Home</a>
  <a href="/profit">&larr; Profit Calc</a>
</div>

<h1>PTO Balances</h1>
<p style="color:#7a7265;font-size:13px;margin-bottom:14px;">Accrual-based tracking. Hours worked from Toast TimeEntries; usage from Gusto import + daily profit entries.</p>

<div class="card">
  <div class="info-grid">
    <div class="info-item"><strong>Period Start:</strong> {period_start}</div>
    <div class="info-item"><strong>Gusto Baseline:</strong> {bl_display}</div>
    <div class="info-item"><strong>Sick Accrual:</strong> 1 hr per {sick_rate} hrs worked (cap {sick_cap} hrs/yr)</div>
    <div class="info-item"><strong>Vacation Accrual:</strong> 1 hr per {vac_rate} hrs worked (no OT)</div>
  </div>
  <div>
    <label for="csvUpload" class="upload-area" id="uploadArea">
      Upload Gusto PTO report (CSV or PDF text) to update usage baseline
      <input type="file" id="csvUpload" accept=".csv" style="display:none;" onchange="handleUpload(this)">
    </label>
    <div id="uploadStatus" style="margin-top:6px;font-size:11px;color:#7a7265;"></div>
  </div>
</div>

<div class="card" style="overflow-x:auto;">
  <table>
    <thead>
      <tr>
        <th style="text-align:left;" rowspan="2">Employee</th>
        <th rowspan="2">Hrs<br>Worked</th>
        <th colspan="3" class="section-hdr" style="border-bottom:1px solid #e0d5bf;">Sick Time (hrs)</th>
        <th colspan="3" class="section-hdr" style="border-bottom:1px solid #e0d5bf;">Vacation (hrs)</th>
      </tr>
      <tr>
        <th>Accrued</th><th>Used</th><th>Balance</th>
        <th>Accrued</th><th>Used</th><th>Balance</th>
      </tr>
    </thead>
    <tbody>{table_rows}</tbody>
  </table>
  <div id="status"></div>
</div>

</div>
<script>
function handleUpload(input) {{
    const file = input.files[0];
    if (!file) return;
    const status = document.getElementById('uploadStatus');
    status.textContent = 'Processing...';

    const reader = new FileReader();
    reader.onload = function(e) {{
        const text = e.target.result;
        // Send to server for processing
        fetch('/api/pto/upload', {{
            method: 'POST',
            headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify({{ csv_text: text }})
        }}).then(r => r.json()).then(d => {{
            if (d.ok) {{
                status.textContent = 'Updated ' + d.count + ' employees. Reloading...';
                setTimeout(() => window.location.reload(), 1000);
            }} else {{
                status.textContent = 'Error: ' + (d.error || 'Unknown');
            }}
        }});
    }};
    reader.readAsText(file);
    input.value = '';
}}
</script>
</body></html>""", content_type='text/html')


@app.route('/api/pto/upload', methods=['POST'])
@owner_required
def api_pto_upload():
    """Process uploaded Gusto PTO CSV and update baseline."""
    import csv as csv_mod
    from io import StringIO
    body = request.get_json(silent=True) or {}
    csv_text = body.get("csv_text", "")
    if not csv_text:
        return jsonify({"ok": False, "error": "No CSV data"})

    # Load existing PTO data
    pto = _load_pto_data()
    aliases = _cfg.get("employee_aliases", {})

    # Name mapping: "First Last" → "last, first" with special cases
    GUSTO_NAMES = {
        "sheung kwan yin": "yin, shangjun",
        "rosemary vasquez": "andrea vasquez, rosmery",
        "elizabeth tytell": "tytell, ellie",
        "josiah odonnell": "odonnell, jed",
        "abigail o'halloran": "o'halloran, abby",
        "abigail ohalloran": "o'halloran, abby",
        "jacob nagle": "nagle, jake",
        "gabriela levitt": "levitt, gabby",
        "deixon leon": "leon lacruz, deixon",
        "lourdes valeria jerez": "jerez, valeria",
        "leidy hincapie": "chavarria, leidy",
        "leidy hincapi\u00e9": "chavarria, leidy",
        "jairo catano": "catano, leon",
        "simone zierton-singleton": "zierten-singleton, simone",
        "andrea vasquez": "vasquez, andrea",
    }

    def gusto_name_to_config(name):
        nl = name.strip().lower()
        if nl in GUSTO_NAMES:
            return GUSTO_NAMES[nl]
        parts = nl.split()
        if len(parts) == 2:
            return f"{parts[1]}, {parts[0]}"
        return nl

    # Parse CSV
    lines = csv_text.strip().split('\n')
    reader = csv_mod.reader(lines)
    header = None
    count = 0
    gusto_used = {}
    for row in reader:
        if not header:
            header = [h.strip().lower() for h in row]
            continue
        if len(row) < 4:
            continue
        name = row[0].strip()
        if name.lower() in ("grand totals", "employee", ""):
            continue
        try:
            sick_hrs = float(row[1].replace('$', '').replace(',', '') or 0)
        except (ValueError, IndexError):
            sick_hrs = 0
        try:
            vac_hrs = float(row[3].replace('$', '').replace(',', '') or 0)
        except (ValueError, IndexError):
            vac_hrs = 0
        key = gusto_name_to_config(name)
        key = aliases.get(key, key)
        gusto_used[key] = {"sick": sick_hrs, "vacation": vac_hrs}
        count += 1

    pto["gusto_used"] = gusto_used
    pto["baseline_date"] = datetime.now().strftime("%Y-%m-%d")

    os.makedirs(os.path.dirname(PTO_BALANCES_PATH), exist_ok=True)
    with open(PTO_BALANCES_PATH, 'w') as f:
        json.dump(pto, f, indent=2)

    return jsonify({"ok": True, "count": count})


# ── Driver Tip Tracking ──

def _driver_week_detail(w, auto_open=False):
    """Build HTML for a single week's expandable detail block."""
    m = w["mustafa"]
    mb = w["metrobi"]
    adj = w["total_tip_adjustment"]
    adj_color = "#d9342b" if adj > 0 else "#7a7265"

    m_summary = '%d deliver%s, $%s payout' % (m["count"], "y" if m["count"] == 1 else "ies", "{:,.2f}".format(m.get("payout", 0))) if m["count"] > 0 else "No deliveries"
    mb_tip_cost = mb.get("cost_from_tips", 0)
    mb_summary = '%d deliver%s, $%s from tips' % (mb["count"], "y" if mb["count"] == 1 else "ies", "{:,.2f}".format(mb_tip_cost)) if mb["count"] > 0 else "No deliveries"

    # Mustafa detail table — fee, tips, fee used, reserve, payout
    m_detail = ""
    if m["count"] > 0:
        _TH = 'style="padding:4px 8px;text-align:%s;font-weight:600;color:#7a7265;"'
        _TD = 'style="padding:4px 8px;text-align:%s;"'
        m_rows = ""
        for o in m["orders"]:
            dt = o["date"][5:].replace("-", "/")
            m_rows += '<tr><td %s>%s</td><td %s>%s</td><td %s>$%s</td><td %s>$%s</td><td %s>$%s</td><td %s>$%s</td><td %s>$%s</td></tr>' % (
                _TD % "left", dt, _TD % "left", o["name"],
                _TD % "right", "{:,.2f}".format(o.get("delivery_fee", 0)),
                _TD % "right", "{:,.2f}".format(o["tips"]),
                _TD % "right", "{:,.2f}".format(o.get("fee_to_driver", 0)),
                _TD % "right", "{:,.2f}".format(o.get("reserve", 0)),
                _TD % "right", "{:,.2f}".format(o.get("payout", 0)))
        reserve_note = ""
        if m.get("reserve", 0) > 0:
            reserve_note = " | Reserve banked: $%s" % "{:,.2f}".format(m["reserve"])
        shortfall_note = ""
        if m.get("shortfall", 0) > 0:
            shortfall_note = " | Shortfall (from reserve): $%s" % "{:,.2f}".format(m["shortfall"])
        m_detail = """
        <div style="margin-bottom:12px;">
          <div style="font-weight:600;font-size:12px;color:#475417;margin-bottom:4px;">Mustafa Orders <span style="font-weight:400;color:#7a7265;">($45 guarantee/delivery)</span></div>
          <table style="width:100%%;border-collapse:collapse;font-size:13px;">
            <thead><tr style="border-bottom:1px solid #e0d5bf;">
              <th %s>Date</th>
              <th %s>Order</th>
              <th %s>Del. Fee</th>
              <th %s>Tips</th>
              <th %s>Fee Used</th>
              <th %s>Reserve</th>
              <th %s>Payout</th>
            </tr></thead>
            <tbody>%s
              <tr style="border-top:1px solid #e0d5bf;font-weight:600;">
                <td colspan="2" style="padding:4px 8px;">Total</td>
                <td style="padding:4px 8px;text-align:right;">$%s</td>
                <td style="padding:4px 8px;text-align:right;">$%s</td>
                <td style="padding:4px 8px;text-align:right;">$%s</td>
                <td style="padding:4px 8px;text-align:right;">$%s</td>
                <td style="padding:4px 8px;text-align:right;">$%s</td>
              </tr>
            </tbody>
          </table>
          <div style="margin-top:6px;font-size:12px;color:#7a7265;font-style:italic;">
            Tip pool deduction: $%s (Mustafa keeps his tips)%s%s
          </div>
        </div>""" % (
            _TH % "left", _TH % "left", _TH % "right", _TH % "right",
            _TH % "right", _TH % "right", _TH % "right",
            m_rows,
            "{:,.2f}".format(m.get("fees", 0)),
            "{:,.2f}".format(m["tips"]),
            "{:,.2f}".format(m.get("fee_used", 0)),
            "{:,.2f}".format(m.get("reserve", 0)),
            "{:,.2f}".format(m.get("payout", 0)),
            "{:,.2f}".format(m["tips"]),
            reserve_note, shortfall_note)

    # Metrobi detail table — fee covers cost first, remainder from tips
    mb_detail = ""
    if mb["count"] > 0:
        _TH = 'style="padding:4px 8px;text-align:%s;font-weight:600;color:#7a7265;"'
        _TD = 'style="padding:4px 8px;text-align:%s;"'
        mb_rows = ""
        for o in mb["orders"]:
            dt = o["date"][5:].replace("-", "/")
            from_tips = o.get("cost_from_tips", 0)
            to_team = o.get("tips_to_team", o["tips"])
            team_color = "#4a7c1f" if to_team >= 0 else "#d9342b"
            mb_rows += '<tr><td %s>%s</td><td %s>%s</td><td %s>$%s</td><td %s>$%s</td><td %s>$%s</td><td %s>$%s</td><td %s><span style="color:%s;">$%s</span></td></tr>' % (
                _TD % "left", dt, _TD % "left", o["name"],
                _TD % "right", "{:,.2f}".format(o["delivery_fee"]),
                _TD % "right", "{:,.2f}".format(o["tips"]),
                _TD % "right", "{:,.2f}".format(o["driver_cost"]),
                _TD % "right", "{:,.2f}".format(from_tips),
                _TD % "right", team_color, "{:,.2f}".format(to_team))
        tips_to_team = mb.get("tips_to_team", 0)
        team_color_t = "#4a7c1f" if tips_to_team >= 0 else "#d9342b"
        mb_detail = """
        <div style="margin-bottom:12px;">
          <div style="font-weight:600;font-size:12px;color:#475417;margin-bottom:4px;">Metrobi Orders</div>
          <table style="width:100%%;border-collapse:collapse;font-size:13px;">
            <thead><tr style="border-bottom:1px solid #e0d5bf;">
              <th %s>Date</th>
              <th %s>Order</th>
              <th %s>Del. Fee</th>
              <th %s>Tips</th>
              <th %s>Cost</th>
              <th %s>From Tips</th>
              <th %s>Tips to Team</th>
            </tr></thead>
            <tbody>%s
              <tr style="border-top:1px solid #e0d5bf;font-weight:600;">
                <td colspan="2" style="padding:4px 8px;">Total</td>
                <td style="padding:4px 8px;text-align:right;">$%s</td>
                <td style="padding:4px 8px;text-align:right;">$%s</td>
                <td style="padding:4px 8px;text-align:right;">$%s</td>
                <td style="padding:4px 8px;text-align:right;">$%s</td>
                <td style="padding:4px 8px;text-align:right;color:%s;">$%s</td>
              </tr>
            </tbody>
          </table>
          <div style="margin-top:6px;font-size:12px;color:#7a7265;font-style:italic;">
            Del. fee covers cost first. Shortfall comes from tips. Remaining tips stay with the team.
          </div>
        </div>""" % (
            _TH % "left", _TH % "left", _TH % "right", _TH % "right",
            _TH % "right", _TH % "right", _TH % "right",
            mb_rows,
            "{:,.2f}".format(mb["fees_collected"]),
            "{:,.2f}".format(mb["tips"]),
            "{:,.2f}".format(mb["driver_cost"]),
            "{:,.2f}".format(mb_tip_cost),
            team_color_t, "{:,.2f}".format(tips_to_team))

    # Adjustment summary
    parts = []
    if m["count"] > 0:
        parts.append("Mustafa tips $%s" % "{:,.2f}".format(m["tips"]))
    if mb["count"] > 0 and mb_tip_cost > 0:
        parts.append("Metrobi from tips $%s" % "{:,.2f}".format(mb_tip_cost))
    breakdown = " + ".join(parts) if parts else ""
    adj_summary = """
    <div style="margin-top:8px;padding:10px;background:#f0eed8;border-radius:6px;font-size:13px;">
      <strong>Tip Pool Deduction: <span style="color:%s;">$%s</span></strong>""" % (adj_color, "{:,.2f}".format(adj))
    if breakdown:
        adj_summary += " (%s)" % breakdown
    adj_summary += """
      <br><span style="font-size:11px;color:#7a7265;">Deduct this from the team tip pool before distributing</span>
    </div>"""

    open_attr = " open" if auto_open else ""
    return """
    <div style="background:#fff;border-radius:10px;border:1px solid #e0d5bf;margin-bottom:8px;">
      <details%s>
        <summary style="padding:14px 16px;cursor:pointer;display:flex;justify-content:space-between;align-items:center;list-style:none;">
          <div style="display:flex;gap:16px;align-items:center;flex-wrap:wrap;">
            <span style="font-weight:600;min-width:130px;">%s</span>
            <span style="font-size:13px;color:#7a7265;">Mustafa: %s</span>
            <span style="font-size:13px;color:#7a7265;">Metrobi: %s</span>
          </div>
          <span style="font-weight:700;color:%s;font-size:16px;white-space:nowrap;">-$%s</span>
        </summary>
        <div style="padding:0 16px 16px;border-top:1px solid #e0d5bf;">
          %s
          %s
          %s
        </div>
      </details>
    </div>""" % (open_attr, w["week_label"], m_summary, mb_summary,
                 adj_color, "{:,.2f}".format(adj),
                 m_detail, mb_detail, adj_summary)


@app.route('/drivers')
@owner_required
def drivers_page():
    """Weekly driver tip tracking — Mustafa & Metrobi."""
    try:
        from tools.catering.drivers import get_driver_weekly_data
        data = get_driver_weekly_data()
    except Exception as e:
        logger.error("Error loading driver data: %s", e, exc_info=True)
        return _error_page(f"Error loading driver data: {e}")

    weeks = data.get("weeks", [])
    mtd = data.get("mtd", {})

    # Week selection — offset 0 = this week (most recent), 1 = last week, etc.
    try:
        week_idx = int(request.args.get("week", "0"))
    except (ValueError, TypeError):
        week_idx = 0
    if week_idx < 0:
        week_idx = 0
    if weeks and week_idx >= len(weeks):
        week_idx = len(weeks) - 1

    sel = weeks[week_idx] if weeks else None
    prev_w = weeks[week_idx + 1] if weeks and week_idx + 1 < len(weeks) else None

    def _kpi(label, val):
        return f'<div style="text-align:center;"><div style="font-size:11px;color:#7a7265;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px;">{label}</div><div style="font-size:22px;font-weight:700;color:#2d2a24;">${val:,.2f}</div></div>'

    sel_m = sel["mustafa"]["tips"] if sel else 0
    sel_mb = sel["metrobi"]["cost_from_tips"] if sel else 0
    sel_adj = sel["total_tip_adjustment"] if sel else 0
    prev_m = prev_w["mustafa"]["tips"] if prev_w else 0
    prev_mb = prev_w["metrobi"]["cost_from_tips"] if prev_w else 0
    prev_adj = prev_w["total_tip_adjustment"] if prev_w else 0

    sel_label = sel["week_label"] if sel else "No data"
    prev_label = "Previous Week" if prev_w else "N/A"

    kpi_html = f"""
    <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-bottom:24px;">
      <div style="background:#fff;border-radius:10px;padding:20px;border:1px solid #e0d5bf;">
        <div style="font-size:12px;font-weight:600;color:#475417;margin-bottom:14px;">{sel_label}</div>
        <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;">
          {_kpi("Mustafa Tips", sel_m)}
          {_kpi("Metrobi from Tips", sel_mb)}
          {_kpi("Total Deduction", sel_adj)}
        </div>
      </div>
      <div style="background:#fff;border-radius:10px;padding:20px;border:1px solid #e0d5bf;">
        <div style="font-size:12px;font-weight:600;color:#475417;margin-bottom:14px;">{prev_label}</div>
        <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;">
          {_kpi("Mustafa Tips", prev_m)}
          {_kpi("Metrobi from Tips", prev_mb)}
          {_kpi("Total Deduction", prev_adj)}
        </div>
      </div>
      <div style="background:#fff;border-radius:10px;padding:20px;border:1px solid #e0d5bf;">
        <div style="font-size:12px;font-weight:600;color:#475417;margin-bottom:14px;">Month to Date</div>
        <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;">
          {_kpi("Mustafa Tips", mtd.get("mustafa_tips", 0))}
          {_kpi("Metrobi from Tips", mtd.get("metrobi_tip_cost", 0))}
          {_kpi("Total Deduction", mtd.get("total", 0))}
        </div>
      </div>
    </div>"""

    # Week selector buttons
    preset_labels = ["This Week", "Last Week", "2 Weeks Ago", "3 Weeks Ago"]
    presets_html = ""
    max_presets = min(len(preset_labels), len(weeks))
    for i in range(max_presets):
        active = ' style="background:#4a7c1f;color:#fff;border-color:#4a7c1f;"' if i == week_idx else ""
        presets_html += f'<a href="/drivers?week={i}" class="wk-btn"{active}>{preset_labels[i]}</a> '

    # Prev / Next nav
    nav_html = ""
    if weeks:
        prev_link = f'<a href="/drivers?week={week_idx + 1}" class="wk-nav">&larr; Older</a>' if week_idx + 1 < len(weeks) else '<span class="wk-nav" style="visibility:hidden;">&larr; Older</span>'
        next_link = f'<a href="/drivers?week={week_idx - 1}" class="wk-nav">Newer &rarr;</a>' if week_idx > 0 else '<span class="wk-nav" style="visibility:hidden;">Newer &rarr;</span>'
        center_label = sel["week_label"] if sel else ""
        nav_html = f'{prev_link}<span style="font-weight:600;font-size:14px;">{center_label}</span>{next_link}'

    # Selected week detail (auto-expanded)
    selected_detail = ""
    if sel:
        selected_detail = _driver_week_detail(sel, auto_open=True)

    # All other weeks collapsed below
    other_weeks = ""
    for i, w in enumerate(weeks):
        if i == week_idx:
            continue
        other_weeks += _driver_week_detail(w, auto_open=False)

    empty_msg = ""
    if not weeks:
        diag = ""
        try:
            from tools.catering.drivers import _last_diag
            if _last_diag:
                d = _last_diag
                parts = []
                parts.append("API Key: %s" % ("set" if d.get("api_key_set") else "MISSING"))
                if d.get("status"):
                    parts.append("Status: %s" % d["status"])
                if d.get("total_pages"):
                    parts.append("Pages scanned: %s" % d["total_pages"])
                if d.get("error"):
                    parts.append("Error: %s" % d["error"])
                diag = '<div style="font-size:11px;color:#999;margin-top:8px;">' + " | ".join(parts) + '</div>'
        except Exception as e:
            logger.debug("Driver diag info failed: %s", e)
        empty_msg = '<div style="text-align:center;padding:40px;color:#7a7265;">No driver delivery data found in Notion. Make sure Delivery Method is set to Mustafa or Metrobi on completed orders.%s</div>' % diag

    return Response(f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Driver Tip Tracking - Livite</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{font-family:'DM Sans',sans-serif;background:#F5EDDC;color:#2d2a24;padding:20px;}}
.container{{max-width:960px;margin:0 auto;}}
.nav{{margin-bottom:20px;}}
.nav a{{color:#4a7c1f;text-decoration:none;font-size:14px;font-weight:500;}}
.nav a:hover{{text-decoration:underline;}}
h1{{font-size:24px;font-weight:700;margin-bottom:8px;}}
.subtitle{{font-size:13px;color:#7a7265;margin-bottom:24px;}}
.wk-selector{{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:18px;}}
.wk-btn{{display:inline-block;padding:8px 16px;border-radius:6px;font-size:13px;font-weight:500;color:#2d2a24;background:#fff;border:1px solid #e0d5bf;text-decoration:none;cursor:pointer;}}
.wk-btn:hover{{background:#f0eed8;}}
.wk-pager{{display:flex;justify-content:space-between;align-items:center;margin-bottom:24px;}}
.wk-nav{{color:#4a7c1f;text-decoration:none;font-size:13px;font-weight:500;padding:4px 10px;}}
.wk-nav:hover{{text-decoration:underline;}}
details summary::-webkit-details-marker{{display:none;}}
details summary::marker{{display:none;content:'';}}
details[open] summary{{border-bottom:none;}}
@media(max-width:768px){{
  .container{{padding:0;}}
  h1{{font-size:20px;}}
  .wk-selector{{gap:4px;}}
  .wk-btn{{padding:5px 10px;font-size:12px;}}
}}
</style>
</head><body>
<div class="container">
  <div class="nav"><a href="/">&larr; Home</a></div>
  <h1>Driver Tip Tracking</h1>
  <div class="subtitle">Weekly breakdown for payroll tip adjustments (Mon-Sun)</div>
  <div class="wk-selector">{presets_html}</div>
  <div class="wk-pager">{nav_html}</div>
  {kpi_html}
  {selected_detail}
  {f'<div style="font-size:14px;font-weight:600;color:#475417;margin:16px 0 8px;">All Weeks</div>' + other_weeks if other_weeks else ''}
  {empty_msg}
</div>
</body></html>""", content_type='text/html')


# ── Trend Scout ──

@app.route('/trends')
@owner_required
def trends_page():
    """Trend Scout — Instagram food trend analysis."""
    try:
        from tools.trends import get_recent_reports, build_trends_page
        recent = get_recent_reports(limit=10)
        html = build_trends_page(recent_reports=recent)
        return Response(html, content_type='text/html')
    except Exception as e:
        logger.error("Error loading Trend Scout: %s", e, exc_info=True)
        return _error_page(f"Error loading Trend Scout: {e}")


@app.route('/api/trends', methods=['POST'])
@owner_required
def api_trends():
    """Run trend scrape + Claude analysis, save to Notion, return report."""
    try:
        from tools.trends import (
            scrape_hashtag, build_analysis_prompt,
            analyze_with_claude, format_report,
            save_report_to_notion,
        )

        body = request.get_json(silent=True) or {}
        terms = body.get('terms', [])
        results_limit = body.get('results_limit', 30)

        if not terms or not isinstance(terms, list):
            return jsonify({"error": "Provide 'terms' as a list of search terms."}), 400

        # Safety caps
        terms = terms[:5]
        results_limit = min(max(int(results_limit), 10), 50)

        apify_token = os.getenv('APIFY_API_TOKEN', '')
        if not apify_token:
            return jsonify({"error": "APIFY_API_TOKEN not configured."}), 503

        # Scrape
        all_posts = {}
        for term in terms:
            posts = scrape_hashtag(term, results_limit)
            all_posts[term] = posts

        post_counts = {t: len(p) for t, p in all_posts.items()}
        total_posts = sum(post_counts.values())

        if total_posts == 0:
            return jsonify({"error": "No posts found. Try different search terms."}), 404

        # Analyze
        user_prompt = build_analysis_prompt(terms, all_posts)
        analysis = analyze_with_claude(user_prompt)

        # Format + save to Notion
        report_md = format_report(analysis, terms, post_counts)
        notion_url = save_report_to_notion(report_md, analysis, terms, post_counts)

        return jsonify({
            "analysis": analysis,
            "total_posts": total_posts,
            "post_counts": post_counts,
            "search_terms": terms,
            "notion_url": notion_url,
            "posts": all_posts,
        })
    except Exception as e:
        logger.error("api_trends failed: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


# ── Forkable Email Automation ──

@app.route('/forkable')
@owner_required
def forkable_status_page():
    """Forkable email automation status page."""
    try:
        from tools.catering.forkable import poller
    except Exception as e:
        return _error_page("Forkable module error: %s" % e)

    last_poll = poller.last_poll.strftime("%Y-%m-%d %H:%M:%S") if poller.last_poll else "Never"
    status_color = "#4a7c1f" if poller.running else "#d9342b"
    status_text = "Running" if poller.running else "Stopped"

    log_html = ""
    for entry in reversed(poller.activity_log):
        log_html += '<div style="padding:4px 0;border-bottom:1px solid #f0eed8;font-size:13px;">%s</div>' % entry

    err_html = ""
    for entry in reversed(poller.errors):
        err_html += '<div style="padding:4px 0;border-bottom:1px solid #f0eed8;font-size:13px;color:#d9342b;">%s</div>' % entry

    return Response(f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Forkable Automation - Livite</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{font-family:'DM Sans',sans-serif;background:#F5EDDC;color:#2d2a24;padding:16px;}}
.container{{max-width:960px;margin:0 auto;}}
.nav a{{color:#4a7c1f;text-decoration:none;font-size:14px;font-weight:500;}}
.card{{background:#fff;border-radius:10px;padding:16px;border:1px solid #e0d5bf;margin-bottom:12px;}}
.btn{{display:inline-block;padding:8px 16px;border-radius:6px;font-size:13px;font-weight:500;text-decoration:none;cursor:pointer;border:none;}}
</style>
</head><body>
<div class="container">
  <div class="nav" style="margin-bottom:16px;"><a href="/">&larr; Home</a></div>
  <h1 style="font-size:24px;font-weight:700;margin-bottom:4px;">Forkable Email Automation</h1>
  <div style="font-size:13px;color:#7a7265;margin-bottom:20px;">Polls Gmail for Forkable orders, parses with Claude, creates Notion entries</div>

  <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-bottom:20px;">
    <div class="card">
      <div style="font-size:11px;color:#7a7265;text-transform:uppercase;">Status</div>
      <div style="font-size:20px;font-weight:700;color:{status_color};">{status_text}</div>
    </div>
    <div class="card">
      <div style="font-size:11px;color:#7a7265;text-transform:uppercase;">Last Poll</div>
      <div style="font-size:14px;font-weight:600;">{last_poll}</div>
    </div>
    <div class="card">
      <div style="font-size:11px;color:#7a7265;text-transform:uppercase;">Processed</div>
      <div style="font-size:20px;font-weight:700;">{len(poller.processed_ids)}</div>
    </div>
  </div>

  <div style="margin-bottom:12px;">
    <form method="POST" action="/forkable/poll" style="display:inline;">
      <button type="submit" class="btn" style="background:#4a7c1f;color:#fff;">Poll Now</button>
    </form>
    {"" if poller.running else '<form method="POST" action="/forkable/start" style="display:inline;margin-left:8px;"><button type="submit" class="btn" style="background:#475417;color:#fff;">Start Poller</button></form>'}
    {"<form method='POST' action='/forkable/stop' style='display:inline;margin-left:8px;'><button type='submit' class='btn' style='background:#d9342b;color:#fff;'>Stop Poller</button></form>" if poller.running else ""}
  </div>

  <div class="card">
    <div style="font-size:14px;font-weight:600;color:#475417;margin-bottom:8px;">Activity Log</div>
    {log_html if log_html else '<div style="color:#7a7265;font-size:13px;">No activity yet</div>'}
  </div>

  {"<div class='card'><div style='font-size:14px;font-weight:600;color:#d9342b;margin-bottom:8px;'>Errors</div>" + err_html + "</div>" if err_html else ""}
</div>
</body></html>""", content_type='text/html')


@app.route('/forkable/poll', methods=['POST'])
@owner_required
def forkable_poll_now():
    """Trigger an immediate poll cycle."""
    from tools.catering.forkable import poller
    try:
        count = poller.poll_once()
        return redirect('/forkable')
    except Exception as e:
        return _error_page("Poll error: %s" % e)


@app.route('/forkable/start', methods=['POST'])
@owner_required
def forkable_start():
    """Start the background poller."""
    from tools.catering.forkable import poller
    poller.start()
    return redirect('/forkable')


@app.route('/forkable/stop', methods=['POST'])
@owner_required
def forkable_stop():
    """Stop the background poller."""
    from tools.catering.forkable import poller
    poller.stop()
    return redirect('/forkable')


# ── Payroll ─────────────────────────────────────────────────────────

@app.route('/payroll')
@owner_required
def payroll_page():
    """Payroll calculator — Gusto CSV export."""
    from tools.payroll import (
        aggregate_toast_hours, aggregate_toast_tips, get_week_bounds,
        date_to_blob_format, calculate_tips, get_manager_ot_balances,
        compute_manager_ot, load_payroll_state, build_gusto_rows,
        rows_to_csv, _load_config, fetch_meire_commission,
        get_employee_cash_tips, detect_auto_clockouts,
    )

    cfg = _load_config()
    wages = cfg.get("wages", {})
    payroll_cfg = cfg.get("payroll", {})
    danielle_sm_rate = payroll_cfg.get("danielle_sm_rate", 19.00)

    # Week selection
    try:
        week_offset = int(request.args.get("week", "1"))
    except (ValueError, TypeError):
        week_offset = 1
    if week_offset < 0:
        week_offset = 0

    monday, sunday = get_week_bounds(week_offset)
    start_str = date_to_blob_format(monday)
    end_str = date_to_blob_format(sunday)
    week_end_iso = sunday.isoformat()

    # Load saved state for this week (if previously saved)
    saved = load_payroll_state(week_end_iso)

    # Pull hours from Toast
    try:
        toast_hours = aggregate_toast_hours(start_str, end_str)
    except Exception as e:
        logger.warning("Payroll: Toast fetch failed: %s", e)
        toast_hours = {}

    # Auto-pull tip totals from Toast
    toast_tips = {"gross_tips": 0.0, "shipday_tips": 0.0, "net_tips": 0.0}
    try:
        toast_tips = aggregate_toast_tips(start_str, end_str)
    except Exception as e:
        logger.warning("Payroll: Toast tips fetch failed: %s", e)

    # Use saved overrides if available, otherwise Toast
    employee_hours = saved.get("employee_hours") or toast_hours
    saved_tip_pool = saved.get("tip_pool", 0.0)
    tip_pool = saved_tip_pool if saved_tip_pool > 0 else toast_tips["gross_tips"]
    shipday_adj = toast_tips.get("shipday_tips", 0.0)
    catering_tips = toast_tips.get("catering_tips", 0.0)
    catering_excluded = toast_tips.get("catering_excluded", 0.0)
    cash_tips_total = saved.get("cash_tips_total", 0.0)
    danielle_sm_hours = saved.get("danielle_sm_hours", 0.0)
    sick_map = saved.get("sick_hours", {})
    cash_tips_map = saved.get("cash_tips", {})
    # Auto-populate per-employee cash tips from /tips entry page
    if not cash_tips_map:
        try:
            submitted_cash = get_employee_cash_tips(start_str, end_str)
            if submitted_cash:
                cash_tips_map = submitted_cash
                # Also set total cash tips from submitted entries
                if cash_tips_total == 0:
                    cash_tips_total = round(sum(submitted_cash.values()), 2)
        except Exception as e:
            logger.warning("Payroll: cash tips fetch failed: %s", e)
    tip_overrides = saved.get("tip_overrides", {})

    # Auto-pull Meire commission from Notion
    meire_notion = {"commission": 0.0, "orders": [], "error": None}
    try:
        meire_notion = fetch_meire_commission(
            monday.isoformat(), sunday.isoformat()
        )
    except Exception as e:
        logger.warning("Payroll: Meire commission fetch failed: %s", e)
        meire_notion["error"] = str(e)

    # Use saved commission if available, otherwise auto-pulled from Notion
    saved_meire = saved.get("meire_commission", 0.0)
    if saved_meire > 0:
        meire_commission = saved_meire
    else:
        meire_commission = meire_notion.get("commission", 0.0)

    # Pull driver tip adjustment for this week
    driver_adj = 0.0
    driver_adj_detail = ""
    try:
        from tools.catering.drivers import get_driver_weekly_data
        ddata = get_driver_weekly_data()
        mon_iso = monday.isoformat()
        for w in ddata.get("weeks", []):
            if w["start"] == mon_iso:
                driver_adj = w["total_tip_adjustment"]
                m_tips = w["mustafa"]["tips"]
                mb_from = w["metrobi"]["cost_from_tips"]
                parts = []
                if m_tips > 0:
                    parts.append("Mustafa $%.2f" % m_tips)
                if mb_from > 0:
                    parts.append("Metrobi $%.2f" % mb_from)
                driver_adj_detail = " + ".join(parts)
                break
    except Exception as e:
        logger.warning("Payroll: driver data fetch failed: %s", e)

    # Net pool = gross pool minus deductions plus cash tips
    net_tip_pool = max(0, tip_pool - driver_adj - shipday_adj + cash_tips_total)

    # Calculate tips from net pool
    tip_map, tip_rate = calculate_tips(net_tip_pool, employee_hours, cfg)

    # Apply per-employee overrides
    for emp, override_val in tip_overrides.items():
        tip_map[emp] = override_val

    # Manager OT balances
    balances = get_manager_ot_balances()
    manager_ot_info = {}
    managers = [(k, v) for k, v in wages.items() if v.get("type") == "manager"]
    for emp, info in managers:
        hours = employee_hours.get(emp, {}).get("total", 0)
        ot_rate = info.get("ot_rate", 0)
        old_balance = balances.get(emp, {}).get("balance", 0.0)
        diff = round(hours - 50, 2)
        preview_balance = round(old_balance + diff, 2)
        payout = 0.0
        if preview_balance > 0:
            payout = round(preview_balance * ot_rate, 2)
        manager_ot_info[emp] = {
            "hours": hours,
            "diff": diff,
            "old_balance": old_balance,
            "preview_balance": preview_balance if preview_balance <= 0 else 0.0,
            "payout": payout,
            "ot_rate": ot_rate,
        }

    # Week preset buttons
    presets = []
    for i in range(4):
        m, s = get_week_bounds(i)
        label = "This Week" if i == 0 else ("Last Week" if i == 1 else f"{i} Wks Ago")
        active = "background:#475417;color:#fff;" if i == week_offset else ""
        presets.append(f'<a href="/payroll?week={i}" style="display:inline-block;padding:6px 14px;border-radius:6px;font-size:12px;font-weight:600;text-decoration:none;color:#475417;border:1px solid #e0d5bf;{active}">{label}</a>')
    presets_html = " ".join(presets)

    # Prev/Next
    prev_off = week_offset + 1
    next_off = max(0, week_offset - 1)
    nav_arrows = f'<a href="/payroll?week={prev_off}" style="text-decoration:none;font-size:16px;color:#475417;">&larr;</a>'
    nav_arrows += f' <span style="font-size:13px;font-weight:600;color:#2d2a24;">{monday.strftime("%b %d")} - {sunday.strftime("%b %d, %Y")}</span> '
    if week_offset > 0:
        nav_arrows += f'<a href="/payroll?week={next_off}" style="text-decoration:none;font-size:16px;color:#475417;">&rarr;</a>'

    # Build employee table rows
    all_emps = sorted(wages.keys())
    emp_rows = ""
    total_reg = total_ot = total_tips = total_cash = total_bonus = 0
    _DASH = "-"
    _INP = 'style="width:70px;padding:2px 4px;border:1px solid #e0d5bf;border-radius:4px;font-family:JetBrains Mono,monospace;font-size:11px;text-align:right;"'
    for emp in all_emps:
        info = wages[emp]
        emp_type = info.get("type", "hourly")
        gusto_id = str(info.get("gusto_id", ""))
        parts = emp.split(",", 1)
        last = parts[0].strip().title()
        first = parts[1].strip().title() if len(parts) > 1 else ""
        display_name = f"{first} {last}"
        emp_safe = emp.replace("'", "\\'")

        hours = employee_hours.get(emp, {})
        has_hours = hours.get("total", 0) > 0

        # Determine displayed values
        if emp_type == "owner":
            reg_display = "40.00"
            ot_display = _DASH
            reg_val = 40.0
            ot_val = 0.0
        elif emp_type == "manager":
            reg_display = "50.00"
            ot_display = _DASH
            reg_val = 50.0
            ot_val = 0.0
        elif has_hours:
            reg_val = hours.get("regular", 0)
            ot_val = hours.get("ot", 0)
            reg_display = f"{reg_val:.2f}"
            ot_display = f"{ot_val:.2f}" if ot_val > 0 else _DASH
        else:
            reg_display = _DASH
            ot_display = _DASH
            reg_val = 0
            ot_val = 0

        tips = tip_map.get(emp, 0)
        cash = cash_tips_map.get(emp, 0)
        sick = sick_map.get(emp, 0)
        is_tipped = info.get("tips", False)

        # Bonus (manager OT payout + Danielle SM)
        bonus = 0.0
        note_parts = []
        if emp_type == "manager":
            bonus += manager_ot_info.get(emp, {}).get("payout", 0)
        if emp == "cohen, danielle" and danielle_sm_hours > 0:
            sm_bonus = round(danielle_sm_hours * danielle_sm_rate, 2)
            bonus += sm_bonus
            note_parts.append(f"SM {danielle_sm_hours}hrs@${danielle_sm_rate:.0f}")

        if tips > 0:
            note_parts.append(f"Tips ${tip_rate:.2f} per hour")

        note = "; ".join(note_parts)

        # Accumulate totals
        if isinstance(reg_val, (int, float)):
            total_reg += reg_val
        if isinstance(ot_val, (int, float)):
            total_ot += ot_val
        total_tips += tips
        total_cash += cash
        total_bonus += bonus

        mono = "font-family:'JetBrains Mono',monospace;font-size:12px;"
        row_opacity = "" if has_hours or emp_type in ("owner", "manager") else "opacity:0.4;"
        bonus_display = f"${bonus:.2f}" if bonus > 0 else _DASH
        bonus_id = ' id="danielleBonusCell"' if emp == "cohen, danielle" else ''
        sick_display = f"{sick:.1f}" if sick > 0 else _DASH

        # Tips: editable input for tipped employees, dash for others
        if is_tipped:
            tips_cell = f'<input type="number" {_INP} data-emp="{emp_safe}" data-field="tips" value="{tips:.2f}" step="0.01" onchange="markTipOverride(this)">'
        else:
            tips_cell = _DASH

        # Cash tips: editable for tipped employees
        if is_tipped:
            cash_cell = f'<input type="number" {_INP} data-emp="{emp_safe}" data-field="cash" value="{cash:.2f}" step="0.01" onchange="recalcTips()">'
        else:
            cash_cell = _DASH

        # Hours cells: editable for hourly, static for owner/manager
        if emp_type in ("owner", "manager"):
            reg_cell = f'<span>{reg_display}</span>'
            ot_cell = f'<span>{ot_display}</span>'
        else:
            reg_cell = f'<input type="number" {_INP} data-emp="{emp_safe}" data-field="reg" value="{reg_val:.2f}" step="0.01" min="0" onchange="onHoursChange(this)">'
            ot_cell = f'<input type="number" {_INP} data-emp="{emp_safe}" data-field="ot" value="{ot_val:.2f}" step="0.01" min="0" onchange="onHoursChange(this)">'

        emp_rows += f"""<tr style="{row_opacity}">
  <td style="font-size:12px;font-weight:500;">{display_name}</td>
  <td style="text-align:center;">{reg_cell}</td>
  <td style="text-align:center;">{ot_cell}</td>
  <td style="text-align:center;">{tips_cell}</td>
  <td style="text-align:center;">{cash_cell}</td>
  <td{bonus_id} style="text-align:center;{mono}">{bonus_display}</td>
  <td style="text-align:center;{mono}">{sick_display}</td>
  <td style="font-size:11px;color:#7a7265;"{' data-tipped="1"' if is_tipped else ''}>{note}</td>
</tr>"""

    # Meire row
    meire_cfg = payroll_cfg.get("meire", {})
    meire_display = f"${meire_commission:.2f}" if meire_commission > 0 else _DASH
    emp_rows += f"""<tr>
  <td style="font-size:12px;font-weight:500;">Meire Medeiros</td>
  <td style="text-align:center;font-family:'JetBrains Mono',monospace;font-size:12px;">40.00</td>
  <td style="text-align:center;{mono}">{_DASH}</td>
  <td style="text-align:center;">{_DASH}</td>
  <td style="text-align:center;">{_DASH}</td>
  <td style="text-align:center;{mono}">{_DASH}</td>
  <td style="text-align:center;{mono}">{_DASH}</td>
  <td style="font-size:11px;color:#7a7265;">Commission: {meire_display}</td>
</tr>"""

    # Totals row
    emp_rows += f"""<tr style="border-top:2px solid #475417;font-weight:600;">
  <td>TOTAL</td>
  <td style="text-align:center;font-family:'JetBrains Mono',monospace;font-size:12px;">{total_reg:.2f}</td>
  <td style="text-align:center;font-family:'JetBrains Mono',monospace;font-size:12px;">{total_ot:.2f}</td>
  <td style="text-align:center;font-family:'JetBrains Mono',monospace;font-size:12px;">${total_tips:.2f}</td>
  <td style="text-align:center;font-family:'JetBrains Mono',monospace;font-size:12px;">${total_cash:.2f}</td>
  <td style="text-align:center;font-family:'JetBrains Mono',monospace;font-size:12px;">${total_bonus:.2f}</td>
  <td></td><td></td>
</tr>"""

    # Manager OT table
    mgr_rows = ""
    for emp, info in sorted(manager_ot_info.items()):
        parts = emp.split(",", 1)
        name = f"{parts[1].strip().title()} {parts[0].strip().title()}" if len(parts) > 1 else emp.title()
        diff_color = "#4a7c1f" if info["diff"] >= 0 else "#d9342b"
        bal_color = "#4a7c1f" if info["preview_balance"] >= 0 else "#d9342b"
        payout_display = f"${info['payout']:.2f}" if info["payout"] > 0 else "$0.00"
        mono = "font-family:'JetBrains Mono',monospace;font-size:12px;"
        mgr_rows += f"""<tr>
  <td style="font-size:12px;font-weight:500;">{name}</td>
  <td style="text-align:center;{mono}">{info["hours"]:.2f}</td>
  <td style="text-align:center;{mono}color:{diff_color};">{info["diff"]:+.2f}</td>
  <td style="text-align:center;{mono}">{info["old_balance"]:+.2f}</td>
  <td style="text-align:center;{mono}color:{bal_color};">{info["preview_balance"]:+.2f}</td>
  <td style="text-align:center;{mono}font-weight:600;">{payout_display}</td>
  <td style="text-align:center;{mono}">${info["ot_rate"]:.0f}/hr</td>
</tr>"""

    # Notion link for Meire
    notion_link = f"https://www.notion.so/{meire_cfg.get('notion_db', '').replace('-', '')}"

    # Meire commission order details (from Notion auto-pull)
    meire_orders_html = ""
    if meire_notion.get("orders"):
        meire_orders_html = '<div style="margin-top:6px;font-size:11px;color:#7a7265;">'
        for mo in meire_notion["orders"]:
            rate_pct = "5%" if mo["order_type"] == "New Client" else ("3%" if mo["order_type"] == "Repeat Client" else "0%")
            badge_color = "#4a7c1f" if mo["order_type"] == "New Client" else "#e67e22"
            meire_orders_html += (
                '<div style="padding:2px 0;">'
                '<span style="display:inline-block;padding:1px 5px;border-radius:3px;'
                'background:%s;color:#fff;font-size:9px;font-weight:600;margin-right:4px;">%s</span>'
                '%s &mdash; $%.2f &times; %s = <strong>$%.2f</strong></div>'
                % (badge_color, mo["order_type"], mo["name"], mo["subtotal"],
                   rate_pct, mo["commission"])
            )
        meire_orders_html += '</div>'
    elif meire_notion.get("error"):
        meire_orders_html = (
            '<div style="margin-top:4px;font-size:11px;color:#d9342b;">'
            'Notion: %s</div>' % meire_notion["error"]
        )
    else:
        meire_orders_html = (
            '<div style="margin-top:4px;font-size:11px;color:#7a7265;">'
            'No commissionable orders this week</div>'
        )

    # Total tipped hours
    total_tipped_hours = 0.0
    for emp_name, hrs in employee_hours.items():
        if wages.get(emp_name, {}).get("tips", False):
            total_tipped_hours += hrs.get("total", 0)

    # Detect auto clock-outs (missed punches)
    auto_clockouts = []
    try:
        auto_clockouts = detect_auto_clockouts(start_str, end_str)
    except Exception as e:
        logger.warning("Payroll: auto clock-out detection failed: %s", e)

    clockout_alert_html = ""
    if auto_clockouts:
        rows_html = ""
        for ac in auto_clockouts:
            parts = ac["employee"].split(",", 1)
            ac_name = ("%s %s" % (parts[1].strip().title(), parts[0].strip().title())) if len(parts) > 1 else ac["employee"].title()
            rows_html += (
                '<tr>'
                '<td style="font-size:12px;font-weight:500;">%s</td>'
                '<td style="text-align:center;font-size:12px;">%s</td>'
                '<td style="font-size:11px;color:#7a7265;">%s</td>'
                '<td style="font-size:11px;color:#7a7265;">%s</td>'
                '<td style="text-align:center;font-family:JetBrains Mono,monospace;font-size:12px;color:#d9342b;font-weight:600;">%.2f</td>'
                '</tr>'
                % (ac_name, ac["date"], ac["clock_in"], ac["clock_out"], ac["hours"])
            )
        clockout_alert_html = (
            '<div style="background:#fff3cd;border:1px solid #e0c36a;border-radius:12px;'
            'padding:14px 20px;margin-bottom:14px;">'
            '<div style="font-size:13px;font-weight:700;color:#856404;margin-bottom:8px;">'
            'Missed Clock-Out Alert &mdash; %d shift(s) auto-clocked out by Toast</div>'
            '<table style="width:100%%;"><thead><tr>'
            '<th style="text-align:left;font-size:10px;padding:4px;">Employee</th>'
            '<th style="font-size:10px;padding:4px;">Date</th>'
            '<th style="font-size:10px;padding:4px;text-align:left;">Clock In</th>'
            '<th style="font-size:10px;padding:4px;text-align:left;">Clock Out</th>'
            '<th style="font-size:10px;padding:4px;">Hours</th>'
            '</tr></thead><tbody>%s</tbody></table>'
            '<div style="font-size:11px;color:#856404;margin-top:6px;">'
            'These employees did not clock out. Toast auto-clocked them out at 4:00 AM. '
            'Verify actual hours worked and adjust in Homebase before running payroll.</div>'
            '</div>' % (len(auto_clockouts), rows_html)
        )

    return Response(f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Payroll - {monday.strftime("%b %d")} to {sunday.strftime("%b %d")}</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{font-family:'DM Sans',sans-serif;background:#F5EDDC;color:#2d2a24;padding:16px;}}
.container{{max-width:1200px;margin:0 auto;}}
h1{{font-size:22px;font-weight:700;color:#475417;margin-bottom:4px;}}
.nav{{display:flex;align-items:center;gap:10px;margin-bottom:16px;}}
.nav a{{color:#4a7c1f;text-decoration:none;font-size:13px;font-weight:500;}}
.nav a:hover{{text-decoration:underline;}}
.card{{background:#fff;border:1px solid #e0d5bf;border-radius:12px;padding:20px;margin-bottom:14px;box-shadow:0 1px 3px rgba(0,0,0,0.04);}}
.card-title{{font-size:14px;font-weight:700;color:#475417;margin-bottom:12px;}}
table{{width:100%;border-collapse:collapse;font-size:13px;}}
th{{text-align:center;font-size:10px;font-weight:600;color:#7a7265;text-transform:uppercase;letter-spacing:0.5px;padding:6px 4px;border-bottom:2px solid #e0d5bf;}}
th:first-child{{text-align:left;}}
td{{padding:6px 4px;border-bottom:1px solid #f0e8d6;font-size:12px;}}
tr:hover{{background:#faf6ee;}}
.week-bar{{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:16px;}}
.input-group{{display:flex;align-items:center;gap:6px;margin-bottom:8px;}}
.input-group label{{font-size:12px;font-weight:600;color:#475417;min-width:130px;}}
.input-group input{{width:120px;padding:6px 10px;border:1px solid #e0d5bf;border-radius:6px;font-size:13px;font-family:'JetBrains Mono',monospace;}}
.input-group .computed{{font-size:12px;color:#7a7265;margin-left:8px;}}
.btn{{display:inline-block;padding:8px 18px;border-radius:8px;font-size:13px;font-weight:600;text-decoration:none;cursor:pointer;border:none;}}
.btn-primary{{background:#475417;color:#fff;}}
.btn-primary:hover{{background:#5a6b20;}}
.btn-outline{{background:transparent;color:#475417;border:1px solid #475417;}}
.btn-outline:hover{{background:#f0edd4;}}
.upload-area{{border:2px dashed #e0d5bf;border-radius:8px;padding:12px;text-align:center;cursor:pointer;color:#7a7265;font-size:12px;transition:border-color 0.2s;margin-top:8px;}}
.upload-area:hover{{border-color:#8cb82e;}}
#status{{font-size:12px;color:#7a7265;margin-top:4px;}}
.grid-2{{display:grid;grid-template-columns:1fr 1fr;gap:14px;}}
@media(max-width:768px){{.grid-2{{grid-template-columns:1fr;}}}}
</style>
</head><body>
<div class="container">

<div class="nav">
  <a href="/">&larr; Home</a>
  <a href="/profit">&larr; Profit</a>
</div>

<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;">
  <h1>Payroll</h1>
  <button class="btn btn-primary" onclick="downloadCSV()">Download Gusto CSV</button>
</div>

<div class="week-bar">
  {presets_html}
  <span style="margin-left:8px;">{nav_arrows}</span>
</div>

{clockout_alert_html}

<div class="grid-2">
  <div class="card">
    <div class="card-title">Tip Pool</div>
    <div class="input-group">
      <label>Gross Pool ($):</label>
      <input type="number" id="tipPool" value="{tip_pool:.2f}" step="0.01" onchange="recalcTips()">
      <span class="computed" style="font-size:10px;">(auto from Toast)</span>
    </div>
    <div style="font-size:11px;color:#7a7265;margin:3px 0;">
      &minus; Driver Adj: <strong style="color:#d9342b;">{"-$%.2f" % driver_adj if driver_adj > 0 else "$0.00"}</strong>
      {(' <span style="font-size:10px;">(' + driver_adj_detail + ')</span>') if driver_adj_detail else ''}
      &nbsp;<a href="/drivers" style="font-size:10px;color:#4a7c1f;">View &rarr;</a>
    </div>
    <div style="font-size:11px;color:#7a7265;margin:3px 0;">
      &minus; ShipDay Tips: <strong style="color:#d9342b;">{"-$%.2f" % shipday_adj if shipday_adj > 0 else "$0.00"}</strong>
      <span style="font-size:10px;">(auto from Toast)</span>
    </div>
    {'<div style="font-size:10px;color:#b08d57;margin:2px 0 4px 0;">Excluded $%.2f pre-captured catering tip from prior period</div>' % catering_excluded if catering_excluded > 0 else ''}
    <div style="font-size:11px;color:#7a7265;margin:3px 0;display:flex;align-items:center;gap:4px;">
      + Cash Tips: <input type="number" id="cashTips" value="{cash_tips_total:.2f}" step="0.01" style="width:90px;padding:2px 6px;border:1px solid #e0d5bf;border-radius:4px;font-size:12px;font-family:'JetBrains Mono',monospace;" onchange="recalcTips()">
      <a href="/tips" target="_blank" style="font-size:10px;color:#4a7c1f;">Entry Page</a>
      <a href="/tips/qr" target="_blank" style="font-size:10px;color:#4a7c1f;">QR Code</a>
    </div>
    <div style="font-size:12px;color:#2d2a24;margin-top:6px;padding-top:6px;border-top:1px solid #e0d5bf;">
      Net Pool: <strong id="netPool">${net_tip_pool:.2f}</strong> &nbsp;|&nbsp;
      Rate: <strong id="tipRate">${tip_rate:.2f}</strong>/hr &nbsp;|&nbsp;
      Tipped Hrs: <strong id="tippedHours">{total_tipped_hours:.2f}</strong>
    </div>
  </div>

  <div class="card">
    <div class="card-title">Special Entries</div>
    <div class="input-group">
      <label>Meire Commission ($):</label>
      <input type="number" id="meireComm" value="{meire_commission}" step="0.01">
      <a href="{notion_link}" target="_blank" style="font-size:11px;color:#4a7c1f;">Notion &rarr;</a>
    </div>
    {meire_orders_html}
    <div class="input-group">
      <label>Danielle SM Hrs:</label>
      <input type="number" id="danielleSM" value="{danielle_sm_hours}" step="0.25" onchange="recalcDanielle()">
      <span class="computed" id="danielleBonus">${danielle_sm_hours * danielle_sm_rate:.2f}</span>
      <span class="computed">@ ${danielle_sm_rate:.0f}/hr</span>
    </div>
  </div>
</div>

<div class="card">
  <div class="card-title">Manager OT Balances</div>
  <table>
    <thead><tr>
      <th style="text-align:left;">Manager</th>
      <th>Hours</th>
      <th>vs 50</th>
      <th>Old Balance</th>
      <th>New Balance</th>
      <th>Payout</th>
      <th>OT Rate</th>
    </tr></thead>
    <tbody>{mgr_rows}</tbody>
  </table>
  <div style="margin-top:10px;">
    <button class="btn btn-outline" onclick="saveBalances()">Save Balances</button>
    <span id="balanceStatus" style="font-size:11px;color:#7a7265;margin-left:8px;"></span>
  </div>
</div>

<div class="card">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
    <div class="card-title" style="margin-bottom:0;">Employee Hours</div>
    <div>
      <a href="/api/payroll/reload-toast?week={week_offset}" class="btn btn-outline" style="font-size:11px;padding:4px 10px;">Reload Toast Data</a>
      <label for="homebaseUpload" class="btn btn-outline" style="font-size:11px;padding:4px 10px;">
        Upload Homebase CSV
        <input type="file" id="homebaseUpload" accept=".csv,.xlsx" style="display:none;" onchange="uploadHomebase(this)">
      </label>
      <span id="uploadStatus" style="font-size:11px;color:#7a7265;margin-left:6px;"></span>
    </div>
  </div>
  <div style="overflow-x:auto;">
    <table>
      <thead><tr>
        <th style="text-align:left;">Employee</th>
        <th>Reg Hrs</th>
        <th>OT Hrs</th>
        <th>Paycheck Tips</th>
        <th>Cash Tips</th>
        <th>Bonus</th>
        <th>Sick Hrs</th>
        <th style="text-align:left;">Note</th>
      </tr></thead>
      <tbody>{emp_rows}</tbody>
    </table>
  </div>
</div>

<div style="display:flex;gap:10px;margin-bottom:24px;">
  <button class="btn btn-primary" onclick="downloadCSV()">Download Gusto CSV</button>
  <button class="btn btn-outline" onclick="saveWeek()">Save Week</button>
  <span id="saveStatus" style="font-size:12px;color:#7a7265;align-self:center;"></span>
</div>

</div>
<script>
const SM_RATE = {danielle_sm_rate};
const WEEK_END = '{week_end_iso}';
const DRIVER_ADJ = {driver_adj};
const SHIPDAY_ADJ = {shipday_adj};

function _getEmpHours(row) {{
  // Read hours from inputs (hourly) or text (owner/manager)
  var regInp = row.querySelector('input[data-field="reg"]');
  var otInp = row.querySelector('input[data-field="ot"]');
  var reg = regInp ? (parseFloat(regInp.value) || 0) : (parseFloat(row.querySelector('td:nth-child(2)').textContent) || 0);
  var ot = otInp ? (parseFloat(otInp.value) || 0) : (parseFloat(row.querySelector('td:nth-child(3)').textContent) || 0);
  return {{reg: reg, ot: ot, total: reg + ot}};
}}

function onHoursChange(inp) {{
  inp.style.borderColor = '#e67e22';
  recalcTips();
}}

function recalcTips() {{
  const gross = parseFloat(document.getElementById('tipPool').value) || 0;
  const cash = parseFloat(document.getElementById('cashTips').value) || 0;
  const net = Math.max(0, gross - DRIVER_ADJ - SHIPDAY_ADJ + cash);
  // Recalculate tipped hours from inputs
  var tippedHrs = 0;
  document.querySelectorAll('input[data-field="tips"]').forEach(function(inp) {{
    var row = inp.closest('tr');
    var h = _getEmpHours(row);
    tippedHrs += h.total;
  }});
  document.getElementById('tippedHours').textContent = tippedHrs.toFixed(2);
  const rate = tippedHrs > 0 ? net / tippedHrs : 0;
  document.getElementById('netPool').textContent = '$' + net.toFixed(2);
  document.getElementById('tipRate').textContent = '$' + rate.toFixed(2);
  // Update all non-overridden tip inputs and notes
  document.querySelectorAll('input[data-field="tips"]').forEach(function(inp) {{
    if (!inp.dataset.overridden) {{
      var h = _getEmpHours(inp.closest('tr'));
      inp.value = (h.total * rate).toFixed(2);
    }}
  }});
  // Update tip note in all tipped employee rows (skip overridden)
  document.querySelectorAll('td[data-tipped="1"]').forEach(function(td) {{
    var row = td.closest('tr');
    var tipInp = row.querySelector('input[data-field="tips"]');
    if (tipInp && !tipInp.dataset.overridden && parseFloat(tipInp.value) > 0) {{
      var parts = td.textContent.split(';').filter(function(p) {{ return p.indexOf('Tips') === -1; }});
      parts.push('Tips $' + rate.toFixed(2) + ' per hour');
      td.textContent = parts.filter(Boolean).map(function(s) {{ return s.trim(); }}).join('; ');
    }}
  }});
}}

function markTipOverride(inp) {{
  inp.dataset.overridden = '1';
  inp.style.borderColor = '#e67e22';
  // Update note for this employee
  var td = inp.closest('tr').querySelector('td[data-tipped="1"]');
  if (td) {{
    var val = parseFloat(inp.value) || 0;
    var parts = td.textContent.split(';').filter(function(p) {{ return p.indexOf('Tips') === -1; }});
    if (val > 0) parts.push('Tips $' + val.toFixed(2) + ' (override)');
    td.textContent = parts.filter(Boolean).map(function(s) {{ return s.trim(); }}).join('; ');
  }}
}}

function recalcDanielle() {{
  const hrs = parseFloat(document.getElementById('danielleSM').value) || 0;
  const bonus = hrs * SM_RATE;
  document.getElementById('danielleBonus').textContent = '$' + bonus.toFixed(2);
  var cell = document.getElementById('danielleBonusCell');
  if (cell) cell.textContent = bonus > 0 ? '$' + bonus.toFixed(2) : '-';
}}

function _collectTipOverrides() {{
  var overrides = {{}};
  document.querySelectorAll('input[data-field="tips"][data-overridden="1"]').forEach(function(inp) {{
    overrides[inp.dataset.emp] = parseFloat(inp.value) || 0;
  }});
  return overrides;
}}

function _collectCashTips() {{
  var cash = {{}};
  document.querySelectorAll('input[data-field="cash"]').forEach(function(inp) {{
    var v = parseFloat(inp.value) || 0;
    if (v > 0) cash[inp.dataset.emp] = v;
  }});
  return cash;
}}

function _collectHoursOverrides() {{
  var hrs = {{}};
  document.querySelectorAll('input[data-field="reg"]').forEach(function(inp) {{
    var emp = inp.dataset.emp;
    var reg = parseFloat(inp.value) || 0;
    var otInp = inp.closest('tr').querySelector('input[data-field="ot"]');
    var ot = otInp ? (parseFloat(otInp.value) || 0) : 0;
    hrs[emp] = {{regular: reg, ot: ot, total: Math.round((reg + ot) * 100) / 100}};
  }});
  return hrs;
}}

function downloadCSV() {{
  const pool = parseFloat(document.getElementById('tipPool').value) || 0;
  const cashTotal = parseFloat(document.getElementById('cashTips').value) || 0;
  const meire = parseFloat(document.getElementById('meireComm').value) || 0;
  const danielle = parseFloat(document.getElementById('danielleSM').value) || 0;
  const overrides = _collectTipOverrides();
  const cash = _collectCashTips();
  const hours = _collectHoursOverrides();
  var form = document.createElement('form');
  form.method = 'POST'; form.action = '/api/payroll/download';
  form.style.display = 'none';
  var fields = {{week: WEEK_END, tip_pool: pool, cash_tips_total: cashTotal,
    meire_commission: meire, danielle_sm_hours: danielle,
    tip_overrides: JSON.stringify(overrides), cash_tips: JSON.stringify(cash),
    employee_hours: JSON.stringify(hours)}};
  for (var k in fields) {{
    var inp = document.createElement('input');
    inp.name = k; inp.value = fields[k]; form.appendChild(inp);
  }}
  document.body.appendChild(form); form.submit(); form.remove();
}}

function saveWeek() {{
  const pool = parseFloat(document.getElementById('tipPool').value) || 0;
  const cashTotal = parseFloat(document.getElementById('cashTips').value) || 0;
  const meire = parseFloat(document.getElementById('meireComm').value) || 0;
  const danielle = parseFloat(document.getElementById('danielleSM').value) || 0;
  const overrides = _collectTipOverrides();
  const cash = _collectCashTips();
  const hours = _collectHoursOverrides();
  fetch('/api/payroll/save', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{week: WEEK_END, tip_pool: pool, cash_tips_total: cashTotal,
      meire_commission: meire, danielle_sm_hours: danielle,
      tip_overrides: overrides, cash_tips: cash, employee_hours: hours}})
  }}).then(r => r.json()).then(d => {{
    document.getElementById('saveStatus').textContent = d.ok ? 'Saved' : 'Error: ' + d.error;
  }});
}}

function saveBalances() {{
  fetch('/api/payroll/save-balances', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{week: WEEK_END}})
  }}).then(r => r.json()).then(d => {{
    document.getElementById('balanceStatus').textContent = d.ok ? 'Balances saved' : 'Error';
  }});
}}

function uploadHomebase(input) {{
  const file = input.files[0];
  if (!file) return;
  const form = new FormData();
  form.append('file', file);
  form.append('week', WEEK_END);
  document.getElementById('uploadStatus').textContent = 'Uploading...';
  fetch('/api/payroll/upload-homebase', {{method: 'POST', body: form}})
    .then(r => r.json())
    .then(d => {{
      if (d.ok) {{
        document.getElementById('uploadStatus').textContent = 'Uploaded — refreshing...';
        location.reload();
      }} else {{
        document.getElementById('uploadStatus').textContent = 'Error: ' + d.error;
      }}
    }});
}}
</script>
</body></html>""", content_type="text/html")


@app.route('/api/payroll/download', methods=['GET', 'POST'])
@owner_required
def payroll_download():
    """Generate and return Gusto CSV."""
    from tools.payroll import (
        aggregate_toast_hours, aggregate_toast_tips, get_week_bounds,
        date_to_blob_format, calculate_tips, get_manager_ot_balances,
        compute_manager_ot, build_gusto_rows, rows_to_csv,
        load_payroll_state, _load_config,
    )

    cfg = _load_config()
    wages = cfg.get("wages", {})
    payroll_cfg = cfg.get("payroll", {})

    # Accept both GET (query params) and POST (form data)
    src = request.form if request.method == 'POST' else request.args
    week_end = src.get("week", "")
    tip_pool = float(src.get("tip_pool", 0))
    cash_tips_total = float(src.get("cash_tips_total", 0))
    meire_commission = float(src.get("meire_commission", 0))
    danielle_sm_hours = float(src.get("danielle_sm_hours", 0))
    danielle_sm_rate = payroll_cfg.get("danielle_sm_rate", 19.00)

    # Tip overrides and cash tips from POST
    tip_overrides = {}
    cash_tips_map = {}
    try:
        raw = src.get("tip_overrides", "{}")
        tip_overrides = json.loads(raw) if raw else {}
    except Exception as e:
        logger.warning("Failed to parse tip_overrides: %s", e)
    try:
        raw = src.get("cash_tips", "{}")
        cash_tips_map = json.loads(raw) if raw else {}
    except Exception as e:
        logger.warning("Failed to parse cash_tips: %s", e)

    # Parse week end to get start
    from datetime import datetime as dt
    try:
        sun = dt.strptime(week_end, "%Y-%m-%d").date()
    except Exception:
        return jsonify({"ok": False, "error": "Invalid week"}), 400
    mon = sun - timedelta(days=6)
    start_str = date_to_blob_format(mon)
    end_str = date_to_blob_format(sun)

    # Use hours from UI if provided, else saved state, else Toast
    ui_hours = {}
    try:
        raw = src.get("employee_hours", "{}")
        ui_hours = json.loads(raw) if raw else {}
    except Exception as e:
        logger.warning("Failed to parse employee_hours: %s", e)
    saved = load_payroll_state(week_end)
    employee_hours = ui_hours or saved.get("employee_hours") or aggregate_toast_hours(start_str, end_str)

    # Driver tip adjustment — back out from pool
    driver_adj = 0.0
    try:
        from tools.catering.drivers import get_driver_weekly_data
        ddata = get_driver_weekly_data()
        mon_iso = mon.isoformat()
        for w in ddata.get("weeks", []):
            if w["start"] == mon_iso:
                driver_adj = w["total_tip_adjustment"]
                break
    except Exception as e:
        logger.warning("Driver tip adjustment fetch failed: %s", e)

    # ShipDay tip deduction
    shipday_adj = 0.0
    try:
        toast_tips = aggregate_toast_tips(start_str, end_str)
        shipday_adj = toast_tips.get("shipday_tips", 0.0)
    except Exception as e:
        logger.warning("ShipDay tip deduction fetch failed: %s", e)
    net_tip_pool = max(0, tip_pool - driver_adj - shipday_adj + cash_tips_total)

    # Tips from net pool
    tip_map, tip_rate = calculate_tips(net_tip_pool, employee_hours, cfg)

    # Apply per-employee overrides
    for emp, val in tip_overrides.items():
        tip_map[emp] = float(val)

    # Manager payouts
    balances = get_manager_ot_balances()
    manager_payouts = {}
    for emp, info in wages.items():
        if info.get("type") == "manager":
            hours = employee_hours.get(emp, {}).get("total", 0)
            ot_rate = info.get("ot_rate", 0)
            result = compute_manager_ot(emp, hours, week_end, ot_rate, balances)
            if result["payout"] > 0:
                manager_payouts[emp] = result["payout"]

    sick_map = saved.get("sick_hours", {})

    rows = build_gusto_rows(
        employee_hours, tip_map, tip_rate, manager_payouts,
        meire_commission, danielle_sm_hours, danielle_sm_rate,
        sick_map, cfg, cash_tips_map=cash_tips_map,
    )
    csv_data = rows_to_csv(rows)

    filename = f"gusto_payroll_{week_end}.csv"
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.route('/api/payroll/save', methods=['POST'])
@owner_required
def payroll_save():
    """Save payroll state for a week."""
    from tools.payroll import (
        save_payroll_state, aggregate_toast_hours, date_to_blob_format,
    )
    data = request.get_json(force=True)
    week_end = data.get("week", "")
    if not week_end:
        return jsonify({"ok": False, "error": "Missing week"})

    # Parse dates
    from datetime import datetime as dt
    try:
        sun = dt.strptime(week_end, "%Y-%m-%d").date()
    except Exception:
        return jsonify({"ok": False, "error": "Invalid week"})
    mon = sun - timedelta(days=6)

    # Use hours from UI if provided (user may have edited), otherwise re-fetch from Toast
    ui_hours = data.get("employee_hours", {})
    if ui_hours:
        hours = ui_hours
    else:
        try:
            hours = aggregate_toast_hours(date_to_blob_format(mon), date_to_blob_format(sun))
        except Exception:
            hours = {}

    state = {
        "employee_hours": hours,
        "tip_pool": data.get("tip_pool", 0),
        "cash_tips_total": data.get("cash_tips_total", 0),
        "meire_commission": data.get("meire_commission", 0),
        "danielle_sm_hours": data.get("danielle_sm_hours", 0),
        "sick_hours": data.get("sick_hours", {}),
        "tip_overrides": data.get("tip_overrides", {}),
        "cash_tips": data.get("cash_tips", {}),
    }
    save_payroll_state(week_end, state)
    return jsonify({"ok": True})


@app.route('/api/payroll/save-balances', methods=['POST'])
@owner_required
def payroll_save_balances():
    """Compute and save manager OT balances for a week."""
    from tools.payroll import (
        get_manager_ot_balances, compute_manager_ot,
        save_manager_ot_balances, aggregate_toast_hours,
        date_to_blob_format, load_payroll_state, _load_config,
    )

    data = request.get_json(force=True)
    week_end = data.get("week", "")

    cfg = _load_config()
    wages = cfg.get("wages", {})

    from datetime import datetime as dt
    try:
        sun = dt.strptime(week_end, "%Y-%m-%d").date()
    except Exception:
        return jsonify({"ok": False, "error": "Invalid week"})
    mon = sun - timedelta(days=6)

    saved = load_payroll_state(week_end)
    employee_hours = saved.get("employee_hours") or aggregate_toast_hours(
        date_to_blob_format(mon), date_to_blob_format(sun)
    )

    balances = get_manager_ot_balances()
    for emp, info in wages.items():
        if info.get("type") == "manager":
            hours = employee_hours.get(emp, {}).get("total", 0)
            ot_rate = info.get("ot_rate", 0)
            compute_manager_ot(emp, hours, week_end, ot_rate, balances)

    save_manager_ot_balances(balances)
    return jsonify({"ok": True})


@app.route('/api/payroll/reload-toast')
@owner_required
def payroll_reload_toast():
    """Clear cached Toast data for the selected week and redirect back."""
    from tools.payroll import get_week_bounds, date_to_blob_format
    import shutil

    try:
        week_offset = int(request.args.get("week", "1"))
    except (ValueError, TypeError):
        week_offset = 1

    monday, sunday = get_week_bounds(week_offset)
    cache_dir = os.path.join(os.path.dirname(__file__), '.tmp')
    cleared = 0

    current = monday
    while current <= sunday:
        date_str = current.strftime("%Y%m%d")
        day_cache = os.path.join(cache_dir, date_str)
        if os.path.isdir(day_cache):
            shutil.rmtree(day_cache)
            cleared += 1
        current += timedelta(days=1)

    logger.info("Payroll: cleared %d day(s) of Toast cache for week %s", cleared, week_offset)
    return redirect(f'/payroll?week={week_offset}')


@app.route('/api/payroll/upload-homebase', methods=['POST'])
@owner_required
def payroll_upload_homebase():
    """Upload Homebase CSV to override Toast hours."""
    from tools.payroll import parse_homebase_csv, save_payroll_state, load_payroll_state

    file = request.files.get("file")
    week_end = request.form.get("week", "")
    if not file or not week_end:
        return jsonify({"ok": False, "error": "Missing file or week"})

    file_bytes = file.read()
    hours = parse_homebase_csv(file_bytes)
    if not hours:
        return jsonify({"ok": False, "error": "Could not parse file"})

    # Merge with existing saved state
    state = load_payroll_state(week_end)
    state["employee_hours"] = hours
    save_payroll_state(week_end, state)
    return jsonify({"ok": True, "employees": len(hours)})


# ── Main ──

_forkable_started = False


def _start_forkable_poller():
    """Start Forkable email poller if Gmail credentials are configured."""
    global _forkable_started
    if _forkable_started:
        return
    _forkable_started = True
    try:
        from tools.catering.forkable import poller, GOOGLE_CLIENT_ID, GOOGLE_REFRESH_TOKEN
        if GOOGLE_CLIENT_ID and GOOGLE_REFRESH_TOKEN:
            poller.start()
        else:
            logger.info("Forkable poller: Gmail credentials not configured, skipping")
    except Exception as e:
        logger.warning("Forkable poller: Failed to start - %s", e)


_vendor_invoice_started = False


def _start_vendor_invoice_poller():
    """Start vendor invoice email poller if Gmail credentials are configured."""
    global _vendor_invoice_started
    if _vendor_invoice_started:
        return
    _vendor_invoice_started = True
    try:
        from tools.gmail_service.vendor_invoice_poller import poller as vip
        from tools.gmail_service.gmail_client import GOOGLE_CLIENT_ID, GOOGLE_REFRESH_TOKEN
        if GOOGLE_CLIENT_ID and GOOGLE_REFRESH_TOKEN:
            vip.start()
        else:
            logger.info("Vendor invoice poller: Gmail credentials not configured, skipping")
    except Exception as e:
        logger.warning("Vendor invoice poller: Failed to start - %s", e)


@app.after_request
def _add_security_headers(response):
    """Add basic security headers to all responses."""
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
    response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    return response


@app.before_request
def _ensure_pollers():
    """Start background pollers on first request (gunicorn-safe)."""
    _start_forkable_poller()
    _start_vendor_invoice_poller()


# ── CRM Cron Trigger ──
# Triggers the Sports Outreach CRM pipeline via GitHub Actions workflow_dispatch.
# Hit /crm-cron every 2 min from cron-job.org to keep the pipeline running.
# workflow_dispatch fires IMMEDIATELY (no 30-min schedule delay).

import requests as _requests

_CRM_GITHUB_PAT = os.environ.get("CRM_GITHUB_PAT", "")
_CRM_REPO = "anthonynagle1/livite-sports-outreach"
_CRM_WORKFLOW = "cron-runner.yml"
_crm_last_trigger = {"time": None, "status": None}
_crm_lock = __import__('threading').Lock()


@app.route('/crm-cron')
def crm_cron():
    """Trigger the CRM pipeline via GitHub Actions. Called by external pinger."""
    if not _CRM_GITHUB_PAT:
        return jsonify({"error": "CRM_GITHUB_PAT not configured"}), 500

    with _crm_lock:
        # Prevent double-triggers within 90 seconds
        if _crm_last_trigger["time"]:
            elapsed = (datetime.now() - _crm_last_trigger["time"]).total_seconds()
            if elapsed < 90:
                return jsonify({
                    "status": "skipped",
                    "reason": f"Last triggered {int(elapsed)}s ago, waiting for cooldown",
                    "last_status": _crm_last_trigger["status"],
                })

    try:
        resp = _requests.post(
            f"https://api.github.com/repos/{_CRM_REPO}/actions/workflows/{_CRM_WORKFLOW}/dispatches",
            headers={
                "Authorization": f"Bearer {_CRM_GITHUB_PAT}",
                "Accept": "application/vnd.github.v3+json",
            },
            json={"ref": "main"},
            timeout=10,
        )

        with _crm_lock:
            _crm_last_trigger["time"] = datetime.now()
            _crm_last_trigger["status"] = "triggered" if resp.status_code == 204 else f"error_{resp.status_code}"

        if resp.status_code == 204:
            return jsonify({"status": "triggered", "time": _crm_last_trigger["time"].isoformat()})
        else:
            return jsonify({"status": "error", "code": resp.status_code, "body": resp.text}), 500

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/crm-sync')
def crm_sync():
    """Manual trigger with UI feedback for Meire."""
    result = crm_cron()
    data = result[0].get_json() if isinstance(result, tuple) else result.get_json()

    if data.get("status") == "triggered":
        msg, color = "CRM Pipeline Triggered! Emails will process in ~60 seconds.", "#2ecc71"
    elif data.get("status") == "skipped":
        msg, color = "Pipeline already triggered recently. Try again in a minute.", "#f39c12"
    else:
        msg, color = f"Error: {data.get('message', data.get('body', 'unknown'))}", "#e74c3c"

    return f"""
    <html>
    <head><title>CRM Sync</title></head>
    <body style="font-family: sans-serif; text-align: center; padding: 60px;">
        <h1 style="color: {color};">{msg}</h1>
        <p><a href="/">Back to Dashboard</a></p>
    </body>
    </html>
    """


# ── Review Monitor ──

@app.route('/reviews')
@owner_required
def reviews_page():
    """Review Monitor — Google review dashboard with AI response drafting."""
    try:
        from tools.reviews.html import build_reviews_page
        return Response(build_reviews_page(), content_type='text/html')
    except Exception as e:
        logger.error("Error loading Review Monitor: %s", e, exc_info=True)
        return _error_page(f"Error loading Review Monitor: {e}")


@app.route('/api/reviews/fetch', methods=['POST'])
@owner_required
def api_reviews_fetch():
    """Fetch Google reviews from Outscraper."""
    try:
        from tools.reviews.core import fetch_reviews, get_review_stats
        body = request.get_json(silent=True) or {}
        force = body.get('force', False)
        reviews = fetch_reviews(limit=50, force=force)
        stats = get_review_stats(reviews)
        return jsonify({"reviews": reviews, "stats": stats})
    except Exception as e:
        logger.error("fetch_reviews failed: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/api/reviews/draft', methods=['POST'])
@owner_required
def api_reviews_draft():
    """Draft a review response using Claude."""
    try:
        from tools.reviews.responder import draft_response
        body = request.get_json(silent=True) or {}
        review_text = body.get('review_text', '')
        rating = body.get('rating', 5)
        reviewer_name = body.get('reviewer_name', '')
        draft = draft_response(review_text, rating, reviewer_name)
        return jsonify({"draft": draft})
    except Exception as e:
        logger.error("draft_response failed: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


# ── Daily Email Report ──
@app.route('/api/send-daily-report', methods=['POST'])
@owner_required
def api_send_daily_report():
    """Manually trigger the daily email report."""
    from send_daily_email import send_daily_report
    date_str = request.args.get('date')
    result = send_daily_report(date_str)
    status = 200 if result.get('ok') else 500
    return jsonify(result), status


# ── Scheduler: cache warmup + daily email ──
def _run_cache_warmup():
    """Warm weather cache for all available dates. Runs at 6am ET daily."""
    try:
        from fetch_weather_data import warm_weather_cache
        cached, total = warm_weather_cache()
        logger.info("[scheduler] Cache warmup: %d new days cached (%d total)", cached, total)
    except Exception as e:
        logger.warning("[scheduler] Cache warmup failed: %s", e)


def _start_scheduler():
    """Start APScheduler for cache warmup (6am ET) and daily email report (configurable)."""
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger

        scheduler = BackgroundScheduler()

        # ── Cache warmup at 6am ET — always enabled ──
        scheduler.add_job(
            _run_cache_warmup,
            CronTrigger(hour=6, minute=0, timezone='US/Eastern'),
            id='cache_warmup',
            name='Daily Cache Warmup',
            replace_existing=True,
        )
        logger.info("Cache warmup scheduled at 06:00 ET")

        # ── Daily email report ──
        try:
            with open(os.path.join(os.path.dirname(__file__), 'config.yaml')) as f:
                cfg = yaml.safe_load(f) or {}
            email_cfg = cfg.get('daily_email', {})
            if email_cfg.get('enabled', False):
                from send_daily_email import send_daily_report
                send_time = email_cfg.get('send_time', '08:00')
                hour, minute = [int(x) for x in send_time.split(':')]
                scheduler.add_job(
                    send_daily_report,
                    CronTrigger(hour=hour, minute=minute, timezone='US/Eastern'),
                    id='daily_email_report',
                    name='Daily Email Report',
                    replace_existing=True,
                )
                logger.info("Daily email scheduled at %s ET", send_time)
            else:
                logger.info("Daily email disabled in config.yaml")
        except Exception as e:
            logger.warning("Daily email scheduler error: %s", e)

        scheduler.start()
    except ImportError:
        logger.info("APScheduler not installed — scheduler disabled")
    except Exception as e:
        logger.warning("Scheduler error: %s", e)


# Start scheduler in production (Render sets RENDER=true) or if explicitly enabled
if os.environ.get('RENDER') or os.environ.get('ENABLE_SCHEDULER'):
    _start_scheduler()


if __name__ == '__main__':
    _load_logo()
    print("\n  Livite Dashboard running at http://localhost:5001")
    print("  Press Ctrl+C to stop.\n")
    app.run(host='127.0.0.1', port=5001, debug=False)
