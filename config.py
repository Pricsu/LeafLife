# config.py
# ─────────────────────────────────────────────────────────────
# Single source of truth for the entire project.
# Edit this file to change ports, topics, thresholds, etc.
# ─────────────────────────────────────────────────────────────

# ── MQTT ──────────────────────────────────────────────────────
MQTT_BROKER   = "broker.hivemq.com"
MQTT_PORT     = 1883
MQTT_TOPIC    = "plantmonitor/sensors"   # publisher → subscriber
MQTT_CLIENT_PUB = "plant-bridge-agri01"
MQTT_CLIENT_SUB = "plant-sub-agri01"

# ── Serial (Arduino) ──────────────────────────────────────────
SERIAL_BAUD   = 9600
SERIAL_PORT   = "COM3"          # None = auto-detect

# ── Sensor calibration (raw ADC 0–1023) ──────────────────────
#   Measure with your actual sensor:
#     Soil dry air  → note value → SOIL_DRY
#     Soil in water → note value → SOIL_WET
SOIL_DRY = 620
SOIL_WET = 280

RAIN_DRY = 1000
RAIN_WET = 250

# ── Logic thresholds (smoothed %) ─────────────────────────────
SOIL_ALERT_DRY   = 25.0   # below → TOO_DRY
SOIL_ALERT_WET   = 85.0   # above → TOO_WET
RAIN_ALERT_HEAVY = 65.0   # above → RAIN_HEAVY

SMOOTH_WINDOW    = 8      # moving-average sample count

# ── CSV ───────────────────────────────────────────────────────
import pathlib
DATA_DIR = pathlib.Path(__file__).parent / "data"

# ── Dashboard ─────────────────────────────────────────────────
DASHBOARD_REFRESH_SEC = 4
DASHBOARD_HISTORY_MIN = 60

# ── Watering / Irrigation ─────────────────────────────────────
# Valve opens automatically when soil_smoothed < SOIL_ALERT_DRY

# Flow rate of the pump/valve (ml per second)
# Typical small USB pump: 30-80 ml/s. Adjust after measuring yours.
PUMP_FLOW_RATE_ML_S = 50.0        # ml per second

# Minimum spray duration (seconds)
WATER_MIN_DURATION_S = 2.0

# Maximum spray duration (seconds) — safety cap for a small pot
WATER_MAX_DURATION_S = 10.0

# Target soil moisture after watering (%)
WATER_TARGET_PCT = 60.0

# How many ml raises moisture 1% for this pot size
# Small pot (1L soil): roughly 5-10 ml per 1%
ML_PER_PCT = 7.0

# Minimum cooldown between two spray events (seconds)
WATER_COOLDOWN_S = 120.0          # 2 minutes

# Don't water if it's already raining heavily
BLOCK_WATERING_ON_RAIN = True

# ── Weather API (OpenWeatherMap) ──────────────────────────────
# Get your free API key at: https://openweathermap.org/api
# Free tier: 1000 calls/day — we cache for 30 min so ~48 calls/day
OWM_API_KEY = "YOUR_API_KEY_HERE"     # <-- paste your key here

# Your location — find lat/lon at https://www.latlong.net/
OWM_LAT      = 46.7712                  # example: Cluj-Napoca, Romania
OWM_LON      = 23.6236

# How many hours ahead to check for rain
WEATHER_LOOKAHEAD_H = 6

# Skip watering if forecast rain probability exceeds this threshold
WEATHER_RAIN_BLOCK_PCT = 70.0           # %

# How often to refresh the weather forecast (seconds)
# Free tier = 1000 calls/day, 30 min = 48 calls/day — well within limits
WEATHER_CACHE_SEC = 1800                # 30 minutes

# ── Ultrasonic / Safety ───────────────────────────────────────
OBSTACLE_DISTANCE_CM = 15.0    # testing range — change to 50.0 for production