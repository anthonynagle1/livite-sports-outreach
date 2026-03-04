"""Review Monitor — HTML page builder."""

from __future__ import annotations


def build_reviews_page() -> str:
    """Build the full Review Monitor HTML page.

    Reviews are fetched client-side via /api/reviews/fetch so the page
    loads instantly and shows a loading state while data arrives.
    """
    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Review Monitor — Livite</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
<style>
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{
    font-family:'DM Sans',sans-serif;
    background:#F5EDDC;
    color:#2d2a24;
    min-height:100vh;
}}
.header{{
    background:#fff;
    border-bottom:1px solid #e0d5bf;
    padding:14px 24px;
    display:flex;
    align-items:center;
    justify-content:space-between;
    position:sticky;top:0;z-index:100;
}}
.header .title{{font-size:20px;font-weight:700;color:#475417;}}
.header a{{color:#475417;text-decoration:none;font-size:13px;font-weight:500;}}
.header a:hover{{text-decoration:underline;}}
.container{{max-width:1100px;margin:0 auto;padding:20px;}}

/* Stats row */
.stats-row{{
    display:grid;
    grid-template-columns:repeat(4,1fr);
    gap:14px;
    margin-bottom:20px;
}}
@media(max-width:768px){{.stats-row{{grid-template-columns:repeat(2,1fr);}}}}
@media(max-width:480px){{.stats-row{{grid-template-columns:1fr;}}}}
.stat-card{{
    background:#fff;
    border:1px solid #e0d5bf;
    border-radius:12px;
    padding:18px;
    text-align:center;
}}
.stat-card .label{{font-size:12px;color:#7a7265;font-weight:500;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:6px;}}
.stat-card .value{{font-size:28px;font-weight:700;color:#2d2a24;}}
.stat-card .sub{{font-size:12px;color:#7a7265;margin-top:4px;}}
.trend-up{{color:#475417;}}
.trend-down{{color:#d9342b;}}
.trend-stable{{color:#7a7265;}}

/* Chart card */
.chart-card{{
    background:#fff;
    border:1px solid #e0d5bf;
    border-radius:12px;
    padding:18px;
    margin-bottom:20px;
    display:flex;
    align-items:center;
    gap:24px;
}}
.chart-card canvas{{max-width:180px;max-height:180px;}}
.chart-card .bars{{flex:1;}}
.bar-row{{display:flex;align-items:center;gap:8px;margin-bottom:6px;}}
.bar-row .star-label{{font-size:13px;font-weight:600;width:14px;text-align:right;}}
.bar-row .bar-track{{flex:1;height:18px;background:#f0e8d6;border-radius:9px;overflow:hidden;}}
.bar-row .bar-fill{{height:100%;border-radius:9px;transition:width 0.6s ease;}}
.bar-row .bar-count{{font-size:12px;color:#7a7265;width:36px;text-align:right;}}
.bar-5{{background:#475417;}}
.bar-4{{background:#8cb82e;}}
.bar-3{{background:#c47d0a;}}
.bar-2{{background:#e67e22;}}
.bar-1{{background:#d9342b;}}

/* Filters */
.filters{{
    display:flex;
    gap:8px;
    margin-bottom:16px;
    flex-wrap:wrap;
    align-items:center;
}}
.filter-btn{{
    padding:6px 14px;
    border:1px solid #e0d5bf;
    border-radius:20px;
    background:#fff;
    font-family:inherit;
    font-size:13px;
    font-weight:500;
    color:#7a7265;
    cursor:pointer;
    transition:all 0.15s;
}}
.filter-btn:hover{{border-color:#475417;color:#475417;}}
.filter-btn.active{{background:#475417;color:#fff;border-color:#475417;}}
.filter-sep{{width:1px;height:24px;background:#e0d5bf;margin:0 4px;}}

/* Review cards */
.review-card{{
    background:#fff;
    border:1px solid #e0d5bf;
    border-radius:12px;
    padding:20px;
    margin-bottom:12px;
    border-left:4px solid #e0d5bf;
    transition:box-shadow 0.15s;
}}
.review-card:hover{{box-shadow:0 2px 8px rgba(0,0,0,0.06);}}
.review-card.rating-5{{border-left-color:#475417;}}
.review-card.rating-4{{border-left-color:#8cb82e;}}
.review-card.rating-3{{border-left-color:#c47d0a;}}
.review-card.rating-2{{border-left-color:#e67e22;}}
.review-card.rating-1{{border-left-color:#d9342b;}}
.review-header{{display:flex;align-items:center;gap:10px;margin-bottom:10px;}}
.review-avatar{{
    width:36px;height:36px;border-radius:50%;
    background:#f0e8d6;
    display:flex;align-items:center;justify-content:center;
    font-weight:600;font-size:14px;color:#7a7265;
    overflow:hidden;
}}
.review-avatar img{{width:100%;height:100%;object-fit:cover;}}
.review-meta .author{{font-weight:600;font-size:14px;color:#2d2a24;}}
.review-meta .date{{font-size:12px;color:#7a7265;}}
.stars{{color:#f4b400;font-size:15px;letter-spacing:1px;}}
.stars .empty{{color:#e0e0e0;}}
.review-text{{font-size:14px;line-height:1.6;color:#2d2a24;margin:10px 0;}}
.review-text:empty{{display:none;}}

/* Existing owner response */
.owner-response{{
    background:#f0f8f0;
    border:1px solid #c8e6c8;
    border-radius:8px;
    padding:12px 14px;
    margin-top:10px;
    font-size:13px;
    line-height:1.5;
    color:#2d5a2d;
}}
.owner-response .resp-label{{font-weight:600;font-size:11px;color:#475417;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px;}}

/* Draft area */
.draft-area{{margin-top:12px;}}
.draft-btn{{
    padding:8px 16px;
    background:linear-gradient(135deg,#475417,#3d6819);
    color:#fff;
    border:none;
    border-radius:8px;
    font-family:inherit;
    font-size:13px;
    font-weight:600;
    cursor:pointer;
    transition:opacity 0.15s;
}}
.draft-btn:hover{{opacity:0.9;}}
.draft-btn:disabled{{opacity:0.5;cursor:not-allowed;}}
.draft-result{{margin-top:10px;display:none;}}
.draft-result.visible{{display:block;}}
.draft-textarea{{
    width:100%;
    min-height:80px;
    padding:12px;
    border:1px solid #c8e6c8;
    border-radius:8px;
    background:#f0f8f0;
    font-family:inherit;
    font-size:13px;
    line-height:1.5;
    color:#2d5a2d;
    resize:vertical;
}}
.draft-actions{{display:flex;gap:8px;margin-top:8px;}}
.copy-btn,.regen-btn{{
    padding:6px 14px;
    border-radius:6px;
    font-family:inherit;
    font-size:12px;
    font-weight:600;
    cursor:pointer;
    transition:opacity 0.15s;
}}
.copy-btn{{background:#475417;color:#fff;border:none;}}
.copy-btn:hover{{opacity:0.9;}}
.regen-btn{{background:#fff;color:#475417;border:1px solid #475417;}}
.regen-btn:hover{{background:#f5f5f0;}}

/* Loading */
.loading-state{{
    text-align:center;
    padding:60px 20px;
    color:#7a7265;
}}
.spinner{{
    width:36px;height:36px;
    border:3px solid #e0d5bf;
    border-top-color:#475417;
    border-radius:50%;
    animation:spin 0.8s linear infinite;
    margin:0 auto 12px;
}}
@keyframes spin{{to{{transform:rotate(360deg);}}}}
.empty-state{{text-align:center;padding:40px;color:#7a7265;font-size:14px;}}
.badge{{
    display:inline-block;
    padding:2px 8px;
    border-radius:10px;
    font-size:11px;
    font-weight:600;
}}
.badge-needs{{background:#fff3cd;color:#856404;}}
.badge-done{{background:#d4edda;color:#155724;}}

/* Refresh button */
.refresh-btn{{
    padding:6px 14px;
    background:#fff;
    border:1px solid #e0d5bf;
    border-radius:8px;
    font-family:inherit;
    font-size:12px;
    font-weight:500;
    color:#7a7265;
    cursor:pointer;
}}
.refresh-btn:hover{{border-color:#475417;color:#475417;}}
.header-right{{display:flex;align-items:center;gap:12px;}}
.last-updated{{font-size:11px;color:#b0a99a;}}
</style>
</head>
<body>

<div class="header">
    <span class="title">Review Monitor</span>
    <div class="header-right">
        <span class="last-updated" id="lastUpdated"></span>
        <button class="refresh-btn" onclick="loadReviews(true)" id="refreshBtn">Refresh</button>
        <a href="/">Dashboard Home</a>
    </div>
</div>

<div class="container">
    <!-- Stats Row -->
    <div class="stats-row" id="statsRow" style="display:none;">
        <div class="stat-card">
            <div class="label">Overall Rating</div>
            <div class="value" id="avgRating">—</div>
            <div class="sub" id="totalReviews"></div>
        </div>
        <div class="stat-card">
            <div class="label">Needs Response</div>
            <div class="value" id="needsResponse">—</div>
            <div class="sub">reviews without reply</div>
        </div>
        <div class="stat-card">
            <div class="label">Response Rate</div>
            <div class="value" id="responseRate">—</div>
            <div class="sub">of reviews answered</div>
        </div>
        <div class="stat-card">
            <div class="label">30-Day Trend</div>
            <div class="value" id="trendValue">—</div>
            <div class="sub" id="trendSub"></div>
        </div>
    </div>

    <!-- Rating Distribution -->
    <div class="chart-card" id="chartCard" style="display:none;">
        <canvas id="ratingChart" width="170" height="170"></canvas>
        <div class="bars" id="barChart"></div>
    </div>

    <!-- Filters -->
    <div class="filters" id="filters" style="display:none;">
        <button class="filter-btn active" data-filter="all" onclick="setFilter('all',this)">All</button>
        <button class="filter-btn" data-filter="5" onclick="setFilter('5',this)">5 Star</button>
        <button class="filter-btn" data-filter="4" onclick="setFilter('4',this)">4 Star</button>
        <button class="filter-btn" data-filter="3" onclick="setFilter('3',this)">3 Star</button>
        <button class="filter-btn" data-filter="low" onclick="setFilter('low',this)">1-2 Star</button>
        <div class="filter-sep"></div>
        <button class="filter-btn" data-filter="needs" onclick="setFilter('needs',this)">Needs Response</button>
        <button class="filter-btn" data-filter="responded" onclick="setFilter('responded',this)">Responded</button>
    </div>

    <!-- Loading State -->
    <div class="loading-state" id="loadingState">
        <div class="spinner"></div>
        <div>Loading reviews...</div>
    </div>

    <!-- Review Feed -->
    <div id="reviewFeed"></div>
</div>

<script>
let allReviews = [];
let currentFilter = 'all';
let ratingChartInstance = null;

async function loadReviews(force) {{
    const btn = document.getElementById('refreshBtn');
    btn.disabled = true;
    btn.textContent = 'Loading...';
    document.getElementById('loadingState').style.display = 'block';
    document.getElementById('reviewFeed').innerHTML = '';

    try {{
        const resp = await fetch('/api/reviews/fetch', {{
            method: 'POST',
            headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify({{force: !!force}})
        }});
        const data = await resp.json();
        if (data.error) throw new Error(data.error);

        allReviews = data.reviews || [];
        renderStats(data.stats || {{}});
        renderBars(data.stats || {{}});
        renderChart(data.stats || {{}});
        applyFilter();

        document.getElementById('statsRow').style.display = 'grid';
        document.getElementById('chartCard').style.display = 'flex';
        document.getElementById('filters').style.display = 'flex';
        document.getElementById('lastUpdated').textContent = 'Updated ' + new Date().toLocaleTimeString();
    }} catch(e) {{
        document.getElementById('reviewFeed').innerHTML =
            '<div class="empty-state">Error loading reviews: ' + e.message + '</div>';
    }} finally {{
        document.getElementById('loadingState').style.display = 'none';
        btn.disabled = false;
        btn.textContent = 'Refresh';
    }}
}}

function renderStats(stats) {{
    document.getElementById('avgRating').innerHTML = (stats.avg_rating || 0).toFixed(1) + ' ' + starsHtml(stats.avg_rating || 0, 16);
    document.getElementById('totalReviews').textContent = (stats.total || 0) + ' reviews';
    document.getElementById('needsResponse').textContent = stats.needs_response || 0;
    document.getElementById('responseRate').textContent = (stats.response_rate || 0).toFixed(0) + '%';

    const trend = stats.trend || 'stable';
    const trendEl = document.getElementById('trendValue');
    const trendSub = document.getElementById('trendSub');
    if (trend === 'improving') {{
        trendEl.innerHTML = '<span class="trend-up">&uarr; ' + (stats.recent_avg || 0).toFixed(1) + '</span>';
        trendSub.textContent = 'vs ' + (stats.prior_avg || 0).toFixed(1) + ' prior 30d';
    }} else if (trend === 'declining') {{
        trendEl.innerHTML = '<span class="trend-down">&darr; ' + (stats.recent_avg || 0).toFixed(1) + '</span>';
        trendSub.textContent = 'vs ' + (stats.prior_avg || 0).toFixed(1) + ' prior 30d';
    }} else {{
        trendEl.innerHTML = '<span class="trend-stable">&mdash; ' + (stats.recent_avg || 0).toFixed(1) + '</span>';
        trendSub.textContent = (stats.recent_count || 0) + ' reviews last 30d';
    }}
}}

function renderBars(stats) {{
    const dist = stats.distribution || {{}};
    const total = stats.total || 1;
    const colors = {{5:'bar-5',4:'bar-4',3:'bar-3',2:'bar-2',1:'bar-1'}};
    let html = '';
    for (let i = 5; i >= 1; i--) {{
        const count = dist[i] || 0;
        const pct = (count / total * 100).toFixed(0);
        html += `<div class="bar-row">
            <span class="star-label">${{i}}</span>
            <div class="bar-track"><div class="bar-fill ${{colors[i]}}" style="width:${{pct}}%"></div></div>
            <span class="bar-count">${{count}}</span>
        </div>`;
    }}
    document.getElementById('barChart').innerHTML = html;
}}

function renderChart(stats) {{
    const dist = stats.distribution || {{}};
    const ctx = document.getElementById('ratingChart').getContext('2d');
    if (ratingChartInstance) ratingChartInstance.destroy();
    ratingChartInstance = new Chart(ctx, {{
        type: 'doughnut',
        data: {{
            labels: ['5 Star','4 Star','3 Star','2 Star','1 Star'],
            datasets: [{{
                data: [dist[5]||0, dist[4]||0, dist[3]||0, dist[2]||0, dist[1]||0],
                backgroundColor: ['#4a7c1f','#8cb82e','#c47d0a','#e67e22','#d9342b'],
                borderWidth: 0,
            }}]
        }},
        options: {{
            cutout: '65%',
            plugins: {{
                legend: {{display: false}},
                tooltip: {{
                    callbacks: {{
                        label: function(ctx) {{
                            return ctx.label + ': ' + ctx.raw + ' reviews';
                        }}
                    }}
                }}
            }}
        }}
    }});
}}

function starsHtml(rating, size) {{
    size = size || 14;
    let html = '';
    for (let i = 1; i <= 5; i++) {{
        if (i <= Math.round(rating)) {{
            html += '<span class="stars" style="font-size:' + size + 'px">&#9733;</span>';
        }} else {{
            html += '<span class="stars"><span class="empty" style="font-size:' + size + 'px">&#9733;</span></span>';
        }}
    }}
    return html;
}}

function relativeDate(dateStr) {{
    if (!dateStr) return '';
    const d = new Date(dateStr + (dateStr.includes('Z') ? '' : ' UTC'));
    const now = new Date();
    const diff = Math.floor((now - d) / 1000);
    if (diff < 60) return 'just now';
    if (diff < 3600) return Math.floor(diff/60) + 'm ago';
    if (diff < 86400) return Math.floor(diff/3600) + 'h ago';
    if (diff < 604800) return Math.floor(diff/86400) + 'd ago';
    if (diff < 2592000) return Math.floor(diff/604800) + 'w ago';
    return d.toLocaleDateString('en-US', {{month:'short', day:'numeric', year:'numeric'}});
}}

function setFilter(filter, btn) {{
    currentFilter = filter;
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    applyFilter();
}}

function applyFilter() {{
    let filtered = allReviews;
    if (currentFilter === '5') filtered = allReviews.filter(r => r.rating === 5);
    else if (currentFilter === '4') filtered = allReviews.filter(r => r.rating === 4);
    else if (currentFilter === '3') filtered = allReviews.filter(r => r.rating === 3);
    else if (currentFilter === 'low') filtered = allReviews.filter(r => r.rating <= 2);
    else if (currentFilter === 'needs') filtered = allReviews.filter(r => !r.response_text);
    else if (currentFilter === 'responded') filtered = allReviews.filter(r => !!r.response_text);

    renderReviews(filtered);
}}

function renderReviews(reviews) {{
    const feed = document.getElementById('reviewFeed');
    if (!reviews.length) {{
        feed.innerHTML = '<div class="empty-state">No reviews match this filter.</div>';
        return;
    }}
    let html = '';
    reviews.forEach((r, idx) => {{
        const initials = (r.author || '?')[0].toUpperCase();
        const avatarInner = r.author_image
            ? `<img src="${{r.author_image}}" alt="" loading="lazy">`
            : initials;
        const badge = r.response_text
            ? '<span class="badge badge-done">Responded</span>'
            : '<span class="badge badge-needs">Needs Response</span>';

        html += `<div class="review-card rating-${{r.rating}}" data-idx="${{idx}}">
            <div class="review-header">
                <div class="review-avatar">${{avatarInner}}</div>
                <div class="review-meta">
                    <div class="author">${{escHtml(r.author)}} ${{badge}}</div>
                    <div class="date">${{starsHtml(r.rating, 13)}} &middot; ${{relativeDate(r.date)}}</div>
                </div>
            </div>
            <div class="review-text">${{escHtml(r.text)}}</div>`;

        if (r.response_text) {{
            html += `<div class="owner-response">
                <div class="resp-label">Your Response</div>
                ${{escHtml(r.response_text)}}
            </div>`;
        }} else {{
            html += `<div class="draft-area">
                <button class="draft-btn" onclick="draftResponse(${{idx}}, this)">Draft Response</button>
                <div class="draft-result" id="draft-${{idx}}">
                    <textarea class="draft-textarea" id="textarea-${{idx}}"></textarea>
                    <div class="draft-actions">
                        <button class="copy-btn" onclick="copyDraft(${{idx}})">Copy to Clipboard</button>
                        <button class="regen-btn" onclick="draftResponse(${{idx}}, null, true)">Regenerate</button>
                    </div>
                </div>
            </div>`;
        }}

        html += '</div>';
    }});
    feed.innerHTML = html;
}}

async function draftResponse(idx, btn, regen) {{
    const r = allReviews[idx];
    if (!r) return;

    const draftEl = document.getElementById('draft-' + idx);
    const textarea = document.getElementById('textarea-' + idx);

    if (btn) {{
        btn.disabled = true;
        btn.textContent = 'Drafting...';
    }}
    if (regen) {{
        textarea.value = 'Regenerating...';
    }}

    try {{
        const resp = await fetch('/api/reviews/draft', {{
            method: 'POST',
            headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify({{
                review_text: r.text || '',
                rating: r.rating,
                reviewer_name: r.author || ''
            }})
        }});
        const data = await resp.json();
        if (data.error) throw new Error(data.error);

        textarea.value = data.draft || '';
        draftEl.classList.add('visible');
    }} catch(e) {{
        textarea.value = 'Error: ' + e.message;
        draftEl.classList.add('visible');
    }} finally {{
        if (btn) {{
            btn.disabled = false;
            btn.textContent = 'Draft Response';
        }}
    }}
}}

function copyDraft(idx) {{
    const textarea = document.getElementById('textarea-' + idx);
    navigator.clipboard.writeText(textarea.value).then(() => {{
        const btn = textarea.parentElement.querySelector('.copy-btn');
        const orig = btn.textContent;
        btn.textContent = 'Copied!';
        setTimeout(() => btn.textContent = orig, 1500);
    }});
}}

function escHtml(s) {{
    if (!s) return '';
    return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}}

// Load on page ready
loadReviews(false);
</script>
</body>
</html>"""
