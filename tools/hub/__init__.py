"""
Livite Hub — PWA Business Dashboard Blueprint
Aggregates health, sales, outreach, and deliveries into one mobile view.
Mounted at /hub on the Sales Analysis app.
"""

import logging
import os
import time
from datetime import datetime, timedelta

import requests
from flask import Blueprint, jsonify, request, session, Response

logger = logging.getLogger(__name__)

bp = Blueprint('hub', __name__, url_prefix='/hub')

# ── In-memory cache ──────────────────────────────────────

_cache = {}


def _cache_get(key, ttl=300):
    entry = _cache.get(key)
    if entry and (time.time() - entry['ts'] < ttl):
        return entry['data']
    return None


def _cache_set(key, data):
    _cache[key] = {'data': data, 'ts': time.time()}


def _cache_age(key):
    entry = _cache.get(key)
    return int(time.time() - entry['ts']) if entry else None


def _cache_clear(key=None):
    if key:
        _cache.pop(key, None)
    else:
        _cache.clear()


# ── Pi heartbeat store ───────────────────────────────────

_pi_heartbeat = None


# ── Auth helper (reuses Sales Analysis session) ─────────

def _check_auth():
    return session.get('authenticated') and session.get('role') == 'owner'


def _check_api_key():
    key = request.headers.get('X-API-Key') or request.args.get('api_key')
    return key and key == os.getenv('HUB_API_KEY', '')


# ── Health checks ────────────────────────────────────────

def _get_all_status():
    cached = _cache_get('hub_health', 300)
    if cached:
        cached['cache_age'] = _cache_age('hub_health')
        return cached

    result = {
        'email_service': _check_email(),
        'sales_dashboard': {'status': 'ok', 'color': 'green', 'detail': 'this server'},
        'notion_api': _check_notion(),
        'pi_cron': _check_pi(),
        'cache_age': 0,
    }
    _cache_set('hub_health', result)
    return result


def _check_email():
    url = os.getenv('EMAIL_SERVICE_URL', '')
    if not url:
        return {'status': 'unconfigured', 'color': 'gray'}
    try:
        resp = requests.get(f"{url}/health", timeout=10)
        if resp.status_code == 200:
            try:
                sr = requests.get(f"{url}/status", timeout=10)
                if sr.status_code == 200:
                    data = sr.json()
                    return {'status': 'ok', 'color': 'green',
                            'detail': f"{data.get('processed_emails', 0)} processed today",
                            'pending': data.get('pending_emails', 0)}
            except Exception as e:
                logger.debug("Failed to fetch email service status detail: %s", e)
            return {'status': 'ok', 'color': 'green', 'detail': ''}
        return {'status': f'HTTP {resp.status_code}', 'color': 'red'}
    except requests.Timeout:
        return {'status': 'timeout', 'color': 'red'}
    except requests.ConnectionError:
        return {'status': 'unreachable', 'color': 'red'}
    except Exception as e:
        return {'status': str(e)[:50], 'color': 'red'}


def _check_notion():
    api_key = os.getenv('NOTION_API_KEY', '')
    db_id = os.getenv('NOTION_GAMES_DB', '') or os.getenv('NOTION_PROFIT_DB_ID', '')
    if not api_key or not db_id:
        return {'status': 'unconfigured', 'color': 'gray'}
    try:
        from notion_client import Client
        notion = Client(auth=api_key)
        start = time.time()
        notion.databases.retrieve(database_id=db_id)
        ms = int((time.time() - start) * 1000)
        return {'status': 'ok', 'color': 'green', 'detail': f'{ms}ms'}
    except Exception as e:
        return {'status': str(e)[:50], 'color': 'red'}


def _check_pi():
    global _pi_heartbeat
    if not _pi_heartbeat:
        return {'status': 'no heartbeat', 'color': 'gray', 'detail': 'Waiting for first check-in'}
    age = time.time() - _pi_heartbeat['received_at']
    if age < 900:
        return {'status': 'ok', 'color': 'green', 'detail': f'{int(age/60)}m ago',
                'alerts': _pi_heartbeat.get('alerts', [])}
    elif age < 3600:
        return {'status': 'stale', 'color': 'yellow', 'detail': f'{int(age/60)}m ago'}
    else:
        return {'status': 'offline', 'color': 'red', 'detail': f'{int(age/3600)}h ago'}


# ── Pipeline (Notion) ───────────────────────────────────

def _get_pipeline():
    cached = _cache_get('hub_pipeline', 1800)
    if cached:
        return cached

    api_key = os.getenv('NOTION_API_KEY', '')
    games_db = os.getenv('NOTION_GAMES_DB', '')
    email_queue_db = os.getenv('NOTION_EMAIL_QUEUE_DB', '')
    if not api_key or not games_db or not email_queue_db:
        return None

    from notion_client import Client
    from datetime import date
    notion = Client(auth=api_key)
    today = date.today().isoformat()
    result = {'games': {}, 'queue': {}}

    for status in ['Not Contacted', 'Introduction Email - Sent', 'Follow-Up Email - Sent', 'Responded', 'In Conversation', 'Interested', 'Booked']:
        result['games'][status] = _count_pages(notion, games_db, {
            "and": [
                {"property": "Game Date", "date": {"on_or_after": today}},
                {"property": "Outreach Status", "select": {"equals": status}},
            ]
        })
        time.sleep(0.35)

    for status in ['Draft', 'Approved']:
        result['queue'][status] = _count_pages(notion, email_queue_db, {
            "property": "Status", "select": {"equals": status},
        })
        time.sleep(0.35)

    _cache_set('hub_pipeline', result)
    return result


def _count_pages(notion, db_id, filter_obj):
    count = 0
    has_more = True
    cursor = None
    while has_more:
        kwargs = {"database_id": db_id, "filter": filter_obj, "page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor
        resp = notion.databases.query(**kwargs)
        count += len(resp['results'])
        has_more = resp.get('has_more', False)
        cursor = resp.get('next_cursor')
    return count


# ── Deliveries (Notion) ─────────────────────────────────

def _get_deliveries():
    cached = _cache_get('hub_deliveries', 1800)
    if cached:
        return cached

    api_key = os.getenv('NOTION_API_KEY', '')
    orders_db = os.getenv('NOTION_ORDERS_DB', '')
    if not api_key or not orders_db:
        return []

    from notion_client import Client
    from datetime import date
    notion = Client(auth=api_key)
    today = date.today()
    week_end = today + timedelta(days=7)

    try:
        resp = notion.databases.query(
            database_id=orders_db,
            filter={"and": [
                {"property": "Delivery Date", "date": {"on_or_after": today.isoformat()}},
                {"property": "Delivery Date", "date": {"before": week_end.isoformat()}},
            ]},
            sorts=[{"property": "Delivery Date", "direction": "ascending"}],
        )
        deliveries = []
        for order in resp['results'][:15]:
            props = order['properties']
            title = props.get('Order ID', {}).get('title', [])
            name = title[0]['plain_text'] if title else 'Unknown'
            delivery = props.get('Delivery Date', {}).get('date', {})
            del_date = delivery.get('start', '') if delivery else ''
            status = props.get('Payment Status', {}).get('select', {})
            status_name = status.get('name', '') if status else ''
            amount = props.get('Total Amount', {}).get('number')
            deliveries.append({
                'name': name, 'date': del_date[:10] if del_date else '',
                'status': status_name, 'amount': amount,
            })
        _cache_set('hub_deliveries', deliveries)
        return deliveries
    except Exception:
        return []


# ── Routes ───────────────────────────────────────────────

@bp.route('/')
def hub_dashboard():
    if not _check_auth():
        from flask import redirect, url_for
        return redirect(url_for('login'))

    import os
    from flask import send_from_directory
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    nextjs_hub = os.path.join(project_root, 'web', 'out', 'hub', 'index.html')
    if os.path.exists(nextjs_hub):
        return send_from_directory(os.path.join(project_root, 'web', 'out', 'hub'), 'index.html')

    # Legacy HTML fallback
    name = session.get('name', 'there')
    now = datetime.now()
    hour = now.hour
    if hour < 12:
        greeting = 'Good morning'
    elif hour < 17:
        greeting = 'Good afternoon'
    else:
        greeting = 'Good evening'
    date_str = now.strftime('%A, %B %d %Y')
    return HUB_HTML.replace('{{greeting}}', greeting).replace('{{name}}', name).replace('{{date}}', date_str)


@bp.route('/api/status')
def hub_api_status():
    if not _check_auth():
        return jsonify({'error': 'Unauthorized'}), 401
    return jsonify(_get_all_status())


@bp.route('/api/sales')
def hub_api_sales():
    if not _check_auth():
        return jsonify({'error': 'Unauthorized'}), 401
    # Call _get_profit_data from the main app (lazy import avoids circular)
    yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y%m%d')
    try:
        import importlib
        app_module = importlib.import_module('app')
        data = app_module._get_profit_data(yesterday)
        return jsonify(data or {'error': 'No data available'})
    except Exception:
        return jsonify({'error': 'No data available'})


@bp.route('/api/pipeline')
def hub_api_pipeline():
    if not _check_auth():
        return jsonify({'error': 'Unauthorized'}), 401
    data = _get_pipeline()
    return jsonify(data or {'error': 'No data available'})


@bp.route('/api/deliveries')
def hub_api_deliveries():
    if not _check_auth():
        return jsonify({'error': 'Unauthorized'}), 401
    data = _get_deliveries()
    return jsonify(data or [])


@bp.route('/api/heartbeat', methods=['POST'])
def hub_api_heartbeat():
    if not _check_api_key():
        return jsonify({'error': 'Unauthorized'}), 401
    global _pi_heartbeat
    _pi_heartbeat = {**(request.get_json(silent=True) or {}), 'received_at': time.time()}
    return jsonify({'ok': True})


@bp.route('/api/sales/trigger', methods=['POST'])
def hub_api_sales_trigger():
    if not _check_api_key():
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json(silent=True) or {}
    date_str = data.get('date', '')
    if date_str:
        _cache_clear(f'hub_sales_{date_str}')
        return jsonify({'ok': True})
    return jsonify({'error': 'Missing date'}), 400


@bp.route('/api/refresh', methods=['POST'])
def hub_api_refresh():
    if not _check_auth():
        return jsonify({'error': 'Unauthorized'}), 401
    _cache_clear()
    return jsonify({'ok': True})


# ── HTML ─────────────────────────────────────────────────

HUB_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="theme-color" content="#475417">
<link rel="manifest" href="/static/manifest.json">
<link rel="apple-touch-icon" href="/static/icons/icon-192.png">
<link rel="apple-touch-startup-image" href="/static/icons/icon-512.png">
<meta name="apple-mobile-web-app-title" content="Livite Hub">
<title>Livite Hub</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=DM+Serif+Display&display=swap" rel="stylesheet">
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: 'DM Sans', -apple-system, system-ui, sans-serif;
  background: #F5EDDC;
  color: #2d2a24;
  min-height: 100vh;
  padding-bottom: env(safe-area-inset-bottom, 20px);
  -webkit-font-smoothing: antialiased;
}
.topbar{display:flex;align-items:center;justify-content:space-between;padding:14px 32px;border-bottom:1px solid rgba(0,0,0,0.08);background:rgba(245,237,220,0.85);backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);position:sticky;top:0;z-index:100;}
.topbar-brand{display:flex;align-items:center;gap:10px;text-decoration:none;}
.topbar-logo{width:28px;height:28px;border-radius:8px;background:#475417;display:flex;align-items:center;justify-content:center;color:white;font-family:'DM Serif Display',serif;font-size:15px;}
.topbar-title{font-family:'DM Serif Display',serif;font-size:18px;font-weight:400;color:#292524;letter-spacing:0.3px;}
.content { padding: 16px; max-width: 500px; margin: 0 auto; }
.card {
  background: #fff;
  border-radius: 12px;
  padding: 16px;
  margin-bottom: 12px;
  box-shadow: 0 1px 4px rgba(0,0,0,0.06);
}
.card-title {
  font-size: 11px; font-weight: 700; letter-spacing: 0.5px;
  text-transform: uppercase; color: #7a7265; margin-bottom: 12px;
}
.health-row { display: flex; align-items: center; padding: 6px 0; font-size: 14px; }
.health-dot { width: 10px; height: 10px; border-radius: 50%; margin-right: 10px; flex-shrink: 0; }
.dot-green { background: #22c55e; } .dot-yellow { background: #eab308; }
.dot-red { background: #ef4444; } .dot-gray { background: #d1d5db; }
.health-label { flex: 1; color: #2d2a24; }
.health-detail { color: #7a7265; font-size: 13px; }
.sales-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.sales-item { padding: 8px 0; }
.sales-label { font-size: 12px; color: #7a7265; }
.sales-value { font-size: 20px; font-weight: 700; color: #2d2a24; }
.sales-pct { font-size: 13px; color: #7a7265; }
.profit-positive { color: #16a34a; } .profit-negative { color: #dc2626; }
.sales-link { display: block; text-align: center; color: #475417; font-size: 14px; font-weight: 600; padding: 8px; margin-top: 8px; border-top: 1px solid #f0ece4; text-decoration: none; }
.pipeline-row { display: flex; align-items: center; padding: 4px 0; font-size: 14px; }
.pipeline-count { width: 36px; font-weight: 700; color: #475417; text-align: right; margin-right: 12px; }
.pipeline-label { color: #2d2a24; }
.pipeline-queue { font-size: 13px; color: #7a7265; margin-top: 8px; padding-top: 8px; border-top: 1px solid #f0ece4; }
.delivery-item { padding: 8px 0; border-bottom: 1px solid #f0ece4; display: flex; justify-content: space-between; align-items: center; }
.delivery-item:last-child { border-bottom: none; }
.delivery-date { font-size: 12px; color: #7a7265; }
.delivery-name { font-size: 14px; font-weight: 600; }
.delivery-amount { font-size: 14px; font-weight: 700; color: #475417; }
.delivery-status { font-size: 11px; padding: 2px 8px; border-radius: 4px; background: #f0ece4; color: #7a7265; }
.alert-item { display: flex; align-items: flex-start; padding: 8px 0; font-size: 14px; }
.alert-icon { margin-right: 8px; flex-shrink: 0; }
.alert-text { color: #2d2a24; }
.no-alerts { color: #7a7265; font-size: 14px; text-align: center; padding: 8px 0; }
.footer { text-align: center; font-size: 12px; color: #7a7265; padding: 12px; }
.footer a { color: #475417; }
.skeleton { background: linear-gradient(90deg, #f0ece4 25%, #e8e2d6 50%, #f0ece4 75%); background-size: 200% 100%; animation: shimmer 1.5s infinite; border-radius: 4px; height: 20px; margin: 4px 0; }
@keyframes shimmer { 0% { background-position: 200% 0; } 100% { background-position: -200% 0; } }
.offline-banner { display: none; background: #fef3c7; color: #3d6819; text-align: center; padding: 8px; font-size: 13px; }
.ptr-indicator { text-align: center; height: 0; overflow: hidden; transition: height 0.2s; color: #7a7265; font-size: 13px; display: flex; align-items: center; justify-content: center; }
.ptr-indicator.visible { height: 44px; }
.ptr-spinner { width: 18px; height: 18px; border: 2px solid #d1d5db; border-top-color: #475417; border-radius: 50%; animation: spin 0.6s linear infinite; margin-right: 8px; }
@keyframes spin { to { transform: rotate(360deg); } }
.refresh-btn { background: none; border: none; color: #475417; font-size: 13px; font-weight: 600; cursor: pointer; padding: 4px 8px; margin-left: 8px; }
.refresh-btn:active { opacity: 0.5; }
</style>
</head>
<body>
<div class="offline-banner" id="offline-banner">You're offline &mdash; showing cached data</div>
<div class="topbar">
  <a class="topbar-brand" href="/hub">
    <div class="topbar-logo">L</div>
    <span class="topbar-title">Livite Hub</span>
  </a>
</div>
<div class="ptr-indicator" id="ptr"><div class="ptr-spinner"></div>Refreshing...</div>
<div class="content">
  <div class="card" id="health-card">
    <div class="card-title">System Health</div>
    <div id="health-rows">
      <div class="skeleton" style="width:80%"></div>
      <div class="skeleton" style="width:70%"></div>
      <div class="skeleton" style="width:75%"></div>
    </div>
  </div>
  <div class="card" id="sales-card">
    <div class="card-title">Yesterday's Numbers</div>
    <div id="sales-content">
      <div class="skeleton" style="width:60%"></div>
      <div class="skeleton" style="width:50%"></div>
    </div>
  </div>
  <div class="card" id="pipeline-card">
    <div class="card-title">Outreach Pipeline</div>
    <div id="pipeline-content">
      <div class="skeleton" style="width:65%"></div>
      <div class="skeleton" style="width:55%"></div>
    </div>
  </div>
  <div class="card" id="deliveries-card">
    <div class="card-title">This Week's Deliveries</div>
    <div id="deliveries-content">
      <div class="skeleton" style="width:90%"></div>
      <div class="skeleton" style="width:85%"></div>
    </div>
  </div>
  <div class="card" id="alerts-card">
    <div class="card-title">Alerts</div>
    <div id="alerts-content">
      <div class="no-alerts">Checking...</div>
    </div>
  </div>
</div>
<div class="footer">
  <div><span id="updated">Loading...</span><button class="refresh-btn" onclick="manualRefresh()">Refresh</button></div>
  <div style="margin:6px 0"><button class="refresh-btn" onclick="warmCaches()" id="warm-btn">Warm Dashboard Caches</button></div>
  <div id="warm-status" style="font-size:11px;color:#7a7265;min-height:14px"></div>
  <a href="/logout">Sign Out</a>
</div>
<script>
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/static/sw.js').catch(() => {});
}
window.addEventListener('online', () => { document.getElementById('offline-banner').style.display = 'none'; refreshAll(); });
window.addEventListener('offline', () => { document.getElementById('offline-banner').style.display = 'block'; });

const $ = (id) => document.getElementById(id);
function fmt(n) {
  if (n == null) return '\\u2014';
  return '$' + Number(n).toLocaleString('en-US', {minimumFractionDigits: 0, maximumFractionDigits: 0});
}
function pct(n, total) {
  if (!total || !n) return '';
  return '(' + (n / total * 100).toFixed(1) + '%)';
}
async function fetchJSON(url) {
  try {
    const resp = await fetch(url);
    if (resp.status === 401 || resp.redirected) { window.location = '/login'; return null; }
    return await resp.json();
  } catch { return null; }
}

async function loadHealth() {
  const data = await fetchJSON('/hub/api/status');
  if (!data) return;
  const systems = [
    {key: 'email_service', label: 'Email Service'},
    {key: 'sales_dashboard', label: 'Sales Dashboard'},
    {key: 'notion_api', label: 'Notion API'},
    {key: 'pi_cron', label: 'Pi Cron'},
  ];
  let html = '';
  let alerts = [];
  for (const s of systems) {
    const info = data[s.key] || {};
    const color = info.color || 'gray';
    const detail = info.detail || info.status || '';
    html += '<div class="health-row"><div class="health-dot dot-' + color + '"></div><span class="health-label">' + s.label + '</span><span class="health-detail">' + detail + '</span></div>';
    if (color === 'red') alerts.push(s.label + ': ' + info.status);
  }
  const pi = data.pi_cron || {};
  if (pi.alerts && pi.alerts.length) { for (const a of pi.alerts) alerts.push(a); }
  $('health-rows').innerHTML = html;
  window._healthAlerts = alerts;
  renderAlerts();
}

async function loadSales() {
  const data = await fetchJSON('/hub/api/sales');
  if (!data || data.error) {
    $('sales-content').innerHTML = '<div style="color:#7a7265;font-size:14px;text-align:center;padding:12px 0">Sales data unavailable</div>';
    return;
  }
  const revenue = data['Toast Total'] || data['toast_total'] || 0;
  const labor = data['Labor'] || data['labor'] || 0;
  const food = data['Food Cost'] || data['food_cost'] || 0;
  const profit = data['Profit'] || data['profit'] || 0;
  const profitPct = revenue > 0 ? (profit / revenue * 100).toFixed(1) : 0;
  const profitClass = profit >= 0 ? 'profit-positive' : 'profit-negative';
  $('sales-content').innerHTML =
    '<div class="sales-grid">' +
    '<div class="sales-item"><div class="sales-label">Revenue</div><div class="sales-value">' + fmt(revenue) + '</div></div>' +
    '<div class="sales-item"><div class="sales-label">Labor</div><div class="sales-value">' + fmt(labor) + '</div><div class="sales-pct">' + pct(labor, revenue) + '</div></div>' +
    '<div class="sales-item"><div class="sales-label">Food Cost</div><div class="sales-value">' + fmt(food) + '</div><div class="sales-pct">' + pct(food, revenue) + '</div></div>' +
    '<div class="sales-item"><div class="sales-label">Profit</div><div class="sales-value ' + profitClass + '">' + fmt(profit) + '</div><div class="sales-pct ' + profitClass + '">' + profitPct + '%</div></div>' +
    '</div>' +
    '<a class="sales-link" href="/">View Full Dashboard &rarr;</a>';
}

async function loadPipeline() {
  const data = await fetchJSON('/hub/api/pipeline');
  if (!data || data.error) {
    $('pipeline-content').innerHTML = '<div style="color:#7a7265;font-size:14px;text-align:center">Pipeline data unavailable</div>';
    return;
  }
  const g = data.games || {};
  const q = data.queue || {};
  let html = '';
  [['Not Contacted','Not Contacted'],['Intro Sent','Introduction Email - Sent'],['Follow-Up Sent','Follow-Up Email - Sent'],['Responded','Responded'],['In Conversation','In Conversation'],['Interested','Interested'],['Booked','Booked']].forEach(function(pair) {
    html += '<div class="pipeline-row"><span class="pipeline-count">' + (g[pair[1]] || 0) + '</span><span class="pipeline-label">' + pair[0] + '</span></div>';
  });
  html += '<div class="pipeline-queue">Queue: ' + (q['Draft'] || 0) + ' draft, ' + (q['Approved'] || 0) + ' approved</div>';
  $('pipeline-content').innerHTML = html;
}

async function loadDeliveries() {
  const data = await fetchJSON('/hub/api/deliveries');
  if (!data || !data.length) {
    $('deliveries-content').innerHTML = '<div class="no-alerts">No deliveries this week</div>';
    $('deliveries-card').querySelector('.card-title').textContent = "This Week's Deliveries";
    return;
  }
  $('deliveries-card').querySelector('.card-title').textContent = "This Week's Deliveries (" + data.length + ")";
  let html = '';
  for (const d of data) {
    const amount = d.amount ? fmt(d.amount) : '';
    const status = d.status ? '<span class="delivery-status">' + d.status + '</span>' : '';
    html += '<div class="delivery-item"><div><div class="delivery-date">' + d.date + '</div><div class="delivery-name">' + d.name + '</div></div><div style="text-align:right"><div class="delivery-amount">' + amount + '</div>' + status + '</div></div>';
  }
  $('deliveries-content').innerHTML = html;
}

window._healthAlerts = [];
function renderAlerts() {
  const alerts = window._healthAlerts || [];
  if (!alerts.length) { $('alerts-content').innerHTML = '<div class="no-alerts">All clear</div>'; return; }
  let html = '';
  for (const a of alerts) { html += '<div class="alert-item"><span class="alert-icon">&#9888;&#65039;</span><span class="alert-text">' + a + '</span></div>'; }
  $('alerts-content').innerHTML = html;
}

let lastRefresh = null;
let isRefreshing = false;
async function refreshAll(showSpinner) {
  if (isRefreshing) return;
  isRefreshing = true;
  if (showSpinner) $('ptr').classList.add('visible');
  await Promise.all([loadHealth(), loadSales(), loadPipeline(), loadDeliveries()]);
  lastRefresh = new Date();
  updateTimestamp();
  isRefreshing = false;
  $('ptr').classList.remove('visible');
}
function updateTimestamp() {
  if (!lastRefresh) return;
  const diff = Math.floor((Date.now() - lastRefresh) / 1000);
  let text;
  if (diff < 10) text = 'Just now';
  else if (diff < 60) text = diff + 's ago';
  else if (diff < 3600) text = Math.floor(diff/60) + 'm ago';
  else text = lastRefresh.toLocaleTimeString('en-US', {hour:'numeric', minute:'2-digit'});
  $('updated').textContent = 'Updated ' + text;
}
setInterval(updateTimestamp, 15000);
function manualRefresh() { refreshAll(true); }

let touchStart = 0;
let pulling = false;
document.addEventListener('touchstart', function(e) { if (window.scrollY === 0) touchStart = e.touches[0].clientY; }, {passive: true});
document.addEventListener('touchmove', function(e) { if (!touchStart) return; if (e.touches[0].clientY - touchStart > 60 && window.scrollY === 0) pulling = true; }, {passive: true});
document.addEventListener('touchend', function() { if (pulling) refreshAll(true); touchStart = 0; pulling = false; }, {passive: true});

refreshAll(false);
let refreshInterval = setInterval(function() { refreshAll(false); }, 300000);
document.addEventListener('visibilitychange', function() {
  if (document.hidden) { clearInterval(refreshInterval); }
  else { refreshAll(false); refreshInterval = setInterval(function() { refreshAll(false); }, 300000); }
});

// ── Warm dashboard caches ──
async function warmCaches() {
  var btn = $('warm-btn');
  var status = $('warm-status');
  btn.disabled = true;
  btn.textContent = 'Warming...';

  // Step 1: Weather
  status.textContent = 'Loading weather data...';
  try {
    var wr = await fetch('/cache/warm', {method: 'POST'});
    var wd = await wr.json();
    if (wd.status === 'ok') {
      status.textContent = wd.message + '. Now caching dashboards...';
    }
  } catch(e) { status.textContent = 'Weather failed, continuing...'; }

  // Step 2: Precache last 14 days
  var now = new Date();
  var end = new Date(now); end.setDate(end.getDate() - 1);
  var start = new Date(now); start.setDate(start.getDate() - 14);
  function pad(d) { return d.getFullYear() + String(d.getMonth()+1).padStart(2,'0') + String(d.getDate()).padStart(2,'0'); }
  var startStr = pad(start), endStr = pad(end);
  var done = false;
  while (!done) {
    try {
      var pr = await fetch('/api/precache?start=' + startStr + '&end=' + endStr);
      var pd = await pr.json();
      if (pd.done) { done = true; }
      else if (pd.remaining !== undefined) {
        var total = 14;
        var cached = total - pd.remaining;
        status.textContent = 'Cached ' + cached + ' of ' + total + ' days...';
      }
      else { done = true; }
    } catch(e) { done = true; }
  }

  // Step 3: Aggregate
  try {
    status.textContent = 'Building aggregations...';
    await fetch('/api/precache/aggregate?start=' + startStr + '&end=' + endStr);
  } catch(e) {}

  btn.disabled = false;
  btn.textContent = 'Warm Dashboard Caches';
  status.textContent = 'All caches warm!';
  status.style.color = '#16a34a';
  setTimeout(function() { status.textContent = ''; status.style.color = '#7a7265'; }, 5000);
}
</script>
</body>
</html>"""
