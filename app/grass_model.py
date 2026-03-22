"""
Grass Growth Model — Pasture Predictions Backend
=================================================

The model is built on established pasture science:

1. **Temperature Response Function (TRF)**
   Based on the beta-function used in LINGRA and DairyMod models.
   - Base temperature (T_base):    4 °C  — below this, zero growth
   - Optimal temperature (T_opt): 20 °C  — peak growth rate
   - Maximum temperature (T_max): 35 °C  — above this, zero growth
   - Shape produces a smooth bell curve peaking at T_opt.

2. **Moisture Modifier**
   Logistic curve: growth is near-zero below ~10% VWC, peaks above 35% VWC,
   then plateaus (waterlogging modelled as a slight decline above 55%).

3. **Height Modifier (logistic ceiling)**
   Grass growth rate slows as the sward approaches its ceiling height (~25 cm).
   Mimics the self-shading / tiller death dynamic in a real sward.

4. **Potential Growth Rate (PGR)**
   Maximum potential at optimal conditions: 2.8 cm/day (ryegrass/fescue blend,
   temperate climate, adequate fertility). Calibrated to USDA NRCS pasture data.

   PGR_actual = PGR_max × TRF(temp) × moisture_mod(vwc) × height_mod(h)

5. **7-day forecast**
   Steps forward one day at a time using weather forecast temperatures if
   available, otherwise uses the last known temperature.

References
----------
- Schapendonk et al. (1998) LINGRA grass model
- USDA NRCS Pasture and Hay Planting Guide
- DairyNZ Pasture Growth Predictor (2019)
"""

from __future__ import annotations
from typing import List, Optional
import math


# ── Constants ─────────────────────────────────────────────────────────────────

PGR_MAX_CM_DAY  = 2.8   # cm/day at optimal conditions
T_BASE          = 4.0   # °C — no growth below this
T_OPT           = 20.0  # °C — peak growth
T_MAX           = 35.0  # °C — no growth above this
SWARD_CEILING   = 25.0  # cm — maximum sward height
ENTRY_HEIGHT    = 6.0   # cm — optimal grazing entry height
EXIT_HEIGHT     = 4.0   # cm — minimum post-grazing residual


# ── Sub-models ────────────────────────────────────────────────────────────────

def temperature_response(temp_c: float) -> float:
    """
    Beta-function temperature response (0.0 – 1.0).
    Smooth curve peaking at T_OPT, zero at T_BASE and T_MAX.
    """
    if temp_c <= T_BASE or temp_c >= T_MAX:
        return 0.0
    # Normalised beta function
    T_range = T_MAX - T_BASE
    T_norm  = (temp_c - T_BASE) / T_range
    T_opt_n = (T_OPT  - T_BASE) / T_range
    # Shape parameter alpha so peak is at T_opt_n
    alpha = T_opt_n / (1.0 - T_opt_n)
    # Beta PDF shape (simplified — equivalent to (t^a * (1-t)^b) normalised)
    try:
        raw = (T_norm ** alpha) * ((1.0 - T_norm) ** 1.0)
        peak = (T_opt_n ** alpha) * ((1.0 - T_opt_n) ** 1.0)
        return min(1.0, raw / peak)
    except ZeroDivisionError:
        return 0.0


def moisture_modifier(vwc_pct: float) -> float:
    """
    Soil moisture modifier (0.0 – 1.0).
    Logistic rise from 10% VWC, plateau at 35-55%, slight decline above 55%
    (waterlogging reduces oxygen availability for roots).
    """
    if vwc_pct < 0:
        return 0.0
    # Rising phase: logistic from 10% to 35%
    if vwc_pct <= 35.0:
        return 1.0 / (1.0 + math.exp(-0.25 * (vwc_pct - 22.0)))
    # Waterlogging penalty above 55%
    if vwc_pct > 55.0:
        penalty = 0.015 * (vwc_pct - 55.0)
        return max(0.3, 1.0 - penalty)
    return 1.0


def height_modifier(height_cm: float) -> float:
    """
    Logistic ceiling effect (0.0 – 1.0).
    Growth rate is highest at low heights, diminishes as sward fills the
    canopy and approaches the ceiling.
    """
    if height_cm <= 0:
        return 1.0
    # Logistic decrease: full rate at 0 cm, ~10% rate at ceiling
    k = 0.22
    midpoint = SWARD_CEILING * 0.55
    return 1.0 / (1.0 + math.exp(k * (height_cm - midpoint)))


# ── Core prediction ───────────────────────────────────────────────────────────

def predict_growth_rate(
    height_cm:     float,
    temp_c:        float,
    soil_moisture: float,
) -> float:
    """
    Predict today's grass growth rate in cm/day.

    Parameters
    ----------
    height_cm     : current sward height
    temp_c        : air temperature (°C)
    soil_moisture : volumetric water content (%)

    Returns
    -------
    float : predicted growth rate in cm/day
    """
    trf  = temperature_response(temp_c)
    mmod = moisture_modifier(soil_moisture)
    hmod = height_modifier(height_cm)
    pgr  = PGR_MAX_CM_DAY * trf * mmod * hmod
    return round(pgr, 3)


def predict_7day(
    current_height_cm: float,
    soil_moisture:     float,
    temps_7day:        List[float],       # list of 7 forecast temps (°C)
) -> dict:
    """
    Step forward 7 days using daily forecast temperatures.

    Returns a dict with keys: pgr_today, day1..day7, days_to_ready.
    """
    if len(temps_7day) < 7:
        # pad with last known temp
        last = temps_7day[-1] if temps_7day else 15.0
        temps_7day = (temps_7day + [last] * 7)[:7]

    heights = []
    h = current_height_cm
    pgr_today = predict_growth_rate(h, temps_7day[0], soil_moisture)

    for i, t in enumerate(temps_7day):
        pgr = predict_growth_rate(h, t, soil_moisture)
        h   = min(SWARD_CEILING, h + pgr)
        heights.append(round(h, 2))

    # Days until ready (≥ ENTRY_HEIGHT)
    days_to_ready: Optional[int] = None
    h_check = current_height_cm
    for day in range(1, 31):  # look ahead up to 30 days
        t = temps_7day[min(day - 1, 6)]
        pgr = predict_growth_rate(h_check, t, soil_moisture)
        h_check = min(SWARD_CEILING, h_check + pgr)
        if h_check >= ENTRY_HEIGHT:
            days_to_ready = day
            break

    return {
        "pgr_cm_day":    pgr_today,
        "day1_cm":       heights[0],
        "day2_cm":       heights[1],
        "day3_cm":       heights[2],
        "day4_cm":       heights[3],
        "day5_cm":       heights[4],
        "day6_cm":       heights[5],
        "day7_cm":       heights[6],
        "days_to_ready": days_to_ready,
    }


# ── Rotation advisor ─────────────────────────────────────────────────────────

def rotation_advice(paddocks: list) -> list:
    """
    Given a list of paddock dicts with grass_height, soil_moisture,
    temp_c, and status, return a ranked list of grazing recommendations.

    Each item: {paddock_name, action, reason, priority (1=highest)}
    """
    advice = []
    for pk in paddocks:
        h   = pk.get("grass_height_cm", 5.0)
        m   = pk.get("soil_moisture", 30.0)
        t   = pk.get("temp_c", 16.0)
        st  = pk.get("status", "ready")
        pgr = predict_growth_rate(h, t, m)

        if st == "grazing" and h <= EXIT_HEIGHT:
            advice.append({
                "paddock": pk.get("name", "?"),
                "action":  "ROTATE OUT",
                "reason":  f"Height {h:.1f}cm has reached exit threshold {EXIT_HEIGHT}cm",
                "priority": 1,
            })
        elif st in ("ready", "resting") and h >= ENTRY_HEIGHT:
            advice.append({
                "paddock": pk.get("name", "?"),
                "action":  "GRAZE NOW",
                "reason":  f"Height {h:.1f}cm ≥ entry target {ENTRY_HEIGHT}cm, PGR {pgr:.2f}cm/day",
                "priority": 2,
            })
        elif st == "resting":
            days_needed = max(0, math.ceil((ENTRY_HEIGHT - h) / pgr)) if pgr > 0 else 99
            advice.append({
                "paddock": pk.get("name", "?"),
                "action":  "CONTINUE RESTING",
                "reason":  f"~{days_needed} days until ready at {pgr:.2f}cm/day",
                "priority": 3,
            })
        elif m < 20.0:
            advice.append({
                "paddock": pk.get("name", "?"),
                "action":  "CHECK MOISTURE",
                "reason":  f"Soil moisture {m:.0f}% below 20% threshold",
                "priority": 2,
            })

    advice.sort(key=lambda x: x["priority"])
    return advice
