"""Build the /trends page HTML — search form, loading state, report display."""

from __future__ import annotations

import html as _html


def build_trends_page(recent_reports=None) -> str:
    """Build the trends page HTML.

    Args:
        recent_reports: List of dicts from get_recent_reports()
                        ({title, url, created_time}).
    """
    reports = recent_reports or []

    # Build past reports HTML
    if reports:
        items = []
        for r in reports:
            date_str = r.get("created_time", "")[:10]
            title = _html.escape(r.get("title", "Untitled"))
            url = _html.escape(r.get("url", "#"))
            items.append(
                f'<a href="{url}" target="_blank" class="report-link">'
                f'<span class="rl-date">{date_str}</span>'
                f'<span class="rl-title">{title}</span>'
                f'</a>'
            )
        reports_html = "\n".join(items)
    else:
        reports_html = '<div class="empty">No reports yet. Run your first search!</div>'

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Trend Scout — Livite</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{font-family:'DM Sans',sans-serif;background:#F5EDDC;min-height:100vh;}}
.hdr{{background:#fff;border-bottom:1px solid #e2d9c8;padding:12px 20px;display:flex;align-items:center;justify-content:space-between;}}
.hdr-title{{font-size:18px;font-weight:700;color:#475417;}}
.hdr a{{color:#4a7c1f;text-decoration:none;font-size:13px;font-weight:500;}}
.hdr a:hover{{text-decoration:underline;}}
.wrap{{max-width:800px;margin:0 auto;padding:20px;}}
.card{{background:#fff;border:1px solid #e0d5bf;border-radius:12px;padding:20px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,.06);}}
.card h2{{font-size:12px;text-transform:uppercase;letter-spacing:1px;color:#7a7265;margin-bottom:14px;font-weight:600;}}
.fg{{display:flex;flex-direction:column;gap:12px;}}
.fr{{display:flex;gap:10px;align-items:flex-end;}}
.fl{{flex:1;}}
.fl label{{font-size:12px;color:#7a7265;font-weight:500;display:block;margin-bottom:4px;}}
.fl input,.fl select{{width:100%;padding:10px 14px;font-size:14px;border:1px solid #e2d9c8;border-radius:8px;background:#faf6ee;color:#1a2e05;font-family:inherit;}}
.fl input:focus{{outline:none;border-color:#4a7c1f;}}
.hint{{font-size:11px;color:#7a7265;margin-top:2px;}}
.btn{{padding:10px 24px;background:#4a7c1f;color:#fff;border:none;border-radius:8px;font-size:14px;font-weight:600;cursor:pointer;font-family:inherit;white-space:nowrap;}}
.btn:hover{{background:#3d6819;}}
.btn:disabled{{background:#c4b89a;cursor:not-allowed;}}

/* Loading */
.ld{{display:none;text-align:center;padding:40px 20px;}}
.ld.on{{display:block;}}
.sp{{width:40px;height:40px;border:3px solid #e2d9c8;border-top:3px solid #4a7c1f;border-radius:50%;animation:spin .8s linear infinite;margin:0 auto;}}
@keyframes spin{{0%{{transform:rotate(0)}}100%{{transform:rotate(360deg)}}}}
.ld-msg{{color:#7a7265;font-size:14px;margin-top:12px;}}
.ld-sub{{color:#a0a090;font-size:11px;margin-top:4px;}}

/* Report */
.rpt{{display:none;}}
.rpt.on{{display:block;}}
.rc{{background:#faf6ee;border:1px solid #e0d5bf;border-radius:8px;padding:12px;margin-bottom:8px;font-size:13px;line-height:1.5;}}
.tag{{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:500;margin-right:4px;}}
.tag-hot{{background:rgba(217,52,43,.10);color:#d9342b;}}
.tag-warm{{background:rgba(196,125,10,.10);color:#c47d0a;}}
.tag-emerging{{background:rgba(74,124,31,.10);color:#4a7c1f;}}
.tag-easy{{background:rgba(74,124,31,.10);color:#4a7c1f;}}
.tag-medium{{background:rgba(196,125,10,.10);color:#c47d0a;}}
.tag-hard{{background:rgba(217,52,43,.10);color:#d9342b;}}
.nlink{{display:inline-block;margin-top:8px;color:#4a7c1f;font-size:12px;font-weight:500;text-decoration:none;}}
.nlink:hover{{text-decoration:underline;}}

/* Past reports */
.report-link{{display:flex;align-items:center;gap:10px;padding:8px 12px;border-bottom:1px solid #e0d5bf;text-decoration:none;color:inherit;}}
.report-link:hover{{background:#faf6ee;}}
.report-link:last-child{{border-bottom:none;}}
.rl-date{{font-size:12px;color:#7a7265;min-width:80px;}}
.rl-title{{font-size:13px;color:#2d2a24;font-weight:500;}}
.empty{{color:#7a7265;font-size:13px;text-align:center;padding:12px;}}

/* Stats bar */
.stats-bar{{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:14px;}}
.stat-pill{{background:#faf6ee;border:1px solid #e0d5bf;border-radius:20px;padding:6px 14px;font-size:12px;color:#2d2a24;}}
.stat-pill strong{{color:#475417;}}

/* Source posts */
.hashtag-hdr{{display:flex;align-items:center;justify-content:space-between;cursor:pointer;padding:8px 0;border-bottom:1px solid #e0d5bf;margin-bottom:10px;}}
.hashtag-hdr:hover{{color:#4a7c1f;}}
.hashtag-hdr h3{{font-size:14px;font-weight:600;margin:0;}}
.hashtag-hdr .toggle{{font-size:12px;color:#7a7265;}}
.post-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:10px;margin-bottom:16px;}}
.post-card{{background:#faf6ee;border:1px solid #e0d5bf;border-radius:8px;overflow:hidden;font-size:12px;}}
.post-card img{{width:100%;height:160px;object-fit:cover;display:block;background:#e8e2d6;}}
.post-card .pc-body{{padding:8px 10px;}}
.post-card .pc-meta{{display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;}}
.post-card .pc-user{{font-weight:600;color:#475417;}}
.post-card .pc-eng{{color:#7a7265;font-size:11px;}}
.post-card .pc-cap{{color:#2d2a24;line-height:1.4;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden;}}
.post-card a{{text-decoration:none;color:inherit;display:block;}}
.post-card a:hover{{background:#f0eadc;}}
.no-img{{height:60px;display:flex;align-items:center;justify-content:center;background:#e8e2d6;color:#7a7265;font-size:11px;}}

@media(max-width:600px){{
    .fr{{flex-direction:column;}}
    .wrap{{padding:12px;}}
    .card{{padding:16px;}}
    .post-grid{{grid-template-columns:1fr 1fr;}}
}}
</style></head>
<body>
<div class="hdr">
<span class="hdr-title">Trend Scout</span>
<a href="/">Dashboard</a>
</div>

<div class="wrap">

<div class="card">
<h2>Search Instagram Food Trends</h2>
<div class="fg">
<div class="fl">
<label>Search Terms</label>
<input type="text" id="terms" placeholder="birria tacos, new sauces, plating trends" />
<div class="hint">Comma-separated. Each term becomes a hashtag search.</div>
</div>
<div class="fr">
<div class="fl" style="max-width:200px;">
<label>Results per term</label>
<select id="limit">
<option value="15">15 (faster)</option>
<option value="30" selected>30 (default)</option>
<option value="50">50 (thorough)</option>
</select>
</div>
<button class="btn" id="goBtn" onclick="go()">Search &amp; Analyze</button>
</div>
</div>
</div>

<div class="card ld" id="ld">
<div class="sp"></div>
<div class="ld-msg" id="ldMsg">Scraping Instagram hashtags...</div>
<div class="ld-sub">This typically takes 30-45 seconds</div>
</div>

<div class="rpt" id="rpt"></div>

<div class="card">
<h2>Past Reports</h2>
{reports_html}
</div>

</div>

<script>
var goBtn=document.getElementById('goBtn'),
    ld=document.getElementById('ld'),
    ldMsg=document.getElementById('ldMsg'),
    rpt=document.getElementById('rpt');

var msgs=['Scraping Instagram hashtags...','Gathering post data from Apify...',
  'Analyzing engagement metrics...','Sending data to Claude for analysis...',
  'Identifying trends and recipe ideas...','Building your report...'];
var mi=0,iv=null;

function startP(){{mi=0;ldMsg.textContent=msgs[0];iv=setInterval(function(){{mi=Math.min(mi+1,msgs.length-1);ldMsg.textContent=msgs[mi];}},6000);}}
function stopP(){{if(iv)clearInterval(iv);}}

function go(){{
  var raw=document.getElementById('terms').value.trim();
  if(!raw){{alert('Enter at least one search term');return;}}
  var terms=raw.split(',').map(function(t){{return t.trim();}}).filter(Boolean);
  var lim=parseInt(document.getElementById('limit').value);
  goBtn.disabled=true;
  ld.classList.add('on');
  rpt.classList.remove('on');
  rpt.innerHTML='';
  startP();
  fetch('/api/trends',{{
    method:'POST',
    headers:{{'Content-Type':'application/json'}},
    body:JSON.stringify({{terms:terms,results_limit:lim}})
  }})
  .then(function(r){{
    if(!r.ok)return r.json().then(function(d){{throw new Error(d.error||'Request failed');}});
    return r.json();
  }})
  .then(function(d){{stopP();ld.classList.remove('on');goBtn.disabled=false;render(d);}})
  .catch(function(e){{stopP();ld.classList.remove('on');goBtn.disabled=false;
    rpt.classList.add('on');
    rpt.innerHTML='<div class="card"><h2>Error</h2><p style="color:#d9342b;">'+e.message+'</p></div>';
  }});
}}

function esc(s){{var d=document.createElement('div');d.textContent=s;return d.innerHTML;}}

function fmtNum(n){{if(n>=1000)return (n/1000).toFixed(1)+'k';return n;}}

function toggleGroup(id){{
  var el=document.getElementById(id);
  if(!el)return;
  var vis=el.style.display!=='none';
  el.style.display=vis?'none':'block';
  var btn=el.previousElementSibling;
  if(btn){{var t=btn.querySelector('.toggle');if(t)t.textContent=vis?'Show':'Hide';}}
}}

function render(d){{
  rpt.classList.add('on');
  var a=d.analysis||{{}};
  var posts=d.posts||{{}};
  var h='';

  // Summary
  h+='<div class="card"><h2>Summary</h2>';
  h+='<p style="font-size:14px;line-height:1.6;">'+esc(a.summary||'No summary')+'</p>';
  h+='<div style="font-size:11px;color:#7a7265;margin-top:8px;">Posts analyzed: '+(d.total_posts||0)+'</div>';
  if(d.notion_url)h+='<br><a class="nlink" href="'+d.notion_url+'" target="_blank">View in Notion &rarr;</a>';
  h+='</div>';

  // Per-hashtag stats bar
  var terms=d.search_terms||[];
  var pc=d.post_counts||{{}};
  if(terms.length){{
    h+='<div class="card"><h2>Data Sources</h2><div class="stats-bar">';
    for(var ti=0;ti<terms.length;ti++){{
      var term=terms[ti];
      var tp=posts[term]||[];
      var cnt=pc[term]||tp.length;
      var totalLikes=0,totalComments=0;
      for(var pi=0;pi<tp.length;pi++){{totalLikes+=tp[pi].likes||0;totalComments+=tp[pi].comments||0;}}
      var avgL=cnt>0?Math.round(totalLikes/cnt):0;
      var avgC=cnt>0?Math.round(totalComments/cnt):0;
      h+='<div class="stat-pill"><strong>#'+esc(term.replace(/\\s+/g,'').toLowerCase())+'</strong> &mdash; ';
      h+=cnt+' posts | '+fmtNum(avgL)+' avg likes | '+fmtNum(avgC)+' avg comments</div>';
    }}
    h+='</div></div>';
  }}

  // Trending Themes
  if(a.trending_themes&&a.trending_themes.length){{
    h+='<div class="card"><h2>Trending Themes</h2>';
    a.trending_themes.forEach(function(t){{
      var c='tag-'+(t.strength||'warm');
      h+='<div class="rc"><span class="tag '+c+'">'+(t.strength||'').toUpperCase()+'</span> <strong>'+esc(t.theme)+'</strong><br>'+esc(t.description)+'</div>';
    }});
    h+='</div>';
  }}

  // Recipe Ideas
  if(a.recipe_ideas&&a.recipe_ideas.length){{
    h+='<div class="card"><h2>Recipe Ideas</h2>';
    a.recipe_ideas.forEach(function(r){{
      var c='tag-'+(r.difficulty||'medium');
      h+='<div class="rc"><span class="tag '+c+'">'+(r.difficulty||'medium').toUpperCase()+'</span> <strong>'+esc(r.name)+'</strong><br>'+esc(r.description)+'<br><em style="font-size:12px;color:#7a7265;">Why trending: '+esc(r.why_trending)+'</em></div>';
    }});
    h+='</div>';
  }}

  // Sauce Spotlight
  if(a.sauce_spotlight&&a.sauce_spotlight.length){{
    h+='<div class="card"><h2>Sauce Spotlight</h2>';
    a.sauce_spotlight.forEach(function(s){{
      h+='<div class="rc"><strong>'+esc(s.name)+'</strong>: '+esc(s.description)+'<br><em style="font-size:12px;color:#7a7265;">Pairs with: '+esc(s.pairs_with||'various')+'</em></div>';
    }});
    h+='</div>';
  }}

  // Ingredient Watch
  if(a.ingredient_watch&&a.ingredient_watch.length){{
    h+='<div class="card"><h2>Ingredient Watch</h2>';
    a.ingredient_watch.forEach(function(i){{
      h+='<div class="rc"><strong>'+esc(i.ingredient)+'</strong>: '+esc(i.trend)+'<br><em style="font-size:12px;color:#7a7265;">'+esc(i.usage_ideas||'')+'</em></div>';
    }});
    h+='</div>';
  }}

  // Action Items
  if(a.action_items&&a.action_items.length){{
    h+='<div class="card"><h2>Action Items</h2>';
    a.action_items.forEach(function(item){{
      h+='<div class="rc" style="padding:8px 12px;">&#9744; '+esc(item)+'</div>';
    }});
    h+='</div>';
  }}

  // Top Posts (enhanced with links + images)
  if(a.top_posts&&a.top_posts.length){{
    h+='<div class="card"><h2>Top Posts (Claude Picks)</h2>';
    a.top_posts.forEach(function(p){{
      // Try to find matching raw post for URL + image
      var rawPost=findPost(posts,p.owner);
      var postUrl=rawPost?rawPost.url:'';
      var imgUrl=rawPost?rawPost.image_url:'';
      h+='<div class="rc" style="display:flex;gap:10px;align-items:flex-start;">';
      if(imgUrl)h+='<a href="'+esc(postUrl)+'" target="_blank" rel="noopener" style="flex-shrink:0;"><img src="'+esc(imgUrl)+'" style="width:80px;height:80px;object-fit:cover;border-radius:6px;display:block;"></a>';
      h+='<div>';
      if(postUrl)h+='<a href="'+esc(postUrl)+'" target="_blank" rel="noopener" style="color:#475417;font-weight:600;text-decoration:none;">@'+esc(p.owner)+'</a>';
      else h+='<strong>@'+esc(p.owner)+'</strong>';
      h+=' <span style="color:#7a7265;font-size:12px;">('+fmtNum(p.likes||0)+' likes)</span>';
      h+='<br><span style="font-size:12px;">'+esc(p.why_notable||'')+'</span>';
      h+='</div></div>';
    }});
    h+='</div>';
  }}

  // Source Posts gallery — grouped by hashtag
  var hasAnyPosts=false;
  for(var k in posts){{if(posts[k]&&posts[k].length){{hasAnyPosts=true;break;}}}}
  if(hasAnyPosts){{
    h+='<div class="card"><h2>Source Posts</h2>';
    h+='<div style="font-size:12px;color:#7a7265;margin-bottom:12px;">Raw Instagram posts used to generate the analysis above. Sorted by engagement.</div>';
    var gi=0;
    for(var ti2=0;ti2<terms.length;ti2++){{
      var term2=terms[ti2];
      var tp2=posts[term2]||[];
      if(!tp2.length)continue;
      // Sort by engagement (likes + comments) descending
      var sorted=tp2.slice().sort(function(a,b){{return (b.likes+b.comments)-(a.likes+a.comments);}});
      var top10=sorted.slice(0,10);
      var gid='pg-'+gi;
      gi++;
      h+='<div class="hashtag-hdr" onclick="toggleGroup(\\''+gid+'\\')"><h3>#'+esc(term2.replace(/\\s+/g,'').toLowerCase())+' <span style="font-weight:400;color:#7a7265;font-size:12px;">('+tp2.length+' posts, showing top '+top10.length+')</span></h3><span class="toggle">Show</span></div>';
      h+='<div id="'+gid+'" style="display:none;"><div class="post-grid">';
      for(var pi2=0;pi2<top10.length;pi2++){{
        var p2=top10[pi2];
        var cap2=(p2.caption||'').substring(0,120);
        if(p2.caption&&p2.caption.length>120)cap2+='...';
        h+='<div class="post-card">';
        if(p2.url)h+='<a href="'+esc(p2.url)+'" target="_blank" rel="noopener">';
        if(p2.image_url)h+='<img src="'+esc(p2.image_url)+'" alt="" loading="lazy">';
        else h+='<div class="no-img">No image</div>';
        h+='<div class="pc-body"><div class="pc-meta"><span class="pc-user">@'+esc(p2.owner)+'</span><span class="pc-eng">'+fmtNum(p2.likes||0)+' likes</span></div>';
        h+='<div class="pc-cap">'+esc(cap2)+'</div></div>';
        if(p2.url)h+='</a>';
        h+='</div>';
      }}
      h+='</div></div>';
    }}
    h+='</div>';
  }}

  rpt.innerHTML=h;
}}

function findPost(posts,owner){{
  // Find a raw post by owner username
  for(var k in posts){{
    var arr=posts[k]||[];
    for(var i=0;i<arr.length;i++){{
      if(arr[i].owner===owner)return arr[i];
    }}
  }}
  return null;
}}
</script>
</body></html>"""
