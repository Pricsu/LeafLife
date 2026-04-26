# 🌿 Plant Monitor

An automated soil moisture monitoring and smart irrigation system built with Arduino UNO R3, Python, MQTT, and Streamlit.

---

## System Overview

```
Arduino UNO R3
  ├── Capacitive Soil Moisture Sensor v2.0  (A0)
  ├── Raindrop Module                        (A1)
  ├── TB6612FNG Motor Driver + DC Water Pump (D3, D4, D5, D6)
  ├── Servo Motor                            (D11)
  └── 2x HC-SR04 Ultrasonic Sensors          (D8/D9 and D12/D13)
         │
         │  USB Serial  9600 baud
         ▼
    bridge.py           reads Serial, publishes to MQTT
         │                       forwards pump commands back to Arduino
         │  MQTT  broker.hivemq.com
         ▼
    subscriber.py       receives sensor data, runs logic, saves CSV
         │
         ├── logic.py        converts ADC → %, smoothing, alerts
         ├── watering.py     irrigation decision + pump command
         ├── weather.py      OpenWeatherMap forecast (rain prediction)
         └── store.py        writes data/readings_YYYY-MM-DD.csv
                    │
                    ▼
         dashboard/app.py    Streamlit real-time dashboard
```

---

## Hardware

### Components

| Component | Purpose |
|---|---|
| Arduino UNO R3 | Microcontroller — reads sensors, controls motor |
| Capacitive Soil Moisture Sensor v2.0 | Measures soil humidity |
| Raindrop Module | Detects active rainfall |
| TB6612FNG Motor Driver | Controls DC water pump |
| DC Water Pump | Delivers water to the plant |
| Servo Motor | Rotates ultrasonic sensors 15°–165° |
| 2x HC-SR04 Ultrasonic Sensors | 360° obstacle/person detection |

### Wiring

**Capacitive Soil Moisture Sensor v2.0**
| Sensor | Arduino |
|---|---|
| VCC | 3.3V ⚠ NOT 5V — damages the sensor |
| GND | GND |
| AOUT | A0 |

**Raindrop Module**
| Sensor | Arduino |
|---|---|
| VCC | 5V |
| GND | GND |
| AO | A1 |

**TB6612FNG Motor Driver**
| TB6612FNG | Arduino |
|---|---|
| PWMA | D3 (PWM) |
| AIN1 | D5 |
| AIN2 | D4 |
| STBY | D6 |
| VM | External power supply + |
| VCC | 5V |
| GND | GND + external supply GND |
| A01 | Motor + |
| A02 | Motor - |

**HC-SR04 Sensor A (front — covers 0°–180°)**
| Sensor | Arduino |
|---|---|
| VCC | 5V |
| GND | GND |
| TRIG | D8 |
| ECHO | D9 |

**HC-SR04 Sensor B (back — covers 180°–360°)**
| Sensor | Arduino |
|---|---|
| VCC | 5V |
| GND | GND |
| TRIG | D12 |
| ECHO | D13 |

**Servo Motor**
| Servo | Arduino |
|---|---|
| Signal | D11 |
| VCC | 5V external (use external supply for large servos) |
| GND | GND |

> Mount Sensor A and Sensor B exactly 180° apart on the servo arm. One sweep from 15°→165° gives full 360° coverage.

---

## Software Setup

### 1 — Flash the Arduino

Open `arduino/soil_rain_monitor.ino` in Arduino IDE and upload to the UNO R3.

Verify in Serial Monitor (9600 baud) — you should see:
```json
{"type":"boot","msg":"ready — dual sensor 360"}
{"type":"sensor","soil":542,"rain":310,"ms":2000}
```

### 2 — Install Python dependencies

```bash
pip install -r requirements.txt
```

### 3 — Configure the project

Edit `config.py` — the only file you need to touch:

```python
# Serial port (leave None to auto-detect, or set manually)
SERIAL_PORT = None          # "COM3" on Windows, "/dev/ttyACM0" on Linux

# Sensor calibration — measure with your actual sensors
SOIL_DRY = 620              # ADC reading in dry air
SOIL_WET = 280              # ADC reading submerged in water
RAIN_DRY = 1000             # ADC reading on dry board
RAIN_WET = 250              # ADC reading on wet board

# Watering thresholds
SOIL_ALERT_DRY = 25.0       # water when soil drops below this %
WATER_TARGET_PCT = 60.0     # stop watering when this % is reached

# OpenWeatherMap (optional)
OWM_API_KEY = "your_key"    # get free key at openweathermap.org/api
OWM_LAT = 46.7712           # your latitude
OWM_LON = 23.6236           # your longitude

# Obstacle detection
OBSTACLE_DISTANCE_CM = 15.0 # testing — set to 50.0 for production
```

### 4 — Calibrate your sensors

With the Arduino plugged in, open Serial Monitor at 9600 baud:

| Test | Action | Note the value |
|---|---|---|
| `SOIL_DRY` | Hold sensor in dry air | `soil` value |
| `SOIL_WET` | Submerge sensor in water | `soil` value |
| `RAIN_DRY` | Leave rain board completely dry | `rain` value |
| `RAIN_WET` | Drop water on rain board | `rain` value |

---

## Running

Open three terminals in the project folder:

```bash
# Terminal 1 — bridge (Arduino → MQTT)
python bridge.py

# No Arduino? Run in simulation mode:
python bridge.py --simulate

# Terminal 2 — subscriber (MQTT → logic → CSV)
python subscriber.py

# Terminal 3 — dashboard
streamlit run dashboard/app.py
```

Open **http://localhost:8501** in your browser.

---

## How It Works

### Sensor Pipeline

1. Arduino reads soil moisture and rain sensors every 2 seconds
2. Averages 10 ADC samples to reduce noise
3. Sends JSON over Serial: `{"type":"sensor","soil":542,"rain":310,"ms":2000}`
4. `bridge.py` receives the JSON and publishes it to MQTT
5. `subscriber.py` receives it, converts ADC → %, applies smoothing and logic
6. Results are saved to `data/readings_YYYY-MM-DD.csv`

### Irrigation Decision Chain

When a sensor reading arrives, watering is evaluated in this order:

```
1. Soil moisture >= threshold?     → no action needed
2. Still in cooldown period?       → wait (default: 2 minutes)
3. Rain sensor active (> 65%)?     → skip, rain sensor detects rain
4. Weather forecast rain > 70%?    → skip, rain expected soon
5. All clear                       → calculate duration and spray
```

### Watering Duration Calculation

```
deficit      = TARGET_PCT - current_soil_pct
ml_needed    = deficit × ML_PER_PCT
duration_s   = ml_needed ÷ PUMP_FLOW_RATE_ML_S
duration_s   = clamp(duration_s, MIN=2s, MAX=10s)
```

### 360° Safety System

Before the pump starts and continuously during watering:

1. Servo sweeps from 15° to 165°
2. Sensor A reads the front arc (15°–165°)
3. Sensor B (mounted 180° opposite) reads the back arc (195°–345°)
4. Together they cover the full 360°
5. If anything is detected within `OBSTACLE_DISTANCE_CM`:
   - **Before pump starts** → `BLOCKED` — pump never activates
   - **During pumping** → `ABORTED` — pump stops immediately

### MQTT Topics

| Topic | Direction | Content |
|---|---|---|
| `plantmonitor/sensors` | Arduino → Python | Soil + rain ADC values |
| `plantmonitor/pump` | Python → Arduino | Pump START command + duration |
| `plantmonitor/pump/status` | Arduino → Python | RUNNING / DONE / ABORTED / BLOCKED |
| `plantmonitor/ultrasonic` | Arduino → Python | Sweep angle + distance readings |

---

## Project Structure

```
plant_monitor/
├── arduino/
│   └── soil_rain_monitor.ino   # Arduino sketch (all sensor + motor logic)
├── dashboard/
│   └── app.py                  # Streamlit live dashboard
├── data/                       # Auto-created — CSV files saved here
│   ├── readings_YYYY-MM-DD.csv # Sensor readings
│   └── watering_log.csv        # Irrigation events
├── bridge.py                   # Serial ↔ MQTT bidirectional bridge
├── subscriber.py               # MQTT subscriber + orchestrator
├── logic.py                    # Sensor processing (ADC → % + smoothing)
├── store.py                    # CSV writer
├── watering.py                 # Irrigation controller
├── weather.py                  # OpenWeatherMap integration
├── config.py                   # All settings in one place
└── requirements.txt
```

---

## Alert System

| Alert | Condition | Action |
|---|---|---|
| `TOO_DRY` | soil smoothed < 25% | Watering triggered |
| `TOO_WET` | soil smoothed > 85% | Watering blocked |
| `RAIN_HEAVY` | rain sensor > 65% | Watering blocked |
| `RAIN_FORECAST` | OWM rain prob > 70% in next 6h | Watering skipped |
| `ABORTED` | Obstacle during pumping | Pump stops immediately |
| `BLOCKED` | Obstacle before pumping | Pump never starts |

---

## Soil Moisture Reference

| Status | Range | Action |
|---|---|---|
| `dry` | 0–25% | Water immediately |
| `low` | 25–45% | Water soon |
| `optimal` | 45–75% | No action needed |
| `wet` | 75–100% | Stop watering |

---

## Troubleshooting

| Problem | Likely cause | Fix |
|---|---|---|
| `PermissionError` on serial port | Arduino IDE Serial Monitor is open | Close Serial Monitor, then run bridge.py |
| `MQTT disconnected rc=7` | Broker rate limit | Change `MQTT_BROKER` in config.py to `broker.emqx.io` |
| Motor doesn't spin | STBY pin not HIGH or no common GND | Check TB6612FNG wiring, ensure external GND is shared |
| Soil reads wrong % | Wrong calibration values | Measure `SOIL_DRY` / `SOIL_WET` and update config.py |
| Watering never triggers | `SOIL_ALERT_DRY` too low | Raise threshold in config.py (e.g. 30.0 or 35.0) |
| Obstacle always detected | `OBSTACLE_DISTANCE_CM` too large | Lower to 15.0 for testing |
| Dashboard not updating | Old Streamlit version | Run `pip install --upgrade streamlit` (needs ≥ 1.37) |

---

## Adding a Real Relay (Future)

When you want to control the pump via Raspberry Pi GPIO instead of TB6612FNG, replace `_open_valve()` in `watering.py`:

```python
import RPi.GPIO as GPIO
RELAY_PIN = 17
GPIO.setmode(GPIO.BCM)
GPIO.setup(RELAY_PIN, GPIO.OUT)

def _open_valve(self, duration_s):
    GPIO.output(RELAY_PIN, GPIO.HIGH)
    time.sleep(duration_s)
    GPIO.output(RELAY_PIN, GPIO.LOW)
```

---

## Weather API Setup

1. Register at **https://openweathermap.org/api** (free)
2. Copy your API key
3. Find your coordinates at **https://www.latlong.net**
4. Update `config.py`:

```python
OWM_API_KEY = "your_api_key_here"
OWM_LAT     = 46.7712
OWM_LON     = 23.6236
```

Free tier allows 1000 calls/day. The system caches forecasts for 30 minutes, using only ~48 calls/day.
