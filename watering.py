# watering.py
# Decides when and how long to water the plant.
# When it's time to water, it sends a command to the Arduino
# via MQTT and the Arduino runs the pump for exactly that duration.
#
# Before watering it checks:
#   1. Is the soil actually dry enough?
#   2. Are we still in the cooldown period from the last spray?
#   3. Is the rain sensor detecting rain right now?
#   4. Is rain forecast in the next few hours?
# If any of those say no — we skip it and log the reason.

from __future__ import annotations

import csv
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Callable

from config import (
    BLOCK_WATERING_ON_RAIN, DATA_DIR, ML_PER_PCT,
    PUMP_FLOW_RATE_ML_S, RAIN_ALERT_HEAVY, SOIL_ALERT_DRY,
    WATER_COOLDOWN_S, WATER_MAX_DURATION_S,
    WATER_MIN_DURATION_S, WATER_TARGET_PCT,
)

log = logging.getLogger(__name__)

TOPIC_PUMP_CMD = "plantmonitor/pump"

LOG_FILE   = DATA_DIR / "watering_log.csv"
LOG_FIELDS = [
    "timestamp_iso", "unix_ts",
    "soil_pct_before", "rain_pct",
    "duration_s", "ml_dispensed",
    "weather_rain_prob", "weather_blocked",
    "reason",
]


def _ensure_log():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not LOG_FILE.exists() or LOG_FILE.stat().st_size == 0:
        with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=LOG_FIELDS).writeheader()


def _append_log(row: dict):
    _ensure_log()
    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=LOG_FIELDS).writerow(row)


def load_watering_log() -> list[dict]:
    _ensure_log()
    with open(LOG_FILE, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


@dataclass
class SprayEvent:
    timestamp:         float
    timestamp_iso:     str
    soil_pct_before:   float
    rain_pct:          float
    duration_s:        float
    ml_dispensed:      float
    weather_rain_prob: float
    weather_blocked:   bool
    reason:            str


class WateringController:
    # pass in the mqtt publish function after connecting
    # so the controller can send pump commands without owning the client

    def __init__(self, weather_service=None, mqtt_publish: Optional[Callable] = None):
        self._last_spray_ts: float = 0.0
        self._daily_ml: float      = 0.0
        self._daily_date: str      = ""
        self._weather              = weather_service
        self._publish              = mqtt_publish

    def set_mqtt_publish(self, fn: Callable):
        self._publish = fn

    def evaluate(
        self,
        soil_pct: float,
        rain_pct: float,
        ts: Optional[float] = None,
    ) -> Optional[SprayEvent]:

        ts = ts or time.time()

        # reset daily water counter at midnight
        today = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        if today != self._daily_date:
            self._daily_ml   = 0.0
            self._daily_date = today

        # check the weather forecast
        wx_prob, wx_blocked, wx_reason = 0.0, False, ""
        if self._weather:
            info = self._weather.get()
            if info:
                wx_prob    = info.max_rain_prob_pct
                wx_blocked = info.blocks_watering
                wx_reason  = info.block_reason

        # 1. soil is fine, nothing to do
        if soil_pct >= SOIL_ALERT_DRY:
            return None

        # 2. too soon since last watering — soil needs time to absorb
        if time.time() - self._last_spray_ts < WATER_COOLDOWN_S:
            return None

        # 3. it's already raining — no point watering
        if BLOCK_WATERING_ON_RAIN and rain_pct >= RAIN_ALERT_HEAVY:
            reason = f"Rain sensor active ({rain_pct:.0f}%) — skipping."
            log.info("SKIP: %s", reason)
            self._log_skip(ts, soil_pct, rain_pct, wx_prob, reason)
            return None

        # 4. rain is coming soon — save the water
        if wx_blocked:
            log.info("SKIP (forecast): %s", wx_reason)
            self._log_skip(ts, soil_pct, rain_pct, wx_prob, wx_reason)
            return None

        # all checks passed — figure out how long to run the pump
        deficit_pct  = max(0.0, WATER_TARGET_PCT - soil_pct)
        ml_needed    = deficit_pct * ML_PER_PCT
        duration_s   = ml_needed / PUMP_FLOW_RATE_ML_S
        duration_s   = max(WATER_MIN_DURATION_S, min(WATER_MAX_DURATION_S, duration_s))
        ml_dispensed = round(duration_s * PUMP_FLOW_RATE_ML_S, 1)

        wx_note = f" Weather is clear ({wx_prob:.0f}% rain chance)." if self._weather else ""
        reason  = (
            f"Soil at {soil_pct:.1f}% (below {SOIL_ALERT_DRY}%). "
            f"Need {deficit_pct:.1f}% more to reach target ({WATER_TARGET_PCT}%). "
            f"Running pump for {duration_s:.1f}s -> ~{ml_dispensed}ml.{wx_note}"
        )

        # send the START command to the Arduino via MQTT
        cmd = json.dumps({
            "cmd":      "START",
            "duration": round(duration_s, 2),
            "ml":       ml_dispensed,
        })

        if self._publish:
            self._publish(TOPIC_PUMP_CMD, cmd)
            log.info("Pump command sent: %s", cmd)
        else:
            log.warning("No MQTT publish function set — command not sent!")

        self._last_spray_ts = ts
        self._daily_ml     += ml_dispensed

        ts_iso = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        event  = SprayEvent(
            timestamp         = ts,
            timestamp_iso     = ts_iso,
            soil_pct_before   = round(soil_pct, 2),
            rain_pct          = round(rain_pct, 2),
            duration_s        = round(duration_s, 2),
            ml_dispensed      = ml_dispensed,
            weather_rain_prob = wx_prob,
            weather_blocked   = False,
            reason            = reason,
        )

        _append_log({
            "timestamp_iso":     ts_iso,
            "unix_ts":           ts,
            "soil_pct_before":   soil_pct,
            "rain_pct":          rain_pct,
            "duration_s":        round(duration_s, 2),
            "ml_dispensed":      ml_dispensed,
            "weather_rain_prob": wx_prob,
            "weather_blocked":   False,
            "reason":            reason,
        })

        return event

    def _log_skip(self, ts, soil_pct, rain_pct, wx_prob, reason):
        # log events where we decided NOT to water — useful to track water savings
        ts_iso = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        _append_log({
            "timestamp_iso":     ts_iso,
            "unix_ts":           ts,
            "soil_pct_before":   round(soil_pct, 2),
            "rain_pct":          round(rain_pct, 2),
            "duration_s":        0,
            "ml_dispensed":      0,
            "weather_rain_prob": wx_prob,
            "weather_blocked":   True,
            "reason":            reason,
        })

    @property
    def cooldown_remaining(self) -> float:
        return max(0.0, WATER_COOLDOWN_S - (time.time() - self._last_spray_ts))

    @property
    def daily_ml(self) -> float:
        return self._daily_ml