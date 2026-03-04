"""Scheduled vs Actual review page — compare published schedules to Toast actuals.

Lightweight shell with async data fetch, same pattern as html.py.
"""

from __future__ import annotations

import os
import sys

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_BASE_DIR, "tools"))

from htmlrender.sections import _CSS
from tools.scheduling.html import _sub_nav_html, _SCHEDULE_CSS

_PURPLE = "#9b72c4"
_GREEN = "#8cb82e"
_RED = "#e86040"
_AMBER = "#e8a830"

_REVIEW_CSS = """
/* Review-specific */
.var-pos{color:#e86040;font-weight:600;}
.var-neg{color:#8cb82e;font-weight:600;}
.var-zero{color:var(--muted);}
.review-table{width:100%;border-collapse:collapse;font-size:13px;}
.review-table th,.review-table td{padding:8px 6px;border-bottom:1px solid var(--border);}
.review-table thead th{font-size:11px;font-weight:600;color:var(--muted);text-transform:uppercase;
  letter-spacing:.3px;text-align:center;}
.review-table thead th:first-child{text-align:left;}
.expand-btn{cursor:pointer;font-size:10px;color:var(--muted);border:1px solid var(--border);
  border-radius:4px;padding:2px 6px;background:var(--bg);}
.expand-btn:hover{background:var(--border);}
.day-detail{display:none;font-size:12px;}
.day-detail td{padding:4px 6px;color:var(--muted);border-bottom:1px solid rgba(0,0,0,.05);}
.week-select{font-size:14px;padding:6px 12px;border:1px solid var(--border);border-radius:6px;
  background:var(--bg);color:var(--text);cursor:pointer;}
"""

_JS_TEMPLATE = """
'use strict';
var DATA = null;
var WEEK = '__WEEK__';

async function loadReview(){
  var el = document.getElementById('loading-text');
  el.textContent = 'Loading scheduled vs actual data...';
  try {
    var resp = await fetch('/api/schedule/review/data?week=' + WEEK);
    if (!resp.ok) throw new Error('Server returned ' + resp.status);
    DATA = await resp.json();
    if (DATA.error) throw new Error(DATA.error);
    document.getElementById('loading').style.display = 'none';
    document.getElementById('content').style.display = 'block';
    renderAll();
  } catch(err){
    el.textContent = 'Error: ' + err.message;
    el.style.color = '#e86040';
  }
}

function renderAll(){
  renderOverview();
  renderChart();
  renderTable();
}

function renderOverview(){
  var t = DATA.totals || {};
  var items = [
    {l:'Scheduled Hours',v:t.scheduled||0},
    {l:'Actual Hours',v:t.actual||0},
    {l:'Variance',v:(t.variance>0?'+':'')+t.variance+'h'},
    {l:'Variance %',v:(t.variance_pct>0?'+':'')+t.variance_pct+'%'}
  ];
  var h = '<div class="card"><div class="card-title">Week Summary';
  h += '<span class="card-subtitle">' + esc(DATA.week_label||'') + '</span></div>';
  h += '<div class="stat-grid">';
  for (var i=0;i<items.length;i++){
    var color = 'var(--text)';
    if (i>=2){
      var v = parseFloat(String(items[i].v));
      if (v > 0) color = '#e86040';
      else if (v < 0) color = '#8cb82e';
    }
    h += '<div class="stat-card"><div class="stat-val" style="color:'+color+'">'+items[i].v+'</div>';
    h += '<div class="stat-lbl">'+items[i].l+'</div></div>';
  }
  h += '</div></div>';
  document.getElementById('overview').innerHTML = h;
}

function renderChart(){
  var emps = DATA.employees || [];
  if (!emps.length) return;
  // Show top 15 by absolute variance
  var sorted = emps.slice().sort(function(a,b){return Math.abs(b.variance)-Math.abs(a.variance);});
  var top = sorted.slice(0, 15);
  var labels = top.map(function(e){return e.display_name||e.name;});
  var sched = top.map(function(e){return e.scheduled_hrs;});
  var actual = top.map(function(e){return e.actual_hrs;});

  var h = '<div class="card"><div class="card-title">Scheduled vs Actual Hours</div>';
  h += '<canvas id="reviewChart" height="280"></canvas></div>';
  document.getElementById('chart-container').innerHTML = h;

  new Chart(document.getElementById('reviewChart'),{
    type:'bar',
    data:{labels:labels, datasets:[
      {label:'Scheduled',data:sched,backgroundColor:'#8cb82e'},
      {label:'Actual',data:actual,backgroundColor:'#4a9cd8'}
    ]},
    options:{
      indexAxis:'y',
      responsive:true,
      scales:{x:{beginAtZero:true}},
      plugins:{legend:{display:true}}
    }
  });
}

function renderTable(){
  var emps = DATA.employees || [];
  if (!emps.length){
    document.getElementById('table-container').innerHTML = '<div class="card"><p style="color:var(--muted);text-align:center;">No data available for this week.</p></div>';
    return;
  }
  var hdr = '<th style="text-align:left;">Employee</th><th>Dept</th><th>Sched Hrs</th>';
  hdr += '<th>Actual Hrs</th><th>Variance</th><th>Shifts (S/A)</th><th></th>';

  var rows = '';
  for (var i=0;i<emps.length;i++){
    var e = emps[i];
    var vc = e.variance > 0 ? 'var-pos' : (e.variance < 0 ? 'var-neg' : 'var-zero');
    var vs = (e.variance > 0 ? '+' : '') + e.variance.toFixed(1) + 'h';
    rows += '<tr>';
    rows += '<td style="font-weight:600;">' + esc(e.display_name||e.name) + '</td>';
    rows += '<td style="text-align:center;">' + (e.department||'').toUpperCase() + '</td>';
    rows += '<td style="text-align:center;">' + e.scheduled_hrs.toFixed(1) + '</td>';
    rows += '<td style="text-align:center;">' + e.actual_hrs.toFixed(1) + '</td>';
    rows += '<td style="text-align:center;" class="'+vc+'">' + vs + '</td>';
    rows += '<td style="text-align:center;">' + e.scheduled_shifts + ' / ' + e.actual_shifts + '</td>';
    rows += '<td><button class="expand-btn" onclick="toggleDetail('+i+')">Detail</button></td>';
    rows += '</tr>';

    // Day detail rows (hidden)
    var days = e.days || [];
    for (var di=0;di<days.length;di++){
      var dd = days[di];
      var sh = dd.sched_hrs > 0 ? dd.sched_start+'-'+dd.sched_end+' ('+dd.sched_hrs+'h)' : '--';
      var ah = dd.actual_hrs > 0 ? dd.actual_start+'-'+dd.actual_end+' ('+dd.actual_hrs+'h)' : '--';
      var dv = dd.actual_hrs - dd.sched_hrs;
      var dvc = dv > 0 ? 'var-pos' : (dv < 0 ? 'var-neg' : 'var-zero');
      var dvs = dv !== 0 ? ((dv>0?'+':'')+dv.toFixed(1)+'h') : '--';
      rows += '<tr class="day-detail" id="detail-'+i+'">';
      rows += '<td style="padding-left:24px;">'+esc(dd.dow)+' '+esc(dd.date.slice(5))+'</td>';
      rows += '<td></td>';
      rows += '<td style="text-align:center;font-size:11px;">'+sh+'</td>';
      rows += '<td style="text-align:center;font-size:11px;">'+ah+'</td>';
      rows += '<td style="text-align:center;" class="'+dvc+'">'+dvs+'</td>';
      rows += '<td colspan="2"></td>';
      rows += '</tr>';
    }
  }

  var h = '<div class="card"><div class="card-title">Employee Breakdown</div>';
  h += '<div style="overflow-x:auto;"><table class="review-table">';
  h += '<thead><tr>'+hdr+'</tr></thead><tbody>'+rows+'</tbody></table></div></div>';
  document.getElementById('table-container').innerHTML = h;
}

function toggleDetail(idx){
  var rows = document.querySelectorAll('#detail-'+idx);
  for (var i=0;i<rows.length;i++){
    rows[i].style.display = rows[i].style.display === 'table-row' ? 'none' : 'table-row';
  }
}

function changeWeek(){
  var sel = document.getElementById('week-select');
  if (sel && sel.value){
    window.location.href = '/schedule/review?week=' + sel.value;
  }
}

function esc(s){var d=document.createElement('div');d.textContent=s||'';return d.innerHTML;}

loadReview();
"""


def build_review_page(logo_b64: str = "", week: str = "",
                      saved_weeks: list = None) -> str:
    """Build the Scheduled vs Actual review page.

    Args:
        logo_b64: base64-encoded logo
        week: ISO week string like '2026-W08'
        saved_weeks: list of dicts from persistence.list_schedules()
    """
    if saved_weeks is None:
        saved_weeks = []

    logo_html = ""
    if logo_b64:
        logo_html = (
            f'<img src="data:image/png;base64,{logo_b64}" '
            'style="height:28px;margin-bottom:8px;" alt="Livite">'
        )

    sub_nav = _sub_nav_html("review")

    # Week selector dropdown
    options = ""
    for sw in saved_weeks:
        if sw.get("status") != "published":
            continue
        sel = "selected" if sw["week"] == week else ""
        label = sw.get("week_label") or sw["week"]
        options += f'<option value="{sw["week"]}" {sel}>{label}</option>'

    week_selector = ""
    if options:
        week_selector = (
            '<select id="week-select" class="week-select" onchange="changeWeek()">'
            + options + '</select>'
        )

    js_code = _JS_TEMPLATE.replace("__WEEK__", week)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Livite - Schedule Review</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
<style>
{_CSS}
{_SCHEDULE_CSS}
{_REVIEW_CSS}
</style>
</head>
<body>

<div style="text-align:center;padding:24px 16px 8px;">
  {logo_html}
  <h1 style="font-size:22px;margin:8px 0 4px;">Scheduled vs Actual</h1>
  <div style="margin-top:8px;">{week_selector}</div>
  {sub_nav}
</div>

<div id="loading" style="text-align:center;padding:40px 16px;">
  <div id="loading-text" style="color:var(--muted);font-size:14px;">Loading...</div>
</div>

<div id="content" style="display:none;">
  <div id="overview"></div>
  <div id="chart-container"></div>
  <div id="table-container"></div>
</div>

<script>
{js_code}
</script>

</body>
</html>"""
