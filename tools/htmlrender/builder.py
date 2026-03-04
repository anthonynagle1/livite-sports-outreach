"""Main dashboard builder: assembles sections into complete HTML."""

from __future__ import annotations


from .components import _next_chart_id, _reset_chart_counter, _g, _gl, _safe, fmt_currency, render_divider
from .sections import (
    render_header, render_comparison_bar, render_anomalies,
    render_executive_summary, render_revenue_channels,
    render_order_intelligence, render_baskets_crosssell,
    render_modifiers, render_kitchen_speed,
    render_labor_staffing, render_payments,
    render_customers, render_footer,
    render_analyst_insights, render_weather_seasonality,
    render_weather_analysis,
    render_daily_trends, render_daily_table,
    _render_date_picker, _CSS, _LOADING_SNIPPET,
)

def _chat_fab(date_str: str, metrics_context: str = "") -> str:
    """Laurie 2.0 chat panel embedded in dashboard as slide-out sidebar."""
    ds = _safe(date_str)
    import json as _json
    # Truncate context to avoid JS parse issues on large range dashboards
    if len(metrics_context) > 8000:
        metrics_context = metrics_context[:8000] + "\n[truncated]"
    ctx_json = _json.dumps(metrics_context)  # JS-safe string
    return """
<style>
#laurie-fab{position:fixed;bottom:20px;right:20px;width:60px;height:60px;background:#475417;
  border:none;border-radius:50%;cursor:pointer;z-index:10000;padding:0;
  box-shadow:0 4px 12px rgba(0,0,0,0.2);transition:transform 0.15s;overflow:hidden;
  color:#fff;font-size:11px;font-weight:700;font-family:'DM Sans',sans-serif;
  display:flex;align-items:center;justify-content:center;}
#laurie-fab:hover{transform:scale(1.1);}
#laurie-fab img{width:100%;height:100%;object-fit:cover;border-radius:50%;position:absolute;top:0;left:0;pointer-events:none;}
#laurie-panel{position:fixed;top:0;right:-420px;width:400px;height:100vh;
  background:#F5EDDC;border-left:1px solid #e2d9c8;z-index:9999;
  display:flex;flex-direction:column;transition:right 0.3s ease;
  box-shadow:-4px 0 20px rgba(0,0,0,0.15);}
#laurie-panel.open{right:0;}
.lp-header{background:#fff;border-bottom:1px solid #e2d9c8;padding:12px 16px;
  display:flex;align-items:center;justify-content:space-between;flex-shrink:0;}
.lp-header-left{display:flex;align-items:center;gap:10px;}
.lp-header-left img{width:36px;height:auto;flex-shrink:0;}
.lp-title{font-size:16px;font-weight:700;color:#475417;font-family:'DM Sans',sans-serif;}
.lp-close{background:none;border:none;font-size:22px;cursor:pointer;color:#7a7a6f;padding:4px 8px;}
.lp-close:hover{color:#475417;}
.lp-body{flex:1;display:flex;overflow:hidden;}
.lp-laurie{width:120px;flex-shrink:0;display:flex;flex-direction:column;
  align-items:center;justify-content:flex-end;padding:10px 4px 0;}
.lp-laurie img{width:110px;height:auto;transform-origin:bottom center;}
.lp-chat-col{flex:1;display:flex;flex-direction:column;min-width:0;}
.lp-messages{flex:1;overflow-y:auto;padding:12px;font-family:'DM Sans',sans-serif;}
.lp-msg{margin-bottom:12px;display:flex;align-items:flex-start;}
.lp-msg.user{justify-content:flex-end;}
.lp-msg.user .lp-bubble{background:#475417;color:#fff;border-radius:14px 14px 4px 14px;}
.lp-msg.ai .lp-bubble{background:#fff;color:#1a2e05;border-radius:14px 14px 14px 4px;border:1px solid #e2d9c8;}
.lp-bubble{padding:8px 14px;max-width:90%;font-size:13px;line-height:1.45;
  white-space:pre-wrap;word-wrap:break-word;font-family:'DM Sans',sans-serif;}
.lp-typing{color:#7a7a6f;font-size:12px;font-style:italic;padding:6px 14px;}
.lp-input{background:#fff;border-top:1px solid #e2d9c8;padding:10px 12px;flex-shrink:0;}
.lp-input-row{display:flex;gap:6px;align-items:flex-end;}
.lp-input-row textarea{flex:1;padding:8px 12px;font-size:13px;border:1px solid #e2d9c8;
  border-radius:8px;background:#faf6ee;color:#1a2e05;font-family:'DM Sans',sans-serif;
  resize:none;min-height:38px;max-height:100px;line-height:1.4;}
.lp-input-row textarea:focus{outline:none;border-color:#475417;}
.lp-send{padding:8px 14px;background:#475417;color:#fff;border:none;border-radius:8px;
  font-size:13px;font-weight:600;cursor:pointer;font-family:'DM Sans',sans-serif;}
.lp-send:hover{background:#3d6819;}
.lp-send:disabled{background:#c4b89a;cursor:not-allowed;}
.lp-chip{background:#faf6ee;border:1px solid #e2d9c8;border-radius:16px;padding:5px 12px;
  font-size:12px;color:#475417;cursor:pointer;font-family:'DM Sans',sans-serif;
  transition:background 0.15s,border-color 0.15s;}
.lp-chip:hover{background:#e8e0cc;border-color:#475417;}

@keyframes lp-sway{0%,100%{transform:rotate(0deg);}25%{transform:rotate(1.5deg);}75%{transform:rotate(-1.5deg);}}
@keyframes lp-headshake{0%,100%{transform:rotate(0deg);}15%{transform:rotate(2deg);}30%{transform:rotate(-2deg);}45%{transform:rotate(1.5deg);}60%{transform:rotate(-1deg);}75%{transform:rotate(0deg);}}
@keyframes lp-breathe{0%,100%{transform:scale(1);}50%{transform:scale(1.015);}}
.lp-anim-sway{animation:lp-sway 2.5s ease-in-out infinite;}
.lp-anim-headshake{animation:lp-headshake 1.8s ease-in-out infinite;}
.lp-anim-breathe{animation:lp-breathe 3s ease-in-out infinite;}

@keyframes toggle-hint{0%{box-shadow:0 0 0 0 rgba(71,84,23,0.3);}70%{box-shadow:0 0 0 6px rgba(71,84,23,0);}100%{box-shadow:0 0 0 0 rgba(71,84,23,0);}}
.toggle-hint{font-size:9px;color:var(--muted,#7a7a6f);margin-top:3px;letter-spacing:0.3px;opacity:0.7;}
.toggle-pulse{animation:toggle-hint 1.5s ease-out 1.5s 2;}

@media(max-width:600px){
  #laurie-panel{width:100vw;right:-100vw;}
  .lp-laurie{display:none;}
  .lp-chip{font-size:11px;padding:4px 10px;}
}
</style>

<button id="laurie-fab" onclick="toggleLauriePanel()" title="Ask Laurie 2.0">
  Ask<br>Laurie
  <img src="/static/avatar.png" alt="Laurie 2.0" onerror="this.style.display='none'">
</button>

<div id="laurie-panel">
  <div class="lp-header">
    <div class="lp-header-left">
      <img src="/static/avatar.png" alt="Laurie">
      <span class="lp-title">Laurie 2.0</span>
    </div>
    <button class="lp-close" onclick="toggleLauriePanel()">&times;</button>
  </div>
  <div class="lp-body">
    <div class="lp-laurie">
      <img src="/static/avatar.png" class="lp-anim-breathe" id="lpLaurieImg">
    </div>
    <div class="lp-chat-col">
      <div class="lp-messages" id="lpMessages">
        <div class="lp-msg ai"><div class="lp-bubble">Oh good, you're here. I've been sitting with these numbers ALL day.\n\nAsk me anything about what you see.</div></div>
        <div id="lpSuggestions" style="display:flex;flex-wrap:wrap;gap:6px;padding:4px 12px 8px;">
          <button class="lp-chip" onclick="lpAsk(this)">How did we do today?</button>
          <button class="lp-chip" onclick="lpAsk(this)">What were our top sellers?</button>
          <button class="lp-chip" onclick="lpAsk(this)">How's our labor looking?</button>
          <button class="lp-chip" onclick="lpAsk(this)">Any red flags?</button>
          <button class="lp-chip" onclick="lpAsk(this)">Channel breakdown</button>
          <button class="lp-chip" onclick="lpAsk(this)">Busiest hour?</button>
        </div>
      </div>
      <div class="lp-input">
        <div class="lp-input-row">
          <textarea id="lpQuestion" placeholder="Ask about this data..." rows="1"
            onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();lpSend();}"></textarea>
          <button class="lp-send" id="lpSendBtn" onclick="lpSend()">Send</button>
        </div>
      </div>
    </div>
  </div>
</div>

<script>
try {
var lpDate='""" + ds + """';
var lpContext=""" + ctx_json + """;
} catch(e) { var lpDate=''; var lpContext=''; console.warn('Laurie context parse error',e); }
var lpAnims=['lp-anim-sway','lp-anim-headshake','lp-anim-breathe'];
var lpAnimIdx=2;
setInterval(function(){
  var img=document.getElementById('lpLaurieImg');
  if(img){img.className=lpAnims[lpAnimIdx];lpAnimIdx=(lpAnimIdx+1)%lpAnims.length;}
},6000);

function toggleLauriePanel(){
  document.getElementById('laurie-panel').classList.toggle('open');
}
function lpAddMsg(role,text){
  var c=document.getElementById('lpMessages');
  var d=document.createElement('div');
  d.className='lp-msg '+role;
  var b=document.createElement('div');
  b.className='lp-bubble';
  b.textContent=text;
  d.appendChild(b);
  c.appendChild(d);
  c.scrollTop=c.scrollHeight;
  if(role==='ai'){
    var img=document.getElementById('lpLaurieImg');
    if(img){img.className='lp-anim-headshake';setTimeout(function(){img.className='lp-anim-breathe';},3000);}
  }
}
function lpAsk(btn){
  var q=btn.textContent;
  document.getElementById('lpQuestion').value=q;
  var s=document.getElementById('lpSuggestions');
  if(s)s.style.display='none';
  lpSend();
}
function lpSend(){
  var inp=document.getElementById('lpQuestion');
  var q=inp.value.trim();
  if(!q)return;
  lpAddMsg('user',q);
  inp.value='';inp.style.height='38px';
  var btn=document.getElementById('lpSendBtn');
  btn.disabled=true;
  var c=document.getElementById('lpMessages');
  var t=document.createElement('div');
  t.className='lp-typing';t.id='lpTyping';t.textContent='Thinking...';
  c.appendChild(t);c.scrollTop=c.scrollHeight;
  var img=document.getElementById('lpLaurieImg');
  if(img)img.className='lp-anim-sway';
  fetch('/api/chat',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({question:q,date:lpDate,model:'haiku',context:lpContext})
  })
  .then(function(r){
    if(!r.ok){return r.text().then(function(t){throw new Error('HTTP '+r.status+': '+t.substring(0,200));});}
    return r.json();
  })
  .then(function(data){
    var tp=document.getElementById('lpTyping');if(tp)tp.remove();
    var txt=data.error?('Error: '+data.error):data.answer;
    if(data.cost)txt+='\n\n('+data.cost+')';
    lpAddMsg('ai',txt);
    btn.disabled=false;
  })
  .catch(function(e){
    var tp=document.getElementById('lpTyping');if(tp)tp.remove();
    lpAddMsg('ai','Error: '+(e.message||'Something went wrong'));
    btn.disabled=false;
  });
}
document.getElementById('lpQuestion').addEventListener('input',function(){
  this.style.height='38px';this.style.height=Math.min(this.scrollHeight,100)+'px';
});
</script>"""


def build_dashboard(metrics: dict, comparisons: dict | None = None,
                    anomalies: list | None = None, date_str: str = "",
                    prev_date_str: str = "", next_date_str: str = "",
                    analyst_insights: list | None = None,
                    logo_b64: str = "",
                    chat_enabled: bool = False,
                    chat_context: str = "") -> str:
    """Assemble all sections into a complete, self-contained HTML document."""
    if comparisons is None:
        comparisons = {}

    date_display = _g(metrics, "date_display", default="")
    title = f"Livite Daily Dashboard &mdash; {_safe(date_display)}" if date_display else "Livite Daily Dashboard"

    # Reset chart counter for each dashboard build
    _reset_chart_counter()

    sections = []
    sections.append(render_header(metrics, logo_b64=logo_b64))

    # Home button (back to date picker)
    sections.append(
        '<div style="margin-bottom:12px;">'
        '<a href="/" style="color:var(--green);text-decoration:none;font-size:12px;font-weight:500;">'
        '\u2190 Dashboard Home</a></div>'
    )

    # Date navigation bar (daily mode: prev/next, range mode: date picker)
    is_range = metrics.get("is_range", False)
    if date_str and not is_range:
        nav_parts = ['<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;font-size:12px;">']
        if prev_date_str:
            nav_parts.append(
                f'<a href="/daily/{prev_date_str}" '
                f'style="color:var(--green);text-decoration:none;padding:6px 14px;border:1px solid var(--border);border-radius:6px;background:var(--surface2);">'
                f'\u2190 Prev Day</a>'
            )
        else:
            nav_parts.append('<span></span>')
        nav_parts.append(
            f'<span style="color:var(--muted);font-family:\'JetBrains Mono\',monospace;">{_safe(date_str)}</span>'
        )
        if next_date_str:
            nav_parts.append(
                f'<a href="/daily/{next_date_str}" '
                f'style="color:var(--green);text-decoration:none;padding:6px 14px;border:1px solid var(--border);border-radius:6px;background:var(--surface2);">'
                f'Next Day \u2192</a>'
            )
        else:
            nav_parts.append('<span></span>')
        nav_parts.append('</div>')
        sections.append("".join(nav_parts))

    # Date range picker (shown on all dashboards)
    sections.append(_render_date_picker())

    comp_bar = render_comparison_bar(comparisons)
    if comp_bar:
        sections.append(comp_bar)

    anomaly_html = render_anomalies(anomalies)
    if anomaly_html:
        sections.append(anomaly_html)

    exec_html = render_executive_summary(metrics, comparisons)
    if exec_html:
        sections.append(exec_html)

    # Analyst insights (fun callout cards)
    if analyst_insights:
        insights_html = render_analyst_insights(analyst_insights)
        if insights_html:
            sections.append(insights_html)

    # Daily trend charts + breakdown table (range dashboards only)
    if is_range:
        trends_html = render_daily_trends(metrics)
        if trends_html:
            sections.append(trends_html)
        table_html = render_daily_table(metrics)
        if table_html:
            sections.append(table_html)

    sections.append(render_divider())

    rev_html = render_revenue_channels(metrics, comparisons)
    if rev_html:
        sections.append(rev_html)

    oi_html = render_order_intelligence(metrics)
    if oi_html:
        sections.append(oi_html)

    sections.append(render_divider())

    basket_html = render_baskets_crosssell(metrics)
    if basket_html:
        sections.append(basket_html)

    mod_html = render_modifiers(metrics)
    if mod_html:
        sections.append(mod_html)

    sections.append(render_divider())

    kit_html = render_kitchen_speed(metrics)
    if kit_html:
        sections.append(kit_html)

    sections.append(render_divider())

    lab_html = render_labor_staffing(metrics, comparisons)
    if lab_html:
        sections.append(lab_html)

    sections.append(render_divider())

    pay_html = render_payments(metrics)
    if pay_html:
        sections.append(pay_html)

    cust_html = render_customers(metrics)
    if cust_html:
        sections.append(cust_html)

    weather_html = render_weather_seasonality(metrics)
    if weather_html:
        sections.append(weather_html)

    # Weather analysis workbench (range dashboards only)
    if is_range:
        wx_html = render_weather_analysis(metrics)
        if wx_html:
            sections.append(wx_html)

    sections.append(render_divider())
    sections.append(render_footer(metrics))

    # Strip consecutive/trailing dividers
    cleaned = []
    prev_was_divider = False
    for s in sections:
        if not s:
            continue
        is_divider = s.strip() == '<hr class="divider">'
        if is_divider and prev_was_divider:
            continue
        cleaned.append(s)
        prev_was_divider = is_divider

    if len(cleaned) >= 2 and cleaned[-2].strip() == '<hr class="divider">' and 'text-align:center' in cleaned[-1]:
        cleaned.pop(-2)

    body_content = "\n\n".join(cleaned)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=DM+Serif+Display&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
<style>
{_CSS}
</style>
</head>
<body>

{body_content}

<script>
/* ── Comparison Period Toggle (WoW / YoY / SWLY) ── */
function setPeriod(p) {{
  document.querySelectorAll('.period-btn').forEach(function(b) {{
    if (b.dataset.period === p) {{
      b.style.background = 'var(--livite-green)';
      b.style.color = 'var(--livite-cream)';
      b.style.fontWeight = '600';
      b.classList.add('active');
    }} else {{
      b.style.background = 'var(--surface2)';
      b.style.color = 'var(--muted)';
      b.style.fontWeight = 'normal';
      b.classList.remove('active');
    }}
  }});
  document.querySelectorAll('.period-detail').forEach(function(el) {{
    el.style.display = el.dataset.periodDetail === p ? 'block' : 'none';
  }});
  document.querySelectorAll('.delta-toggle').forEach(function(el) {{
    var html = el.dataset[p + 'Html'] || '';
    el.textContent = '';
    var temp = document.createElement('span');
    temp.innerHTML = html;
    while(temp.firstChild) el.appendChild(temp.firstChild);
  }});
}}

/* ── Item View Toggle (First Party / Third Party) ── */
function toggleItemView(target) {{
  document.querySelectorAll('.item-panel').forEach(function(el) {{
    el.style.display = 'none';
  }});
  var panel = document.getElementById(target);
  if (panel) panel.style.display = 'block';
  document.querySelectorAll('.item-toggle-btn').forEach(function(b) {{
    if (b.dataset.target === target) {{
      b.style.background = 'var(--livite-green)';
      b.style.color = 'var(--livite-cream)';
    }} else {{
      b.style.background = 'var(--surface2)';
      b.style.color = 'var(--muted)';
    }}
  }});
}}

// ── Mobile chart scaling ──
(function() {{
  if (window.innerWidth > 600) return;
  var charts = document.querySelectorAll('.lvc');
  for (var i = 0; i < charts.length; i++) {{
    var el = charts[i];
    var h = parseInt(el.style.height);
    if (h > 0) {{
      // Scale charts: tall ones shrink more, small ones stay reasonable
      var newH = Math.max(Math.round(h * 0.55), 100);
      el.style.height = newH + 'px';
    }}
  }}
  // Also reduce inline font sizes that are too large for mobile
  var allEls = document.querySelectorAll('[style]');
  for (var j = 0; j < allEls.length; j++) {{
    var s = allEls[j].style;
    // Shrink large inline font-sizes
    if (s.fontSize) {{
      var fs = parseInt(s.fontSize);
      if (fs >= 26) s.fontSize = '18px';
      else if (fs >= 20) s.fontSize = '16px';
      else if (fs >= 16) s.fontSize = '13px';
    }}
    // Shrink inline fixed widths that could overflow
    if (s.width && s.width.indexOf('px') > -1) {{
      var w = parseInt(s.width);
      if (w > 200) s.width = '100%';
      else if (w > 120) s.width = Math.round(w * 0.7) + 'px';
    }}
    // Fix inline padding
    if (s.padding && s.padding.indexOf('28px') > -1) {{
      s.padding = '10px 6px';
    }}
  }}
  // Trigger Chart.js resize after height changes
  setTimeout(function() {{
    if (window.Chart && Chart.instances) {{
      Object.values(Chart.instances).forEach(function(c) {{ c.resize(); }});
    }}
  }}, 100);
}})();
</script>

{_chat_fab(date_str, chat_context) if chat_enabled else ''}

{_LOADING_SNIPPET}
</body>
</html>"""

    return html
