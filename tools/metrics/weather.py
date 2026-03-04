"""Weather & seasonality metrics: correlate weather with revenue."""

from __future__ import annotations

import os
import yaml
from datetime import datetime, timedelta

_CONFIG_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_EVENTS_PATH = os.path.join(_CONFIG_DIR, 'docs', 'events.yaml')

# ── Events loader ──

_events_cache = None

def _load_events():
    global _events_cache
    if _events_cache is not None:
        return _events_cache
    try:
        with open(_EVENTS_PATH, 'r') as f:
            data = yaml.safe_load(f)
        # Build date → event lookup
        events = {}
        for ev in data.get('holidays', []):
            d = ev.get('date', '')
            # Convert "2024-11-28" → "20241128"
            date_str = d.replace('-', '')
            events[date_str] = {"name": ev.get('name', ''), "type": ev.get('type', 'holiday')}
        _events_cache = events
        return events
    except Exception:
        return {}


def get_events_for_date(date_str):
    """Get events for a date string (YYYYMMDD). Returns list of event dicts."""
    events = _load_events()
    ev = events.get(date_str)
    return [ev] if ev else []


# ── Weather metrics ──

def compute_weather_metrics(weather_data, date_str, historical_weather=None,
                            historical_revenue=None):
    """Compute weather metrics for the dashboard.

    Args:
        weather_data: dict from get_daily_weather() for this date
        date_str: YYYYMMDD string
        historical_weather: list of weather dicts (last 30+ days) for correlation
        historical_revenue: list of dicts with {date, revenue} for correlation

    Returns:
        dict with weather metrics for dashboard rendering
    """
    if not weather_data:
        return {}

    result = {
        # Current day weather
        "temp_high": weather_data.get("temp_high"),
        "temp_low": weather_data.get("temp_low"),
        "temp_avg": weather_data.get("temp_avg"),
        "conditions": weather_data.get("conditions", "Unknown"),
        "weather_code": weather_data.get("weather_code"),
        "precipitation_inches": weather_data.get("precipitation_inches", 0),
        "rain_inches": weather_data.get("rain_inches", 0),
        "snow_inches": weather_data.get("snow_inches", 0),
        "wind_max_mph": weather_data.get("wind_max_mph"),
        "precip_type": weather_data.get("precip_type", "none"),
        "bad_weather": weather_data.get("bad_weather", False),
        "sunrise": weather_data.get("sunrise", ""),
        "sunset": weather_data.get("sunset", ""),
        "day_length_hours": weather_data.get("day_length_hours"),
        # Events
        "events": get_events_for_date(date_str),
    }

    # ── Correlation analysis (needs historical data) ──
    if historical_weather and historical_revenue:
        result.update(_compute_correlations(historical_weather, historical_revenue))

    return result


def _compute_correlations(weather_list, revenue_list):
    """Compute weather-revenue correlations from historical data.

    Args:
        weather_list: list of weather dicts with 'date' key
        revenue_list: list of dicts with 'date' and 'revenue' keys
    """
    # Build revenue lookup
    rev_lookup = {r['date']: r['revenue'] for r in revenue_list if r.get('revenue', 0) > 0}

    # Pair weather with revenue
    paired = []
    for w in weather_list:
        d = w.get('date', '')
        if d in rev_lookup:
            paired.append({
                'date': d,
                'revenue': rev_lookup[d],
                'temp_high': w.get('temp_high'),
                'conditions': w.get('conditions', ''),
                'bad_weather': w.get('bad_weather', False),
                'precip_type': w.get('precip_type', 'none'),
                'sunset': w.get('sunset', ''),
                'day_length_hours': w.get('day_length_hours'),
            })

    if not paired:
        return {}

    result = {}

    # Revenue vs Temperature scatter data (for chart)
    temp_revenue_points = [
        {"temp": p["temp_high"], "revenue": round(p["revenue"], 2), "date": p["date"]}
        for p in paired if p["temp_high"] is not None
    ]
    result["temp_revenue_scatter"] = temp_revenue_points

    # Bad weather vs good weather revenue comparison
    bad_days = [p for p in paired if p["bad_weather"]]
    good_days = [p for p in paired if not p["bad_weather"]]

    if bad_days:
        result["bad_weather_avg_revenue"] = round(
            sum(p["revenue"] for p in bad_days) / len(bad_days), 2
        )
        result["bad_weather_days"] = len(bad_days)
    if good_days:
        result["good_weather_avg_revenue"] = round(
            sum(p["revenue"] for p in good_days) / len(good_days), 2
        )
        result["good_weather_days"] = len(good_days)

    if bad_days and good_days:
        bad_avg = result["bad_weather_avg_revenue"]
        good_avg = result["good_weather_avg_revenue"]
        if good_avg > 0:
            result["weather_impact_pct"] = round((bad_avg - good_avg) / good_avg * 100, 1)

    # Day-of-week seasonality
    dow_totals = {}
    dow_counts = {}
    for p in paired:
        try:
            dt = datetime.strptime(p['date'], "%Y%m%d")
            dow = dt.strftime("%A")  # Monday, Tuesday, etc.
            dow_totals[dow] = dow_totals.get(dow, 0) + p['revenue']
            dow_counts[dow] = dow_counts.get(dow, 0) + 1
        except Exception:
            pass

    dow_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    dow_avgs = {}
    for dow in dow_order:
        if dow in dow_totals and dow_counts.get(dow, 0) > 0:
            dow_avgs[dow] = round(dow_totals[dow] / dow_counts[dow], 2)
    result["dow_avg_revenue"] = dow_avgs

    # Revenue trend (daily revenue over the historical window, for time series chart)
    result["daily_revenue_trend"] = [
        {"date": p["date"], "revenue": round(p["revenue"], 2), "conditions": p["conditions"]}
        for p in sorted(paired, key=lambda x: x["date"])
    ]

    return result
