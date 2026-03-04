"""Recipe costing page — view, add, edit recipes with auto-calculated costs.

Lightweight shell with async JS data fetch, same pattern as scheduling pages.
"""
from __future__ import annotations

import os
import sys

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_BASE_DIR, "tools"))

from htmlrender.sections import _CSS

_RECIPE_CSS = """
/* Recipe-specific */
.recipe-nav{display:flex;justify-content:center;gap:12px;margin:12px 0 4px;flex-wrap:wrap;}
.recipe-nav a{font-size:13px;color:var(--muted);text-decoration:none;padding:4px 12px;
  border-radius:16px;border:1px solid var(--border);}
.recipe-nav a:hover{background:var(--border);}
.recipe-nav a.active{background:var(--text);color:#fff;border-color:var(--text);}
.recipe-table{width:100%;border-collapse:collapse;font-size:13px;}
.recipe-table th,.recipe-table td{padding:8px 6px;border-bottom:1px solid var(--border);}
.recipe-table thead th{font-size:11px;font-weight:600;color:var(--muted);text-transform:uppercase;
  letter-spacing:.3px;text-align:center;}
.recipe-table thead th:first-child{text-align:left;}
.recipe-table tbody td{text-align:center;}
.recipe-table tbody td:first-child{text-align:left;font-weight:600;}
.fc-green{color:#16a34a;font-weight:600;}
.fc-yellow{color:#ca8a04;font-weight:600;}
.fc-red{color:#dc2626;font-weight:600;}
.badge{display:inline-block;font-size:10px;padding:2px 8px;border-radius:10px;font-weight:600;
  text-transform:uppercase;letter-spacing:.3px;}
.badge-menu{background:#e8f5e9;color:#2e7d32;}
.badge-prep{background:#e3f2fd;color:#1565c0;}
.add-btn{background:#475417;color:#fff;border:none;padding:8px 16px;border-radius:6px;
  font-size:13px;font-weight:600;cursor:pointer;}
.add-btn:hover{background:#3a4512;}
.modal-overlay{position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.5);
  display:none;z-index:1000;justify-content:center;align-items:flex-start;padding:40px 16px;}
.modal{background:#fff;border-radius:12px;max-width:700px;width:100%;max-height:85vh;
  overflow-y:auto;padding:24px;}
.modal h2{font-size:18px;margin:0 0 16px;}
.form-group{margin-bottom:12px;}
.form-group label{display:block;font-size:12px;font-weight:600;color:var(--muted);
  margin-bottom:4px;text-transform:uppercase;letter-spacing:.3px;}
.form-group input,.form-group select{width:100%;padding:8px 10px;border:1px solid var(--border);
  border-radius:6px;font-size:14px;font-family:inherit;background:var(--bg);}
.form-group input:focus,.form-group select:focus{outline:none;border-color:#475417;}
.ing-row{display:flex;gap:6px;align-items:center;margin-bottom:8px;flex-wrap:wrap;
  padding:8px;border-radius:6px;background:var(--bg);border:1px solid transparent;transition:border-color .2s;}
.ing-row:hover{border-color:var(--border);}
.ing-row input{flex:1;min-width:60px;padding:6px 8px;border:1px solid var(--border);
  border-radius:4px;font-size:13px;background:#fff;}
.ing-row input.ing-name{flex:3;min-width:140px;}
.ing-row input.ing-qty{flex:0 0 60px;}
.ing-row input.ing-uom{flex:0 0 60px;}
.ing-row input.ing-yield{flex:0 0 60px;}
.ing-remove{background:none;border:none;color:#dc2626;cursor:pointer;font-size:16px;padding:4px;}
.ing-match{width:8px;height:8px;border-radius:50%;flex-shrink:0;}
.ing-match.matched{background:#16a34a;}
.ing-match.unmatched{background:#dc2626;}
.ing-match.unknown{background:#d1d5db;}
.ing-line-cost{font-size:11px;color:var(--muted);min-width:55px;text-align:right;font-weight:600;}
.ing-line-cost.has-cost{color:#16a34a;}
.cost-summary{background:var(--bg);border-radius:8px;padding:12px;margin-top:12px;
  display:flex;gap:16px;flex-wrap:wrap;justify-content:center;font-size:13px;}
.cost-summary .cost-item{text-align:center;}
.cost-summary .cost-val{font-size:18px;font-weight:700;color:var(--text);}
.cost-summary .cost-lbl{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.3px;}
.btn-row{display:flex;gap:8px;margin-top:16px;}
.btn-save{background:#475417;color:#fff;border:none;padding:8px 20px;border-radius:6px;
  font-size:14px;font-weight:600;cursor:pointer;}
.btn-save:hover{background:#3a4512;}
.btn-cancel{background:var(--bg);color:var(--text);border:1px solid var(--border);padding:8px 20px;
  border-radius:6px;font-size:14px;cursor:pointer;}
.btn-cancel:hover{background:var(--border);}
.btn-delete{background:#dc2626;color:#fff;border:none;padding:8px 16px;border-radius:6px;
  font-size:13px;cursor:pointer;margin-left:auto;}
.btn-delete:hover{background:#b91c1c;}
.detail-card{background:var(--bg);border-radius:8px;padding:12px;margin-top:8px;}
.detail-table{width:100%;border-collapse:collapse;font-size:12px;}
.detail-table th,.detail-table td{padding:4px 6px;text-align:center;}
.detail-table th{font-size:10px;color:var(--muted);text-transform:uppercase;border-bottom:1px solid var(--border);}
.detail-table td:first-child,.detail-table th:first-child{text-align:left;}
.unmatched{color:#dc2626;font-style:italic;}
.autocomplete-list{position:absolute;background:#fff;border:1px solid var(--border);border-radius:6px;
  max-height:220px;overflow-y:auto;z-index:100;box-shadow:0 4px 12px rgba(0,0,0,0.15);font-size:13px;width:100%;}
.autocomplete-list .ac-item{padding:8px 10px;cursor:pointer;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #f0f0f0;}
.autocomplete-list .ac-item:last-child{border-bottom:none;}
.autocomplete-list .ac-item:hover{background:#f5f9f0;}
.autocomplete-list .ac-item.ac-selected{background:#f5f9f0;}
.ac-name{font-weight:500;}
.ac-meta{font-size:11px;color:var(--muted);display:flex;gap:8px;align-items:center;}
.ac-price{color:#16a34a;font-weight:600;}
.ac-vendor{color:#6b7280;}
.ac-unit{color:#8b5cf6;}
.ac-new{font-size:11px;color:#1a5276;font-style:italic;}
.stat-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:12px;}
.stat-card{text-align:center;padding:8px;}
.stat-val{font-size:22px;font-weight:700;color:var(--text);}
.stat-lbl{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.3px;margin-top:2px;}
/* Page tabs */
.page-tabs{display:flex;gap:0;border-bottom:2px solid var(--border);margin:12px 16px 0;}
.page-tab{padding:10px 20px;font-size:14px;font-weight:600;cursor:pointer;border:none;background:none;
  color:var(--muted);border-bottom:2px solid transparent;margin-bottom:-2px;transition:all .2s;}
.page-tab:hover{color:var(--text);}
.page-tab.active{color:#475417;border-bottom-color:#475417;}
/* Coverage tracker */
.cov-summary{display:flex;gap:16px;flex-wrap:wrap;justify-content:center;padding:16px;background:var(--bg);border-radius:8px;margin-bottom:16px;}
.cov-stat{text-align:center;min-width:100px;}
.cov-stat .cov-val{font-size:24px;font-weight:700;}
.cov-stat .cov-lbl{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.3px;}
.cov-progress{height:8px;background:#e5e7eb;border-radius:4px;overflow:hidden;margin:4px 0;}
.cov-progress-bar{height:100%;border-radius:4px;transition:width .5s;}
.cov-cat-card{border:1px solid var(--border);border-radius:8px;margin-bottom:12px;overflow:hidden;}
.cov-cat-header{display:flex;justify-content:space-between;align-items:center;padding:10px 14px;
  background:var(--bg);cursor:pointer;user-select:none;}
.cov-cat-header:hover{background:#eee8d5;}
.cov-cat-name{font-size:14px;font-weight:700;}
.cov-cat-stats{font-size:12px;color:var(--muted);display:flex;gap:12px;align-items:center;}
.cov-cat-badge{font-size:11px;font-weight:600;padding:2px 8px;border-radius:10px;}
.cov-cat-badge.full{background:#dcfce7;color:#16a34a;}
.cov-cat-badge.partial{background:#fef9c3;color:#a16207;}
.cov-cat-badge.none{background:#fee2e2;color:#dc2626;}
.cov-item-table{width:100%;border-collapse:collapse;font-size:13px;}
.cov-item-table th{font-size:10px;font-weight:600;color:var(--muted);text-transform:uppercase;
  letter-spacing:.3px;padding:6px 10px;text-align:left;border-bottom:1px solid var(--border);}
.cov-item-table th:not(:first-child){text-align:right;}
.cov-item-table td{padding:8px 10px;border-bottom:1px solid #f5f5f0;}
.cov-item-table td:not(:first-child){text-align:right;}
.cov-item-table tr:last-child td{border-bottom:none;}
.cov-item-table tr.has-recipe td:first-child{color:#16a34a;}
.cov-item-table tr.needs-recipe td:first-child{color:#dc2626;}
.cov-status-dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;}
.cov-status-dot.green{background:#16a34a;}
.cov-status-dot.red{background:#dc2626;}
.cov-add-btn{font-size:11px;padding:3px 10px;background:#475417;color:#fff;border:none;
  border-radius:4px;cursor:pointer;font-weight:600;}
.cov-add-btn:hover{background:#3a4512;}
/* Modifiers tab */
.mod-group{border:1px solid var(--border);border-radius:8px;margin-bottom:12px;overflow:hidden;}
.mod-group-header{display:flex;justify-content:space-between;align-items:center;padding:10px 14px;
  background:var(--bg);cursor:pointer;user-select:none;}
.mod-group-header:hover{background:#eee8d5;}
.mod-group-name{font-size:14px;font-weight:700;}
.mod-group-count{font-size:12px;color:var(--muted);}
.mod-table{width:100%;border-collapse:collapse;font-size:13px;}
.mod-table th{font-size:10px;font-weight:600;color:var(--muted);text-transform:uppercase;
  letter-spacing:.3px;padding:6px 10px;text-align:left;border-bottom:1px solid var(--border);}
.mod-table th:not(:first-child){text-align:right;}
.mod-table td{padding:8px 10px;border-bottom:1px solid #f5f5f0;}
.mod-table td:not(:first-child){text-align:right;}
.mod-table tr:last-child td{border-bottom:none;}
.mod-type-badge{display:inline-block;font-size:10px;padding:2px 8px;border-radius:10px;font-weight:600;
  text-transform:uppercase;letter-spacing:.3px;}
.mod-type-badge.add-on{background:#e8f5e9;color:#2e7d32;}
.mod-type-badge.sub{background:#e3f2fd;color:#1565c0;}
.mod-type-badge.remove{background:#fce4ec;color:#c62828;}
.mod-edit-btn{font-size:11px;padding:3px 10px;background:var(--bg);color:var(--text);border:1px solid var(--border);
  border-radius:4px;cursor:pointer;font-weight:500;}
.mod-edit-btn:hover{background:var(--border);}
.mod-summary{display:flex;gap:16px;flex-wrap:wrap;justify-content:center;padding:16px;background:var(--bg);
  border-radius:8px;margin-bottom:16px;}
.mod-stat{text-align:center;min-width:90px;}
.mod-stat .mod-val{font-size:22px;font-weight:700;}
.mod-stat .mod-lbl{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.3px;}
/* Recipe detail panel (Meez-inspired) */
.detail-panel{background:#fff;border:1px solid var(--border);border-radius:0 0 8px 8px;margin-top:-1px;}
.detail-tabs{display:flex;gap:0;border-bottom:1px solid var(--border);background:var(--bg);}
.detail-tab{padding:8px 16px;font-size:12px;font-weight:600;cursor:pointer;border:none;background:none;
  color:var(--muted);border-bottom:2px solid transparent;margin-bottom:-1px;transition:all .2s;text-transform:uppercase;letter-spacing:.3px;}
.detail-tab:hover{color:var(--text);}
.detail-tab.active{color:#475417;border-bottom-color:#475417;background:#fff;}
.detail-tab-body{padding:16px;display:none;}
.detail-tab-body.active{display:block;}
/* Prep method */
.prep-info-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:12px;margin-bottom:16px;}
.prep-info-item{background:var(--bg);border-radius:6px;padding:8px 12px;}
.prep-info-label{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.3px;margin-bottom:2px;}
.prep-info-value{font-size:14px;font-weight:600;}
.step-list{list-style:none;padding:0;counter-reset:step;}
.step-item{display:flex;gap:12px;margin-bottom:14px;align-items:flex-start;}
.step-num{font-size:14px;font-weight:700;color:#475417;min-width:28px;height:28px;background:#e8f5e9;
  border-radius:50%;display:flex;align-items:center;justify-content:center;flex-shrink:0;}
.step-text{font-size:14px;line-height:1.6;padding-top:3px;}
/* Batch scaler */
.batch-scaler{display:flex;align-items:center;gap:8px;margin-bottom:16px;padding:10px 14px;background:var(--bg);border-radius:8px;}
.batch-scaler label{font-size:12px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.3px;}
.batch-scaler input{width:60px;padding:4px 8px;border:1px solid var(--border);border-radius:4px;font-size:14px;
  text-align:center;font-weight:600;}
.batch-quick{padding:4px 10px;border:1px solid var(--border);border-radius:4px;font-size:12px;cursor:pointer;
  background:#fff;font-weight:600;transition:all .15s;}
.batch-quick:hover,.batch-quick.active{background:#475417;color:#fff;border-color:#475417;}
/* Allergen badges */
.allergen-badges{display:flex;gap:4px;flex-wrap:wrap;}
.allergen-badge{font-size:9px;padding:2px 6px;border-radius:8px;font-weight:600;text-transform:uppercase;
  letter-spacing:.2px;background:#fff3cd;color:#856404;border:1px solid #ffc107;}
.allergen-badge.dairy{background:#e3f2fd;color:#0d47a1;border-color:#90caf9;}
.allergen-badge.eggs{background:#fff8e1;color:#e65100;border-color:#ffcc02;}
.allergen-badge.fish{background:#e0f7fa;color:#006064;border-color:#80deea;}
.allergen-badge.shellfish{background:#fce4ec;color:#880e4f;border-color:#f48fb1;}
.allergen-badge.tree_nuts{background:#efebe9;color:#4e342e;border-color:#bcaaa4;}
.allergen-badge.peanuts{background:#fff3e0;color:#e65100;border-color:#ffb74d;}
.allergen-badge.wheat{background:#f3e5f5;color:#6a1b9a;border-color:#ce93d8;}
.allergen-badge.soy{background:#e8f5e9;color:#1b5e20;border-color:#81c784;}
.allergen-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:8px;margin-top:8px;}
.allergen-check{display:flex;align-items:center;gap:6px;padding:6px 10px;border:1px solid var(--border);
  border-radius:6px;cursor:pointer;font-size:13px;transition:all .15s;}
.allergen-check:hover{border-color:#475417;}
.allergen-check.checked{background:#fff3cd;border-color:#ffc107;}
.allergen-check input{display:none;}
/* Book filter pills */
.book-filter{display:flex;gap:6px;flex-wrap:wrap;margin:12px 0 4px;justify-content:center;}
.book-pill{padding:4px 12px;border-radius:14px;font-size:12px;font-weight:600;cursor:pointer;
  border:1px solid var(--border);background:#fff;color:var(--muted);transition:all .15s;}
.book-pill:hover{border-color:#475417;color:var(--text);}
.book-pill.active{background:#475417;color:#fff;border-color:#475417;}
.book-badge{font-size:10px;padding:2px 8px;border-radius:10px;font-weight:500;background:#f0f0f0;color:var(--muted);}
/* Sub-recipe indicator */
.sub-recipe-indicator{display:inline-flex;align-items:center;gap:4px;color:#1565c0;cursor:pointer;}
.sub-recipe-indicator:hover{text-decoration:underline;}
.sub-recipe-icon{font-size:11px;}
"""

_JS_TEMPLATE = r"""'use strict';
var RECIPES = [];
var ITEMS = [];
var ITEMS_MAP = {};  // lowercase name -> item with price data
var EDITING = null;
var AC_INDEX = -1;  // keyboard nav index for autocomplete
var ACTIVE_BOOK = 'all';
var TOAST_ITEMS = [];  // Toast POS menu items for linking
var ALLERGEN_LIST = [
  {id:'dairy',label:'Dairy'},{id:'eggs',label:'Eggs'},{id:'fish',label:'Fish'},
  {id:'shellfish',label:'Shellfish'},{id:'tree_nuts',label:'Tree Nuts'},
  {id:'peanuts',label:'Peanuts'},{id:'wheat',label:'Wheat/Gluten'},{id:'soy',label:'Soy'}
];

async function loadData(){
  var el = document.getElementById('loading-text');
  el.textContent = 'Loading recipes and prices...';
  try {
    var [rResp, iResp, tResp] = await Promise.all([
      fetch('/api/recipes/data'),
      fetch('/api/recipes/ingredients'),
      fetch('/api/recipes/toast-items')
    ]);
    if (!rResp.ok) throw new Error('Failed to load recipes');
    if (!iResp.ok) throw new Error('Failed to load ingredients');
    RECIPES = await rResp.json();
    ITEMS = await iResp.json();
    if (tResp.ok) TOAST_ITEMS = await tResp.json();
    // Build lookup map for instant price matching
    ITEMS_MAP = {};
    for (var i=0;i<ITEMS.length;i++){
      ITEMS_MAP[ITEMS[i].name.toLowerCase()] = ITEMS[i];
    }
    document.getElementById('loading').style.display = 'none';
    document.getElementById('content').style.display = 'block';
    renderAll();
  } catch(err){
    el.textContent = 'Error: ' + err.message;
    el.style.color = '#dc2626';
  }
}

function renderAll(){
  renderOverview();
  renderBookFilter();
  renderTable();
}

function renderOverview(){
  var menu = RECIPES.filter(function(r){return r.type==='menu';});
  var prep = RECIPES.filter(function(r){return r.type==='prep';});
  var avgFc = 0;
  if (menu.length){
    var total = 0;
    for (var i=0;i<menu.length;i++) total += (menu[i].cost||{}).food_cost_pct||0;
    avgFc = (total/menu.length).toFixed(1);
  }
  var matched = 0, unmatched = 0;
  for (var i=0;i<RECIPES.length;i++){
    var ings = (RECIPES[i].cost||{}).ingredients||[];
    for (var j=0;j<ings.length;j++){
      if (ings[j].matched) matched++; else unmatched++;
    }
  }

  var linked = menu.filter(function(r){return r.toast_menu_name;}).length;

  var h = '<div class="stat-grid">';
  h += '<div class="stat-card"><div class="stat-val">'+RECIPES.length+'</div><div class="stat-lbl">Total Recipes</div></div>';
  h += '<div class="stat-card"><div class="stat-val">'+menu.length+'</div><div class="stat-lbl">Menu Items</div></div>';
  h += '<div class="stat-card"><div class="stat-val">'+linked+'</div><div class="stat-lbl">Toast-Linked</div></div>';
  h += '<div class="stat-card"><div class="stat-val">'+avgFc+'%</div><div class="stat-lbl">Avg Food Cost</div></div>';
  h += '</div>';

  // Ingredient match
  h += '<div style="margin-top:4px;text-align:center;font-size:13px;">';
  h += '<span style="color:var(--muted);">Ingredients: </span>';
  h += '<span style="color:#16a34a;font-weight:600;">'+matched+' priced</span>';
  if (unmatched > 0) h += ' <span style="color:#dc2626;font-weight:600;">'+unmatched+' unpriced</span>';
  h += '</div>';

  // Coverage + ingredient report buttons
  h += '<div style="margin-top:12px;text-align:center;display:flex;gap:8px;justify-content:center;flex-wrap:wrap;">';
  h += '<button class="add-btn" style="font-size:11px;padding:6px 14px;background:#1a5276;" onclick="loadCoverage()">Check Sales Coverage</button>';
  h += '<button class="add-btn" style="font-size:11px;padding:6px 14px;background:#7c3aed;" onclick="loadIngredientReport()">Ingredient Match Report</button>';
  h += '</div>';
  h += '<div id="coverage-result" style="margin-top:8px;font-size:13px;color:var(--muted);"></div>';
  h += '<div id="ingredient-report" style="margin-top:8px;"></div>';

  document.getElementById('overview').innerHTML = h;
}

function renderBookFilter(){
  var books = {};
  for (var i=0;i<RECIPES.length;i++){
    var b = RECIPES[i].book || '';
    if (b) books[b] = (books[b]||0) + 1;
  }
  var bk = Object.keys(books).sort();
  if (!bk.length){document.getElementById('book-filter').innerHTML='';return;}
  var h = '<div class="book-filter">';
  h += '<span class="book-pill'+(ACTIVE_BOOK==='all'?' active':'')+'" onclick="filterBook(\'all\')">All ('+RECIPES.length+')</span>';
  for (var i=0;i<bk.length;i++){
    h += '<span class="book-pill'+(ACTIVE_BOOK===bk[i]?' active':'')+'" onclick="filterBook(\''+esc(bk[i]).replace(/'/g,"\\'")+'\')">'+esc(bk[i])+' ('+books[bk[i]]+')</span>';
  }
  h += '</div>';
  document.getElementById('book-filter').innerHTML = h;
}
function filterBook(book){
  ACTIVE_BOOK = book;
  renderBookFilter();
  renderTable();
}
function getFilteredRecipes(){
  if (ACTIVE_BOOK === 'all') return RECIPES;
  return RECIPES.filter(function(r){return (r.book||'') === ACTIVE_BOOK;});
}

function fcClass(pct){
  if (pct <= 30) return 'fc-green';
  if (pct <= 38) return 'fc-yellow';
  return 'fc-red';
}

function renderTable(){
  var filtered = getFilteredRecipes();
  if (!filtered.length){
    var msg = RECIPES.length ? 'No recipes in this book.' : 'No recipes yet. Add your first one below.';
    document.getElementById('table-container').innerHTML = '<div class="card" style="text-align:center;padding:30px 20px;">'
      + '<p style="color:var(--muted);margin-bottom:16px;">'+msg+'</p>'
      + '<button class="add-btn" style="font-size:14px;padding:10px 24px;" onclick="newRecipe()">+ Add Recipe</button>'
      + '</div>';
    return;
  }
  var hdr = '<th style="text-align:left;">Recipe</th><th>Book</th><th>Type</th>';
  hdr += '<th>Portion Cost</th><th>Menu Price</th><th>Food Cost %</th><th></th>';

  var rows = '';
  for (var fi=0;fi<filtered.length;fi++){
    var r = filtered[fi];
    // Find original index in RECIPES for detail panel
    var origIdx = RECIPES.indexOf(r);
    var c = r.cost || {};
    var fc = c.food_cost_pct || 0;
    var badge = r.type === 'menu' ? 'badge-menu' : 'badge-prep';
    rows += '<tr style="cursor:pointer;" onclick="toggleDetail('+origIdx+')">';
    // Name + allergen badges
    rows += '<td><div style="font-weight:600;">'+esc(r.name)+'</div>';
    var allergens = r.allergens || [];
    if (allergens.length){
      rows += '<div class="allergen-badges" style="margin-top:3px;">';
      for (var ai=0;ai<allergens.length;ai++){
        var aLabel = allergens[ai].replace('_',' ');
        rows += '<span class="allergen-badge '+esc(allergens[ai])+'">'+esc(aLabel)+'</span>';
      }
      rows += '</div>';
    }
    rows += '</td>';
    rows += '<td><span class="book-badge">'+esc(r.book||'--')+'</span></td>';
    rows += '<td><span class="badge '+badge+'">'+esc(r.type||'menu')+'</span></td>';
    rows += '<td>$'+c.portion_cost.toFixed(2)+'</td>';
    rows += '<td>'+(r.menu_price ? '$'+r.menu_price.toFixed(2) : '--')+'</td>';
    rows += '<td class="'+fcClass(fc)+'">'+(r.menu_price ? fc.toFixed(1)+'%' : '--')+'</td>';
    rows += '<td style="white-space:nowrap;">';
    rows += '<button class="add-btn" style="font-size:11px;padding:4px 10px;" onclick="event.stopPropagation();editRecipe('+origIdx+')">Edit</button> ';
    rows += '<button class="add-btn" style="font-size:11px;padding:4px 8px;background:#6b7280;" onclick="event.stopPropagation();duplicateRecipe('+origIdx+')" title="Duplicate">Copy</button>';
    rows += '</td>';
    rows += '</tr>';

    // Detail row (hidden) — full-width tabbed panel
    rows += '<tr id="detail-'+origIdx+'" style="display:none;">';
    rows += '<td colspan="7"><div class="detail-panel" id="detail-content-'+origIdx+'"></div></td>';
    rows += '</tr>';
  }

  var h = '<div class="card"><div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">';
  h += '<div class="card-title" style="margin:0;">Recipe Database</div>';
  h += '<button class="add-btn" onclick="newRecipe()">+ Add Recipe</button>';
  h += '</div>';
  h += '<div style="overflow-x:auto;"><table class="recipe-table">';
  h += '<thead><tr>'+hdr+'</tr></thead><tbody>'+rows+'</tbody></table></div></div>';
  document.getElementById('table-container').innerHTML = h;
}

function toggleDetail(idx){
  var row = document.getElementById('detail-'+idx);
  if (row.style.display === 'table-row'){
    row.style.display = 'none';
    return;
  }
  row.style.display = 'table-row';
  renderDetailPanel(idx, 1);
}
function renderDetailPanel(idx, scale){
  if (!scale) scale = 1;
  var r = RECIPES[idx];
  var c = r.cost || {};
  var ings = c.ingredients || [];
  var steps = r.steps || [];

  var h = '';
  // Tabs
  h += '<div class="detail-tabs">';
  h += '<button class="detail-tab active" onclick="switchDetailTab(this,\'prep\','+idx+')">Prep Method</button>';
  h += '<button class="detail-tab" onclick="switchDetailTab(this,\'cost\','+idx+')">Cost</button>';
  h += '<button class="detail-tab" onclick="switchDetailTab(this,\'allergens\','+idx+')">Allergens</button>';
  h += '</div>';

  // ── Prep Method tab ──
  h += '<div class="detail-tab-body active" data-dtab="prep">';
  // Photo
  if (r.photo){
    h += '<img src="'+r.photo+'" style="max-width:100%;max-height:200px;border-radius:8px;margin-bottom:12px;object-fit:cover;" alt="'+esc(r.name)+'">';
  }
  // Info grid
  h += '<div class="prep-info-grid">';
  if (r.prep_time) h += '<div class="prep-info-item"><div class="prep-info-label">Prep Time</div><div class="prep-info-value">'+r.prep_time+' min</div></div>';
  if (r.cook_time) h += '<div class="prep-info-item"><div class="prep-info-label">Cook Time</div><div class="prep-info-value">'+r.cook_time+' min</div></div>';
  if (r.prep_time || r.cook_time){
    var total = (r.prep_time||0) + (r.cook_time||0);
    h += '<div class="prep-info-item"><div class="prep-info-label">Total Time</div><div class="prep-info-value">'+total+' min</div></div>';
  }
  h += '<div class="prep-info-item"><div class="prep-info-label">Portions</div><div class="prep-info-value">'+(c.portions||1)+'</div></div>';
  if (r.menu_price) h += '<div class="prep-info-item"><div class="prep-info-label">Menu Price</div><div class="prep-info-value">$'+r.menu_price.toFixed(2)+'</div></div>';
  h += '</div>';
  // Equipment
  if (r.equipment){
    h += '<div style="margin-bottom:12px;"><span style="font-size:11px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.3px;">Equipment</span>';
    h += '<div style="font-size:13px;margin-top:2px;">'+esc(r.equipment)+'</div></div>';
  }
  // Steps
  if (steps.length){
    h += '<div style="font-size:11px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.3px;margin-bottom:8px;">Instructions</div>';
    for (var si=0;si<steps.length;si++){
      var stepText = typeof steps[si] === 'string' ? steps[si] : (steps[si].text || '');
      h += '<div class="step-item"><div class="step-num">'+(si+1)+'</div><div class="step-text">'+esc(stepText)+'</div></div>';
    }
  } else {
    h += '<p style="color:var(--muted);font-size:13px;font-style:italic;">No instructions added yet. Click Edit to add steps.</p>';
  }
  // Actions
  h += '<div style="margin-top:12px;display:flex;gap:8px;">';
  h += '<button class="add-btn" style="font-size:11px;padding:4px 12px;background:#1a5276;" onclick="event.stopPropagation();showRecipeCard('+idx+')">Print Recipe Card</button>';
  h += '<button class="add-btn" style="font-size:11px;padding:4px 10px;" onclick="event.stopPropagation();editRecipe('+idx+')">Edit Recipe</button>';
  h += '</div>';
  h += '</div>';

  // ── Cost tab ──
  h += '<div class="detail-tab-body" data-dtab="cost">';
  // Batch scaler
  h += '<div class="batch-scaler">';
  h += '<label>Batch Scale:</label>';
  h += '<button class="batch-quick'+(scale===0.5?' active':'')+'" onclick="renderDetailPanel('+idx+',0.5)">0.5x</button>';
  h += '<button class="batch-quick'+(scale===1?' active':'')+'" onclick="renderDetailPanel('+idx+',1)">1x</button>';
  h += '<button class="batch-quick'+(scale===2?' active':'')+'" onclick="renderDetailPanel('+idx+',2)">2x</button>';
  h += '<button class="batch-quick'+(scale===5?' active':'')+'" onclick="renderDetailPanel('+idx+',5)">5x</button>';
  h += '<input type="number" step="0.5" min="0.1" value="'+scale+'" onchange="renderDetailPanel('+idx+',parseFloat(this.value)||1)" style="margin-left:4px;">';
  h += '</div>';
  // Ingredient table
  h += '<table class="detail-table"><thead><tr>';
  h += '<th>Ingredient</th><th>Qty</th><th>UOM</th><th>Unit Cost</th>';
  h += '<th>Line Cost</th><th>Yield</th><th>Vendor</th>';
  h += '</tr></thead><tbody>';
  var scaledBatch = 0;
  for (var j=0;j<ings.length;j++){
    var ing = ings[j];
    var scaledQty = ing.quantity * scale;
    var scaledLine = ing.line_cost * scale;
    scaledBatch += scaledLine;
    var cls = ing.matched ? '' : ' class="unmatched"';
    var nameHtml = esc(ing.name);
    if (ing.is_sub_recipe){
      nameHtml = '<span class="sub-recipe-indicator"><span class="sub-recipe-icon">&#x1f517;</span> '+nameHtml+'</span>';
    }
    h += '<tr>';
    h += '<td'+cls+'>'+nameHtml+(ing.matched?'':' <span style="font-size:10px;">(no price)</span>')+'</td>';
    h += '<td>'+(scaledQty % 1 === 0 ? scaledQty : scaledQty.toFixed(2))+'</td>';
    h += '<td>'+esc(ing.uom)+'</td>';
    h += '<td>'+(ing.unit_cost ? '$'+ing.unit_cost.toFixed(4) : '-')+'</td>';
    h += '<td>'+(scaledLine ? '$'+scaledLine.toFixed(2) : '-')+'</td>';
    h += '<td>'+(ing.yield_pct < 1 ? (ing.yield_pct*100).toFixed(0)+'%' : '100%')+'</td>';
    h += '<td>'+esc(ing.vendor||'--')+'</td>';
    h += '</tr>';
  }
  h += '</tbody></table>';
  // Cost summary
  var scaledPortion = (c.portions||1) > 0 ? scaledBatch / (c.portions||1) : 0;
  var scaledFc = r.menu_price > 0 ? (scaledPortion / r.menu_price * 100) : 0;
  var fcCls = fcClass(scaledFc);
  h += '<div class="cost-summary" style="margin-top:12px;">';
  h += '<div class="cost-item"><div class="cost-val">$'+scaledBatch.toFixed(2)+'</div><div class="cost-lbl">Batch Cost'+(scale!==1?' ('+scale+'x)':'')+'</div></div>';
  h += '<div class="cost-item"><div class="cost-val">$'+scaledPortion.toFixed(2)+'</div><div class="cost-lbl">Portion Cost</div></div>';
  if (r.menu_price) h += '<div class="cost-item"><div class="cost-val '+fcCls+'">'+scaledFc.toFixed(1)+'%</div><div class="cost-lbl">Food Cost</div></div>';
  h += '<div class="cost-item"><div class="cost-val">'+(c.portions||1)+'</div><div class="cost-lbl">Portions</div></div>';
  h += '</div>';
  h += '</div>';

  // ── Allergens tab ──
  h += '<div class="detail-tab-body" data-dtab="allergens">';
  var allergens = r.allergens || [];
  if (allergens.length){
    h += '<div style="font-size:13px;margin-bottom:12px;">This recipe contains:</div>';
    h += '<div class="allergen-badges" style="gap:8px;">';
    for (var ai=0;ai<allergens.length;ai++){
      var aLabel = allergens[ai].replace('_',' ');
      h += '<span class="allergen-badge '+esc(allergens[ai])+'" style="font-size:12px;padding:4px 12px;">'+esc(aLabel)+'</span>';
    }
    h += '</div>';
  } else {
    h += '<p style="color:var(--muted);font-size:13px;font-style:italic;">No allergens tagged. Click Edit to add allergen information.</p>';
  }
  h += '</div>';

  document.getElementById('detail-content-'+idx).innerHTML = h;
}
function switchDetailTab(btn, tab, idx){
  var panel = document.getElementById('detail-content-'+idx);
  var tabs = panel.querySelectorAll('.detail-tab');
  var bodies = panel.querySelectorAll('.detail-tab-body');
  for (var i=0;i<tabs.length;i++) tabs[i].classList.remove('active');
  for (var i=0;i<bodies.length;i++) bodies[i].classList.toggle('active', bodies[i].dataset.dtab === tab);
  btn.classList.add('active');
}

// ── Modal: Add / Edit ──

function newRecipe(){
  EDITING = null;
  showModal({name:'',type:'menu',menu_price:0,portions:1,ingredients:[]});
}

function editRecipe(idx){
  EDITING = idx;
  var r = JSON.parse(JSON.stringify(RECIPES[idx]));
  showModal(r);
}

function showModal(recipe){
  var m = document.getElementById('modal');
  document.getElementById('m-name').value = recipe.name || '';
  document.getElementById('m-type').value = recipe.type || 'menu';
  document.getElementById('m-price').value = recipe.menu_price || '';
  document.getElementById('m-portions').value = recipe.portions || 1;
  document.getElementById('m-toast-name').value = recipe.toast_menu_name || '';
  document.getElementById('m-id').value = recipe.id || '';
  // New Meez fields
  document.getElementById('m-book').value = recipe.book || '';
  document.getElementById('m-prep-time').value = recipe.prep_time || '';
  document.getElementById('m-cook-time').value = recipe.cook_time || '';
  document.getElementById('m-equipment').value = recipe.equipment || '';

  // Allergens
  var allergens = recipe.allergens || [];
  var checks = document.querySelectorAll('#m-allergens .allergen-check');
  for (var i=0;i<checks.length;i++){
    var aid = checks[i].dataset.allergen;
    var isChecked = allergens.indexOf(aid) >= 0;
    checks[i].classList.toggle('checked', isChecked);
    checks[i].querySelector('input').checked = isChecked;
  }

  var ings = recipe.ingredients || [];
  var container = document.getElementById('ing-container');
  container.innerHTML = '';
  for (var i=0;i<ings.length;i++){
    addIngredientRow(ings[i]);
  }
  if (!ings.length) addIngredientRow({});

  // Steps (instructions)
  var stepsContainer = document.getElementById('steps-container');
  if (stepsContainer){
    stepsContainer.innerHTML = '';
    var steps = recipe.steps || [];
    for (var i=0;i<steps.length;i++){
      addStepRow(steps[i]);
    }
  }

  // Photo preview
  var photoPreview = document.getElementById('m-photo-preview');
  var photoInput = document.getElementById('m-photo-input');
  if (photoPreview){
    if (recipe.photo){
      photoPreview.src = recipe.photo;
      photoPreview.style.display = 'block';
    } else {
      photoPreview.src = '';
      photoPreview.style.display = 'none';
    }
  }
  if (photoInput) photoInput.value = '';

  document.getElementById('btn-delete').style.display = EDITING !== null ? 'block' : 'none';
  document.getElementById('modal-overlay').style.display = 'flex';
  // Trigger cost calculation after modal is visible
  setTimeout(function(){ updateCosts(); }, 50);
}

function closeModal(){
  document.getElementById('modal-overlay').style.display = 'none';
}

function addIngredientRow(ing){
  var container = document.getElementById('ing-container');
  var row = document.createElement('div');
  row.className = 'ing-row';
  var isSub = !!ing.sub_recipe;
  var nameVal = ing.item || ing.sub_recipe || '';
  var matchInfo = ITEMS_MAP[nameVal.toLowerCase()];
  var matchClass = isSub ? 'matched' : (nameVal ? (matchInfo ? 'matched' : 'unmatched') : 'unknown');
  var subChecked = isSub ? ' checked' : '';
  row.innerHTML = '<div class="ing-match '+matchClass+'"'+(isSub?' style="background:#1565c0;"':'')+'></div>'
    + '<div style="position:relative;flex:3;min-width:140px;">'
    + '<input class="ing-name" placeholder="Start typing ingredient..." value="'+esc(nameVal)+'" oninput="showAutocomplete(this);updateMatchDot(this);updateCosts();" onfocus="showAutocomplete(this)" onkeydown="acKeydown(event,this)" autocomplete="off">'
    + '</div>'
    + '<input class="ing-qty" type="number" step="0.1" placeholder="Qty" value="'+(ing.quantity||'')+'" oninput="updateCosts()">'
    + '<input class="ing-uom" placeholder="UOM" value="'+esc(ing.uom||'')+'" oninput="updateCosts()">'
    + '<input class="ing-yield" type="number" step="0.01" placeholder="Yield" value="'+(ing.yield_pct && ing.yield_pct < 1 ? ing.yield_pct : '')+'" oninput="updateCosts()">'
    + '<span class="ing-line-cost">--</span>'
    + '<label style="font-size:9px;color:var(--muted);display:flex;align-items:center;gap:2px;cursor:pointer;" title="Toggle sub-recipe"><input type="checkbox" class="ing-sub-toggle"'+subChecked+' style="margin:0;">Sub</label>'
    + '<button class="ing-remove" onclick="this.parentElement.remove();updateCosts();" title="Remove">&times;</button>';
  container.appendChild(row);
  if (nameVal) updateCosts();
}

function updateMatchDot(input){
  var row = input.closest('.ing-row');
  var dot = row.querySelector('.ing-match');
  var val = input.value.trim().toLowerCase();
  if (!val) { dot.className = 'ing-match unknown'; return; }
  dot.className = ITEMS_MAP[val] ? 'ing-match matched' : 'ing-match unmatched';
}

function updateCosts(){
  var rows = document.querySelectorAll('#ing-container .ing-row');
  var batchTotal = 0;
  for (var i=0;i<rows.length;i++){
    var name = rows[i].querySelector('.ing-name').value.trim().toLowerCase();
    var qty = parseFloat(rows[i].querySelector('.ing-qty').value) || 0;
    var uom = rows[i].querySelector('.ing-uom').value.trim();
    var yld = parseFloat(rows[i].querySelector('.ing-yield').value);
    if (!yld || yld <= 0 || yld > 1) yld = 1;
    var costEl = rows[i].querySelector('.ing-line-cost');

    var item = ITEMS_MAP[name];
    if (item && item.price_per_unit && qty > 0){
      var lineCost = item.price_per_unit * qty / yld;
      batchTotal += lineCost;
      costEl.textContent = '$' + lineCost.toFixed(2);
      costEl.className = 'ing-line-cost has-cost';
    } else if (qty > 0 && name) {
      costEl.textContent = '?';
      costEl.className = 'ing-line-cost';
    } else {
      costEl.textContent = '--';
      costEl.className = 'ing-line-cost';
    }
  }
  // Update summary
  var portions = parseInt(document.getElementById('m-portions').value) || 1;
  var price = parseFloat(document.getElementById('m-price').value) || 0;
  var portionCost = batchTotal / portions;
  var fcPct = price > 0 ? (portionCost / price * 100) : 0;

  var sumEl = document.getElementById('cost-summary');
  if (sumEl){
    var cls = fcPct <= 30 ? 'fc-green' : (fcPct <= 38 ? 'fc-yellow' : 'fc-red');
    sumEl.innerHTML = '<div class="cost-item"><div class="cost-val">$'+batchTotal.toFixed(2)+'</div><div class="cost-lbl">Batch Cost</div></div>'
      + '<div class="cost-item"><div class="cost-val">$'+portionCost.toFixed(2)+'</div><div class="cost-lbl">Portion Cost</div></div>'
      + (price > 0 ? '<div class="cost-item"><div class="cost-val '+cls+'">'+fcPct.toFixed(1)+'%</div><div class="cost-lbl">Food Cost</div></div>' : '');
  }
}

function showAutocomplete(input){
  // Remove existing
  var old = document.querySelectorAll('.autocomplete-list');
  for (var i=0;i<old.length;i++) old[i].remove();
  AC_INDEX = -1;

  var val = input.value.toLowerCase();
  if (val.length < 1) return;

  // Score and sort: starts-with first, then contains
  var starts = [], contains = [];
  for (var i=0;i<ITEMS.length;i++){
    var n = ITEMS[i].name.toLowerCase();
    if (n.indexOf(val) === 0) starts.push(ITEMS[i]);
    else if (n.indexOf(val) >= 0) contains.push(ITEMS[i]);
  }
  var matches = starts.concat(contains).slice(0, 12);

  var list = document.createElement('div');
  list.className = 'autocomplete-list';
  list.style.position = 'absolute';
  list.style.top = input.offsetHeight + 'px';
  list.style.left = '0';

  for (var i=0;i<matches.length;i++){
    var m = matches[i];
    var opt = document.createElement('div');
    opt.className = 'ac-item';

    var nameHtml = '<span class="ac-name">'+esc(m.name)+'</span>';
    var metaParts = [];
    if (m.price_per_unit) metaParts.push('<span class="ac-price">$'+m.price_per_unit.toFixed(4)+'/'+esc(m.price_unit||m.unit||'ea')+'</span>');
    if (m.vendor) metaParts.push('<span class="ac-vendor">'+esc(m.vendor)+'</span>');
    if (!m.price_per_unit) metaParts.push('<span class="ac-vendor" style="color:#dc2626;">no price</span>');
    var metaHtml = metaParts.length ? '<span class="ac-meta">'+metaParts.join('')+'</span>' : '';
    opt.innerHTML = nameHtml + metaHtml;

    opt.onclick = (function(inp, item){
      return function(){
        selectAutocompleteItem(inp, item);
      };
    })(input, m);
    list.appendChild(opt);
  }

  // Show "new item" hint if no exact match
  if (val.length >= 2 && !ITEMS_MAP[val]){
    var newOpt = document.createElement('div');
    newOpt.className = 'ac-item';
    newOpt.innerHTML = '<span class="ac-new">Use "'+esc(input.value)+'" as new ingredient</span>';
    newOpt.onclick = function(){
      var old2 = document.querySelectorAll('.autocomplete-list');
      for (var j=0;j<old2.length;j++) old2[j].remove();
      updateMatchDot(input);
      updateCosts();
    };
    list.appendChild(newOpt);
  }

  if (list.children.length > 0){
    input.parentElement.appendChild(list);
  }
}

function selectAutocompleteItem(input, item){
  input.value = item.name;
  var row = input.closest('.ing-row');
  // Auto-fill UOM from price unit or item unit
  var uomInput = row.querySelector('.ing-uom');
  if (uomInput && !uomInput.value){
    uomInput.value = item.price_unit || item.unit || '';
  }
  var old = document.querySelectorAll('.autocomplete-list');
  for (var j=0;j<old.length;j++) old[j].remove();
  updateMatchDot(input);
  updateCosts();
  // Focus qty field for fast entry
  var qtyInput = row.querySelector('.ing-qty');
  if (qtyInput) qtyInput.focus();
}

function acKeydown(e, input){
  var list = input.parentElement.querySelector('.autocomplete-list');
  if (!list) return;
  var items = list.querySelectorAll('.ac-item');
  if (e.key === 'ArrowDown'){
    e.preventDefault();
    AC_INDEX = Math.min(AC_INDEX + 1, items.length - 1);
    for (var i=0;i<items.length;i++) items[i].classList.toggle('ac-selected', i===AC_INDEX);
  } else if (e.key === 'ArrowUp'){
    e.preventDefault();
    AC_INDEX = Math.max(AC_INDEX - 1, 0);
    for (var i=0;i<items.length;i++) items[i].classList.toggle('ac-selected', i===AC_INDEX);
  } else if (e.key === 'Enter'){
    e.preventDefault();
    if (AC_INDEX >= 0 && AC_INDEX < items.length){
      items[AC_INDEX].click();
    }
  } else if (e.key === 'Escape'){
    var old = document.querySelectorAll('.autocomplete-list');
    for (var i=0;i<old.length;i++) old[i].remove();
  }
}

// Close autocomplete on outside click
document.addEventListener('click', function(e){
  if (!e.target.classList.contains('ing-name') && e.target.id !== 'm-toast-name'){
    var old = document.querySelectorAll('.autocomplete-list');
    for (var i=0;i<old.length;i++) old[i].remove();
  }
});

// ── Toast Item Autocomplete ──
var TOAST_AC_INDEX = -1;

function showToastAutocomplete(input){
  var old = document.querySelectorAll('.autocomplete-list');
  for (var i=0;i<old.length;i++) old[i].remove();
  TOAST_AC_INDEX = -1;

  var val = input.value.toLowerCase();
  if (val.length < 1 && TOAST_ITEMS.length > 0){
    // Show all items when field is focused with empty value
    var all = TOAST_ITEMS.slice(0, 15);
    renderToastAcList(input, all);
    return;
  }
  if (val.length < 1) return;

  var starts = [], contains = [];
  for (var i=0;i<TOAST_ITEMS.length;i++){
    var n = TOAST_ITEMS[i].name.toLowerCase();
    if (n.indexOf(val) === 0) starts.push(TOAST_ITEMS[i]);
    else if (n.indexOf(val) >= 0) contains.push(TOAST_ITEMS[i]);
  }
  var matches = starts.concat(contains).slice(0, 15);
  renderToastAcList(input, matches);
}

function renderToastAcList(input, matches){
  if (!matches.length) return;
  var list = document.createElement('div');
  list.className = 'autocomplete-list';
  list.style.position = 'absolute';
  list.style.top = input.offsetHeight + 'px';
  list.style.left = '0';

  for (var i=0;i<matches.length;i++){
    var m = matches[i];
    var opt = document.createElement('div');
    opt.className = 'ac-item';
    opt.innerHTML = '<span class="ac-name">'+esc(m.name)+'</span>'
      + '<span class="ac-meta"><span class="ac-unit">'+esc(m.category)+'</span>'
      + '<span class="ac-vendor">~'+m.avg_daily+'/day</span></span>';
    opt.onclick = (function(inp, item){
      return function(){
        inp.value = item.name;
        // Also auto-fill recipe name if empty
        var nameInput = document.getElementById('m-name');
        if (nameInput && !nameInput.value) nameInput.value = item.name;
        var old2 = document.querySelectorAll('.autocomplete-list');
        for (var j=0;j<old2.length;j++) old2[j].remove();
      };
    })(input, m);
    list.appendChild(opt);
  }
  input.parentElement.appendChild(list);
}

function toastAcKeydown(e, input){
  var list = input.parentElement.querySelector('.autocomplete-list');
  if (!list) return;
  var items = list.querySelectorAll('.ac-item');
  if (e.key === 'ArrowDown'){
    e.preventDefault();
    TOAST_AC_INDEX = Math.min(TOAST_AC_INDEX + 1, items.length - 1);
    for (var i=0;i<items.length;i++) items[i].classList.toggle('ac-selected', i===TOAST_AC_INDEX);
  } else if (e.key === 'ArrowUp'){
    e.preventDefault();
    TOAST_AC_INDEX = Math.max(TOAST_AC_INDEX - 1, 0);
    for (var i=0;i<items.length;i++) items[i].classList.toggle('ac-selected', i===TOAST_AC_INDEX);
  } else if (e.key === 'Enter'){
    e.preventDefault();
    if (TOAST_AC_INDEX >= 0 && TOAST_AC_INDEX < items.length) items[TOAST_AC_INDEX].click();
  } else if (e.key === 'Escape'){
    var old = document.querySelectorAll('.autocomplete-list');
    for (var i=0;i<old.length;i++) old[i].remove();
  }
}

async function saveRecipe(){
  var recipe = {
    name: document.getElementById('m-name').value.trim(),
    type: document.getElementById('m-type').value,
    menu_price: parseFloat(document.getElementById('m-price').value) || 0,
    portions: parseInt(document.getElementById('m-portions').value) || 1,
    toast_menu_name: document.getElementById('m-toast-name').value.trim(),
    book: document.getElementById('m-book').value.trim(),
    prep_time: parseInt(document.getElementById('m-prep-time').value) || 0,
    cook_time: parseInt(document.getElementById('m-cook-time').value) || 0,
    equipment: document.getElementById('m-equipment').value.trim(),
    ingredients: []
  };
  var rid = document.getElementById('m-id').value;
  if (rid) recipe.id = rid;

  // Allergens
  var allergenChecks = document.querySelectorAll('#m-allergens .allergen-check input:checked');
  var allergens = [];
  for (var i=0;i<allergenChecks.length;i++){
    allergens.push(allergenChecks[i].parentElement.dataset.allergen);
  }
  if (allergens.length) recipe.allergens = allergens;

  if (!recipe.name){
    alert('Recipe name is required.');
    return;
  }

  var rows = document.querySelectorAll('#ing-container .ing-row');
  for (var i=0;i<rows.length;i++){
    var name = rows[i].querySelector('.ing-name');
    if (!name) continue;
    var nameVal = name.value.trim();
    if (!nameVal) continue;
    var qty = parseFloat(rows[i].querySelector('.ing-qty').value) || 0;
    var uom = rows[i].querySelector('.ing-uom').value.trim();
    var yld = parseFloat(rows[i].querySelector('.ing-yield').value);
    // Check for sub-recipe toggle
    var subToggle = rows[i].querySelector('.ing-sub-toggle');
    var ing;
    if (subToggle && subToggle.checked){
      ing = {sub_recipe: nameVal, quantity: qty, uom: uom};
    } else {
      ing = {item: nameVal, quantity: qty, uom: uom};
    }
    if (yld && yld > 0 && yld < 1) ing.yield_pct = yld;
    recipe.ingredients.push(ing);
  }

  // Collect steps
  var stepRows = document.querySelectorAll('#steps-container .ing-row');
  var steps = [];
  for (var i=0;i<stepRows.length;i++){
    var txt = stepRows[i].querySelector('textarea').value.trim();
    if (txt) steps.push(txt);
  }
  if (steps.length) recipe.steps = steps;

  // Photo (base64 data URL)
  var photoEl = document.getElementById('m-photo-preview');
  if (photoEl && photoEl.src && photoEl.src.indexOf('data:') === 0){
    recipe.photo = photoEl.src;
  }

  try {
    var resp = await fetch('/api/recipes/save', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(recipe)
    });
    if (!resp.ok) throw new Error('Save failed');
    closeModal();
    // Reload
    var rResp = await fetch('/api/recipes/data');
    RECIPES = await rResp.json();
    renderAll();
  } catch(err){
    alert('Error saving recipe: ' + err.message);
  }
}

async function deleteRecipe(){
  var rid = document.getElementById('m-id').value;
  if (!rid || !confirm('Delete this recipe?')) return;
  try {
    var resp = await fetch('/api/recipes/delete', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id: rid})
    });
    if (!resp.ok) throw new Error('Delete failed');
    closeModal();
    var rResp = await fetch('/api/recipes/data');
    RECIPES = await rResp.json();
    renderAll();
  } catch(err){
    alert('Error deleting recipe: ' + err.message);
  }
}

function duplicateRecipe(idx){
  EDITING = null;
  var r = JSON.parse(JSON.stringify(RECIPES[idx]));
  r.name = r.name + ' (Copy)';
  r.id = '';
  r.toast_menu_name = '';
  showModal(r);
}

function esc(s){var d=document.createElement('div');d.textContent=s||'';return d.innerHTML;}

// ── Step-by-step instructions ──

function addStepRow(step){
  var container = document.getElementById('steps-container');
  if (!container) return;
  var idx = container.children.length + 1;
  var row = document.createElement('div');
  row.className = 'ing-row';
  row.style.alignItems = 'flex-start';
  row.innerHTML = '<span style="font-weight:600;color:var(--muted);min-width:24px;">'+idx+'.</span>'
    + '<textarea style="flex:1;min-height:40px;padding:6px 8px;border:1px solid var(--border);border-radius:4px;font-size:13px;font-family:inherit;resize:vertical;background:#fff;" placeholder="Describe this step...">'+(typeof step === 'string' ? esc(step) : esc(step.text||''))+'</textarea>'
    + '<button class="ing-remove" onclick="this.parentElement.remove();renumberSteps();" title="Remove">&times;</button>';
  container.appendChild(row);
}

function renumberSteps(){
  var rows = document.querySelectorAll('#steps-container .ing-row');
  for (var i=0;i<rows.length;i++){
    rows[i].querySelector('span').textContent = (i+1) + '.';
  }
}

// ── Photo handling ──

function previewPhoto(input){
  var preview = document.getElementById('m-photo-preview');
  if (input.files && input.files[0]){
    var reader = new FileReader();
    reader.onload = function(e){
      preview.src = e.target.result;
      preview.style.display = 'block';
    };
    reader.readAsDataURL(input.files[0]);
  }
}

// ── Recipe Card (printable) ──

function showRecipeCard(idx){
  var r = RECIPES[idx];
  var c = r.cost || {};
  var ings = c.ingredients || [];
  var steps = r.steps || [];
  var allergens = r.allergens || [];

  var w = window.open('', '_blank', 'width=800,height=1000');
  var h = '<!DOCTYPE html><html><head><meta charset="UTF-8"><title>'+esc(r.name)+' - Recipe Card</title>';
  h += '<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">';
  h += '<style>*{margin:0;padding:0;box-sizing:border-box;}body{font-family:"DM Sans",sans-serif;padding:0;color:#2d2a24;}';
  h += '.card-page{max-width:750px;margin:0 auto;padding:32px;}';
  // Header
  h += '.card-header{border-bottom:3px solid #475417;padding-bottom:16px;margin-bottom:20px;}';
  h += 'h1{font-size:28px;margin-bottom:6px;}';
  h += '.card-meta{display:flex;gap:16px;flex-wrap:wrap;font-size:13px;color:#6b7280;}';
  h += '.card-meta-item{display:flex;align-items:center;gap:4px;}';
  h += '.card-meta-label{font-weight:600;text-transform:uppercase;font-size:10px;letter-spacing:.3px;}';
  // Two column
  h += '.card-columns{display:grid;grid-template-columns:1fr 1fr;gap:24px;}';
  h += '.section-title{font-size:13px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;color:#475417;border-bottom:2px solid #475417;padding-bottom:4px;margin:0 0 10px;}';
  // Ingredients
  h += 'table{width:100%;border-collapse:collapse;font-size:12px;}';
  h += 'th{text-align:left;font-size:10px;color:#6b7280;text-transform:uppercase;padding:3px 4px;border-bottom:1px solid #d1d5db;}';
  h += 'td{padding:4px;border-bottom:1px solid #f0f0f0;}';
  // Steps
  h += '.step{display:flex;gap:10px;margin-bottom:10px;}.step-num{font-weight:700;color:#475417;min-width:22px;font-size:14px;}';
  h += '.step-text{font-size:13px;line-height:1.5;}';
  // Cost + allergens
  h += '.cost-box{background:#f5f5f0;border-radius:8px;padding:10px;display:flex;gap:16px;justify-content:center;margin-top:16px;}';
  h += '.cost-box div{text-align:center;}.cost-box .val{font-size:16px;font-weight:700;}.cost-box .lbl{font-size:9px;color:#6b7280;text-transform:uppercase;}';
  h += '.allergen-warning{margin-top:12px;padding:8px 12px;background:#fff3cd;border:1px solid #ffc107;border-radius:6px;font-size:12px;}';
  h += '.allergen-warning strong{text-transform:uppercase;font-size:11px;letter-spacing:.3px;}';
  h += '.photo-header{max-width:100%;max-height:180px;border-radius:8px;margin-bottom:16px;object-fit:cover;}';
  h += '@media print{body{padding:0;}.card-page{padding:16px;}button{display:none !important;}.card-columns{grid-template-columns:1fr 1fr;}}</style></head><body>';

  h += '<div class="card-page">';
  // Header
  h += '<div class="card-header">';
  if (r.photo) h += '<img class="photo-header" src="'+r.photo+'" alt="'+esc(r.name)+'">';
  h += '<h1>'+esc(r.name)+'</h1>';
  h += '<div class="card-meta">';
  if (r.book) h += '<div class="card-meta-item"><span class="card-meta-label">Book:</span> '+esc(r.book)+'</div>';
  if (r.type) h += '<div class="card-meta-item"><span class="card-meta-label">Type:</span> '+(r.type==='menu'?'Menu Item':'Prep Recipe')+'</div>';
  h += '<div class="card-meta-item"><span class="card-meta-label">Portions:</span> '+(c.portions||1)+'</div>';
  if (r.menu_price) h += '<div class="card-meta-item"><span class="card-meta-label">Price:</span> $'+r.menu_price.toFixed(2)+'</div>';
  if (r.prep_time) h += '<div class="card-meta-item"><span class="card-meta-label">Prep:</span> '+r.prep_time+' min</div>';
  if (r.cook_time) h += '<div class="card-meta-item"><span class="card-meta-label">Cook:</span> '+r.cook_time+' min</div>';
  h += '</div>';
  if (r.equipment) h += '<div style="font-size:12px;color:#6b7280;margin-top:6px;"><strong>Equipment:</strong> '+esc(r.equipment)+'</div>';
  h += '</div>';

  // Two-column layout
  h += '<div class="card-columns">';
  // Left: Ingredients
  h += '<div>';
  h += '<div class="section-title">Ingredients</div>';
  h += '<table><thead><tr><th>Item</th><th>Qty</th><th>Unit</th></tr></thead><tbody>';
  for (var i=0;i<ings.length;i++){
    var prefix = ings[i].is_sub_recipe ? '&#x1f517; ' : '';
    h += '<tr><td>'+prefix+esc(ings[i].name)+'</td><td>'+ings[i].quantity+'</td><td>'+esc(ings[i].uom)+'</td></tr>';
  }
  h += '</tbody></table>';
  h += '</div>';
  // Right: Instructions
  h += '<div>';
  if (steps.length){
    h += '<div class="section-title">Instructions</div>';
    for (var i=0;i<steps.length;i++){
      var stepText = typeof steps[i] === 'string' ? steps[i] : (steps[i].text || '');
      h += '<div class="step"><div class="step-num">'+(i+1)+'</div><div class="step-text">'+esc(stepText)+'</div></div>';
    }
  }
  h += '</div>';
  h += '</div>';  // end columns

  // Cost summary
  h += '<div class="cost-box">';
  h += '<div><div class="val">$'+c.batch_cost.toFixed(2)+'</div><div class="lbl">Batch Cost</div></div>';
  h += '<div><div class="val">$'+c.portion_cost.toFixed(2)+'</div><div class="lbl">Portion Cost</div></div>';
  if (r.menu_price) h += '<div><div class="val">'+c.food_cost_pct.toFixed(1)+'%</div><div class="lbl">Food Cost</div></div>';
  h += '</div>';

  // Allergen warning
  if (allergens.length){
    h += '<div class="allergen-warning"><strong>Contains:</strong> ';
    h += allergens.map(function(a){return a.replace('_',' ');}).join(', ');
    h += '</div>';
  }

  h += '<div style="text-align:center;margin-top:20px;"><button onclick="window.print()" style="padding:8px 20px;background:#475417;color:#fff;border:none;border-radius:6px;font-size:14px;cursor:pointer;font-family:inherit;">Print</button></div>';
  h += '</div>';  // end card-page
  h += '</body></html>';
  w.document.write(h);
  w.document.close();
}

async function loadCoverage(){
  var el = document.getElementById('coverage-result');
  el.textContent = 'Checking coverage...';
  try {
    var resp = await fetch('/api/recipes/coverage');
    if (!resp.ok) throw new Error('Failed to load coverage');
    var d = await resp.json();
    var h = '<div style="display:flex;gap:16px;justify-content:center;flex-wrap:wrap;margin-top:8px;">';
    h += '<div><strong>'+d.coverage_pct+'%</strong> of sales costed by recipe</div>';
    h += '<div>Theoretical food cost: <strong class="'+fcClass(d.food_cost_pct)+'">'+d.food_cost_pct+'%</strong></div>';
    h += '<div>$'+d.total_food_cost.toFixed(2)+' / $'+d.total_sales.toFixed(2)+'</div>';
    h += '</div>';
    if (d.items){
      var uncov = d.items.filter(function(it){return it.method==='estimate';});
      if (uncov.length){
        h += '<div style="margin-top:8px;font-size:12px;color:var(--muted);">Uncovered (est. 35%): ';
        h += uncov.slice(0,5).map(function(it){return it.menu_item+' ($'+it.sales.toFixed(0)+')';}).join(', ');
        if (uncov.length > 5) h += ' +' + (uncov.length-5) + ' more';
        h += '</div>';
      }
    }
    el.innerHTML = h;
  } catch(err){
    el.textContent = 'Error: ' + err.message;
    el.style.color = '#dc2626';
  }
}

async function loadIngredientReport(){
  var el = document.getElementById('ingredient-report');
  el.innerHTML = '<div style="text-align:center;color:var(--muted);font-size:13px;">Loading ingredient report...</div>';
  try {
    var resp = await fetch('/api/recipes/ingredient-report');
    if (!resp.ok) throw new Error('Failed to load report');
    var d = await resp.json();

    var h = '<div class="card" style="margin-top:8px;">';
    h += '<div class="card-title" style="margin-bottom:8px;">Ingredient Match Report';
    h += ' <span style="font-weight:400;font-size:12px;color:var(--muted);">(' + d.match_pct + '% matched)</span></div>';

    // Unmatched first
    var unmatched = d.items.filter(function(i){return !i.matched;});
    var matched = d.items.filter(function(i){return i.matched;});

    if (unmatched.length){
      h += '<div style="font-size:12px;font-weight:600;color:#dc2626;margin-bottom:6px;">Unpriced (' + unmatched.length + ')</div>';
      h += '<table class="recipe-table" style="margin-bottom:16px;"><thead><tr>';
      h += '<th style="text-align:left;">Ingredient</th><th>Used In</th><th>Action</th></tr></thead><tbody>';
      for (var i=0;i<unmatched.length;i++){
        var it = unmatched[i];
        h += '<tr><td style="color:#dc2626;">'+esc(it.name)+'</td>';
        h += '<td>'+it.recipes.length+' recipe'+(it.recipes.length>1?'s':'')+'</td>';
        h += '<td><a href="/prices/" style="font-size:11px;color:#1a5276;">Add Price</a></td></tr>';
      }
      h += '</tbody></table>';
    }

    if (matched.length){
      h += '<div style="font-size:12px;font-weight:600;color:#16a34a;margin-bottom:6px;">Priced (' + matched.length + ')</div>';
      h += '<table class="recipe-table"><thead><tr>';
      h += '<th style="text-align:left;">Ingredient</th><th>Unit Cost</th><th>Unit</th><th>Vendor</th><th>Used In</th></tr></thead><tbody>';
      for (var i=0;i<matched.length;i++){
        var it = matched[i];
        h += '<tr><td>'+esc(it.name)+'</td>';
        h += '<td>$'+it.unit_cost.toFixed(4)+'</td>';
        h += '<td>'+esc(it.unit)+'</td>';
        h += '<td>'+esc(it.vendor||'--')+'</td>';
        h += '<td>'+it.recipes.length+'</td></tr>';
      }
      h += '</tbody></table>';
    }

    h += '</div>';
    el.innerHTML = h;
  } catch(err){
    el.innerHTML = '<div style="color:#dc2626;font-size:13px;">Error: '+err.message+'</div>';
  }
}

// ── Page Tabs ──

var COVERAGE_DATA = null;
var COVERAGE_LOADED = false;

function switchTab(tab){
  var tabs = document.querySelectorAll('.page-tab');
  for (var i=0;i<tabs.length;i++) tabs[i].classList.toggle('active', tabs[i].dataset.tab === tab);
  document.getElementById('recipes-view').style.display = tab === 'recipes' ? 'block' : 'none';
  document.getElementById('coverage-view').style.display = tab === 'coverage' ? 'block' : 'none';
  document.getElementById('modifiers-view').style.display = tab === 'modifiers' ? 'block' : 'none';
  if (tab === 'coverage' && !COVERAGE_LOADED) loadMenuCoverage();
  if (tab === 'modifiers' && !MODIFIERS_LOADED) loadModifiers();
}

async function loadMenuCoverage(){
  var el = document.getElementById('coverage-content');
  el.innerHTML = '<div style="text-align:center;padding:40px;color:var(--muted);">Scanning sales data across all dates...</div>';
  try {
    var resp = await fetch('/api/recipes/menu-coverage');
    if (!resp.ok) throw new Error('Failed to load coverage data');
    COVERAGE_DATA = await resp.json();
    COVERAGE_LOADED = true;
    renderMenuCoverage();
  } catch(err){
    el.innerHTML = '<div style="text-align:center;padding:40px;color:#dc2626;">Error: '+err.message+'</div>';
  }
}

function renderMenuCoverage(){
  var s = COVERAGE_DATA.summary;
  var items = COVERAGE_DATA.items;

  var h = '';

  // Summary bar
  h += '<div class="cov-summary">';
  h += '<div class="cov-stat"><div class="cov-val">'+s.costed_items+'/'+s.total_items+'</div><div class="cov-lbl">Items Costed</div></div>';
  h += '<div class="cov-stat"><div class="cov-val">'+s.coverage_pct+'%</div><div class="cov-lbl">Item Coverage</div></div>';
  h += '<div class="cov-stat"><div class="cov-val">'+s.revenue_coverage_pct+'%</div><div class="cov-lbl">Revenue Coverage</div></div>';
  h += '<div class="cov-stat"><div class="cov-val">$'+(s.costed_revenue/1000).toFixed(0)+'k</div><div class="cov-lbl">Costed Revenue</div></div>';
  h += '<div class="cov-stat"><div class="cov-val">$'+(s.total_revenue/1000).toFixed(0)+'k</div><div class="cov-lbl">Total Revenue</div></div>';
  h += '</div>';

  // Overall progress bar
  h += '<div class="cov-progress" style="margin-bottom:20px;">';
  h += '<div class="cov-progress-bar" style="width:'+s.coverage_pct+'%;background:#16a34a;"></div>';
  h += '</div>';

  // Category cards
  for (var c=0;c<s.by_category.length;c++){
    var cat = s.by_category[c];
    var catItems = items.filter(function(it){return it.category === cat.category;});
    if (!catItems.length) continue;

    var badgeClass = cat.pct >= 100 ? 'full' : (cat.pct > 0 ? 'partial' : 'none');
    var expanded = cat.pct < 100;  // auto-expand incomplete categories

    h += '<div class="cov-cat-card">';
    h += '<div class="cov-cat-header" onclick="toggleCovCat(this)">';
    h += '<div><span class="cov-cat-name">'+esc(cat.category)+'</span>';
    h += ' <span class="cov-cat-badge '+badgeClass+'">'+cat.costed+'/'+cat.total+'</span></div>';
    h += '<div class="cov-cat-stats">';
    h += '<span>$'+(cat.revenue/1000).toFixed(0)+'k rev</span>';
    h += '<span>'+cat.revenue_pct+'% costed</span>';
    h += '<span style="font-size:16px;">'+(expanded ? '&#9660;' : '&#9654;')+'</span>';
    h += '</div></div>';

    h += '<div class="cov-cat-body" style="display:'+(expanded ? 'block' : 'none')+';padding:0;">';

    // Progress bar
    h += '<div class="cov-progress" style="margin:0 14px 8px;"><div class="cov-progress-bar" style="width:'+cat.pct+'%;background:'+(cat.pct >= 100 ? '#16a34a' : cat.pct > 50 ? '#ca8a04' : '#dc2626')+';"></div></div>';

    h += '<table class="cov-item-table"><thead><tr>';
    h += '<th>Menu Item</th><th>Qty Sold</th><th>Revenue</th><th>Avg/Day</th><th></th>';
    h += '</tr></thead><tbody>';

    for (var j=0;j<catItems.length;j++){
      var it = catItems[j];
      var cls = it.has_recipe ? 'has-recipe' : 'needs-recipe';
      var dot = it.has_recipe ? '<span class="cov-status-dot green"></span>' : '<span class="cov-status-dot red"></span>';
      h += '<tr class="'+cls+'">';
      h += '<td>'+dot+esc(it.name);
      if (it.toast_variants && it.toast_variants.length){
        h += ' <span style="font-size:10px;color:var(--muted);">('+it.toast_variants.length+' variant'+(it.toast_variants.length>1?'s':'')+')</span>';
      }
      h += '</td>';
      h += '<td>'+it.total_qty.toLocaleString()+'</td>';
      h += '<td>$'+it.total_revenue.toLocaleString()+'</td>';
      h += '<td>'+it.avg_daily_qty+'</td>';
      h += '<td>';
      if (it.has_recipe){
        h += '<span style="font-size:11px;color:#16a34a;font-weight:600;">Done</span>';
      } else {
        h += '<button class="cov-add-btn" onclick="createRecipeFromCoverage(\''+esc(it.name).replace(/'/g,"\\'")+'\')">+ Add Recipe</button>';
      }
      h += '</td></tr>';
    }

    h += '</tbody></table></div></div>';
  }

  document.getElementById('coverage-content').innerHTML = h;
}

function toggleCovCat(header){
  var body = header.nextElementSibling;
  var arrow = header.querySelector('.cov-cat-stats span:last-child');
  if (body.style.display === 'none'){
    body.style.display = 'block';
    arrow.innerHTML = '&#9660;';
  } else {
    body.style.display = 'none';
    arrow.innerHTML = '&#9654;';
  }
}

function createRecipeFromCoverage(name){
  switchTab('recipes');
  EDITING = null;
  showModal({name: name, toast_menu_name: name, type: 'menu', menu_price: 0, portions: 1, ingredients: []});
}

// ── Modifiers Tab ──

var MODIFIERS = [];
var MODIFIERS_LOADED = false;
var MOD_EDITING = null;

async function loadModifiers(){
  var el = document.getElementById('modifiers-content');
  el.innerHTML = '<div style="text-align:center;padding:40px;color:var(--muted);">Loading modifiers...</div>';
  try {
    var resp = await fetch('/api/modifiers/data');
    if (!resp.ok) throw new Error('Failed to load modifiers');
    MODIFIERS = await resp.json();
    MODIFIERS_LOADED = true;
    renderModifiers();
  } catch(err){
    el.innerHTML = '<div style="text-align:center;padding:40px;color:#dc2626;">Error: '+err.message+'</div>';
  }
}

function renderModifiers(){
  var h = '';
  var addOns = MODIFIERS.filter(function(m){return m.type==='add-on';});
  var subs = MODIFIERS.filter(function(m){return m.type==='sub';});
  var totalCost = 0, matchedCount = 0, unmatchedCount = 0;
  for (var i=0;i<MODIFIERS.length;i++){
    var c = MODIFIERS[i].cost||{};
    totalCost += c.batch_cost||0;
    var ings = c.ingredients||[];
    for (var j=0;j<ings.length;j++){
      if (ings[j].matched) matchedCount++; else unmatchedCount++;
    }
  }

  // Summary
  h += '<div class="mod-summary">';
  h += '<div class="mod-stat"><div class="mod-val">'+MODIFIERS.length+'</div><div class="mod-lbl">Total Modifiers</div></div>';
  h += '<div class="mod-stat"><div class="mod-val">'+addOns.length+'</div><div class="mod-lbl">Add-Ons</div></div>';
  h += '<div class="mod-stat"><div class="mod-val">'+subs.length+'</div><div class="mod-lbl">Substitutions</div></div>';
  h += '<div class="mod-stat"><div class="mod-val">'+matchedCount+'/'+(matchedCount+unmatchedCount)+'</div><div class="mod-lbl">Priced Ingredients</div></div>';
  h += '</div>';

  // Group by option_group
  var groups = {};
  var groupOrder = [];
  for (var i=0;i<MODIFIERS.length;i++){
    var g = MODIFIERS[i].option_group || 'Other';
    if (!groups[g]){groups[g]=[];groupOrder.push(g);}
    groups[g].push(MODIFIERS[i]);
  }

  for (var gi=0;gi<groupOrder.length;gi++){
    var gName = groupOrder[gi];
    var gMods = groups[gName];
    h += '<div class="mod-group">';
    h += '<div class="mod-group-header" onclick="toggleModGroup(this)">';
    h += '<div><span class="mod-group-name">'+esc(gName)+'</span>';
    h += ' <span class="mod-group-count">'+gMods.length+' modifier'+(gMods.length!==1?'s':'')+'</span></div>';
    h += '<span style="font-size:12px;color:var(--muted);">&#9660;</span>';
    h += '</div>';
    h += '<div>';
    h += '<table class="mod-table"><thead><tr>';
    h += '<th>Modifier</th><th>Type</th><th>Price</th><th>Cost</th><th>Margin</th><th></th>';
    h += '</tr></thead><tbody>';

    for (var mi=0;mi<gMods.length;mi++){
      var m = gMods[mi];
      var cost = (m.cost||{}).batch_cost||0;
      var price = m.menu_price||0;
      var margin = price > 0 ? ((price - cost)/price*100).toFixed(0) : '-';
      var typeClass = m.type === 'add-on' ? 'add-on' : (m.type === 'sub' ? 'sub' : 'remove');
      var typeLabel = m.type === 'add-on' ? 'Add-On' : (m.type === 'sub' ? 'Sub' : 'Remove');

      h += '<tr>';
      h += '<td style="font-weight:600;">'+esc(m.name)+'</td>';
      h += '<td><span class="mod-type-badge '+typeClass+'">'+typeLabel+'</span></td>';
      h += '<td>'+( price > 0 ? '$'+price.toFixed(2) : '-' )+'</td>';
      h += '<td>'+( cost > 0 ? '$'+cost.toFixed(2) : '<span style="color:var(--muted);">-</span>' )+'</td>';
      h += '<td>'+( margin !== '-' ? margin+'%' : '-' )+'</td>';
      h += '<td><button class="mod-edit-btn" onclick="editModifier(\''+esc(m.id)+'\')">Edit</button></td>';
      h += '</tr>';
    }
    h += '</tbody></table></div></div>';
  }

  document.getElementById('modifiers-content').innerHTML = h;
}

function toggleModGroup(header){
  var body = header.nextElementSibling;
  var arrow = header.querySelector('span:last-child');
  if (body.style.display === 'none'){
    body.style.display = 'block';
    arrow.innerHTML = '&#9660;';
  } else {
    body.style.display = 'none';
    arrow.innerHTML = '&#9654;';
  }
}

function editModifier(modId){
  var mod = null;
  for (var i=0;i<MODIFIERS.length;i++){
    if (MODIFIERS[i].id === modId){mod = MODIFIERS[i]; break;}
  }
  if (!mod) return;
  MOD_EDITING = mod;
  showModModal(mod);
}

function newModifier(){
  MOD_EDITING = null;
  showModModal({name:'', option_group:'Salad Add-ons', type:'add-on', menu_price:0, ingredients:[]});
}

function showModModal(mod){
  document.getElementById('mod-modal-title').textContent = MOD_EDITING ? 'Edit Modifier' : 'New Modifier';
  document.getElementById('mm-id').value = mod.id || '';
  document.getElementById('mm-name').value = mod.name || '';
  document.getElementById('mm-group').value = mod.option_group || '';
  document.getElementById('mm-type').value = mod.type || 'add-on';
  document.getElementById('mm-price').value = mod.menu_price || '';

  var container = document.getElementById('mm-ingredients');
  container.innerHTML = '';
  var ings = mod.ingredients || [];
  for (var i=0;i<ings.length;i++){
    addModIngRow(ings[i]);
  }
  if (!ings.length) addModIngRow({});

  document.getElementById('mm-btn-delete').style.display = MOD_EDITING ? 'inline-block' : 'none';
  document.getElementById('mod-modal-overlay').style.display = 'flex';
  updateModCosts();
}

function closeModModal(){
  document.getElementById('mod-modal-overlay').style.display = 'none';
}

function addModIngRow(ing){
  var container = document.getElementById('mm-ingredients');
  var div = document.createElement('div');
  div.className = 'ing-row';
  div.innerHTML = '<span class="ing-match unknown"></span>'
    + '<input class="ing-name" placeholder="Item name" value="'+esc(ing.item||'')+'" oninput="updateModCosts()">'
    + '<input class="ing-qty" type="number" step="0.1" placeholder="Qty" value="'+(ing.quantity||'')+'" oninput="updateModCosts()">'
    + '<input class="ing-uom" placeholder="uom" value="'+esc(ing.uom||'')+'">'
    + '<input class="ing-yield" type="number" step="0.01" placeholder="Yield" value="'+(ing.yield_pct && ing.yield_pct !== 1 ? ing.yield_pct : '')+'">'
    + '<span class="ing-line-cost">-</span>'
    + '<button class="ing-remove" onclick="this.parentElement.remove();updateModCosts();">&times;</button>';
  container.appendChild(div);

  // Autocomplete
  var nameInput = div.querySelector('.ing-name');
  setupAutocomplete(nameInput);
}

function updateModCosts(){
  var rows = document.querySelectorAll('#mm-ingredients .ing-row');
  var total = 0;
  for (var i=0;i<rows.length;i++){
    var name = rows[i].querySelector('.ing-name').value.trim().toLowerCase();
    var qty = parseFloat(rows[i].querySelector('.ing-qty').value) || 0;
    var yld = parseFloat(rows[i].querySelector('.ing-yield').value) || 1;
    var dot = rows[i].querySelector('.ing-match');
    var costEl = rows[i].querySelector('.ing-line-cost');
    var info = ITEMS_MAP[name];
    if (info && info.price_per_unit > 0){
      dot.className = 'ing-match matched';
      var lineCost = info.price_per_unit * qty / yld;
      total += lineCost;
      costEl.textContent = '$'+lineCost.toFixed(2);
      costEl.className = 'ing-line-cost has-cost';
    } else if (name) {
      dot.className = 'ing-match unmatched';
      costEl.textContent = '-';
      costEl.className = 'ing-line-cost';
    } else {
      dot.className = 'ing-match unknown';
      costEl.textContent = '-';
      costEl.className = 'ing-line-cost';
    }
  }
  var price = parseFloat(document.getElementById('mm-price').value) || 0;
  var margin = price > 0 ? ((price - total)/price*100).toFixed(1) : '-';
  document.getElementById('mm-cost-display').innerHTML =
    '<div class="cost-item"><div class="cost-val">$'+total.toFixed(2)+'</div><div class="cost-lbl">Cost</div></div>'
    + '<div class="cost-item"><div class="cost-val">'+(margin !== '-' ? margin+'%' : '-')+'</div><div class="cost-lbl">Margin</div></div>';
}

async function saveModifier(){
  var mod = {
    id: document.getElementById('mm-id').value || undefined,
    name: document.getElementById('mm-name').value.trim(),
    option_group: document.getElementById('mm-group').value.trim(),
    type: document.getElementById('mm-type').value,
    menu_price: parseFloat(document.getElementById('mm-price').value) || 0,
    ingredients: []
  };
  if (!mod.name){alert('Name is required'); return;}

  var rows = document.querySelectorAll('#mm-ingredients .ing-row');
  for (var i=0;i<rows.length;i++){
    var item = rows[i].querySelector('.ing-name').value.trim();
    if (!item) continue;
    var ing = {item: item, quantity: parseFloat(rows[i].querySelector('.ing-qty').value)||0,
      uom: rows[i].querySelector('.ing-uom').value.trim()};
    var yld = parseFloat(rows[i].querySelector('.ing-yield').value);
    if (yld && yld !== 1) ing.yield_pct = yld;
    mod.ingredients.push(ing);
  }

  try {
    var resp = await fetch('/api/modifiers/save', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(mod)});
    if (!resp.ok) throw new Error('Save failed');
    closeModModal();
    MODIFIERS_LOADED = false;
    loadModifiers();
  } catch(err){
    alert('Error saving: '+err.message);
  }
}

async function deleteModifier(){
  if (!MOD_EDITING || !MOD_EDITING.id) return;
  if (!confirm('Delete "'+MOD_EDITING.name+'"?')) return;
  try {
    var resp = await fetch('/api/modifiers/delete', {method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({id:MOD_EDITING.id})});
    if (!resp.ok) throw new Error('Delete failed');
    closeModModal();
    MODIFIERS_LOADED = false;
    loadModifiers();
  } catch(err){
    alert('Error: '+err.message);
  }
}

loadData();
"""


def build_recipe_page(logo_b64: str = "") -> str:
    """Build the recipe costing page HTML."""
    logo_html = ""
    if logo_b64:
        logo_html = (
            f'<img src="data:image/png;base64,{logo_b64}" '
            'style="height:28px;margin-bottom:8px;" alt="Livite">'
        )

    nav_html = (
        '<div class="recipe-nav">'
        '<a href="/">Dashboard</a>'
        '<a href="/schedule">Schedule</a>'
        '<a href="/prices/">Vendor Prices</a>'
        '<a href="/recipes" class="active">Recipes</a>'
        '</div>'
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Livite - Recipe Costing</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
{_CSS}
{_RECIPE_CSS}
</style>
</head>
<body>

<div style="text-align:center;padding:24px 16px 8px;">
  {logo_html}
  <h1 style="font-size:22px;margin:8px 0 4px;">Recipe Costing</h1>
  {nav_html}
</div>

<div id="loading" style="text-align:center;padding:40px 16px;">
  <div id="loading-text" style="color:var(--muted);font-size:14px;">Loading...</div>
</div>

<div id="content" style="display:none;">
  <div class="page-tabs">
    <button class="page-tab active" data-tab="recipes" onclick="switchTab('recipes')">Recipes</button>
    <button class="page-tab" data-tab="modifiers" onclick="switchTab('modifiers')">Modifiers</button>
    <button class="page-tab" data-tab="coverage" onclick="switchTab('coverage')">Menu Coverage</button>
  </div>

  <div id="recipes-view">
    <div class="card" id="overview-card">
      <div class="card-title">Overview</div>
      <div id="overview"></div>
    </div>
    <div id="book-filter"></div>
    <div id="table-container"></div>
  </div>

  <div id="modifiers-view" style="display:none;">
    <div class="card">
      <div class="card-title" style="display:flex;justify-content:space-between;align-items:center;">
        <span>Modifiers &amp; Add-Ons</span>
        <button class="add-btn" onclick="newModifier()">+ New Modifier</button>
      </div>
      <p style="color:var(--muted);font-size:13px;margin:4px 0 12px;">
        Track ingredient costs for every add-on, substitution, and upcharge. Grouped by Toast POS option group.
      </p>
      <div id="modifiers-content"></div>
    </div>
  </div>

  <div id="coverage-view" style="display:none;">
    <div class="card">
      <div class="card-title">Menu Coverage Tracker</div>
      <p style="color:var(--muted);font-size:13px;margin:4px 0 12px;">
        Cross-references all Toast POS sales data with your recipe database. Items in green have recipes; red items still need to be entered.
      </p>
      <div id="coverage-content"></div>
    </div>
  </div>
</div>

<!-- Add/Edit Modal -->
<div id="modal-overlay" class="modal-overlay" onclick="if(event.target===this)closeModal()">
  <div class="modal" id="modal">
    <h2 id="modal-title">Recipe</h2>
    <input type="hidden" id="m-id">
    <div style="display:flex;gap:12px;">
      <div class="form-group" style="flex:2;">
        <label>Recipe Name</label>
        <input type="text" id="m-name" placeholder="e.g. Buffalo Chicken Wrap">
      </div>
      <div class="form-group" style="flex:1;">
        <label>Recipe Book</label>
        <input type="text" id="m-book" placeholder="e.g. Wraps" list="book-list">
        <datalist id="book-list">
          <option value="Wraps">
          <option value="Salads">
          <option value="Bowls">
          <option value="Smoothies">
          <option value="Juices">
          <option value="Soup">
          <option value="Snacks">
          <option value="Prep">
          <option value="Dressings">
          <option value="Matcha">
          <option value="Beverages">
        </datalist>
      </div>
    </div>
    <div class="form-group">
      <label>Toast Menu Name <span style="font-weight:400;font-size:10px;color:var(--muted);">(select from Toast POS items)</span></label>
      <div style="position:relative;">
        <input type="text" id="m-toast-name" placeholder="Search Toast menu items..."
               autocomplete="off"
               oninput="showToastAutocomplete(this)"
               onfocus="showToastAutocomplete(this)"
               onkeydown="toastAcKeydown(event,this)">
      </div>
    </div>
    <div style="display:flex;gap:12px;">
      <div class="form-group" style="flex:1;">
        <label>Type</label>
        <select id="m-type">
          <option value="menu">Menu Item</option>
          <option value="prep">Prep Recipe</option>
        </select>
      </div>
      <div class="form-group" style="flex:1;">
        <label>Menu Price ($)</label>
        <input type="number" id="m-price" step="0.01" placeholder="14.95" oninput="updateCosts()">
      </div>
      <div class="form-group" style="flex:1;">
        <label>Portions</label>
        <input type="number" id="m-portions" min="1" value="1" oninput="updateCosts()">
      </div>
    </div>
    <div style="display:flex;gap:12px;">
      <div class="form-group" style="flex:1;">
        <label>Prep Time (min)</label>
        <input type="number" id="m-prep-time" min="0" placeholder="10">
      </div>
      <div class="form-group" style="flex:1;">
        <label>Cook Time (min)</label>
        <input type="number" id="m-cook-time" min="0" placeholder="15">
      </div>
      <div class="form-group" style="flex:2;">
        <label>Equipment</label>
        <input type="text" id="m-equipment" placeholder="e.g. Grill, mixing bowl">
      </div>
    </div>
    <div class="form-group">
      <label>Ingredients</label>
      <div style="display:flex;gap:6px;margin-bottom:6px;font-size:10px;color:var(--muted);text-transform:uppercase;padding:0 8px;">
        <div style="width:8px;"></div>
        <div style="flex:3;min-width:140px;">Item</div>
        <div style="flex:0 0 60px;">Qty</div>
        <div style="flex:0 0 60px;">UOM</div>
        <div style="flex:0 0 60px;">Yield</div>
        <div style="min-width:55px;text-align:right;">Cost</div>
        <div style="width:30px;">Sub</div>
        <div style="width:24px;"></div>
      </div>
      <div id="ing-container"></div>
      <button type="button" class="add-btn" style="font-size:11px;padding:4px 12px;margin-top:4px;" onclick="addIngredientRow({{}})">+ Add Ingredient</button>
      <div id="cost-summary" class="cost-summary"></div>
    </div>
    <div class="form-group">
      <label>Instructions <span style="font-weight:400;font-size:10px;color:var(--muted);">(optional, for recipe cards)</span></label>
      <div id="steps-container"></div>
      <button type="button" class="add-btn" style="font-size:11px;padding:4px 12px;margin-top:4px;" onclick="addStepRow('')">+ Add Step</button>
    </div>
    <div class="form-group">
      <label>Allergens</label>
      <div id="m-allergens" class="allergen-grid">
        <label class="allergen-check" data-allergen="dairy" onclick="this.classList.toggle('checked');this.querySelector('input').checked=this.classList.contains('checked');"><input type="checkbox"> Dairy</label>
        <label class="allergen-check" data-allergen="eggs" onclick="this.classList.toggle('checked');this.querySelector('input').checked=this.classList.contains('checked');"><input type="checkbox"> Eggs</label>
        <label class="allergen-check" data-allergen="fish" onclick="this.classList.toggle('checked');this.querySelector('input').checked=this.classList.contains('checked');"><input type="checkbox"> Fish</label>
        <label class="allergen-check" data-allergen="shellfish" onclick="this.classList.toggle('checked');this.querySelector('input').checked=this.classList.contains('checked');"><input type="checkbox"> Shellfish</label>
        <label class="allergen-check" data-allergen="tree_nuts" onclick="this.classList.toggle('checked');this.querySelector('input').checked=this.classList.contains('checked');"><input type="checkbox"> Tree Nuts</label>
        <label class="allergen-check" data-allergen="peanuts" onclick="this.classList.toggle('checked');this.querySelector('input').checked=this.classList.contains('checked');"><input type="checkbox"> Peanuts</label>
        <label class="allergen-check" data-allergen="wheat" onclick="this.classList.toggle('checked');this.querySelector('input').checked=this.classList.contains('checked');"><input type="checkbox"> Wheat/Gluten</label>
        <label class="allergen-check" data-allergen="soy" onclick="this.classList.toggle('checked');this.querySelector('input').checked=this.classList.contains('checked');"><input type="checkbox"> Soy</label>
      </div>
    </div>
    <div class="form-group">
      <label>Product Photo <span style="font-weight:400;font-size:10px;color:var(--muted);">(optional)</span></label>
      <div style="display:flex;gap:12px;align-items:center;">
        <input type="file" id="m-photo-input" accept="image/*" onchange="previewPhoto(this)" style="font-size:12px;">
        <img id="m-photo-preview" style="max-height:60px;border-radius:6px;display:none;">
      </div>
    </div>
    <div class="btn-row">
      <button class="btn-save" onclick="saveRecipe()">Save Recipe</button>
      <button class="btn-cancel" onclick="closeModal()">Cancel</button>
      <button class="btn-delete" id="btn-delete" onclick="deleteRecipe()" style="display:none;">Delete</button>
    </div>
  </div>
</div>

<!-- Modifier Add/Edit Modal -->
<div id="mod-modal-overlay" class="modal-overlay" onclick="if(event.target===this)closeModModal()">
  <div class="modal">
    <h2 id="mod-modal-title">Modifier</h2>
    <input type="hidden" id="mm-id">
    <div class="form-group">
      <label>Modifier Name</label>
      <input type="text" id="mm-name" placeholder="e.g. Extra Chicken">
    </div>
    <div style="display:flex;gap:12px;">
      <div class="form-group" style="flex:1;">
        <label>Option Group</label>
        <input type="text" id="mm-group" placeholder="e.g. Salad Add-ons" list="og-list">
        <datalist id="og-list">
          <option value="Salad Add-ons">
          <option value="Wrap Add-Ons">
          <option value="Smoothie Add-Ons">
          <option value="Soup Add Ons">
          <option value="Extra Sauce/Dressing">
          <option value="Wraps">
          <option value="Grain Option">
          <option value="Lettuce Choice">
          <option value="Substitute Smoothie Base">
          <option value="Remove Chicken">
        </datalist>
      </div>
      <div class="form-group" style="flex:0 0 120px;">
        <label>Type</label>
        <select id="mm-type">
          <option value="add-on">Add-On</option>
          <option value="sub">Substitution</option>
          <option value="remove">Removal</option>
        </select>
      </div>
      <div class="form-group" style="flex:0 0 100px;">
        <label>Upcharge ($)</label>
        <input type="number" id="mm-price" step="0.25" placeholder="0.00" oninput="updateModCosts()">
      </div>
    </div>
    <div class="form-group">
      <label>Ingredients</label>
      <div style="display:flex;gap:6px;margin-bottom:6px;font-size:10px;color:var(--muted);text-transform:uppercase;padding:0 8px;">
        <div style="width:8px;"></div>
        <div style="flex:3;min-width:140px;">Item</div>
        <div style="flex:0 0 60px;">Qty</div>
        <div style="flex:0 0 60px;">UOM</div>
        <div style="flex:0 0 60px;">Yield</div>
        <div style="min-width:55px;text-align:right;">Cost</div>
        <div style="width:24px;"></div>
      </div>
      <div id="mm-ingredients"></div>
      <button type="button" class="add-btn" style="font-size:11px;padding:4px 12px;margin-top:4px;" onclick="addModIngRow({{}})">+ Add Ingredient</button>
      <div id="mm-cost-display" class="cost-summary"></div>
    </div>
    <div class="btn-row">
      <button class="btn-save" onclick="saveModifier()">Save Modifier</button>
      <button class="btn-cancel" onclick="closeModModal()">Cancel</button>
      <button class="btn-delete" onclick="deleteModifier()" style="display:none;" id="mm-btn-delete">Delete</button>
    </div>
  </div>
</div>

<script>
{_JS_TEMPLATE}
</script>

</body>
</html>"""
