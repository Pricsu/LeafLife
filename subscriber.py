# subscriber.py
# Listens to MQTT, processes sensor data, triggers watering,
# and logs everything to CSV.
#
# It subscribes to three topics:
#   plantmonitor/sensors      — soil and rain readings from the Arduino
#   plantmonitor/pump/status  — confirmations from the Arduino after watering
#   plantmonitor/ultrasonic   — sweep data from the obstacle detection sensors

import csv
import json
import logging
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path

import paho.mqtt.client as mqtt

from config import MQTT_BROKER, MQTT_CLIENT_SUB, MQTT_PORT, DATA_DIR
from logic import SensorProcessor
from store import save
from watering import WateringController
from weather import WeatherService

TOPIC_SENSORS      = "plantmonitor/sensors"
TOPIC_PUMP_CMD     = "plantmonitor/pump"
TOPIC_PUMP_STATUS  = "plantmonitor/pump/status"
TOPIC_ULTRASONIC   = "plantmonitor/ultrasonic"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  [SUBSCRIBER]  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

processor = SensorProcessor()
weather   = WeatherService()
irrigator = WateringController(weather_service=weather)

# track the current pump state so the dashboard can show it
pump_state = {
    "status":            "IDLE",
    "duration":          0.0,
    "last_done":         None,
    "last_abort_reason": "",
    "last_abort_dist":   0.0,
    "last_abort_angle":  0,
}

# ultrasonic sweep log
ULTRASONIC_LOG    = DATA_DIR / "ultrasonic_log.csv"
ULTRASONIC_FIELDS = ["timestamp_iso", "angle", "dist_cm", "clear", "pump_was_running"]


def _ensure_ultrasonic_log():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not ULTRASONIC_LOG.exists() or ULTRASONIC_LOG.stat().st_size == 0:
        with open(ULTRASONIC_LOG, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=ULTRASONIC_FIELDS).writeheader()


def _log_ultrasonic(angle, dist_cm, clear, pump_running):
    _ensure_ultrasonic_log()
    ts = datetime.now(tz=timezone.utc).isoformat()
    with open(ULTRASONIC_LOG, "a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=ULTRASONIC_FIELDS).writerow({
            "timestamp_iso":    ts,
            "angle":            angle,
            "dist_cm":          dist_cm,
            "clear":            clear,
            "pump_was_running": pump_state["status"] == "RUNNING",
        })


# MQTT client reference — set in main() and used by the watering controller
_mqtt_client = None


def _publish(topic: str, payload: str):
    if _mqtt_client:
        _mqtt_client.publish(topic, payload, qos=1)


def on_connect(client, userdata, flags, reason_code, props):
    if reason_code == 0:
        log.info("Connected to %s", MQTT_BROKER)
        client.subscribe(TOPIC_SENSORS,     qos=1)
        client.subscribe(TOPIC_PUMP_STATUS, qos=1)
        client.subscribe(TOPIC_ULTRASONIC,  qos=1)
        log.info("Subscribed to sensors / pump/status / ultrasonic")
        irrigator.set_mqtt_publish(_publish)
    else:
        log.error("Connection failed: %s", str(reason_code))


def on_message(client, userdata, msg):
    topic = msg.topic
    try:
        payload = json.loads(msg.payload.decode())
    except Exception as e:
        log.warning("Bad payload on %s: %s", topic, e)
        return

    if topic == TOPIC_SENSORS:
        soil_raw = int(payload.get("soil", 0))
        rain_raw = int(payload.get("rain",  0))
        reading  = processor.process(soil_raw, rain_raw)
        save(reading)

        trend = "up" if reading.soil_trend == "rising"  else \
                "dn" if reading.soil_trend == "falling" else "--"
        alert = f"  ALERT {reading.alerts}" if reading.alerts else ""
        log.info("soil=%5.1f%% (%s %s)  rain=%5.1f%% (%s)%s",
                 reading.soil_smoothed, reading.soil_status, trend,
                 reading.rain_smoothed, reading.rain_status, alert)

        # log what the weather is doing
        wx = weather.get()
        if wx:
            block = f" | BLOCKED ({wx.max_rain_prob_pct:.0f}% rain)" if wx.blocks_watering else ""
            log.info("Weather: %s %.1fC  rain %.0f%%%s",
                     wx.current_desc, wx.current_temp_c, wx.max_rain_prob_pct, block)

        # check if we need to water
        cooldown = irrigator.cooldown_remaining
        if cooldown > 0:
            log.info("Cooldown: %.0fs left", cooldown)
        else:
            event = irrigator.evaluate(reading.soil_smoothed, reading.rain_smoothed)
            if event:
                log.info("Pump command sent: %.1fs / %.0fml", event.duration_s, event.ml_dispensed)
                pump_state["status"]   = "SCANNING"
                pump_state["duration"] = event.duration_s

    elif topic == TOPIC_PUMP_STATUS:
        status = payload.get("status", "?")
        pump_state["status"] = status

        if status == "SCANNING":
            log.info("Pump: scanning for obstacles...")

        elif status == "RUNNING":
            dur = float(payload.get("duration", 0))
            pump_state["duration"] = dur
            log.info("Pump: running for %.1fs", dur)

        elif status == "DONE":
            dur = float(payload.get("duration", 0))
            pump_state["last_done"] = datetime.now(tz=timezone.utc).isoformat()
            pump_state["status"]    = "IDLE"
            log.info("Pump: done after %.1fs", dur)

        elif status == "ABORTED":
            dist  = float(payload.get("dist_cm", 0))
            angle = int(payload.get("angle", 0))
            pump_state["status"]            = "IDLE"
            pump_state["last_abort_reason"] = "obstacle during pump"
            pump_state["last_abort_dist"]   = dist
            pump_state["last_abort_angle"]  = angle
            log.warning("Pump: stopped — obstacle at %.1fcm / %d degrees", dist, angle)

        elif status == "BLOCKED":
            reason = payload.get("reason", "obstacle")
            pump_state["status"]            = "IDLE"
            pump_state["last_abort_reason"] = reason
            log.warning("Pump: blocked before starting — %s", reason)

        elif status == "ERROR":
            msg_txt = payload.get("msg", "unknown")
            pump_state["status"] = "IDLE"
            log.error("Pump error: %s", msg_txt)

    elif topic == TOPIC_ULTRASONIC:
        angle   = int(payload.get("angle", 0))
        dist_cm = float(payload.get("dist_cm", 999))
        clear   = bool(payload.get("clear", True))

        _log_ultrasonic(angle, dist_cm, clear, pump_state["status"] == "RUNNING")

        if not clear:
            log.warning("Obstacle: %.1fcm at %d degrees", dist_cm, angle)
        else:
            log.debug("Scan: %d deg -> %.1fcm clear", angle, dist_cm)


def on_disconnect(client, userdata, flags, reason_code, props):
    log.warning("Disconnected: %s — will reconnect", str(reason_code))


def main():
    global _mqtt_client

    log.info("Fetching weather forecast...")
    weather.get()

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=MQTT_CLIENT_SUB,
        protocol=mqtt.MQTTv311,
    )
    client.on_connect    = on_connect
    client.on_message    = on_message
    client.on_disconnect = on_disconnect
    client.reconnect_delay_set(min_delay=2, max_delay=30)

    _mqtt_client = client

    log.info("Connecting to %s:%d ...", MQTT_BROKER, MQTT_PORT)
    client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)

    def _stop(sig, frame):
        log.info("Shutting down...")
        client.loop_stop()
        client.disconnect()
        sys.exit(0)

    signal.signal(signal.SIGINT,  _stop)
    signal.signal(signal.SIGTERM, _stop)

    client.loop_forever()


if __name__ == "__main__":
    main()