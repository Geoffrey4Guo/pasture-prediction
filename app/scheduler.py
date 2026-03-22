"""
Background scheduler — APScheduler
====================================

Jobs registered here run automatically in the background while the FastAPI
server is running.  No extra process needed.

Schedule:
  ┌────────────────────────────────┬──────────────┬──────────────────────────┐
  │ Job                            │ Interval     │ Description              │
  ├────────────────────────────────┼──────────────┼──────────────────────────┤
  │ poll_weather                   │ Every 1 hr   │ Fetch current weather +  │
  │                                │              │ 7-day forecast from OWM  │
  ├────────────────────────────────┼──────────────┼──────────────────────────┤
  │ refresh_predictions            │ Every 15 min │ Re-run grass model for   │
  │                                │              │ all paddocks with recent  │
  │                                │              │ sensor readings           │
  └────────────────────────────────┴──────────────┴──────────────────────────┘

Both jobs run immediately on startup (next_run_time=now) and then on schedule.

Configuration (via .env or environment):
  FARM_LAT   — farm latitude  (default: 38.03, Charlottesville VA area)
  FARM_LON   — farm longitude (default: -78.48)
"""

import os
import logging
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger(__name__)

FARM_LAT = float(os.getenv("FARM_LAT", "38.03"))
FARM_LON = float(os.getenv("FARM_LON", "-78.48"))

scheduler = BackgroundScheduler(timezone="UTC")


def _job_poll_weather():
    """Wrapper with its own import so APScheduler can pickle it."""
    from app.weather import poll_and_store
    result = poll_and_store(FARM_LAT, FARM_LON)
    if result.get("error"):
        log.warning("Weather poll error: %s", result["error"])
    else:
        log.info(
            "Weather polled — %s, forecast %d days",
            result.get("current", {}).get("description", "?"),
            result.get("forecast_days", 0),
        )


def _job_refresh_predictions():
    from app.ingest import run_predictions_for_all_paddocks
    results = run_predictions_for_all_paddocks()
    log.info("Predictions refreshed for %d paddocks", len(results))


def start_scheduler():
    """
    Register all jobs and start the scheduler.
    Called once from main.py lifespan.
    """
    # Weather — every 60 minutes, run immediately on startup
    scheduler.add_job(
        _job_poll_weather,
        trigger=IntervalTrigger(minutes=60),
        id="poll_weather",
        name="OpenWeather poll",
        next_run_time=datetime.now(timezone.utc),   # run immediately
        replace_existing=True,
        misfire_grace_time=120,
    )

    # Grass predictions — every 15 minutes
    scheduler.add_job(
        _job_refresh_predictions,
        trigger=IntervalTrigger(minutes=15),
        id="refresh_predictions",
        name="Grass prediction refresh",
        next_run_time=datetime.now(timezone.utc),
        replace_existing=True,
        misfire_grace_time=60,
    )

    scheduler.start()
    log.info("Scheduler started — weather every 60min, predictions every 15min")


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)
        log.info("Scheduler stopped")
