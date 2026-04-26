# logic.py
# Takes the raw numbers from the Arduino and turns them into
# something useful — moisture percentages, smoothed averages,
# trend direction, and alerts.
# No file reading or writing here, just pure math.

from __future__ import annotations

import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Deque, Literal, Optional

from config import (
    RAIN_ALERT_HEAVY, RAIN_DRY, RAIN_WET,
    SMOOTH_WINDOW,
    SOIL_ALERT_DRY, SOIL_ALERT_WET, SOIL_DRY, SOIL_WET,
)


def _to_pct(raw: int, dry: int, wet: int) -> float:
    # both sensors read high when dry and low when wet, so we flip it
    # to get a percentage where 0% = bone dry and 100% = soaking wet
    clamped = max(min(raw, dry), wet)
    return round((dry - clamped) / (dry - wet) * 100.0, 2)


class _Smoother:
    # keeps a rolling window of the last N readings and averages them
    # this smooths out random spikes in the sensor data
    def __init__(self, window: int):
        self._buf: Deque[float] = deque(maxlen=window)

    def push(self, v: float) -> float:
        self._buf.append(v)
        return round(sum(self._buf) / len(self._buf), 2)

    def trend(self) -> Literal["rising", "falling", "stable"]:
        # compare the first half of the buffer to the second half
        # if the second half is noticeably higher, moisture is rising
        if len(self._buf) < 4:
            return "stable"
        half = len(self._buf) // 2
        old  = sum(list(self._buf)[:half]) / half
        new  = sum(list(self._buf)[half:]) / (len(self._buf) - half)
        diff = new - old
        if diff >  3.0: return "rising"
        if diff < -3.0: return "falling"
        return "stable"


@dataclass
class ProcessedReading:
    timestamp:     float
    timestamp_iso: str

    # what the Arduino actually sent
    soil_raw: int
    rain_raw: int

    # converted to percentages
    soil_pct:      float
    rain_pct:      float
    soil_smoothed: float
    rain_smoothed: float

    # human readable labels
    soil_status: str  # dry / low / optimal / wet
    rain_status: str  # none / light / moderate / heavy
    soil_trend:  str  # rising / falling / stable
    rain_trend:  str

    # pipe-separated so it stays flat in the CSV
    alerts: str = field(default="")

    @staticmethod
    def soil_label(pct: float) -> str:
        if pct < 25: return "dry"
        if pct < 45: return "low"
        if pct < 75: return "optimal"
        return "wet"

    @staticmethod
    def rain_label(pct: float) -> str:
        if pct < 15: return "none"
        if pct < 45: return "light"
        if pct < 70: return "moderate"
        return "heavy"

    def to_dict(self) -> dict:
        return asdict(self)


class SensorProcessor:
    # keep this alive for the whole session — the smoothing buffers
    # need to persist between readings or the averages won't work

    def __init__(self):
        self._soil = _Smoother(SMOOTH_WINDOW)
        self._rain = _Smoother(SMOOTH_WINDOW)

    def process(
        self,
        soil_raw: int,
        rain_raw: int,
        ts: Optional[float] = None,
    ) -> ProcessedReading:
        from datetime import datetime, timezone

        ts     = ts or time.time()
        ts_iso = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()

        soil_pct      = _to_pct(soil_raw, SOIL_DRY, SOIL_WET)
        rain_pct      = _to_pct(rain_raw,  RAIN_DRY, RAIN_WET)
        soil_smoothed = self._soil.push(soil_pct)
        rain_smoothed = self._rain.push(rain_pct)

        alerts = []
        if soil_smoothed < SOIL_ALERT_DRY:   alerts.append("TOO_DRY")
        if soil_smoothed > SOIL_ALERT_WET:   alerts.append("TOO_WET")
        if rain_smoothed > RAIN_ALERT_HEAVY: alerts.append("RAIN_HEAVY")

        return ProcessedReading(
            timestamp      = ts,
            timestamp_iso  = ts_iso,
            soil_raw       = soil_raw,
            rain_raw       = rain_raw,
            soil_pct       = soil_pct,
            rain_pct       = rain_pct,
            soil_smoothed  = soil_smoothed,
            rain_smoothed  = rain_smoothed,
            soil_status    = ProcessedReading.soil_label(soil_smoothed),
            rain_status    = ProcessedReading.rain_label(rain_smoothed),
            soil_trend     = self._soil.trend(),
            rain_trend     = self._rain.trend(),
            alerts         = "|".join(alerts),
        )