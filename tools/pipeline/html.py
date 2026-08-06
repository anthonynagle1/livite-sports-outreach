"""Pipeline HTML renderer — builds a self-contained dark-themed pipeline page."""

from __future__ import annotations

import html as _html
from typing import Any

from htmlrender.components import (
    _reset_chart_counter,
    render_chartjs_bar,
    render_chartjs_pie,
    render_stat,
    render_stat_grid,
    render_card,
    render_table,
    render_insight,
    fmt_currency,
    fmt_pct,
    fmt_num,
    _safe,
    LIVITE_CHART_COLORS,
)
from htmlrender.sections import _CSS, _LOADING_SNIPPET

# ── Funnel bar color gradient (green → coral) ─────────────────────────────
# Uses LIVITE_CHART_COLORS indices: 0=green, 3=teal, 1=blue, 2=purple, 4=amber, 5=coral
_FUNNEL_COLORS = [
    LIVITE_CHART_COLORS[0],  # #8cb82e  — green (top, widest)
    LIVITE_CHART_COLORS[3],  # #2db88a  — teal
    LIVITE_CHART_COLORS[1],  # #4a9cd8  — blue
    LIVITE_CHART_COLORS[2],  # #9b72c4  — purple
    LIVITE_CHART_COLORS[4],  # #e8a830  — amber
    LIVITE_CHART_COLORS[5],  # #e86040  — coral (bottom, narrowest)
    LIVITE_CHART_COLORS[6],  # #475417  — dark green (Booked = final)
]

# Status → badge color class for the upcoming games table
_STATUS_BADGE_CLASS = {
    'Not Contacted': 'a',
    'Introduction Email - Sent': 'b',
    'Follow-Up Email - Sent': 'b',
    'Responded': 'p',
    'In Conversation': 'p',
    'Interested': 'g',
    'Booked': 'g',
    'Not Interested': 'r',
    'No Response': 'a',
    'Out of Office': 'a',
    'Missed': 'r',
}


def build_pipeline_page(metrics: dict, logo_b64: str = "") -> str:
    """Build a complete HTML page for the Catering Pipeline Dashboard.

    Args:
        metrics: dict from ``compute_pipeline_dashboard()``
        logo_b64: optional base64-encoded logo PNG

    Returns:
        Self-contained HTML string.
    """
    _reset_chart_counter()

    parts: list[str] = []
    parts.append(_page_head())
    parts.append(f'<style>{_CSS}</style>')
    parts.append(_pipeline_css())
    parts.append('</head><body>')
    parts.append(_header(metrics, logo_b64))
    parts.append(_kpi_grid(metrics))
    parts.append(_funnel_card(metrics))
    parts.append(_status_distribution_card(metrics))
    parts.append(_email_queue_card(metrics))
    parts.append(_upcoming_games_card(metrics))
    parts.append(_non_funnel_card(metrics))
    parts.append(_footer())
    parts.append(_LOADING_SNIPPET)
    parts.append('</body></html>')
    return '\n'.join(parts)


# ── Page scaffold ─────────────────────────────────────────────────────────


def _page_head() -> str:
    return (
        '<!DOCTYPE html><html lang="en"><head>'
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Catering Pipeline | Livite</title>'
        '<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
        '<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">'
        '<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>'
    )


def _pipeline_css() -> str:
    """Extra CSS specific to the pipeline page (funnel bars, sub-nav)."""
    return """<style>
.pipeline-subnav {
    display:flex; gap:8px; flex-wrap:wrap; justify-content:center;
    margin-bottom:24px;
}
.pipeline-subnav a {
    padding:6px 16px; border-radius:6px; font-size:12px; font-weight:500;
    text-decoration:none; color:var(--muted); background:var(--surface2);
    border:1px solid var(--border); transition:background 0.15s, color 0.15s;
}
.pipeline-subnav a:hover,
.pipeline-subnav a:focus-visible {
    background:var(--livite-green); color:var(--livite-cream);
}
.pipeline-subnav a.active {
    background:var(--livite-green); color:var(--livite-cream);
}
.funnel-bar-row {
    display:flex; align-items:center; gap:10px; margin-bottom:6px;
}
.funnel-label {
    width:180px; flex-shrink:0; font-size:12px; text-align:right;
    color:var(--text); white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
}
.funnel-track {
    flex:1; height:32px; background:var(--surface2); border-radius:6px;
    overflow:hidden; position:relative;
}
.funnel-fill {
    height:100%; border-radius:6px; transition:width 0.4s ease;
}
.funnel-count {
    position:absolute; right:8px; top:50%; transform:translateY(-50%);
    font-size:11px; font-family:'JetBrains Mono',monospace; color:var(--text);
}
.funnel-conv {
    width:60px; flex-shrink:0; font-size:11px; color:var(--muted);
    font-family:'JetBrains Mono',monospace; text-align:right;
}
.eq-card-grid {
    display:grid; grid-template-columns:repeat(3,1fr); gap:10px;
}
@media(max-width:600px){
    .funnel-label { width:100px; font-size:10px; }
    .funnel-track { height:24px; }
    .funnel-count { font-size:9px; }
    .funnel-conv { width:48px; font-size:9px; }
    .eq-card-grid { grid-template-columns:1fr; }
    .pipeline-subnav a { padding:5px 10px; font-size:10px; }
}
</style>"""


# ── Section renderers ─────────────────────────────────────────────────────


def _header(metrics: dict, logo_b64: str) -> str:
    total = metrics.get('total_games', 0)
    logo_html = ''
    if logo_b64:
        logo_html = (
            f'<img src="data:image/png;base64,{logo_b64}" alt="Livite" '
            f'style="height:44px;max-width:80%;margin-bottom:8px;display:block;'
            f'margin-left:auto;margin-right:auto;">'
        )

    return (
        f'<div style="text-align:center;margin-bottom:20px;">'
        f'{logo_html}'
        f'<div style="font-size:12px;text-transform:uppercase;letter-spacing:2px;'
        f'color:var(--muted);margin-bottom:6px;">Catering Pipeline</div>'
        f'<h1>Outreach Dashboard</h1>'
        f'<div class="subtitle">{fmt_num(total)} games tracked</div>'
        f'</div>'
        f'<div class="pipeline-subnav">'
        f'<a href="#funnel" class="active">Funnel</a>'
        f'<a href="#statuses">Statuses</a>'
        f'<a href="#email-queue">Email Queue</a>'
        f'<a href="#upcoming">Upcoming</a>'
        f'</div>'
    )


def _kpi_grid(metrics: dict) -> str:
    total = metrics.get('total_games', 0)
    contacted = metrics.get('contacted_total', 0)
    win_rate = metrics.get('win_rate', 0.0)
    pipeline_val = metrics.get('pipeline_value', 0.0)
    eq = metrics.get('email_queue', {})

    stats = [
        render_stat('Total Games', fmt_num(total)),
        render_stat('Contacted', fmt_num(contacted)),
        render_stat('Win Rate', fmt_pct(win_rate), color='var(--green)' if win_rate > 0 else None),
        render_stat('Pipeline Value', fmt_currency(pipeline_val)),
        render_stat('Email Drafts', fmt_num(eq.get('Draft', 0))),
        render_stat('Emails Approved', fmt_num(eq.get('Approved', 0))),
    ]
    return render_stat_grid(stats)


def _funnel_card(metrics: dict) -> str:
    funnel = metrics.get('funnel', [])
    if not funnel:
        return render_card('CONVERSION FUNNEL', '<div style="color:var(--muted);">No data</div>')

    max_count = max((s['count'] for s in funnel), default=1) or 1

    rows: list[str] = []
    for i, stage in enumerate(funnel):
        count = stage['count']
        pct_w = max(2, count / max_count * 100) if count > 0 else 0
        color = _FUNNEL_COLORS[i % len(_FUNNEL_COLORS)]
        conv = stage.get('conversion_from_prev')
        conv_text = f'{conv:.0f}%' if conv is not None else ''

        rows.append(
            f'<div class="funnel-bar-row">'
            f'<div class="funnel-label">{_safe(stage["stage"])}</div>'
            f'<div class="funnel-track">'
            f'<div class="funnel-fill" style="width:{pct_w:.1f}%;background:{color};"></div>'
            f'<span class="funnel-count">{count}</span>'
            f'</div>'
            f'<div class="funnel-conv">{conv_text}</div>'
            f'</div>'
        )

    content = (
        '<div id="funnel" style="display:flex;flex-direction:column;gap:0;">'
        + '\n'.join(rows)
        + '</div>'
        + '<div style="display:flex;justify-content:space-between;margin-top:8px;'
        + 'font-size:10px;color:var(--muted);padding:0 4px;">'
        + '<span>Stage</span><span>Count</span><span>Conv %</span>'
        + '</div>'
    )
    return render_card('CONVERSION FUNNEL', content)


def _status_distribution_card(metrics: dict) -> str:
    status_counts = metrics.get('status_counts', {})
    if not status_counts:
        return ''

    # Sort by count descending for the chart
    sorted_statuses = sorted(status_counts.items(), key=lambda x: x[1], reverse=True)
    labels = [s[0] for s in sorted_statuses]
    values = [s[1] for s in sorted_statuses]

    # Assign colors based on status type
    colors: list[str] = []
    for label in labels:
        idx = _status_color_index(label)
        colors.append(LIVITE_CHART_COLORS[idx % len(LIVITE_CHART_COLORS)])

    chart = render_chartjs_bar(
        labels=labels,
        datasets=[{'label': 'Games', 'data': values, 'colors': colors}],
        height=max(200, len(labels) * 36),
        horizontal=True,
        bar_thickness=22,
    )
    return f'<div id="statuses">{render_card("STATUS DISTRIBUTION", chart)}</div>'


def _email_queue_card(metrics: dict) -> str:
    eq = metrics.get('email_queue', {})
    draft = eq.get('Draft', 0)
    approved = eq.get('Approved', 0)
    sent = eq.get('Sent', 0)

    stats_html = (
        '<div class="eq-card-grid">'
        + render_stat('Drafts', fmt_num(draft), color='var(--amber)')
        + render_stat('Approved', fmt_num(approved), color='var(--blue)')
        + render_stat('Sent', fmt_num(sent), color='var(--green)')
        + '</div>'
    )
    return f'<div id="email-queue">{render_card("EMAIL QUEUE", stats_html)}</div>'


def _upcoming_games_card(metrics: dict) -> str:
    upcoming = metrics.get('upcoming_games', [])
    if not upcoming:
        return render_card(
            'UPCOMING GAMES (30 DAYS)',
            '<div style="color:var(--muted);font-size:13px;">No upcoming games.</div>',
        )

    headers = ['Date', 'Sport', 'Visiting Team', 'Status']
    rows: list[list[str]] = []
    for g in upcoming:
        date_display = g.get('date_display') or g.get('date', '')
        sport = g.get('sport', '')
        gender = g.get('gender', '')
        sport_label = f'{sport} ({gender})' if gender else sport
        visiting = g.get('visiting_team', '')
        status = g.get('status', '')
        badge_cls = _STATUS_BADGE_CLASS.get(status, 'a')
        status_html = f'<span class="badge {badge_cls}">{_safe(status)}</span>'
        rows.append([_safe(date_display), _safe(sport_label), _safe(visiting), status_html])

    table_html = render_table(headers, rows)
    return f'<div id="upcoming">{render_card("UPCOMING GAMES (30 DAYS)", table_html)}</div>'


def _non_funnel_card(metrics: dict) -> str:
    nf = metrics.get('non_funnel', {})
    total_nf = sum(nf.values())
    if total_nf == 0:
        return ''

    items: list[str] = []
    for status, count in nf.items():
        if count > 0:
            items.append(
                f'<div style="display:flex;justify-content:space-between;'
                f'padding:6px 0;border-bottom:1px solid var(--border);font-size:13px;">'
                f'<span>{_safe(status)}</span>'
                f'<span class="mono" style="font-size:12px;">{count}</span>'
                f'</div>'
            )

    content = (
        '<div style="font-size:12px;color:var(--muted);margin-bottom:8px;">'
        f'{total_nf} games in non-active statuses'
        '</div>'
        + '\n'.join(items)
    )
    return render_card('NON-FUNNEL STATUSES', content)


def _footer() -> str:
    return (
        '<div style="text-align:center;padding:24px 0 12px;font-size:11px;color:var(--muted);">'
        'Livite Catering Pipeline &middot; Data sourced from Notion'
        '</div>'
    )


# ── Helpers ───────────────────────────────────────────────────────────────


def _status_color_index(status: str) -> int:
    """Map an outreach status to a LIVITE_CHART_COLORS index."""
    mapping = {
        'Not Contacted': 4,       # amber
        'Introduction Email - Sent': 1,  # blue
        'Follow-Up Email - Sent': 2,     # purple
        'Responded': 3,           # teal
        'In Conversation': 3,     # teal
        'Interested': 0,          # green
        'Booked': 0,              # green
        'Not Interested': 5,      # coral
        'No Response': 4,         # amber
        'Out of Office': 7,       # pink
        'Missed': 5,              # coral
    }
    return mapping.get(status, 1)
