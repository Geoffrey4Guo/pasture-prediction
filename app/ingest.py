"""
Sensor ingestion + prediction pipeline
=======================================

When a sensor reading arrives (via POST /sensors/ingest or the scheduler),
this module:

  1. Persists the raw SensorReading row
  2. Pulls the latest weather temperature for the paddock's location
  3. Runs the grass growth model → stores a GrassPrediction row
  4. Returns both the reading and prediction

For farms using physical sensors (e.g. Decagon 5TM, Sentek, a Raspberry Pi
with a capacitive moisture probe), the sensor device should POST JSON to:

    POST /sensors/ingest
    {
        "paddock_name": "North Field 1",
        "grass_height_cm": 7.2,
        "soil_moisture": 28.5,
        "soil_temp_c": 14.1,
        "air_temp_c": 16.5,
        "rainfall_mm": 0.0,
        "sensor_id": "node_A"
    }

The scheduler also calls ingest_all_paddocks() every 15 minutes to update
predictions using the latest weather even if no new sensor reading has arrived.
"""

from __future__ import annotations
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import select

from app.db import get_session
from app.models import (
    SensorReading, SensorIngestPayload,
    GrassPrediction, Paddock, WeatherRecord
)
from app.grass_model import predict_7day, predict_growth_rate

log = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _latest_weather_temp(paddock_name: Optional[str] = None) -> float:
    """
    Return the most recent non-forecast temperature from the DB.
    Falls back to 16.0°C if no weather data is available yet.
    """
    with get_session() as sess:
        row = sess.exec(
            select(WeatherRecord)
            .where(WeatherRecord.is_forecast == False)
            .order_by(WeatherRecord.timestamp.desc())
        ).first()
    return row.temperature_c if row else 16.0


def _forecast_temps() -> list[float]:
    """
    Return the next 7 daily forecast temperatures from the DB.
    Falls back to repeating the latest observed temp.
    """
    with get_session() as sess:
        rows = sess.exec(
            select(WeatherRecord)
            .where(WeatherRecord.is_forecast == True)
            .order_by(WeatherRecord.record_date.asc())
            .limit(7)
        ).all()

    if rows:
        return [r.temperature_c for r in rows]

    # No forecast in DB yet — use current temp repeated
    fallback = _latest_weather_temp()
    return [fallback] * 7


# ── Core ingest ───────────────────────────────────────────────────────────────

def ingest_reading(payload: SensorIngestPayload) -> dict:
    """
    Persist one sensor reading and immediately compute + store a
    GrassPrediction for the affected paddock.

    Returns {"reading": ..., "prediction": ...}
    """
    # 1. Resolve paddock_id from name
    paddock_id: Optional[int] = None
    with get_session() as sess:
        pk = sess.exec(
            select(Paddock).where(Paddock.name == payload.paddock_name)
        ).first()
        if pk:
            paddock_id = pk.id

    # 2. Store the raw reading
    reading = SensorReading(
        timestamp       = datetime.now(timezone.utc),
        paddock_id      = paddock_id,
        paddock_name    = payload.paddock_name,
        grass_height_cm = payload.grass_height_cm,
        soil_moisture   = payload.soil_moisture,
        soil_temp_c     = payload.soil_temp_c,
        air_temp_c      = payload.air_temp_c,
        rainfall_mm     = payload.rainfall_mm,
        source          = payload.source,
        sensor_id       = payload.sensor_id,
    )
    with get_session() as sess:
        sess.add(reading)
        sess.commit()
        sess.refresh(reading)
        reading_id = reading.id

    log.info(
        "Sensor reading stored: paddock=%s, height=%.1fcm, moisture=%.1f%%",
        payload.paddock_name,
        payload.grass_height_cm or 0,
        payload.soil_moisture or 0,
    )

    # 3. Run grass model if we have the key inputs
    prediction = None
    h = payload.grass_height_cm
    m = payload.soil_moisture
    t = payload.air_temp_c or _latest_weather_temp(payload.paddock_name)

    if h is not None and m is not None:
        fc_temps = _forecast_temps()
        result   = predict_7day(h, m, fc_temps)
        pgr      = predict_growth_rate(h, t, m)

        pred = GrassPrediction(
            paddock_id        = paddock_id,
            paddock_name      = payload.paddock_name,
            current_height_cm = h,
            soil_moisture     = m,
            temperature_c     = t,
            pgr_cm_day        = pgr,
            **{k: v for k, v in result.items() if k != "pgr_cm_day"},
        )
        with get_session() as sess:
            sess.add(pred)
            sess.commit()
            sess.refresh(pred)
            prediction = pred.model_dump()

        log.info(
            "Grass prediction stored: paddock=%s, PGR=%.2fcm/day, 7d=[%.1f…%.1f]",
            payload.paddock_name, pgr, result["day1_cm"], result["day7_cm"],
        )

    return {
        "reading_id": reading_id,
        "prediction": prediction,
    }


def run_predictions_for_all_paddocks() -> list[dict]:
    """
    Scheduler job (every 15 min):
    For each paddock, grab its latest sensor reading and re-run the
    grass model using the latest weather forecast temperatures.

    This keeps predictions fresh even when no new sensor data has arrived.
    """
    fc_temps = _forecast_temps()
    current_temp = _latest_weather_temp()
    results = []

    with get_session() as sess:
        paddocks = sess.exec(select(Paddock)).all()

    for pk in paddocks:
        # Latest sensor reading for this paddock
        with get_session() as sess:
            latest = sess.exec(
                select(SensorReading)
                .where(SensorReading.paddock_id == pk.id)
                .where(SensorReading.grass_height_cm != None)
                .order_by(SensorReading.timestamp.desc())
            ).first()

        if not latest:
            log.debug("No sensor data for paddock %s — skipping", pk.name)
            continue

        h = latest.grass_height_cm
        m = latest.soil_moisture or 30.0
        t = latest.air_temp_c or current_temp

        result = predict_7day(h, m, fc_temps)
        pgr    = predict_growth_rate(h, t, m)

        pred = GrassPrediction(
            paddock_id        = pk.id,
            paddock_name      = pk.name,
            current_height_cm = h,
            soil_moisture     = m,
            temperature_c     = t,
            pgr_cm_day        = pgr,
            **{k: v for k, v in result.items() if k != "pgr_cm_day"},
        )
        with get_session() as sess:
            sess.add(pred)
            sess.commit()

        results.append({
            "paddock": pk.name,
            "pgr_cm_day": pgr,
            "day7_cm": result["day7_cm"],
            "days_to_ready": result["days_to_ready"],
        })

    log.info("Prediction refresh complete — %d paddocks updated", len(results))
    return results
