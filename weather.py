# weather.py
# Fetches the weather forecast from OpenWeatherMap and checks
# if it's going to rain in the next few hours.
# If rain is likely, it tells the watering controller to skip watering.
#
# The forecast is cached for 30 minutes so we don't burn through
# the free API limit (1000 calls/day). At 30 min intervals
# we only use about 48 calls per day.

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional
import urllib.request
import urllib.error
import json

from config import (
    OWM_API_KEY,
    OWM_LAT,
    OWM_LON,
    WEATHER_CACHE_SEC,
    WEATHER_LOOKAHEAD_H,
    WEATHER_RAIN_BLOCK_PCT,
)

log = logging.getLogger(__name__)

OWM_URL = (
    "https://api.openweathermap.org/data/2.5/forecast"
    "?lat={lat}&lon={lon}&appid={key}&units=metric"
)


@dataclass
class ForecastSlot:
    # one 3-hour forecast slot from OpenWeatherMap
    dt:            int
    dt_txt:        str
    temp_c:        float
    humidity_pct:  float
    rain_prob_pct: float  # this is the "pop" field — probability of precipitation
    rain_mm:       float
    description:   str
    icon:          str


@dataclass
class WeatherInfo:
    # everything we need from the forecast in one place
    fetched_at: float
    location:   str
    slots:      list[ForecastSlot] = field(default_factory=list)

    # these are calculated over the lookahead window (e.g. next 6 hours)
    max_rain_prob_pct: float = 0.0
    total_rain_mm:     float = 0.0
    blocks_watering:   bool  = False
    block_reason:      str   = ""

    # current conditions (taken from the first forecast slot)
    current_temp_c:   float = 0.0
    current_humidity: float = 0.0
    current_desc:     str   = ""
    current_icon:     str   = ""

    @property
    def age_s(self) -> float:
        return time.time() - self.fetched_at

    @property
    def is_stale(self) -> bool:
        return self.age_s > WEATHER_CACHE_SEC

    @property
    def fetched_str(self) -> str:
        from datetime import datetime
        return datetime.fromtimestamp(self.fetched_at).strftime("%H:%M:%S")


class WeatherService:
    # keep one instance of this alive — it holds the cached forecast

    def __init__(self):
        self._cache: Optional[WeatherInfo] = None

    def get(self, force: bool = False) -> Optional[WeatherInfo]:
        # return the cached forecast unless it's stale or we're forcing a refresh
        if OWM_API_KEY == "YOUR_API_KEY_HERE":
            log.warning("OWM_API_KEY not set in config.py — weather disabled.")
            return None

        if force or self._cache is None or self._cache.is_stale:
            self._fetch()

        return self._cache

    def blocks_watering(self) -> tuple[bool, str]:
        info = self.get()
        if info is None:
            return False, ""  # no data means we don't block anything
        if info.blocks_watering:
            return True, info.block_reason
        return False, ""

    def _fetch(self):
        url = OWM_URL.format(lat=OWM_LAT, lon=OWM_LON, key=OWM_API_KEY)
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            log.error("OpenWeatherMap returned HTTP %d: %s", e.code, e.reason)
            if e.code == 401:
                log.error("Your API key looks wrong — check OWM_API_KEY in config.py")
            return
        except Exception as e:
            log.error("Couldn't fetch weather: %s", e)
            return

        self._cache = self._parse(data)
        log.info(
            "Weather updated: max rain %.0f%% in next %dh — blocking watering: %s",
            self._cache.max_rain_prob_pct,
            WEATHER_LOOKAHEAD_H,
            self._cache.blocks_watering,
        )

    def _parse(self, data: dict) -> WeatherInfo:
        now       = time.time()
        lookahead = now + WEATHER_LOOKAHEAD_H * 3600
        location  = data.get("city", {}).get("name", "Unknown")

        slots: list[ForecastSlot] = []
        for item in data.get("list", []):
            slot = ForecastSlot(
                dt            = item["dt"],
                dt_txt        = item.get("dt_txt", ""),
                temp_c        = item["main"]["temp"],
                humidity_pct  = item["main"]["humidity"],
                rain_prob_pct = round(item.get("pop", 0) * 100, 1),
                rain_mm       = item.get("rain", {}).get("3h", 0.0),
                description   = item["weather"][0]["description"].capitalize(),
                icon          = item["weather"][0]["icon"],
            )
            slots.append(slot)

        # only look at slots within our lookahead window
        window     = [s for s in slots if now <= s.dt <= lookahead]
        max_prob   = max((s.rain_prob_pct for s in window), default=0.0)
        total_rain = sum(s.rain_mm for s in window)

        blocks = max_prob >= WEATHER_RAIN_BLOCK_PCT
        reason = ""
        if blocks:
            reason = (
                f"Rain forecast at {max_prob:.0f}% probability "
                f"in the next {WEATHER_LOOKAHEAD_H}h "
                f"(threshold is {WEATHER_RAIN_BLOCK_PCT:.0f}%). "
                f"Skipping watering to save water."
            )

        first = slots[0] if slots else None

        return WeatherInfo(
            fetched_at        = now,
            location          = location,
            slots             = slots[:8],  # next 24h worth of slots
            max_rain_prob_pct = max_prob,
            total_rain_mm     = round(total_rain, 2),
            blocks_watering   = blocks,
            block_reason      = reason,
            current_temp_c    = first.temp_c       if first else 0.0,
            current_humidity  = first.humidity_pct if first else 0.0,
            current_desc      = first.description  if first else "",
            current_icon      = first.icon         if first else "",
        )