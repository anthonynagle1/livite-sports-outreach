"""
Fetch historical + forecast weather data for Boston/Brookline.

Uses Open-Meteo (free, no API key):
  - Archive API for historical weather
  - Forecast API for today/upcoming weather
sunrise-sunset.org (free, no key) for sunset times.
Caches results in .tmp/weather/YYYYMMDD.json.
"""

import os
import json
import requests
from datetime import datetime, timedelta

PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
WEATHER_CACHE_DIR = os.path.join(PROJECT_ROOT, '.tmp', 'weather')

# Brookline, MA coordinates
LATITUDE = 42.3418
LONGITUDE = -71.1219

# WMO Weather Codes → human-readable labels
WMO_CODES = {
    0: "Clear",
    1: "Mostly Clear", 2: "Partly Cloudy", 3: "Overcast",
    45: "Fog", 48: "Freezing Fog",
    51: "Light Drizzle", 53: "Drizzle", 55: "Heavy Drizzle",
    56: "Light Freezing Drizzle", 57: "Freezing Drizzle",
    61: "Light Rain", 63: "Rain", 65: "Heavy Rain",
    66: "Light Freezing Rain", 67: "Freezing Rain",
    71: "Light Snow", 73: "Snow", 75: "Heavy Snow",
    77: "Snow Grains",
    80: "Light Showers", 81: "Showers", 82: "Heavy Showers",
    85: "Light Snow Showers", 86: "Heavy Snow Showers",
    95: "Thunderstorm", 96: "Thunderstorm w/ Hail", 99: "Severe Thunderstorm",
}


def _cache_path(date_str):
    return os.path.join(WEATHER_CACHE_DIR, f"{date_str}.json")


def _read_cache(date_str):
    path = _cache_path(date_str)
    if os.path.exists(path):
        with open(path, 'r') as f:
            return json.load(f)
    return None


def _write_cache(date_str, data):
    os.makedirs(WEATHER_CACHE_DIR, exist_ok=True)
    with open(_cache_path(date_str), 'w') as f:
        json.dump(data, f, indent=2)


def _is_today(date_str):
    return date_str == datetime.now().strftime("%Y%m%d")


def _fetch_open_meteo(date_str):
    """Fetch daily weather from Open-Meteo Historical API."""
    d = datetime.strptime(date_str, "%Y%m%d")
    iso_date = d.strftime("%Y-%m-%d")

    url = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        "start_date": iso_date,
        "end_date": iso_date,
        "daily": ",".join([
            "temperature_2m_max", "temperature_2m_min", "temperature_2m_mean",
            "precipitation_sum", "snowfall_sum", "rain_sum",
            "windspeed_10m_max", "windgusts_10m_max",
            "weather_code",
        ]),
        "temperature_unit": "fahrenheit",
        "windspeed_unit": "mph",
        "precipitation_unit": "inch",
        "timezone": "America/New_York",
    }

    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    daily = data.get("daily", {})
    if not daily or not daily.get("time"):
        return None

    weather_code = daily.get("weather_code", [None])[0]
    return {
        "temp_high": daily.get("temperature_2m_max", [None])[0],
        "temp_low": daily.get("temperature_2m_min", [None])[0],
        "temp_avg": daily.get("temperature_2m_mean", [None])[0],
        "precipitation_inches": daily.get("precipitation_sum", [0])[0] or 0,
        "rain_inches": daily.get("rain_sum", [0])[0] or 0,
        "snow_inches": daily.get("snowfall_sum", [0])[0] or 0,
        "wind_max_mph": daily.get("windspeed_10m_max", [None])[0],
        "wind_gust_mph": daily.get("windgusts_10m_max", [None])[0],
        "weather_code": weather_code,
        "conditions": WMO_CODES.get(weather_code, "Unknown"),
    }


def _fetch_sunset(date_str):
    """Fetch sunset/sunrise from sunrise-sunset.org API."""
    d = datetime.strptime(date_str, "%Y%m%d")
    iso_date = d.strftime("%Y-%m-%d")

    url = "https://api.sunrise-sunset.org/json"
    params = {
        "lat": LATITUDE,
        "lng": LONGITUDE,
        "date": iso_date,
        "formatted": 0,  # ISO 8601 format (UTC)
    }

    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    if data.get("status") != "OK":
        return {}

    results = data["results"]

    def _utc_to_et(iso_str):
        """Convert UTC ISO string to ET time string (HH:MM)."""
        try:
            utc_dt = datetime.fromisoformat(iso_str.replace("+00:00", ""))
            # EST = UTC-5, EDT = UTC-4. Approximate: use -5 Nov-Mar, -4 Apr-Oct
            month = utc_dt.month
            offset = 4 if 3 < month < 11 else 5
            et_dt = utc_dt - timedelta(hours=offset)
            return et_dt.strftime("%I:%M %p").lstrip("0")
        except Exception:
            return iso_str

    return {
        "sunrise": _utc_to_et(results.get("sunrise", "")),
        "sunset": _utc_to_et(results.get("sunset", "")),
        "day_length_hours": round(results.get("day_length", 0) / 3600, 1),
    }


def get_daily_weather(date):
    """Get weather + sunset data for a date. Returns dict or None.

    Args:
        date: datetime object or YYYYMMDD string
    """
    if isinstance(date, datetime):
        date_str = date.strftime("%Y%m%d")
    else:
        date_str = str(date)

    # Check cache (skip today — weather might update)
    if not _is_today(date_str):
        cached = _read_cache(date_str)
        if cached:
            return cached

    # Fetch weather
    try:
        weather = _fetch_open_meteo(date_str)
    except Exception as e:
        print(f"  Weather fetch error for {date_str}: {e}")
        weather = None

    if weather is None:
        return None

    # Fetch sunset
    try:
        sun = _fetch_sunset(date_str)
        weather.update(sun)
    except Exception as e:
        print(f"  Sunset fetch error for {date_str}: {e}")

    weather["date"] = date_str

    # Classify precipitation type
    if weather.get("snow_inches", 0) > 0:
        weather["precip_type"] = "snow"
    elif weather.get("rain_inches", 0) > 0:
        weather["precip_type"] = "rain"
    else:
        weather["precip_type"] = "none"

    # Is it a "bad weather" day? (rain > 0.1" or snow > 0.5" or wind > 30mph)
    weather["bad_weather"] = (
        weather.get("rain_inches", 0) > 0.1
        or weather.get("snow_inches", 0) > 0.5
        or (weather.get("wind_max_mph") or 0) > 30
    )

    # Cache it (not today)
    if not _is_today(date_str):
        _write_cache(date_str, weather)

    return weather


def get_weather_range(start_date, end_date):
    """Get weather for a date range. Returns list of weather dicts.

    Args:
        start_date, end_date: datetime objects
    """
    results = []
    current = start_date
    while current <= end_date:
        w = get_daily_weather(current)
        if w:
            results.append(w)
        current += timedelta(days=1)
    return results


def _fetch_open_meteo_bulk(start_str, end_str):
    """Fetch weather for a date range in a SINGLE API call.

    Returns dict of {date_str: weather_dict} for all dates in range.
    Much faster than per-day fetching (1 call vs hundreds).
    """
    start_iso = f"{start_str[:4]}-{start_str[4:6]}-{start_str[6:]}"
    end_iso = f"{end_str[:4]}-{end_str[4:6]}-{end_str[6:]}"

    url = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        "start_date": start_iso,
        "end_date": end_iso,
        "daily": ",".join([
            "temperature_2m_max", "temperature_2m_min", "temperature_2m_mean",
            "precipitation_sum", "snowfall_sum", "rain_sum",
            "windspeed_10m_max", "windgusts_10m_max",
            "weather_code", "sunrise", "sunset", "daylight_duration",
        ]),
        "temperature_unit": "fahrenheit",
        "windspeed_unit": "mph",
        "precipitation_unit": "inch",
        "timezone": "America/New_York",
    }

    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    daily = data.get("daily", {})
    times = daily.get("time", [])
    if not times:
        return {}

    results = {}
    for i, iso_date in enumerate(times):
        date_str = iso_date.replace("-", "")
        weather_code = (daily.get("weather_code") or [None])[i]

        # Parse sunrise/sunset from Open-Meteo (already in local timezone)
        sunrise_raw = (daily.get("sunrise") or [""])[i] or ""
        sunset_raw = (daily.get("sunset") or [""])[i] or ""
        day_length_s = (daily.get("daylight_duration") or [0])[i] or 0

        def _format_time(iso_str):
            try:
                dt = datetime.fromisoformat(iso_str)
                return dt.strftime("%I:%M %p").lstrip("0")
            except Exception:
                return ""

        weather = {
            "date": date_str,
            "temp_high": (daily.get("temperature_2m_max") or [None])[i],
            "temp_low": (daily.get("temperature_2m_min") or [None])[i],
            "temp_avg": (daily.get("temperature_2m_mean") or [None])[i],
            "precipitation_inches": (daily.get("precipitation_sum") or [0])[i] or 0,
            "rain_inches": (daily.get("rain_sum") or [0])[i] or 0,
            "snow_inches": (daily.get("snowfall_sum") or [0])[i] or 0,
            "wind_max_mph": (daily.get("windspeed_10m_max") or [None])[i],
            "wind_gust_mph": (daily.get("windgusts_10m_max") or [None])[i],
            "weather_code": weather_code,
            "conditions": WMO_CODES.get(weather_code, "Unknown"),
            "sunrise": _format_time(sunrise_raw),
            "sunset": _format_time(sunset_raw),
            "day_length_hours": round(day_length_s / 3600, 1) if day_length_s else None,
        }

        # Classify precipitation
        if weather["snow_inches"] > 0:
            weather["precip_type"] = "snow"
        elif weather["rain_inches"] > 0:
            weather["precip_type"] = "rain"
        else:
            weather["precip_type"] = "none"

        weather["bad_weather"] = (
            weather["rain_inches"] > 0.1
            or weather["snow_inches"] > 0.5
            or (weather["wind_max_mph"] or 0) > 30
        )

        results[date_str] = weather

    return results


# ── Weather impact multipliers (from Rolling P&L Forecast framework) ──

WEATHER_MULTIPLIERS = {
    "heavy_snow": 0.50,     # >6" snow
    "moderate_snow": 0.70,  # 2-6" snow
    "light_snow": 0.85,     # <2" snow
    "heavy_rain": 0.85,     # >0.5" rain
    "light_rain": 0.92,     # <0.5" rain
    "extreme_cold": 0.90,   # <15F
    "extreme_heat": 0.92,   # >95F
    "high_wind": 0.90,      # >30mph gusts
}


def _fetch_open_meteo_forecast(date_str):
    """Fetch weather from Open-Meteo Forecast API (today + upcoming days).

    Uses the forecast endpoint, NOT the archive endpoint.
    Returns same dict structure as _fetch_open_meteo().
    """
    d = datetime.strptime(date_str, "%Y%m%d")
    iso_date = d.strftime("%Y-%m-%d")

    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        "start_date": iso_date,
        "end_date": iso_date,
        "daily": ",".join([
            "temperature_2m_max", "temperature_2m_min", "temperature_2m_mean",
            "precipitation_sum", "snowfall_sum", "rain_sum",
            "windspeed_10m_max", "windgusts_10m_max",
            "weather_code", "sunrise", "sunset", "daylight_duration",
        ]),
        "temperature_unit": "fahrenheit",
        "windspeed_unit": "mph",
        "precipitation_unit": "inch",
        "timezone": "America/New_York",
    }

    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    daily = data.get("daily", {})
    if not daily or not daily.get("time"):
        return None

    weather_code = daily.get("weather_code", [None])[0]

    def _format_time(iso_str):
        try:
            dt = datetime.fromisoformat(iso_str)
            return dt.strftime("%I:%M %p").lstrip("0")
        except Exception:
            return ""

    sunrise_raw = (daily.get("sunrise") or [""])[0] or ""
    sunset_raw = (daily.get("sunset") or [""])[0] or ""
    day_length_s = (daily.get("daylight_duration") or [0])[0] or 0

    return {
        "temp_high": daily.get("temperature_2m_max", [None])[0],
        "temp_low": daily.get("temperature_2m_min", [None])[0],
        "temp_avg": daily.get("temperature_2m_mean", [None])[0],
        "precipitation_inches": daily.get("precipitation_sum", [0])[0] or 0,
        "rain_inches": daily.get("rain_sum", [0])[0] or 0,
        "snow_inches": daily.get("snowfall_sum", [0])[0] or 0,
        "wind_max_mph": daily.get("windspeed_10m_max", [None])[0],
        "wind_gust_mph": daily.get("windgusts_10m_max", [None])[0],
        "weather_code": weather_code,
        "conditions": WMO_CODES.get(weather_code, "Unknown"),
        "sunrise": _format_time(sunrise_raw),
        "sunset": _format_time(sunset_raw),
        "day_length_hours": round(day_length_s / 3600, 1) if day_length_s else None,
    }


def get_today_weather():
    """Get today's weather forecast using Open-Meteo Forecast API.

    Returns same dict structure as get_daily_weather() but uses the
    forecast endpoint (archive API doesn't have today's data).
    """
    date_str = datetime.now().strftime("%Y%m%d")

    try:
        weather = _fetch_open_meteo_forecast(date_str)
    except Exception as e:
        print(f"  Forecast weather fetch error for {date_str}: {e}")
        weather = None

    if weather is None:
        return None

    weather["date"] = date_str

    # Classify precipitation type
    if weather.get("snow_inches", 0) > 0:
        weather["precip_type"] = "snow"
    elif weather.get("rain_inches", 0) > 0:
        weather["precip_type"] = "rain"
    else:
        weather["precip_type"] = "none"

    weather["bad_weather"] = (
        weather.get("rain_inches", 0) > 0.1
        or weather.get("snow_inches", 0) > 0.5
        or (weather.get("wind_max_mph") or 0) > 30
    )

    return weather


def get_forecast_weather(date_str):
    """Get weather forecast for a future date (up to 7 days ahead).

    Args:
        date_str: YYYYMMDD string

    Returns weather dict or None.
    """
    try:
        weather = _fetch_open_meteo_forecast(date_str)
    except Exception as e:
        print(f"  Forecast weather fetch error for {date_str}: {e}")
        return None

    if weather is None:
        return None

    weather["date"] = date_str

    if weather.get("snow_inches", 0) > 0:
        weather["precip_type"] = "snow"
    elif weather.get("rain_inches", 0) > 0:
        weather["precip_type"] = "rain"
    else:
        weather["precip_type"] = "none"

    weather["bad_weather"] = (
        weather.get("rain_inches", 0) > 0.1
        or weather.get("snow_inches", 0) > 0.5
        or (weather.get("wind_max_mph") or 0) > 30
    )

    return weather


def compute_weather_multiplier(weather_data):
    """Compute revenue multiplier and explanation from weather data.

    Applies the worst applicable multiplier (they don't stack — take the
    most impactful one to avoid compounding).

    Returns:
        (multiplier: float, reasons: list[str])
    """
    if not weather_data:
        return 1.0, []

    candidates = []  # (multiplier, reason)

    snow = weather_data.get("snow_inches", 0)
    rain = weather_data.get("rain_inches", 0)
    temp_high = weather_data.get("temp_high")
    temp_low = weather_data.get("temp_low")
    wind = weather_data.get("wind_max_mph") or 0
    wind_gust = weather_data.get("wind_gust_mph") or 0

    # Snow
    if snow > 6:
        candidates.append((WEATHER_MULTIPLIERS["heavy_snow"],
                           "Heavy snow (%.1f in)" % snow))
    elif snow > 2:
        candidates.append((WEATHER_MULTIPLIERS["moderate_snow"],
                           "Moderate snow (%.1f in)" % snow))
    elif snow > 0:
        candidates.append((WEATHER_MULTIPLIERS["light_snow"],
                           "Light snow (%.1f in)" % snow))

    # Rain
    if rain > 0.5:
        candidates.append((WEATHER_MULTIPLIERS["heavy_rain"],
                           "Heavy rain (%.2f in)" % rain))
    elif rain > 0.1:
        candidates.append((WEATHER_MULTIPLIERS["light_rain"],
                           "Light rain (%.2f in)" % rain))

    # Temperature extremes
    if temp_low is not None and temp_low < 15:
        candidates.append((WEATHER_MULTIPLIERS["extreme_cold"],
                           "Extreme cold (low %dF)" % int(temp_low)))
    if temp_high is not None and temp_high > 95:
        candidates.append((WEATHER_MULTIPLIERS["extreme_heat"],
                           "Extreme heat (high %dF)" % int(temp_high)))

    # Wind
    if max(wind, wind_gust) > 30:
        candidates.append((WEATHER_MULTIPLIERS["high_wind"],
                           "High winds (%d mph)" % int(max(wind, wind_gust))))

    if not candidates:
        return 1.0, []

    # Take the worst (lowest) multiplier
    worst = min(candidates, key=lambda x: x[0])
    all_reasons = [c[1] for c in candidates]

    return worst[0], all_reasons


def warm_weather_cache(earliest_str="20241107"):
    """Pre-fetch and cache ALL weather data from earliest date to yesterday.

    Uses a single bulk API call instead of hundreds of individual calls.
    Returns (cached_count, total_days) tuple.
    """
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")

    # Find which dates are already cached
    uncached = []
    current = datetime.strptime(earliest_str, "%Y%m%d")
    end = datetime.strptime(yesterday, "%Y%m%d")
    total_days = 0
    while current <= end:
        ds = current.strftime("%Y%m%d")
        total_days += 1
        if not _read_cache(ds):
            uncached.append(ds)
        current += timedelta(days=1)

    if not uncached:
        return 0, total_days

    # Fetch all uncached dates in one bulk API call
    bulk_start = uncached[0]
    bulk_end = uncached[-1]
    try:
        bulk_data = _fetch_open_meteo_bulk(bulk_start, bulk_end)
    except Exception as e:
        print(f"  Bulk weather fetch error: {e}")
        return 0, total_days

    # Cache each day
    cached_count = 0
    for ds, weather in bulk_data.items():
        if ds in set(uncached):
            _write_cache(ds, weather)
            cached_count += 1

    return cached_count, total_days
