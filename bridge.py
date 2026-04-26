# bridge.py
# Reads sensor data from Arduino over Serial and sends it to MQTT.
# Also listens for pump commands from the subscriber and forwards
# them back to the Arduino. It works both ways.
#
# Run it like this:
#   python bridge.py                   (auto finds the Arduino port)
#   python bridge.py --port COM3       (Windows)
#   python bridge.py --port /dev/ttyACM0  (Linux/Pi)
#   python bridge.py --simulate        (no Arduino needed for testing)

import argparse
import json
import logging
import math
import sys
import time
import threading

import paho.mqtt.client as mqtt
import serial
import serial.tools.list_ports

from config import (
    MQTT_BROKER, MQTT_PORT, MQTT_CLIENT_PUB,
    SERIAL_BAUD, SERIAL_PORT,
)

TOPIC_SENSORS     = "plantmonitor/sensors"
TOPIC_PUMP_CMD    = "plantmonitor/pump"
TOPIC_PUMP_STATUS = "plantmonitor/pump/status"
TOPIC_ULTRASONIC  = "plantmonitor/ultrasonic"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  [BRIDGE]  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# the serial connection — shared between the main loop and the MQTT callback
_ser = None
_ser_lock = threading.Lock()


def make_mqtt() -> mqtt.Client:
    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=MQTT_CLIENT_PUB,
        protocol=mqtt.MQTTv311,
    )

    def on_connect(c, userdata, flags, reason_code, props):
        if reason_code == 0:
            log.info("MQTT connected -> %s:%d", MQTT_BROKER, MQTT_PORT)
            c.subscribe(TOPIC_PUMP_CMD, qos=1)
            log.info("Listening for pump commands on %s", TOPIC_PUMP_CMD)
        else:
            log.error("MQTT connect failed: %s", str(reason_code))

    def on_disconnect(c, userdata, flags, reason_code, props):
        log.warning("MQTT disconnected: %s", str(reason_code))

    def on_message(c, userdata, msg):
        # pump command came in from subscriber — send it straight to the Arduino
        text = msg.payload.decode("utf-8", errors="ignore").strip()
        log.info("Pump command received: %s", text)
        with _ser_lock:
            if _ser and not isinstance(_ser, Simulator):
                try:
                    _ser.write((text + "\n").encode("utf-8"))
                    log.info("-> Arduino: %s", text)
                except Exception as e:
                    log.error("Couldn't write to serial: %s", e)
            else:
                # we're in simulation mode so fake a pump response
                try:
                    payload = json.loads(text)
                    dur = payload.get("duration", 3)
                    log.info("[SIM] Pump running for %.1fs", dur)
                    time.sleep(min(dur, 2))
                    status = json.dumps({
                        "type": "pump",
                        "status": "DONE",
                        "duration": dur
                    })
                    c.publish(TOPIC_PUMP_STATUS, status, qos=1)
                    log.info("[SIM] Pump done -> %s", status)
                except Exception as e:
                    log.error("Simulation pump error: %s", e)

    client.on_connect    = on_connect
    client.on_disconnect = on_disconnect
    client.on_message    = on_message
    return client


def mqtt_connect(client: mqtt.Client):
    # keep trying until we get a connection
    while True:
        try:
            client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
            client.loop_start()
            time.sleep(1)
            return
        except Exception as e:
            log.error("MQTT error: %s — retrying in 5s", e)
            time.sleep(5)


def find_port() -> str:
    # try to find the Arduino automatically by checking common driver names
    ports = list(serial.tools.list_ports.comports())
    keywords = ("arduino", "ch340", "cp210", "ftdi", "usb serial", "usb-serial")
    for p in ports:
        if any(k in (p.description or "").lower() for k in keywords):
            log.info("Auto-detected Arduino on %s (%s)", p.device, p.description)
            return p.device
    if ports:
        log.warning("Couldn't identify Arduino by name — trying %s", ports[0].device)
        return ports[0].device
    raise RuntimeError("No serial ports found. Plug in the Arduino or use --port.")


class Simulator:
    # fake Arduino that generates sine-wave sensor data — useful for testing
    def __init__(self):
        self._t  = 0.0
        self._ms = 0

    def readline(self) -> bytes:
        time.sleep(2)
        self._t  += 0.07
        self._ms += 2000
        soil = int(450 + 170 * math.sin(self._t))
        rain = int(800 + 200 * math.sin(self._t * 0.4 + 1))
        msg  = json.dumps({
            "type": "sensor",
            "soil": soil,
            "rain": rain,
            "ms":   self._ms,
        })
        return (msg + "\n").encode()

    def write(self, data: bytes): pass
    def close(self): pass


def run(port: str | None, simulate: bool):
    global _ser

    _ser = Simulator() if simulate else serial.Serial(
        port or find_port(), SERIAL_BAUD, timeout=10
    )
    if not simulate:
        time.sleep(2)  # give the Arduino a moment to reset after connecting
        log.info("Serial open on %s @ %d baud", _ser.name, SERIAL_BAUD)

    client = make_mqtt()
    mqtt_connect(client)

    log.info("Bridge running. Ctrl-C to stop.")

    try:
        while True:
            line = _ser.readline()
            if not line:
                continue

            text = line.decode("utf-8", errors="ignore").strip()
            if not text.startswith("{"):
                continue  # skip anything that isn't JSON

            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                log.warning("Got bad JSON: %r", text)
                continue

            msg_type = payload.get("type", "sensor")

            if msg_type == "sensor":
                client.publish(TOPIC_SENSORS, text, qos=1)
                log.info("-> sensors  %s", text)

            elif msg_type == "pump":
                client.publish(TOPIC_PUMP_STATUS, text, qos=1)
                status = payload.get("status", "?")
                dur    = payload.get("duration", 0)
                log.info("-> pump/status  %s  (%.1fs)", status, dur)

            elif msg_type == "ultrasonic":
                client.publish(TOPIC_ULTRASONIC, text, qos=1)
                dist  = payload.get("dist_cm", 0)
                angle = payload.get("angle", 0)
                clear = payload.get("clear", True)
                if not clear:
                    log.warning("-> OBSTACLE detected: %.1fcm at %d degrees", dist, angle)
                else:
                    log.debug("-> scan: %d deg -> %.1fcm clear", angle, dist)

            elif msg_type == "boot":
                log.info("Arduino is ready: %s", payload.get("msg", ""))

    except KeyboardInterrupt:
        log.info("Stopped.")
    finally:
        _ser.close()
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Arduino <-> MQTT bridge")
    ap.add_argument("--port",     help="Serial port (e.g. COM3 or /dev/ttyACM0)")
    ap.add_argument("--simulate", action="store_true", help="Run without real hardware")
    args = ap.parse_args()
    run(args.port, args.simulate)