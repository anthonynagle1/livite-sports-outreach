"""Interactive schedule page — client-side rendered with drag-and-drop.

Serves a lightweight loading shell immediately; schedule data is fetched
asynchronously via /api/schedule/data and rendered client-side with:
- Drag-and-drop shift reassignment
- Click-to-explain for empty/unavailable cells
- Live stat and chart updates
"""

from __future__ import annotations

import os
import sys

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_BASE_DIR, "tools"))

from htmlrender.sections import _CSS

# ── Color constants ──
_PURPLE = "#9b72c4"
_GREEN = "#8cb82e"
_BLUE = "#4a9cd8"
_RED = "#e86040"
_AMBER = "#e8a830"


def _sub_nav_html(active="schedule"):
    """Navigation strip with active page highlighted."""
    links = [
        ("Home", "/"),
        ("Today", "/today"),
        ("This Week", "/week"),
        ("Forecast", "/forecast"),
        ("Schedule", "/schedule"),
        ("Review", "/schedule/review"),
        ("Employees", "/employees"),
    ]
    items = []
    for label, href in links:
        cls = "nav-pill active" if label.lower() == active else "nav-pill"
        items.append(f'<a href="{href}" class="{cls}">{label}</a>')
    return (
        '<div style="display:flex;justify-content:center;gap:8px;margin-top:12px;flex-wrap:wrap;">'
        + "".join(items)
        + '</div>'
    )


# ── Schedule-specific CSS ──
_SCHEDULE_CSS = """
/* Nav pills */
.nav-pill{font-size:12px;padding:5px 12px;border-radius:6px;background:var(--bg);
  color:var(--text);text-decoration:none;font-weight:500;}
.nav-pill.active{background:""" + _PURPLE + """;color:#fff;font-weight:600;}

/* Loading */
.skel{background:var(--surface);border-radius:8px;padding:24px;animation:pulse 1.5s ease-in-out infinite;}
@keyframes pulse{0%,100%{opacity:1;}50%{opacity:.5;}}
.progress-wrap{width:260px;margin:0 auto;padding-top:4px;}
.progress-track{height:8px;background:var(--border);border-radius:4px;overflow:hidden;}
.progress-fill{height:100%;width:0%;background:""" + _GREEN + """;border-radius:4px;transition:width .6s ease;}
.progress-pct{font-size:12px;color:var(--muted);margin-top:6px;text-align:center;font-variant-numeric:tabular-nums;}

/* Schedule grid */
.sched-table{width:100%;border-collapse:collapse;font-size:13px;}
.sched-table th,.sched-table td{padding:8px 6px;border-bottom:1px solid var(--border);}
.sched-table thead th{font-size:11px;font-weight:600;color:var(--muted);text-transform:uppercase;
  letter-spacing:.3px;text-align:center;}
.sched-table thead th:first-child{text-align:left;}
.dept-row td{background:var(--bg);font-weight:700;font-size:11px;color:var(--muted);padding:8px;
  text-transform:uppercase;letter-spacing:.5px;}

/* Shift cells */
.shift-cell{cursor:grab;transition:all .15s;user-select:none;text-align:center;position:relative;}
.shift-cell:active{cursor:grabbing;}
.shift-cell.dragging{opacity:.35;transform:scale(.95);}
.shift-badge{display:inline-block;padding:4px 8px;border-radius:6px;font-size:12px;white-space:nowrap;}
.shift-badge.manager{background:rgba(140,184,46,.15);}
.shift-badge.staff{background:rgba(74,156,216,.1);}

/* Drop targets */
.drop-target{outline:2px dashed """ + _GREEN + """ !important;outline-offset:-2px;
  background:rgba(140,184,46,.08) !important;}

/* Empty / unavailable cells */
.empty-avail{cursor:pointer;transition:background .15s;min-height:36px;text-align:center;position:relative;}
.empty-avail:hover{background:rgba(140,184,46,.06);}
.unavail-cell{background:#f0f0f0;text-align:center;color:#ccc;cursor:pointer;min-height:36px;}
.unavail-cell:hover{background:#e8e8e8;}

/* Tooltip */
.tooltip-popup{position:fixed;background:var(--surface);border:1px solid var(--border);
  border-radius:8px;padding:12px 16px;box-shadow:0 4px 16px rgba(0,0,0,.15);
  font-size:13px;max-width:300px;z-index:1000;pointer-events:auto;}
.tooltip-popup .tt-title{font-weight:600;margin-bottom:4px;}
.tooltip-popup .tt-detail{color:var(--muted);font-size:12px;line-height:1.4;}
.tooltip-popup .tt-avail{font-size:11px;color:var(--muted);margin-top:6px;
  padding-top:6px;border-top:1px solid var(--border);}

/* Cards */
.card{background:var(--surface);border-radius:8px;padding:16px;margin:12px 16px;}
.card-title{font-size:14px;font-weight:600;margin-bottom:12px;}
.card-subtitle{font-size:11px;color:var(--muted);font-weight:400;margin-left:8px;}

/* Stat grid */
.stat-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;}
@media(max-width:500px){.stat-grid{grid-template-columns:repeat(2,1fr);}}
.stat-card{background:var(--bg);border-radius:8px;padding:14px 8px;text-align:center;}
.stat-val{font-size:20px;font-weight:700;}
.stat-lbl{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.3px;margin-top:2px;}

/* Edit modal */
.edit-overlay{position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.4);
  z-index:1100;display:flex;align-items:center;justify-content:center;}
.edit-panel{background:var(--surface);border-radius:12px;padding:24px;width:320px;
  max-width:90vw;box-shadow:0 8px 32px rgba(0,0,0,.25);}
.edit-panel h3{margin:0 0 16px;font-size:18px;}
.edit-row{display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;}
.edit-row label{font-size:13px;font-weight:500;color:var(--muted);}
.edit-row input[type=time]{font-size:16px;padding:8px 10px;border:1px solid var(--border);
  border-radius:6px;background:var(--bg);color:var(--text);}
.edit-hours{font-size:14px;color:var(--muted);text-align:center;margin-bottom:16px;}
.edit-avail{font-size:11px;color:var(--muted);margin-bottom:12px;text-align:center;}
.edit-btns{display:flex;gap:8px;}
.edit-btns button{flex:1;padding:10px;border:none;border-radius:8px;font-size:14px;
  font-weight:600;cursor:pointer;}
.edit-btns .btn-save{background:#8cb82e;color:#fff;}
.edit-btns .btn-cancel{background:var(--bg);color:var(--text);}

/* Move confirmation toast */
.toast-msg{position:fixed;bottom:24px;left:50%;transform:translateX(-50%);
  background:#333;color:#fff;padding:10px 20px;border-radius:8px;font-size:13px;
  z-index:1001;opacity:0;transition:opacity .3s;}
.toast-msg.show{opacity:1;}

/* Save/Publish controls */
.persist-bar{display:flex;align-items:center;justify-content:center;gap:8px;margin-top:10px;flex-wrap:wrap;}
.persist-btn{font-size:12px;padding:5px 14px;border-radius:6px;border:none;cursor:pointer;font-weight:600;}
.persist-btn.save{background:#4a9cd8;color:#fff;}
.persist-btn.save:hover{background:#3a8cc8;}
.persist-btn.publish{background:#8cb82e;color:#fff;}
.persist-btn.publish:hover{background:#7ca81e;}
.persist-btn:disabled{opacity:.5;cursor:not-allowed;}
.status-badge{font-size:11px;padding:3px 10px;border-radius:10px;font-weight:600;letter-spacing:.3px;}
.status-badge.draft{background:rgba(74,156,216,.15);color:#4a9cd8;}
.status-badge.published{background:rgba(140,184,46,.15);color:#8cb82e;}
.status-badge.unsaved{background:rgba(200,200,200,.3);color:var(--muted);}
"""


# ── JavaScript (plain string — no f-string interpolation needed) ──
# __WEEK__ placeholder is replaced at render time.
_JS_TEMPLATE = """
'use strict';
const WEEK = '__WEEK__';
let DATA = null;
let dragState = null;
let dragJustEnded = false;

// ── Loading with % progress bar ──
const loadSteps = [
  {pct:5,  msg:'Generating revenue forecast...'},
  {pct:20, msg:'Pulling historical staffing data...'},
  {pct:40, msg:'Computing labor demand curves...'},
  {pct:60, msg:'Assigning shifts...'},
  {pct:80, msg:'Optimizing coverage...'},
  {pct:92, msg:'Finalizing schedule...'}
];
let stepIdx = 0;
function setProgress(pct, msg){
  var fill = document.getElementById('progress-fill');
  var pctEl = document.getElementById('progress-pct');
  var txtEl = document.getElementById('loading-text');
  if (fill) fill.style.width = pct + '%';
  if (pctEl) pctEl.textContent = pct + '%';
  if (txtEl && msg) txtEl.textContent = msg;
}
setProgress(loadSteps[0].pct, loadSteps[0].msg);
const msgTimer = setInterval(function(){
  stepIdx++;
  if (stepIdx < loadSteps.length){
    setProgress(loadSteps[stepIdx].pct, loadSteps[stepIdx].msg);
  }
}, 3500);

async function loadSchedule(){
  try {
    var resp = await fetch('/api/schedule/data?week=' + WEEK);
    if (!resp.ok) throw new Error('Server returned ' + resp.status);
    DATA = await resp.json();
    clearInterval(msgTimer);
    setProgress(100, 'Done');
    setTimeout(function(){
      document.getElementById('loading').style.display = 'none';
      document.getElementById('content').style.display = 'block';
      var wl = document.getElementById('week-label');
      if (wl) wl.textContent = DATA.week_label || '';
      renderAll();
      // Set status from saved schedule metadata
      if (DATA._status) updateStatusBadge(DATA._status);
      else updateStatusBadge('unsaved');
    }, 300);
  } catch(err) {
    clearInterval(msgTimer);
    var lt = document.getElementById('loading-text');
    if (lt) { lt.textContent = 'Error: ' + err.message; lt.style.color = '#e86040'; }
    var pctEl = document.getElementById('progress-pct');
    if (pctEl) pctEl.textContent = 'Failed';
    var fill = document.getElementById('progress-fill');
    if (fill) { fill.style.width = '100%'; fill.style.background = '#e86040'; }
  }
}

function renderAll(){
  renderOverview();
  renderDemandChart();
  renderScheduleGrid();
  renderCoverageChart();
  renderRevLaborChart();
  renderEmployeeSummary();
  document.getElementById('footer').textContent = 'Generated ' + new Date().toLocaleString();
}

// ── Overview Stats ──
function renderOverview(){
  var d = DATA;
  var rev = d.total_revenue_forecast || 0;
  var hrs = d.total_labor_hours || 0;
  var cost = d.labor_cost_estimate || 0;
  var lpct = rev > 0 ? (cost / rev * 100) : 0;
  var splh = d.projected_splh || 0;
  var cov = d.coverage_score || 0;
  var items = [
    {l:'Week Revenue',v:fmtC(rev)}, {l:'Labor Hours',v:Math.round(hrs)},
    {l:'Labor Cost',v:fmtC(cost)}, {l:'Labor %',v:lpct.toFixed(1)+'%'},
    {l:'SPLH',v:fmtC(splh)}, {l:'Coverage',v:cov.toFixed(1)+'%'}
  ];
  var h = '<div class="card"><div class="card-title">Schedule Overview</div><div class="stat-grid">';
  for (var i=0;i<items.length;i++){
    h += '<div class="stat-card"><div class="stat-val">'+items[i].v+'</div><div class="stat-lbl">'+items[i].l+'</div></div>';
  }
  h += '</div></div>';
  document.getElementById('overview').innerHTML = h;
}

// ── Demand Chart ──
function renderDemandChart(){
  var days = DATA.days || [];
  if (!days.length) return;
  var labels = days.map(function(d){return d.dow_name;});
  var foh = days.map(function(d){return (d.demand||{}).foh_hours||0;});
  var boh = days.map(function(d){return (d.demand||{}).boh_hours||0;});
  var h = '<div class="card"><div class="card-title">Daily Labor Demand (Hours)</div>';
  h += '<canvas id="demandChart" height="220"></canvas></div>';
  document.getElementById('demand-chart-container').innerHTML = h;
  new Chart(document.getElementById('demandChart'),{
    type:'bar', data:{labels:labels, datasets:[
      {label:'FOH Hours',data:foh,backgroundColor:'#8cb82e'},
      {label:'BOH Hours',data:boh,backgroundColor:'#4a9cd8'}
    ]},
    options:{responsive:true,
      scales:{x:{stacked:true},y:{stacked:true,beginAtZero:true}},
      plugins:{legend:{display:true}}}
  });
}

// ── Schedule Grid (interactive) ──
function renderScheduleGrid(){
  var days = DATA.days || [];
  var emps = DATA.employee_summary || [];
  var avail = DATA.availability || {};
  if (!days.length || !emps.length) return;

  // Build shift lookup: employee -> dow -> shift
  var sm = {};
  for (var di=0;di<days.length;di++){
    var d = days[di];
    var shifts = d.shifts || [];
    for (var si=0;si<shifts.length;si++){
      var s = shifts[si];
      if (!sm[s.employee]) sm[s.employee] = {};
      sm[s.employee][d.dow_name] = s;
    }
  }

  // Header
  var hdr = '<th style="min-width:90px;">Employee</th>';
  for (var di=0;di<days.length;di++){
    var d = days[di];
    hdr += '<th style="min-width:70px;">'+esc(d.dow_name)+'<br><span style="font-weight:400;font-size:10px;">'+esc(d.date.slice(5))+'</span></th>';
  }
  hdr += '<th>Hours</th><th style="text-align:right;">Cost</th>';

  var rows = '';
  var curDept = '';
  for (var ei=0;ei<emps.length;ei++){
    var emp = emps[ei];
    var dept = emp.department || '';
    if (dept !== curDept){
      curDept = dept;
      rows += '<tr class="dept-row"><td colspan="'+(days.length+3)+'">'+(dept?dept.toUpperCase():'OTHER')+'</td></tr>';
    }
    var name = emp.name;
    var display = esc(emp.display_name || name);
    var wh = emp.weekly_hours || 0;
    var mh = emp.max_hours || 40;
    var hc = wh > mh ? '#e86040' : (wh >= mh*.9 ? '#e8a830' : 'var(--text)');

    var cells = '';
    for (var di=0;di<days.length;di++){
      var dow = days[di].dow_name;
      var shift = (sm[name]||{})[dow];
      if (shift){
        var sf = fmtT(shift.start);
        var ef = fmtT(shift.end);
        var bc = shift.is_manager ? 'manager' : 'staff';
        cells += '<td class="shift-cell" draggable="true" data-emp="'+escA(name)+'" data-day="'+di+'">';
        cells += '<div class="shift-badge '+bc+'">'+sf+'-'+ef;
        cells += '<br><span style="font-size:10px;color:var(--muted);">'+shift.hours+'h</span></div></td>';
      } else {
        var ea = (avail[name]||{})[dow];
        if (ea === null || ea === undefined){
          cells += '<td class="unavail-cell" data-emp="'+escA(name)+'" data-day="'+di+'">--</td>';
        } else {
          cells += '<td class="empty-avail" data-emp="'+escA(name)+'" data-day="'+di+'">&nbsp;</td>';
        }
      }
    }
    rows += '<tr><td style="font-weight:600;font-size:12px;white-space:nowrap;">'+display+'</td>';
    rows += cells;
    rows += '<td style="color:'+hc+';font-weight:600;text-align:center;">'+wh.toFixed(0);
    rows += '<span style="font-size:10px;color:var(--muted);">/'+mh.toFixed(0)+'</span></td>';
    rows += '<td style="text-align:right;font-size:12px;">'+fmtC(emp.cost||0)+'</td></tr>';
  }

  // Totals
  var dayTot = '';
  for (var di=0;di<days.length;di++){
    var d = days[di];
    dayTot += '<td style="font-weight:700;text-align:center;border-top:2px solid var(--border);">';
    dayTot += (d.headcount||0)+' staff<br><span style="font-size:10px;color:var(--muted);">'+(d.total_hours||0).toFixed(0)+'h</span></td>';
  }
  rows += '<tr><td style="font-weight:700;border-top:2px solid var(--border);">TOTAL</td>'+dayTot;
  rows += '<td style="font-weight:700;text-align:center;border-top:2px solid var(--border);">'+(DATA.total_labor_hours||0).toFixed(0)+'h</td>';
  rows += '<td style="font-weight:700;text-align:right;border-top:2px solid var(--border);">'+fmtC(DATA.labor_cost_estimate||0)+'</td></tr>';

  var html = '<div class="card"><div class="card-title">Weekly Schedule';
  html += '<span class="card-subtitle">Tap shift to edit &bull; Drag to move &bull; Tap empty cell for details</span></div>';
  html += '<div style="overflow-x:auto;-webkit-overflow-scrolling:touch;">';
  html += '<table class="sched-table"><thead><tr>'+hdr+'</tr></thead>';
  html += '<tbody>'+rows+'</tbody></table></div></div>';

  document.getElementById('schedule-grid-container').innerHTML = html;
  initDragDrop();
  initCellClicks();
}

// ── Drag and Drop ──
function initDragDrop(){
  var cells = document.querySelectorAll('.shift-cell');
  for (var i=0;i<cells.length;i++){
    cells[i].addEventListener('dragstart', onDragStart);
    cells[i].addEventListener('dragend', onDragEnd);
  }
  var targets = document.querySelectorAll('.empty-avail');
  for (var i=0;i<targets.length;i++){
    targets[i].addEventListener('dragover', onDragOver);
    targets[i].addEventListener('dragleave', onDragLeave);
    targets[i].addEventListener('drop', onDrop);
  }
}

function onDragStart(e){
  var emp = e.currentTarget.dataset.emp;
  var day = parseInt(e.currentTarget.dataset.day);
  dragState = {emp:emp, day:day};
  e.currentTarget.classList.add('dragging');
  e.dataTransfer.effectAllowed = 'move';
  e.dataTransfer.setData('text/plain', emp+'|'+day);
  // Highlight valid targets (same employee, different day, available)
  setTimeout(function(){
    var empties = document.querySelectorAll('.empty-avail');
    for (var i=0;i<empties.length;i++){
      if (empties[i].dataset.emp === emp && parseInt(empties[i].dataset.day) !== day){
        empties[i].classList.add('drop-target');
      }
    }
  }, 0);
}

function onDragOver(e){
  if (!dragState) return;
  if (e.currentTarget.dataset.emp !== dragState.emp) return;
  if (parseInt(e.currentTarget.dataset.day) === dragState.day) return;
  e.preventDefault();
  e.dataTransfer.dropEffect = 'move';
}

function onDragLeave(e){/* CSS handles visual */}

function onDragEnd(e){
  e.currentTarget.classList.remove('dragging');
  var all = document.querySelectorAll('.drop-target');
  for (var i=0;i<all.length;i++) all[i].classList.remove('drop-target');
  dragState = null;
  dragJustEnded = true;
  setTimeout(function(){ dragJustEnded = false; }, 100);
}

function onDrop(e){
  e.preventDefault();
  if (!dragState) return;
  var tEmp = e.currentTarget.dataset.emp;
  var tDay = parseInt(e.currentTarget.dataset.day);
  if (tEmp !== dragState.emp || tDay === dragState.day) return;
  applyMove(dragState.emp, dragState.day, tDay);
  // Clean up
  var all = document.querySelectorAll('.drop-target,.dragging');
  for (var i=0;i<all.length;i++) all[i].classList.remove('drop-target','dragging');
  dragState = null;
}

async function applyMove(employee, fromIdx, toIdx){
  try {
    var resp = await fetch('/api/schedule/move', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({employee:employee, from_day:fromIdx, to_day:toIdx, week:WEEK})
    });
    if (!resp.ok){
      var err = await resp.json();
      showToast(err.error || 'Move failed');
      return;
    }
    DATA = await resp.json();
    renderAll();
    showToast('Shift moved successfully');
  } catch(err){
    showToast('Error: ' + err.message);
  }
}

// ── Click-to-Explain ──
function initCellClicks(){
  var container = document.getElementById('schedule-grid-container');
  container.addEventListener('click', function(e){
    // Click on a shift badge → open editor (skip if just finished dragging)
    var shiftCell = e.target.closest('.shift-cell');
    if (shiftCell && !dragJustEnded){
      e.stopPropagation();
      openShiftEditor(shiftCell.dataset.emp, parseInt(shiftCell.dataset.day));
      return;
    }
    // Click on empty/unavail → show explanation
    var cell = e.target.closest('.empty-avail, .unavail-cell');
    if (!cell) return;
    e.stopPropagation();
    var emp = cell.dataset.emp;
    var dayIdx = parseInt(cell.dataset.day);
    showExplanation(emp, dayIdx, cell);
  });
}

function showExplanation(employee, dayIdx, cell){
  var days = DATA.days || [];
  var avail = DATA.availability || {};
  var dow = days[dayIdx] ? days[dayIdx].dow_name : '';
  var empAvail = (avail[employee]||{})[dow];
  var displayName = employee.split(', ').reverse().join(' ');
  // Capitalize first letters
  displayName = displayName.replace(/\\b\\w/g, function(c){return c.toUpperCase();});

  var title, detail, availInfo = '';

  if (empAvail === null || empAvail === undefined){
    title = 'Not Available';
    detail = displayName + ' is not available on ' + dow + 's.';
    // Show which days they ARE available
    var empAllAvail = avail[employee] || {};
    var availDays = [];
    var dowNames = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
    for (var i=0;i<dowNames.length;i++){
      if (empAllAvail[dowNames[i]] !== null && empAllAvail[dowNames[i]] !== undefined){
        availDays.push(dowNames[i]);
      }
    }
    if (availDays.length) availInfo = 'Available: ' + availDays.join(', ');
  } else {
    // Available but not scheduled
    var summary = null;
    var summaries = DATA.employee_summary || [];
    for (var i=0;i<summaries.length;i++){
      if (summaries[i].name === employee) { summary = summaries[i]; break; }
    }
    var wh = summary ? summary.weekly_hours : 0;
    var mh = summary ? summary.max_hours : 40;

    if (wh >= mh){
      title = 'Max Hours Reached';
      detail = displayName + ' has used ' + wh.toFixed(0) + ' of ' + mh.toFixed(0) + ' weekly hours. No capacity for additional shifts.';
    } else if (wh >= mh * 0.85){
      title = 'Near Max Hours';
      detail = displayName + ' has ' + (mh - wh).toFixed(1) + 'h remaining this week (' + wh.toFixed(0) + '/' + mh.toFixed(0) + '). Shift was skipped to preserve hours for other days.';
    } else {
      title = 'Available - Not Assigned';
      detail = displayName + ' is available ' + empAvail.start + '-' + empAvail.end + ' but demand was covered by other staff.';
    }
    availInfo = 'Availability: ' + empAvail.start + ' - ' + empAvail.end;
  }

  var rect = cell.getBoundingClientRect();
  showTooltip(title, detail, availInfo, rect.left + rect.width/2, rect.bottom + 6);
}

// ── Tooltip ──
function showTooltip(title, detail, availInfo, x, y){
  var tip = document.getElementById('tooltip');
  var html = '<div class="tt-title">' + esc(title) + '</div>';
  html += '<div class="tt-detail">' + esc(detail) + '</div>';
  if (availInfo) html += '<div class="tt-avail">' + esc(availInfo) + '</div>';
  tip.innerHTML = html;
  tip.style.display = 'block';
  // Position: keep on screen
  var maxX = window.innerWidth - 320;
  tip.style.left = Math.max(8, Math.min(x - 140, maxX)) + 'px';
  tip.style.top = Math.min(y, window.innerHeight - 120) + 'px';
  setTimeout(function(){
    document.addEventListener('click', dismissTooltip, {once:true});
  }, 50);
}

function dismissTooltip(){
  document.getElementById('tooltip').style.display = 'none';
}

// ── Toast notification ──
function showToast(msg){
  var el = document.getElementById('toast');
  el.textContent = msg;
  el.classList.add('show');
  setTimeout(function(){ el.classList.remove('show'); }, 2500);
}

// ── Coverage Chart ──
function renderCoverageChart(){
  var days = DATA.days || [];
  if (!days.length) return;
  var labels = days.map(function(d){return d.dow_name;});
  var pcts = days.map(function(d){
    var c = d.coverage || [];
    if (!c.length) return 0;
    var filled = c.filter(function(s){return s.filled;}).length;
    return Math.round(filled / c.length * 100);
  });
  var colors = pcts.map(function(p){return p>=90?'#8cb82e':p>=70?'#e8a830':'#e86040';});
  var h = '<div class="card"><div class="card-title">Daily Coverage</div>';
  h += '<canvas id="covChart" height="180"></canvas></div>';
  document.getElementById('coverage-chart-container').innerHTML = h;
  new Chart(document.getElementById('covChart'),{
    type:'bar', data:{labels:labels, datasets:[{label:'Coverage %',data:pcts,backgroundColor:colors}]},
    options:{responsive:true,
      scales:{y:{beginAtZero:true,max:100,ticks:{callback:function(v){return v+'%';}}}},
      plugins:{legend:{display:false}}}
  });
}

// ── Revenue vs Labor ──
function renderRevLaborChart(){
  var days = DATA.days || [];
  if (!days.length) return;
  var labels = days.map(function(d){return d.dow_name;});
  var revs = days.map(function(d){return d.revenue_forecast||0;});
  var costs = days.map(function(d){return d.labor_cost||0;});
  var h = '<div class="card"><div class="card-title">Revenue vs Labor Cost</div>';
  h += '<canvas id="rlChart" height="220"></canvas></div>';
  document.getElementById('rev-labor-container').innerHTML = h;
  new Chart(document.getElementById('rlChart'),{
    type:'bar', data:{labels:labels, datasets:[
      {label:'Revenue',data:revs,backgroundColor:'#8cb82e'},
      {label:'Labor Cost',data:costs,backgroundColor:'#e86040'}
    ]},
    options:{responsive:true,
      scales:{y:{beginAtZero:true,ticks:{callback:function(v){return '$'+v.toLocaleString();}}}},
      plugins:{legend:{display:true}}}
  });
}

// ── Employee Summary ──
function renderEmployeeSummary(){
  var emps = DATA.employee_summary || [];
  if (!emps.length) return;
  var rows = '';
  for (var i=0;i<emps.length;i++){
    var e = emps[i];
    var wh = e.weekly_hours||0, mh = e.max_hours||40, pm = e.pct_max||0;
    var st;
    if (wh > mh) st = '<span style="color:#e86040;font-weight:600;">OT</span>';
    else if (pm >= 90) st = '<span style="color:#e8a830;font-weight:600;">Near Max</span>';
    else st = '<span style="color:#8cb82e;">OK</span>';
    rows += '<tr><td>'+esc(e.display_name||e.name)+'</td><td>'+(e.department||'').toUpperCase()+'</td>';
    rows += '<td>'+wh.toFixed(1)+'</td><td>'+mh.toFixed(0)+'</td>';
    rows += '<td>'+(e.shifts||0)+'</td><td>'+fmtC(e.cost||0)+'</td><td>'+st+'</td></tr>';
  }
  var h = '<div class="card"><div class="card-title">Employee Summary</div>';
  h += '<div style="overflow-x:auto;"><table class="sched-table">';
  h += '<thead><tr><th style="text-align:left;">Employee</th><th>Dept</th><th>Hours</th><th>Max</th><th>Shifts</th><th>Cost</th><th>Status</th></tr></thead>';
  h += '<tbody>'+rows+'</tbody></table></div></div>';
  document.getElementById('employee-summary-container').innerHTML = h;
}

// ── Shift Editor Modal ──
function openShiftEditor(employee, dayIdx){
  dismissTooltip();
  var days = DATA.days || [];
  var day = days[dayIdx];
  if (!day) return;
  var dow = day.dow_name;

  // Find the shift
  var shift = null;
  var shifts = day.shifts || [];
  for (var i = 0; i < shifts.length; i++){
    if (shifts[i].employee === employee) { shift = shifts[i]; break; }
  }
  if (!shift) return;

  var avail = (DATA.availability || {})[employee] || {};
  var dayAvail = avail[dow];
  var availStr = dayAvail ? dayAvail.start + ' - ' + dayAvail.end : '';

  var displayName = employee.split(', ').reverse().join(' ');
  displayName = displayName.replace(/\\b\\w/g, function(c){return c.toUpperCase();});

  var el = document.getElementById('edit-modal');
  el.innerHTML = '<div class="edit-panel">' +
    '<h3>' + esc(displayName) + '</h3>' +
    '<div style="font-size:13px;color:var(--muted);margin-bottom:16px;">' + esc(dow) + ' \\u2014 ' + esc(day.date) + '</div>' +
    (availStr ? '<div class="edit-avail">Availability: ' + esc(availStr) + '</div>' : '') +
    '<div class="edit-row"><label>Start</label><input type="time" id="edit-start" value="' + shift.start + '"></div>' +
    '<div class="edit-row"><label>End</label><input type="time" id="edit-end" value="' + shift.end + '"></div>' +
    '<div class="edit-hours" id="edit-hours">' + shift.hours + 'h</div>' +
    '<div class="edit-btns">' +
    '<button class="btn-cancel" onclick="closeShiftEditor()">Cancel</button>' +
    '<button class="btn-save" onclick="saveShiftEdit(\\'' + escA(employee) + '\\',' + dayIdx + ')">Save</button>' +
    '</div></div>';
  el.style.display = 'flex';

  document.getElementById('edit-start').addEventListener('change', updateEditHours);
  document.getElementById('edit-end').addEventListener('change', updateEditHours);

  // Close on overlay click (outside panel)
  el.addEventListener('click', function(e){
    if (e.target === el) closeShiftEditor();
  }, {once:true});
}

function updateEditHours(){
  var startEl = document.getElementById('edit-start');
  var endEl = document.getElementById('edit-end');
  if (!startEl || !endEl || !startEl.value || !endEl.value) return;
  var sp = startEl.value.split(':');
  var ep = endEl.value.split(':');
  var sh = parseInt(sp[0]) + parseInt(sp[1])/60;
  var eh = parseInt(ep[0]) + parseInt(ep[1])/60;
  var hours = eh - sh;
  var display = document.getElementById('edit-hours');
  if (display){
    if (hours <= 0) display.textContent = 'Invalid times';
    else display.textContent = hours.toFixed(1) + 'h';
  }
}

function closeShiftEditor(){
  document.getElementById('edit-modal').style.display = 'none';
}

async function saveShiftEdit(employee, dayIdx){
  var startEl = document.getElementById('edit-start');
  var endEl = document.getElementById('edit-end');
  var start = startEl.value;
  var end = endEl.value;
  try {
    var resp = await fetch('/api/schedule/edit', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({employee: employee, day: dayIdx, start: start, end: end, week: WEEK})
    });
    if (!resp.ok){
      var err = await resp.json();
      showToast(err.error || 'Edit failed');
      return;
    }
    DATA = await resp.json();
    closeShiftEditor();
    renderAll();
    showToast('Shift updated');
  } catch(err){
    showToast('Error: ' + err.message);
  }
}

// ── Utilities ──
function fmtC(n){return '$' + Math.round(n).toLocaleString();}
function fmtT(t){
  var p = t.split(':'); var h = parseInt(p[0]); var m = parseInt(p[1]||0);
  var s = h < 12 ? 'a' : 'p';
  if (h > 12) h -= 12; if (h === 0) h = 12;
  return m > 0 ? h+':'+String(m).padStart(2,'0')+s : h+s;
}
function esc(s){
  var d = document.createElement('div'); d.textContent = s||''; return d.innerHTML;
}
function escA(s){
  return (s||'').replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ── Persistence (Save/Publish) ──
var scheduleStatus = 'unsaved';  // unsaved | draft | published

function updateStatusBadge(status){
  scheduleStatus = status;
  var badge = document.getElementById('status-badge');
  if (!badge) return;
  badge.className = 'status-badge ' + status;
  badge.textContent = status.charAt(0).toUpperCase() + status.slice(1);
  // Update button states
  var saveBtn = document.getElementById('btn-save');
  var pubBtn = document.getElementById('btn-publish');
  if (saveBtn) saveBtn.disabled = false;
  if (pubBtn) pubBtn.disabled = (status !== 'draft');
}

async function saveScheduleDraft(){
  if (!DATA) return;
  var btn = document.getElementById('btn-save');
  if (btn) { btn.disabled = true; btn.textContent = 'Saving...'; }
  try {
    var resp = await fetch('/api/schedule/save', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({week: WEEK, schedule: DATA})
    });
    if (!resp.ok){ var err = await resp.json(); showToast(err.error||'Save failed'); return; }
    var result = await resp.json();
    updateStatusBadge(result.status || 'draft');
    showToast('Schedule saved as draft');
  } catch(err){
    showToast('Error: ' + err.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Save Draft'; }
  }
}

async function publishSchedule(){
  if (!DATA) return;
  var btn = document.getElementById('btn-publish');
  if (btn) { btn.disabled = true; btn.textContent = 'Publishing...'; }
  try {
    // Save first, then publish
    var resp = await fetch('/api/schedule/save', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({week: WEEK, schedule: DATA})
    });
    if (!resp.ok){ var err = await resp.json(); showToast(err.error||'Save failed'); return; }
    var resp2 = await fetch('/api/schedule/publish', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({week: WEEK})
    });
    if (!resp2.ok){ var err = await resp2.json(); showToast(err.error||'Publish failed'); return; }
    var result = await resp2.json();
    updateStatusBadge('published');
    showToast('Schedule published');
  } catch(err){
    showToast('Error: ' + err.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Publish'; }
  }
}

// Start
loadSchedule();
"""


# ── Page Builder ──

def build_schedule_page(logo_b64: str = "", week: str = "this") -> str:
    """Build the interactive schedule page shell.

    This returns immediately (no data fetching). The browser JS
    fetches /api/schedule/data asynchronously and renders client-side.
    """
    logo_html = ""
    if logo_b64:
        logo_html = (
            f'<img src="data:image/png;base64,{logo_b64}" '
            'style="height:28px;margin-bottom:8px;" alt="Livite">'
        )

    this_style = f"background:{_PURPLE};color:#fff;font-weight:600;" if week == "this" else "background:var(--bg);color:var(--text);"
    next_style = f"background:{_PURPLE};color:#fff;font-weight:600;" if week == "next" else "background:var(--bg);color:var(--text);"

    sub_nav = _sub_nav_html("schedule")

    js_code = _JS_TEMPLATE.replace("__WEEK__", week)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Livite - Labor Schedule</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
<style>
{_CSS}
{_SCHEDULE_CSS}
</style>
</head>
<body>

<!-- Header (renders immediately) -->
<div style="text-align:center;padding:24px 16px 8px;">
  {logo_html}
  <h1 style="font-size:22px;margin:8px 0 4px;">Labor Schedule</h1>
  <div id="week-label" style="font-size:14px;color:var(--muted);">Loading...</div>
  <div style="display:flex;justify-content:center;gap:8px;margin-top:12px;">
    <a href="/schedule?week=this" style="font-size:12px;padding:5px 14px;border-radius:6px;{this_style}text-decoration:none;">This Week</a>
    <a href="/schedule?week=next" style="font-size:12px;padding:5px 14px;border-radius:6px;{next_style}text-decoration:none;">Next Week</a>
  </div>
  <div class="persist-bar">
    <span id="status-badge" class="status-badge unsaved">Unsaved</span>
    <button id="btn-save" class="persist-btn save" onclick="saveScheduleDraft()">Save Draft</button>
    <button id="btn-publish" class="persist-btn publish" onclick="publishSchedule()" disabled>Publish</button>
  </div>
  {sub_nav}
</div>

<!-- Loading skeleton (shown until data arrives) -->
<div id="loading">
  <div style="text-align:center;padding:30px 0 20px;">
    <div id="loading-text" style="color:var(--muted);font-size:14px;">
      Generating revenue forecast...
    </div>
    <div class="progress-wrap">
      <div class="progress-track"><div id="progress-fill" class="progress-fill"></div></div>
      <div id="progress-pct" class="progress-pct">0%</div>
    </div>
  </div>
  <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;padding:0 16px;">
    <div class="skel" style="height:60px;"></div>
    <div class="skel" style="height:60px;"></div>
    <div class="skel" style="height:60px;"></div>
  </div>
  <div class="skel" style="height:180px;margin:16px;"></div>
  <div class="skel" style="height:300px;margin:16px;"></div>
</div>

<!-- Content (hidden until data loads) -->
<div id="content" style="display:none;">
  <div id="overview"></div>
  <div id="demand-chart-container"></div>
  <div id="schedule-grid-container"></div>
  <div id="coverage-chart-container"></div>
  <div id="rev-labor-container"></div>
  <div id="employee-summary-container"></div>
  <div id="footer" style="text-align:center;padding:16px;font-size:11px;color:var(--muted);"></div>
</div>

<!-- Shift Edit Modal -->
<div id="edit-modal" class="edit-overlay" style="display:none;"></div>

<!-- Tooltip for explanations -->
<div id="tooltip" class="tooltip-popup" style="display:none;"></div>

<!-- Toast for notifications -->
<div id="toast" class="toast-msg"></div>

<script>
{js_code}
</script>

</body>
</html>"""


# ── Availability CSS & JS ──

_AVAIL_CSS = """
/* Availability matrix */
.avail-table{width:100%;border-collapse:collapse;font-size:13px;}
.avail-table th,.avail-table td{padding:8px 6px;border-bottom:1px solid var(--border);text-align:center;}
.avail-table thead th{font-size:11px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.3px;}
.avail-table thead th:first-child{text-align:left;}
.dept-row td{background:var(--bg);font-weight:700;font-size:11px;color:var(--muted);padding:8px;
  text-transform:uppercase;letter-spacing:.5px;}

.avail-on{background:rgba(140,184,46,.08);cursor:pointer;transition:background .15s;white-space:nowrap;}
.avail-on:hover{background:rgba(140,184,46,.18);}
.avail-off{background:#f5f5f5;color:#bbb;cursor:pointer;transition:background .15s;}
.avail-off:hover{background:#eee;color:#999;}
.emp-name{font-weight:600;font-size:12px;text-align:left;white-space:nowrap;cursor:pointer;}
.emp-name:hover{color:""" + _PURPLE + """;}
.emp-meta{font-size:10px;color:var(--muted);font-weight:400;}
.avail-time{font-size:12px;font-weight:500;}
"""

_AVAIL_JS = """
'use strict';
var AVAIL_DATA = __AVAIL_JSON__;

function fmtShort(t){
  var p = t.split(':'); var h = parseInt(p[0]); var m = parseInt(p[1]||0);
  var s = h < 12 ? 'a' : 'p';
  if (h > 12) h -= 12; if (h === 0) h = 12;
  return m > 0 ? h+':'+String(m).padStart(2,'0')+s : h+s;
}
function esc(s){var d=document.createElement('div');d.textContent=s||'';return d.innerHTML;}
function escA(s){return(s||'').replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}

function showToast(msg){
  var el = document.getElementById('toast');
  el.textContent = msg;
  el.classList.add('show');
  setTimeout(function(){ el.classList.remove('show'); }, 2500);
}

// ── Edit modal (reused pattern) ──
function openAvailEditor(emp, dow){
  var empData = (AVAIL_DATA.employees || {})[emp];
  if (!empData) return;
  var avail = (empData.availability || {})[dow];
  var displayName = empData.display_name || emp;

  var startVal = avail ? avail.start : '07:00';
  var endVal = avail ? avail.end : '21:00';

  var el = document.getElementById('avail-modal');
  el.innerHTML = '<div class="edit-panel">' +
    '<h3>' + esc(displayName) + '</h3>' +
    '<div style="font-size:13px;color:var(--muted);margin-bottom:16px;">' + esc(dow) + '</div>' +
    '<div class="edit-row"><label>Start</label><input type="time" id="ae-start" value="' + startVal + '"></div>' +
    '<div class="edit-row"><label>End</label><input type="time" id="ae-end" value="' + endVal + '"></div>' +
    '<div class="edit-btns" style="margin-bottom:8px;">' +
    '<button class="btn-cancel" onclick="closeAvailEditor()">Cancel</button>' +
    '<button class="btn-save" onclick="saveAvailEdit(\\'' + escA(emp) + '\\',\\'' + escA(dow) + '\\')">Save</button>' +
    '</div>' +
    (avail ? '<button onclick="setDayOff(\\'' + escA(emp) + '\\',\\'' + escA(dow) + '\\')" ' +
      'style="width:100%;padding:8px;border:1px solid #e86040;border-radius:8px;background:transparent;color:#e86040;font-size:13px;cursor:pointer;">Set to OFF</button>' : '') +
    '</div>';
  el.style.display = 'flex';
  el.addEventListener('click', function(e){ if(e.target===el) closeAvailEditor(); }, {once:true});
}

function closeAvailEditor(){
  document.getElementById('avail-modal').style.display = 'none';
}

async function saveAvailEdit(emp, dow){
  var start = document.getElementById('ae-start').value;
  var end = document.getElementById('ae-end').value;
  if (!start || !end) { showToast('Enter both times'); return; }

  var changes = {availability: {}};
  // Preserve existing availability, update this DOW
  var empData = (AVAIL_DATA.employees || {})[emp] || {};
  var existing = empData.availability || {};
  for (var d in existing) changes.availability[d] = existing[d];
  changes.availability[dow] = {start: start, end: end};

  try {
    var resp = await fetch('/api/availability', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({employee: emp, changes: changes})
    });
    if (!resp.ok){ var err = await resp.json(); showToast(err.error||'Save failed'); return; }
    // Update local data
    if (!AVAIL_DATA.employees[emp]) AVAIL_DATA.employees[emp] = {};
    if (!AVAIL_DATA.employees[emp].availability) AVAIL_DATA.employees[emp].availability = {};
    AVAIL_DATA.employees[emp].availability[dow] = {start: start, end: end};
    closeAvailEditor();
    renderMatrix();
    showToast('Saved');
  } catch(err){ showToast('Error: '+err.message); }
}

async function setDayOff(emp, dow){
  var changes = {availability: {}};
  var empData = (AVAIL_DATA.employees || {})[emp] || {};
  var existing = empData.availability || {};
  for (var d in existing) changes.availability[d] = existing[d];
  changes.availability[dow] = null;

  try {
    var resp = await fetch('/api/availability', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({employee: emp, changes: changes})
    });
    if (!resp.ok){ var err = await resp.json(); showToast(err.error||'Save failed'); return; }
    AVAIL_DATA.employees[emp].availability[dow] = null;
    closeAvailEditor();
    renderMatrix();
    showToast('Set to OFF');
  } catch(err){ showToast('Error: '+err.message); }
}

// ── Render matrix ──
function renderMatrix(){
  var emps = AVAIL_DATA.employees || {};
  var dows = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];

  // Group by department
  var groups = {};
  var order = [];
  for (var name in emps){
    var dept = (emps[name].department || 'other').toUpperCase();
    if (!groups[dept]){ groups[dept] = []; order.push(dept); }
    groups[dept].push(name);
  }

  var hdr = '<th>Employee</th><th>Max Hrs</th>';
  for (var i=0;i<dows.length;i++) hdr += '<th>'+dows[i]+'</th>';

  var rows = '';
  for (var gi=0;gi<order.length;gi++){
    var dept = order[gi];
    rows += '<tr class="dept-row"><td colspan="9">'+dept+'</td></tr>';
    var names = groups[dept];
    for (var ni=0;ni<names.length;ni++){
      var name = names[ni];
      var emp = emps[name];
      var display = esc(emp.display_name || name);
      var maxH = emp.max_hours_week || 40;
      var notes = emp.notes || '';

      rows += '<tr>';
      rows += '<td class="emp-name" title="'+escA(notes)+'">' + display;
      if (notes) rows += '<br><span class="emp-meta">'+esc(notes.substring(0,40))+'</span>';
      rows += '</td>';
      rows += '<td style="font-size:12px;">'+maxH+'</td>';

      for (var di=0;di<dows.length;di++){
        var dow = dows[di];
        var avail = (emp.availability||{})[dow];
        if (avail && avail.start){
          rows += '<td class="avail-on" onclick="openAvailEditor(\\''+escA(name)+'\\',\\''+escA(dow)+'\\')">';
          rows += '<span class="avail-time">'+fmtShort(avail.start)+'-'+fmtShort(avail.end)+'</span></td>';
        } else {
          rows += '<td class="avail-off" onclick="openAvailEditor(\\''+escA(name)+'\\',\\''+escA(dow)+'\\')">';
          rows += 'OFF</td>';
        }
      }
      rows += '</tr>';
    }
  }

  var html = '<div style="overflow-x:auto;-webkit-overflow-scrolling:touch;">';
  html += '<table class="avail-table"><thead><tr>'+hdr+'</tr></thead>';
  html += '<tbody>'+rows+'</tbody></table></div>';
  document.getElementById('matrix-container').innerHTML = html;
}

renderMatrix();
"""


def build_availability_page(avail_data: dict, logo_b64: str = "") -> str:
    """Build the employee availability matrix page."""
    import json

    logo_html = ""
    if logo_b64:
        logo_html = (
            f'<img src="data:image/png;base64,{logo_b64}" '
            'style="height:28px;margin-bottom:8px;" alt="Livite">'
        )

    sub_nav = _sub_nav_html("availability")
    avail_json = json.dumps(avail_data)
    js_code = _AVAIL_JS.replace("__AVAIL_JSON__", avail_json)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Livite - Employee Availability</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
{_CSS}
{_SCHEDULE_CSS}
{_AVAIL_CSS}
</style>
</head>
<body>

<div style="text-align:center;padding:24px 16px 8px;">
  {logo_html}
  <h1 style="font-size:22px;margin:8px 0 4px;">Employee Availability</h1>
  <div style="font-size:13px;color:var(--muted);margin-bottom:4px;">Tap any cell to edit</div>
  {sub_nav}
</div>

<div class="card">
  <div class="card-title">Availability Matrix</div>
  <div id="matrix-container"></div>
</div>

<!-- Edit modal -->
<div id="avail-modal" class="edit-overlay" style="display:none;"></div>

<!-- Toast -->
<div id="toast" class="toast-msg"></div>

<script>
{js_code}
</script>

</body>
</html>"""
