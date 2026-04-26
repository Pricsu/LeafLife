# store.py
# Saves a processed sensor reading to a CSV file.
# Creates a new file each day so the data stays manageable.

import csv
from datetime import datetime
from pathlib import Path

from config import DATA_DIR
from logic import ProcessedReading

FIELDS = [
    "timestamp_iso", "timestamp",
    "soil_raw", "rain_raw",
    "soil_pct", "rain_pct",
    "soil_smoothed", "rain_smoothed",
    "soil_status", "rain_status",
    "soil_trend", "rain_trend",
    "alerts",
]


def _path() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    name = datetime.now().strftime("readings_%Y-%m-%d.csv")
    return DATA_DIR / name


def _ensure_header(p: Path):
    # write the header row if the file is brand new
    if not p.exists() or p.stat().st_size == 0:
        with open(p, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=FIELDS).writeheader()


def save(reading: ProcessedReading):
    p = _path()
    _ensure_header(p)
    row = {k: reading.to_dict()[k] for k in FIELDS}
    with open(p, "a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=FIELDS).writerow(row)