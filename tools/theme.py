"""Livite Design System — shared CSS, fonts, and nav bar.

Single source of truth so every page stays consistent.
Ported from the hub theme in Livite Main Agent sync_trigger/app.py.
"""

# ── Google Fonts (includes DM Serif Display) ──────────────────────────
GOOGLE_FONTS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link href="https://fonts.googleapis.com/css2?'
    'family=DM+Sans:ital,opsz,wght@0,9..40,300;0,9..40,400;0,9..40,500;'
    '0,9..40,600;0,9..40,700&family=DM+Serif+Display&'
    'family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">'
)

# ── Python-side color constants ───────────────────────────────────────
ACCENT = "#475417"
ACCENT_LIGHT = "#5a6e1e"
ACCENT_DARK = "#3d6819"
ACCENT_DIM = "rgba(71, 84, 23, 0.08)"
ACCENT_BORDER = "rgba(71, 84, 23, 0.15)"
BG = "#F5EDDC"
BG_CARD = "#FFFEF9"
TEXT = "#292524"
TEXT_SECONDARY = "#57534e"
TEXT_MUTED = "#a8a29e"
SUCCESS = "#15803d"
ERROR = "#dc2626"
WARNING = "#ca8a04"

# ── CSS Custom Properties ─────────────────────────────────────────────
CSS_VARS = """:root {
    --bg: #F5EDDC;
    --bg-card: #FFFEF9;
    --bg-card-hover: #FFFDF5;
    --bg-inset: rgba(0, 0, 0, 0.03);
    --accent: #475417;
    --accent-light: #5a6e1e;
    --accent-dark: #3d6819;
    --accent-dim: rgba(71, 84, 23, 0.08);
    --accent-border: rgba(71, 84, 23, 0.15);
    --success: #15803d;
    --success-bg: rgba(21, 128, 61, 0.08);
    --error: #dc2626;
    --error-bg: rgba(220, 38, 38, 0.08);
    --warning: #ca8a04;
    --warning-bg: rgba(202, 138, 4, 0.08);
    --text: #292524;
    --text-secondary: #57534e;
    --text-muted: #a8a29e;
    --border: rgba(0, 0, 0, 0.08);
    --border-strong: rgba(0, 0, 0, 0.12);
    --shadow-sm: 0 1px 2px rgba(0,0,0,0.04);
    --shadow-md: 0 4px 12px rgba(0,0,0,0.06);
    --shadow-lg: 0 8px 24px rgba(0,0,0,0.08);
}"""

# ── Topbar CSS ────────────────────────────────────────────────────────
TOPBAR_CSS = """
.topbar {
    display: flex; align-items: center; justify-content: space-between;
    padding: 14px 32px; border-bottom: 1px solid var(--border);
    background: rgba(245, 237, 220, 0.85);
    backdrop-filter: blur(16px); -webkit-backdrop-filter: blur(16px);
    position: sticky; top: 0; z-index: 100;
}
.topbar-brand { display: flex; align-items: center; gap: 10px; text-decoration: none; }
.topbar-logo {
    width: 28px; height: 28px; border-radius: 8px; background: var(--accent);
    display: flex; align-items: center; justify-content: center;
    color: white; font-family: 'DM Serif Display', serif; font-size: 15px;
}
.topbar-title {
    font-family: 'DM Serif Display', serif; font-size: 18px; font-weight: 400;
    color: var(--text); letter-spacing: 0.3px;
}
.topbar-nav { display: flex; gap: 4px; flex-wrap: wrap; }
.topbar-nav a {
    font-size: 13px; font-weight: 500; color: var(--text-muted);
    text-decoration: none; padding: 6px 14px; border-radius: 8px;
    transition: all 0.2s ease;
}
.topbar-nav a:hover { color: var(--text); background: var(--bg-inset); }
.topbar-nav a.active { color: var(--accent); background: var(--accent-dim); }
@media(max-width:600px) {
    .topbar { padding: 10px 16px; }
    .topbar-title { font-size: 15px; }
    .topbar-nav a { font-size: 11px; padding: 4px 10px; }
}
"""

# ── Animation keyframes ───────────────────────────────────────────────
ANIMATIONS = """
@keyframes fadeUp {
    from { opacity: 0; transform: translateY(12px); }
    to { opacity: 1; transform: translateY(0); }
}
@keyframes scaleIn {
    from { opacity: 0; transform: scale(0.97); }
    to { opacity: 1; transform: scale(1); }
}
"""


def topbar_html(active: str = "home", extra_links: list | None = None) -> str:
    """Render the frosted-glass topbar with L logo."""
    default_links = [
        ("home", "/", "Home"),
        ("hub", "/hub", "Hub"),
        ("invoices", "/invoices/", "Invoices"),
        ("prices", "/prices/", "Prices"),
    ]
    links = extra_links if extra_links is not None else default_links
    nav_items = "".join(
        f'<a href="{href}" class="{"active" if key == active else ""}">{label}</a>'
        for key, href, label in links
    )
    return f"""<header class="topbar">
    <a class="topbar-brand" href="/">
        <div class="topbar-logo">L</div>
        <span class="topbar-title">Livite</span>
    </a>
    <nav class="topbar-nav">{nav_items}</nav>
</header>"""


def base_head(title: str = "Livite", extra_css: str = "") -> str:
    """Return a complete <head> block with fonts, CSS vars, topbar styles."""
    return f"""<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    {GOOGLE_FONTS}
    <style>
        {CSS_VARS}
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: 'DM Sans', -apple-system, sans-serif;
            background: var(--bg); color: var(--text);
            min-height: 100vh; -webkit-font-smoothing: antialiased;
        }}
        {TOPBAR_CSS}
        {ANIMATIONS}
        {extra_css}
    </style>
</head>"""
