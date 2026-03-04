"""Employee Database page — client-side rendered with edit modals.

Serves a lightweight loading shell immediately; employee data is fetched
asynchronously via /api/employees/data and rendered client-side with:
- Editable employee cards/table
- Department and role management
- Availability grid editing
- Add/remove employees
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


def _sub_nav_html(active="employees"):
    """Navigation strip with active page highlighted."""
    links = [
        ("Home", "/"),
        ("Today", "/today"),
        ("This Week", "/week"),
        ("Forecast", "/forecast"),
        ("Schedule", "/schedule"),
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


# ── Employee-page CSS ──
_EMP_CSS = """
/* Nav pills */
.nav-pill{font-size:12px;padding:5px 12px;border-radius:6px;background:var(--bg);
  color:var(--text);text-decoration:none;font-weight:500;}
.nav-pill.active{background:""" + _PURPLE + """;color:#fff;font-weight:600;}

/* Stat grid */
.stat-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;}
@media(max-width:600px){.stat-grid{grid-template-columns:repeat(2,1fr);}}
.stat-card{background:var(--bg);border-radius:8px;padding:14px 8px;text-align:center;}
.stat-val{font-size:20px;font-weight:700;}
.stat-lbl{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.3px;margin-top:2px;}

/* Cards */
.card{background:var(--surface);border-radius:8px;padding:16px;margin:12px 16px;}
.card-title{font-size:14px;font-weight:600;margin-bottom:12px;}

/* Employee table */
.emp-table{width:100%;border-collapse:collapse;font-size:13px;}
.emp-table th,.emp-table td{padding:10px 8px;border-bottom:1px solid var(--border);}
.emp-table thead th{font-size:11px;font-weight:600;color:var(--muted);text-transform:uppercase;
  letter-spacing:.3px;text-align:left;}
.emp-table tbody tr{cursor:pointer;transition:background .12s;}
.emp-table tbody tr:hover{background:rgba(140,184,46,.06);}

/* Badges */
.dept-badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600;}
.dept-foh{background:rgba(140,184,46,.15);color:#4a7c1f;}
.dept-boh{background:rgba(74,156,216,.15);color:#1a6eb5;}
.dept-both{background:rgba(155,114,196,.15);color:#7c4daa;}
.role-chip{display:inline-block;padding:2px 6px;border-radius:4px;font-size:10px;
  background:var(--bg);color:var(--muted);margin:1px 2px;}

/* Edit modal */
.edit-overlay{position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.4);
  z-index:1100;display:flex;align-items:center;justify-content:center;overflow-y:auto;padding:20px 0;}
.edit-panel{background:var(--surface);border-radius:12px;padding:24px;width:440px;
  max-width:92vw;box-shadow:0 8px 32px rgba(0,0,0,.25);max-height:90vh;overflow-y:auto;}
.edit-panel h3{margin:0 0 16px;font-size:18px;}
.edit-field{margin-bottom:14px;}
.edit-field label{display:block;font-size:12px;font-weight:600;color:var(--muted);
  text-transform:uppercase;letter-spacing:.3px;margin-bottom:4px;}
.edit-field input,.edit-field select,.edit-field textarea{width:100%;box-sizing:border-box;
  font-size:14px;padding:8px 10px;border:1px solid var(--border);border-radius:6px;
  background:var(--bg);color:var(--text);font-family:inherit;}
.edit-field textarea{resize:vertical;min-height:50px;}

/* Role chips in editor */
.role-toggle{display:inline-block;padding:4px 10px;border-radius:12px;font-size:12px;
  margin:3px 2px;cursor:pointer;border:1px solid var(--border);transition:all .15s;user-select:none;}
.role-toggle.active{background:""" + _GREEN + """;color:#fff;border-color:""" + _GREEN + """;}
.role-toggle.inactive{background:var(--bg);color:var(--muted);}
.add-role-btn{display:inline-block;padding:4px 10px;border-radius:12px;font-size:12px;
  margin:3px 2px;cursor:pointer;border:1px dashed var(--border);color:var(--muted);
  background:transparent;transition:all .15s;}
.add-role-btn:hover{border-color:""" + _GREEN + """;color:""" + _GREEN + """;}

/* Availability grid in editor */
.avail-grid{display:grid;grid-template-columns:repeat(7,1fr);gap:4px;text-align:center;}
@media(max-width:500px){.avail-grid{grid-template-columns:repeat(4,1fr);}}
.avail-day{border:1px solid var(--border);border-radius:6px;padding:6px 2px;font-size:11px;}
.avail-day .dow{font-weight:600;margin-bottom:4px;}
.avail-day input[type=time]{width:100%;font-size:11px;padding:2px;border:1px solid var(--border);
  border-radius:4px;box-sizing:border-box;background:var(--bg);color:var(--text);}
.avail-day.off{background:#f0f0f0;opacity:.6;}
.avail-toggle{font-size:10px;cursor:pointer;color:""" + _GREEN + """;margin-top:4px;display:block;}

/* Buttons */
.btn-row{display:flex;gap:8px;margin-top:16px;}
.btn-row button{flex:1;padding:10px;border:none;border-radius:8px;font-size:14px;
  font-weight:600;cursor:pointer;}
.btn-save{background:""" + _GREEN + """;color:#fff;}
.btn-cancel{background:var(--bg);color:var(--text);}
.btn-danger{background:""" + _RED + """;color:#fff;}
.btn-add{background:""" + _GREEN + """;color:#fff;border:none;border-radius:8px;padding:8px 16px;
  font-size:13px;font-weight:600;cursor:pointer;}

/* Toast notification */
.toast-msg{position:fixed;bottom:24px;left:50%;transform:translateX(-50%);
  background:#333;color:#fff;padding:10px 20px;border-radius:8px;font-size:13px;
  z-index:1200;opacity:0;transition:opacity .3s;}
.toast-msg.show{opacity:1;}

/* Search */
.search-box{width:100%;box-sizing:border-box;font-size:14px;padding:8px 12px;
  border:1px solid var(--border);border-radius:6px;background:var(--bg);color:var(--text);
  margin-bottom:12px;}
"""


# ── JavaScript ──
_JS_CODE = """
'use strict';
let DATA = null;
let editingEmployee = null;

async function loadData(){
  try {
    var resp = await fetch('/api/employees/data');
    if (!resp.ok) throw new Error('Server returned ' + resp.status);
    DATA = await resp.json();
    document.getElementById('loading').style.display = 'none';
    document.getElementById('content').style.display = 'block';
    renderAll();
  } catch(err) {
    document.getElementById('loading').innerHTML =
      '<div style="text-align:center;padding:40px;color:#e86040;">Error: ' + esc(err.message) + '</div>';
  }
}

function renderAll(){
  renderStats();
  renderTable();
}

// ── Stats ──
function renderStats(){
  var emps = DATA.employees || [];
  var foh = emps.filter(function(e){return e.department==='FOH';}).length;
  var boh = emps.filter(function(e){return e.department==='BOH';}).length;
  var both = emps.filter(function(e){return e.department==='both';}).length;
  var wages = emps.filter(function(e){return e.wage>0;}).map(function(e){return e.wage;});
  var avg = wages.length ? wages.reduce(function(a,b){return a+b;},0)/wages.length : 0;
  var items = [
    {v:emps.length, l:'Total Staff'},
    {v:foh + ' / ' + boh + (both?' + '+both:''), l:'FOH / BOH'},
    {v:'$'+avg.toFixed(2), l:'Avg Wage'},
    {v:(DATA.role_options||[]).length, l:'Roles'}
  ];
  var h = '<div class="stat-grid">';
  for (var i=0;i<items.length;i++){
    h += '<div class="stat-card"><div class="stat-val">'+items[i].v+'</div><div class="stat-lbl">'+items[i].l+'</div></div>';
  }
  h += '</div>';
  document.getElementById('stats-container').innerHTML = h;
}

// ── Employee Table ──
function renderTable(){
  var emps = DATA.employees || [];
  var filter = (document.getElementById('search-input')||{}).value || '';
  filter = filter.toLowerCase();
  var filtered = emps;
  if (filter){
    filtered = emps.filter(function(e){
      return (e.display_name||'').toLowerCase().indexOf(filter) >= 0 ||
             (e.name||'').toLowerCase().indexOf(filter) >= 0 ||
             (e.department||'').toLowerCase().indexOf(filter) >= 0 ||
             (e.roles||[]).join(' ').toLowerCase().indexOf(filter) >= 0;
    });
  }

  var rows = '';
  for (var i=0;i<filtered.length;i++){
    var e = filtered[i];
    var dc = e.department==='FOH'?'dept-foh':e.department==='BOH'?'dept-boh':'dept-both';
    var roles = (e.roles||[]).map(function(r){return '<span class="role-chip">'+esc(r)+'</span>';}).join('');
    if (!roles) roles = '<span style="color:var(--muted);font-size:11px;">none</span>';
    var avail = compactAvail(e.availability||{});
    var wageStr = e.type==='salaried' ? '$'+((e.wage||0)*10).toFixed(0)+'/day' :
                  e.type==='owner' ? 'Owner' :
                  '$'+(e.wage||0).toFixed(2)+'/hr';
    rows += '<tr onclick="openEditor(\\''+escA(e.name)+'\\')">';
    rows += '<td style="font-weight:600;white-space:nowrap;">'+esc(e.display_name||e.name)+'</td>';
    rows += '<td><span class="dept-badge '+dc+'">'+esc(e.department||'?')+'</span></td>';
    rows += '<td>'+roles+'</td>';
    rows += '<td>'+wageStr+'</td>';
    rows += '<td style="text-align:center;">'+e.max_hours_week+'</td>';
    rows += '<td style="font-size:11px;color:var(--muted);">'+esc(avail)+'</td>';
    rows += '</tr>';
  }

  var h = '<input type="text" id="search-input" class="search-box" placeholder="Search employees..." oninput="renderTable()"';
  h += ' value="'+escA(filter)+'">';
  h += '<div style="overflow-x:auto;"><table class="emp-table">';
  h += '<thead><tr><th>Employee</th><th>Dept</th><th>Roles</th><th>Wage</th><th>Max Hrs</th><th>Availability</th></tr></thead>';
  h += '<tbody>'+rows+'</tbody></table></div>';
  if (!filtered.length){
    h += '<div style="text-align:center;padding:20px;color:var(--muted);">No employees match the search.</div>';
  }
  document.getElementById('table-container').innerHTML = h;
  // Restore focus to search if it was active
  var si = document.getElementById('search-input');
  if (si && filter) { si.focus(); si.selectionStart = si.selectionEnd = filter.length; }
}

function compactAvail(avail){
  var days = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
  var on = [];
  for (var i=0;i<days.length;i++){
    if (avail[days[i]] !== null && avail[days[i]] !== undefined) on.push(days[i]);
  }
  if (on.length === 7) return 'Every day';
  if (on.length === 0) return 'None';
  // Check for contiguous range
  var first = days.indexOf(on[0]);
  var last = days.indexOf(on[on.length-1]);
  if (last - first + 1 === on.length && on.length >= 3) return on[0]+'-'+on[on.length-1];
  return on.join(', ');
}

// ── Editor Modal ──
function openEditor(empName){
  editingEmployee = empName;
  var emp = null;
  var emps = DATA.employees || [];
  for (var i=0;i<emps.length;i++){
    if (emps[i].name === empName) { emp = emps[i]; break; }
  }
  if (!emp) return;
  var isNew = false;
  renderEditorContent(emp, isNew);
}

function openAddEmployee(){
  editingEmployee = null;
  var emp = {
    name: '', display_name: '', department: 'FOH', roles: [],
    wage: 16.00, type: 'hourly', max_hours_week: 40,
    availability: {Mon:null, Tue:null, Wed:null, Thu:null, Fri:null, Sat:null, Sun:null},
    notes: ''
  };
  renderEditorContent(emp, true);
}

function renderEditorContent(emp, isNew){
  var el = document.getElementById('edit-modal');
  var title = isNew ? 'Add Employee' : esc(emp.display_name || emp.name);
  var roles = DATA.role_options || [];
  var empRoles = emp.roles || [];
  var days = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];

  var h = '<div class="edit-panel">';
  h += '<h3>' + title + '</h3>';

  // Name fields (only for new)
  if (isNew){
    h += '<div class="edit-field"><label>Name (last, first)</label>';
    h += '<input type="text" id="ed-name" placeholder="smith, jane" value=""></div>';
  }
  h += '<div class="edit-field"><label>Display Name</label>';
  h += '<input type="text" id="ed-display" value="'+escA(emp.display_name||'')+'"></div>';

  // Department
  h += '<div class="edit-field"><label>Department</label>';
  h += '<select id="ed-dept">';
  var depts = ['FOH','BOH','both'];
  for (var i=0;i<depts.length;i++){
    var sel = emp.department === depts[i] ? ' selected' : '';
    h += '<option value="'+depts[i]+'"'+sel+'>'+depts[i]+'</option>';
  }
  h += '</select></div>';

  // Roles
  h += '<div class="edit-field"><label>Roles <span id="manage-roles-btn" style="font-size:10px;cursor:pointer;color:' + '#4a9cd8' + ';text-transform:none;letter-spacing:0;font-weight:400;" onclick="toggleManageRoles()">[manage]</span></label><div id="ed-roles">';
  for (var i=0;i<roles.length;i++){
    var active = empRoles.indexOf(roles[i]) >= 0;
    var cls = active ? 'role-toggle active' : 'role-toggle inactive';
    h += '<span class="'+cls+'" data-role="'+escA(roles[i])+'" onclick="toggleRole(this)">'+esc(roles[i])+'<span class="role-x" style="display:none;margin-left:4px;color:#e86040;font-weight:700;" onclick="event.stopPropagation();deleteRole(\\''+escA(roles[i])+'\\')">x</span></span>';
  }
  h += '<span class="add-role-btn" onclick="promptNewRole()">+ Add</span>';
  h += '</div></div>';

  // Wage + Max Hours (side by side)
  h += '<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">';
  h += '<div class="edit-field"><label>Wage ($/hr)</label>';
  h += '<input type="number" id="ed-wage" step="0.25" min="0" value="'+(emp.wage||0).toFixed(2)+'"></div>';
  h += '<div class="edit-field"><label>Max Hours/Week</label>';
  h += '<input type="number" id="ed-maxhrs" step="1" min="0" max="80" value="'+(emp.max_hours_week||40)+'"></div>';
  h += '</div>';

  // Employee type
  h += '<div class="edit-field"><label>Type</label>';
  h += '<select id="ed-type">';
  var types = ['hourly','manager','salaried','owner'];
  for (var i=0;i<types.length;i++){
    var sel = emp.type === types[i] ? ' selected' : '';
    h += '<option value="'+types[i]+'"'+sel+'>'+types[i]+'</option>';
  }
  h += '</select></div>';

  // Availability grid
  h += '<div class="edit-field"><label>Availability</label><div class="avail-grid">';
  for (var i=0;i<days.length;i++){
    var d = days[i];
    var da = (emp.availability||{})[d];
    var isOff = da === null || da === undefined;
    h += '<div class="avail-day'+(isOff?' off':'')+'" id="avail-'+d+'">';
    h += '<div class="dow">'+d+'</div>';
    h += '<input type="time" id="avail-start-'+d+'" value="'+(isOff?'07:00':da.start||'07:00')+'"'+(isOff?' disabled':'')+'>';
    h += '<input type="time" id="avail-end-'+d+'" value="'+(isOff?'21:00':da.end||'21:00')+'"'+(isOff?' disabled':'')+'>';
    h += '<span class="avail-toggle" onclick="toggleDay(\\''+d+'\\')">'+(isOff?'Turn On':'Turn Off')+'</span>';
    h += '</div>';
  }
  h += '</div></div>';

  // Notes
  h += '<div class="edit-field"><label>Notes</label>';
  h += '<textarea id="ed-notes">'+esc(emp.notes||'')+'</textarea></div>';

  // Buttons
  h += '<div class="btn-row">';
  h += '<button class="btn-cancel" onclick="closeEditor()">Cancel</button>';
  if (!isNew){
    h += '<button class="btn-danger" onclick="confirmRemove(\\''+escA(emp.name)+'\\')">Remove</button>';
  }
  h += '<button class="btn-save" onclick="saveEmployee('+(isNew?'true':'false')+')">Save</button>';
  h += '</div></div>';

  el.innerHTML = h;
  el.style.display = 'flex';
  el.addEventListener('click', function(e){ if(e.target===el) closeEditor(); }, {once:true});
}

function toggleRole(el){
  if (el.classList.contains('active')){
    el.classList.remove('active');
    el.classList.add('inactive');
  } else {
    el.classList.remove('inactive');
    el.classList.add('active');
  }
}

var manageMode = false;
function toggleManageRoles(){
  manageMode = !manageMode;
  var xs = document.querySelectorAll('#ed-roles .role-x');
  for (var i=0;i<xs.length;i++) xs[i].style.display = manageMode ? 'inline' : 'none';
  var btn = document.getElementById('manage-roles-btn');
  if (btn) btn.textContent = manageMode ? '[done]' : '[manage]';
}

function promptNewRole(){
  var role = prompt('Enter new role name:');
  if (!role || !role.trim()) return;
  role = role.trim().toLowerCase();
  fetch('/api/employees/add-role', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({role: role})
  }).then(function(r){ return r.json(); }).then(function(){
    if ((DATA.role_options||[]).indexOf(role) < 0){
      DATA.role_options.push(role);
    }
    var container = document.getElementById('ed-roles');
    var btn = container.querySelector('.add-role-btn');
    var chip = document.createElement('span');
    chip.className = 'role-toggle active';
    chip.dataset.role = role;
    chip.innerHTML = role + '<span class="role-x" style="display:' + (manageMode?'inline':'none') + ';margin-left:4px;color:#e86040;font-weight:700;" onclick="event.stopPropagation();deleteRole(\\'' + escA(role) + '\\')">x</span>';
    chip.onclick = function(){ toggleRole(chip); };
    container.insertBefore(chip, btn);
    showToast('Role "'+role+'" added');
  });
}

async function deleteRole(role){
  if (!confirm('Delete role "'+role+'"? It will be removed from all employees.')) return;
  try {
    var resp = await fetch('/api/employees/delete-role', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({role: role})
    });
    if (!resp.ok){
      var err = await resp.json();
      showToast(err.error || 'Delete failed');
      return;
    }
    // Remove from local data
    var idx = (DATA.role_options||[]).indexOf(role);
    if (idx >= 0) DATA.role_options.splice(idx, 1);
    // Remove from all employees locally
    var emps = DATA.employees || [];
    for (var i=0;i<emps.length;i++){
      var ri = (emps[i].roles||[]).indexOf(role);
      if (ri >= 0) emps[i].roles.splice(ri, 1);
    }
    // Remove chip from editor
    var chips = document.querySelectorAll('#ed-roles .role-toggle');
    for (var i=0;i<chips.length;i++){
      if (chips[i].dataset.role === role) chips[i].remove();
    }
    showToast('Role "'+role+'" deleted');
  } catch(err){
    showToast('Error: ' + err.message);
  }
}

function toggleDay(dow){
  var cell = document.getElementById('avail-'+dow);
  var startEl = document.getElementById('avail-start-'+dow);
  var endEl = document.getElementById('avail-end-'+dow);
  var toggle = cell.querySelector('.avail-toggle');
  if (cell.classList.contains('off')){
    cell.classList.remove('off');
    startEl.disabled = false;
    endEl.disabled = false;
    toggle.textContent = 'Turn Off';
  } else {
    cell.classList.add('off');
    startEl.disabled = true;
    endEl.disabled = true;
    toggle.textContent = 'Turn On';
  }
}

function closeEditor(){
  document.getElementById('edit-modal').style.display = 'none';
  editingEmployee = null;
}

async function saveEmployee(isNew){
  var days = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
  var name = isNew ? (document.getElementById('ed-name')||{}).value||'' : editingEmployee;
  if (!name || !name.trim()){
    showToast('Name is required');
    return;
  }
  name = name.trim().toLowerCase();

  var display = (document.getElementById('ed-display')||{}).value || '';
  var dept = (document.getElementById('ed-dept')||{}).value || 'FOH';
  var wage = parseFloat((document.getElementById('ed-wage')||{}).value) || 0;
  var maxHrs = parseInt((document.getElementById('ed-maxhrs')||{}).value) || 40;
  var type = (document.getElementById('ed-type')||{}).value || 'hourly';
  var notes = (document.getElementById('ed-notes')||{}).value || '';

  // Collect roles
  var roleEls = document.querySelectorAll('#ed-roles .role-toggle.active');
  var roles = [];
  for (var i=0;i<roleEls.length;i++) roles.push(roleEls[i].dataset.role);

  // Collect availability
  var availability = {};
  for (var i=0;i<days.length;i++){
    var d = days[i];
    var cell = document.getElementById('avail-'+d);
    if (cell.classList.contains('off')){
      availability[d] = null;
    } else {
      availability[d] = {
        start: document.getElementById('avail-start-'+d).value || '07:00',
        end: document.getElementById('avail-end-'+d).value || '21:00'
      };
    }
  }

  var url = isNew ? '/api/employees/add' : '/api/employees/update';
  var body = isNew ? {
    name: name, display_name: display, department: dept,
    roles: roles, wage: wage, type: type, max_hours_week: maxHrs,
    availability: availability, notes: notes
  } : {
    employee: name,
    changes: {
      display_name: display, department: dept, roles: roles,
      wage: wage, type: type, max_hours_week: maxHrs,
      availability: availability, notes: notes
    }
  };

  try {
    var resp = await fetch(url, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(body)
    });
    if (!resp.ok){
      var err = await resp.json();
      showToast(err.error || 'Save failed');
      return;
    }
    closeEditor();
    showToast(isNew ? 'Employee added' : 'Employee updated');
    // Reload data
    await loadData();
  } catch(err){
    showToast('Error: ' + err.message);
  }
}

async function confirmRemove(empName){
  if (!confirm('Remove '+empName+' from the roster? This cannot be undone.')) return;
  try {
    var resp = await fetch('/api/employees/remove', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({employee: empName})
    });
    if (!resp.ok){
      var err = await resp.json();
      showToast(err.error || 'Remove failed');
      return;
    }
    closeEditor();
    showToast('Employee removed');
    await loadData();
  } catch(err){
    showToast('Error: ' + err.message);
  }
}

// ── Toast ──
function showToast(msg){
  var el = document.getElementById('toast');
  el.textContent = msg;
  el.classList.add('show');
  setTimeout(function(){ el.classList.remove('show'); }, 2500);
}

// ── Utilities ──
function esc(s){
  var d = document.createElement('div'); d.textContent = s||''; return d.innerHTML;
}
function escA(s){
  return (s||'').replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/'/g,'&#39;');
}

// Start
loadData();
"""


# ── Page Builder ──

def build_employees_page(logo_b64: str = "") -> str:
    """Build the employee database page shell."""
    logo_html = ""
    if logo_b64:
        logo_html = (
            f'<img src="data:image/png;base64,{logo_b64}" '
            'style="height:28px;margin-bottom:8px;" alt="Livite">'
        )

    sub_nav = _sub_nav_html("employees")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Livite - Employee Database</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
{_CSS}
{_EMP_CSS}
</style>
</head>
<body>

<!-- Header -->
<div style="text-align:center;padding:24px 16px 8px;">
  {logo_html}
  <h1 style="font-size:22px;margin:8px 0 4px;">Employee Database</h1>
  <div style="font-size:13px;color:var(--muted);">Manage staff, departments, roles, and availability</div>
  {sub_nav}
</div>

<!-- Loading -->
<div id="loading">
  <div style="text-align:center;padding:40px;color:var(--muted);">Loading employees...</div>
</div>

<!-- Content -->
<div id="content" style="display:none;">
  <div class="card">
    <div id="stats-container"></div>
  </div>
  <div class="card">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
      <div class="card-title" style="margin-bottom:0;">Staff Roster</div>
      <button class="btn-add" onclick="openAddEmployee()">+ Add Employee</button>
    </div>
    <div id="table-container"></div>
  </div>
</div>

<!-- Edit Modal -->
<div id="edit-modal" class="edit-overlay" style="display:none;"></div>

<!-- Toast -->
<div id="toast" class="toast-msg"></div>

<script>
{_JS_CODE}
</script>

</body>
</html>"""
