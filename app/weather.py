"""
Weather ingestion — OpenWeatherMap API
======================================

Fetches:
  - Current weather  →  /data/2.5/weather
  - 7-day forecast   →  /data/2.5/forecast (5-day/3h, grouped to daily)

Both endpoints require the free-tier API key (OPENWEATHER_API_KEY in .env).
Results are persisted to the WeatherRecord table.

The scheduler calls poll_and_store() every hour.
"""

from __future__ import annotations
import os
import logging
from datetime import datetime, timezone
from typing import Optional

import requests
from dotenv import load_dotenv

from app.db import get_session
from app.models import WeatherRecord

load_dotenv()
log = logging.getLogger(__name__)

OWM_BASE    = "https://api.openweathermap.org/data/2.5"
OWM_ONECALL = "https://api.openweathermap.org/data/3.0/onecall"


def _api_key() -> str:
    key = os.getenv("OPENWEATHER_API_KEY", "").strip()
    if not key or key == "INSERT_OPENWEATHER_KEY_HERE":
        raise RuntimeError(
            "OPENWEATHER_API_KEY is not set. "
            "Open your .env file and replace INSERT_OPENWEATHER_KEY_HERE "
            "with your key from https://openweathermap.org/api"
        )
    return key


# ── Current weather ───────────────────────────────────────────────────────────

def fetch_current(lat: float, lon: float) -> dict:
    """
    Fetch current weather from OWM /weather endpoint.
    Returns a dict ready to create a WeatherRecord.
    """
    r = requests.get(
        f"{OWM_BASE}/weather",
        params={"lat": lat, "lon": lon, "appid": _api_key(), "units": "metric"},
        timeout=10,
    )
    r.raise_for_status()
    d = r.json()

    now = datetime.now(timezone.utc)
    return {
        "timestamp":       now,
        "record_date":     now.strftime("%Y-%m-%d"),
        "is_forecast":     False,
        "lat":             lat,
        "lon":             lon,
        "temperature_c":   d["main"]["temp"],
        "precipitation_mm": d.get("rain", {}).get("1h", 0.0),
        "wind_ms":         d["wind"].get("speed"),
        "humidity_pct":    d["main"].get("humidity"),
        "description":     d["weather"][0]["description"].capitalize()
                           if d.get("weather") else None,
        "source":          "openweather",
    }


# ── 7-day forecast ────────────────────────────────────────────────────────────

def fetch_forecast(lat: float, lon: float) -> list[dict]:
    """
    Fetch 5-day / 3-hour forecast from OWM, group into daily summaries.
    Returns a list of up to 7 dicts, each representing one future day.
    """
    r = requests.get(
        f"{OWM_BASE}/forecast",
        params={"lat": lat, "lon": lon, "appid": _api_key(), "units": "metric"},
        timeout=10,
    )
    r.raise_for_status()
    items = r.json().get("list", [])

    # Group 3-hour slots by date
    by_day: dict[str, list] = {}
    for item in items:
        dt   = datetime.fromtimestamp(item["dt"], tz=timezone.utc)
        date = dt.strftime("%Y-%m-%d")
        by_day.setdefault(date, []).append(item)

    records = []
    for date, slots in sorted(by_day.items()):
        temps  = [s["main"]["temp"] for s in slots]
        precip = sum(s.get("rain", {}).get("3h", 0.0) for s in slots)
        winds  = [s["wind"]["speed"] for s in slots if "wind" in s]
        humids = [s["main"]["humidity"] for s in slots]
        desc   = slots[len(slots)//2]["weather"][0]["description"].capitalize() \
                 if slots[len(slots)//2].get("weather") else None

        records.append({
            "timestamp":       datetime.now(timezone.utc),
            "record_date":     date,
            "is_forecast":     True,
            "lat":             lat,
            "lon":             lon,
            "temperature_c":   round(sum(temps) / len(temps), 1),
            "precipitation_mm": round(precip, 1),
            "wind_ms":         round(sum(winds) / len(winds), 1) if winds else None,
            "humidity_pct":    round(sum(humids) / len(humids), 1) if humids else None,
            "description":     (desc + " (forecast)") if desc else "Forecast",
            "source":          "openweather",
        })

    return records[:7]  # cap at 7 days


# ── Persist to DB ─────────────────────────────────────────────────────────────

def _upsert_weather(records: list[dict]) -> int:
    """
    Insert new weather records, skipping any (record_date, is_forecast)
    combo that was already stored today.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    saved = 0
    with get_session() as sess:
        for rec in records:
            # Only skip duplicate current-weather rows on the same day
            # (forecasts are always refreshed)
            if not rec["is_forecast"]:
                from sqlmodel import select
                existing = sess.exec(
                    select(WeatherRecord)
                    .where(WeatherRecord.record_date == rec["record_date"])
                    .where(WeatherRecord.is_forecast == False)
                ).first()
                if existing:
                    # Update in place
                    for k, v in rec.items():
                        setattr(existing, k, v)
                    sess.add(existing)
                    sess.commit()
                    continue

            wr = WeatherRecord(**rec)
            sess.add(wr)
            sess.commit()
            saved += 1

    return saved


def poll_and_store(lat: float, lon: float) -> dict:
    """
    Full poll cycle:
      1. Fetch current weather + store
      2. Fetch 7-day forecast + store
      3. Return summary dict

    Called by the APScheduler job every hour.
    """
    log.info("Weather poll started for lat=%s lon=%s", lat, lon)
    result = {"current": None, "forecast_days": 0, "error": None}

    try:
        current = fetch_current(lat, lon)
        _upsert_weather([current])
        result["current"] = {
            "temp_c":        current["temperature_c"],
            "precip_mm":     current["precipitation_mm"],
            "description":   current["description"],
        }

        forecasts = fetch_forecast(lat, lon)
        _upsert_weather(forecasts)
        result["forecast_days"] = len(forecasts)

        log.info(
            "Weather poll complete — current %.1f°C, %d forecast days",
            current["temperature_c"],
            len(forecasts),
        )
    except Exception as exc:
        log.error("Weather poll failed: %s", exc)
        result["error"] = str(exc)

    return result
