"""
dashboard/app.py  — Plant Monitor
Smooth auto-update using st.fragment(run_every=...) — no page reload.
"""

import glob
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import (
    DASHBOARD_REFRESH_SEC, DATA_DIR,
    SOIL_ALERT_DRY, WATER_TARGET_PCT, PUMP_FLOW_RATE_ML_S,
    WATER_COOLDOWN_S, WEATHER_RAIN_BLOCK_PCT, WEATHER_LOOKAHEAD_H,
    OWM_API_KEY,
)
from watering import load_watering_log
from weather import WeatherService

# ── Page config ───────────────────────────────────────────────
st.set_page_config(page_title="Plant Monitor", page_icon="🌿", layout="wide")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Serif+Display&family=DM+Mono:wght@400;500&family=DM+Sans:wght@300;400;500&display=swap');
:root {
  --bg:#0e1410; --surface:#161d18; --surface2:#1e2921; --border:#2a3a2c;
  --green:#5dbe7a; --amber:#d4943a; --blue:#5b9bd5; --water:#38bdf8;
  --red:#f87171; --muted:#5a7060; --text:#c8dcc0; --dim:#7a9a80;
  --sky:#93c5fd; --sun:#fcd34d;
}
html,body,[class*="css"]{font-family:'DM Sans',sans-serif;background:var(--bg);color:var(--text);}
.stApp{background:var(--bg);}
#MainMenu,footer,header{visibility:hidden;}
.block-container{padding:2rem 2.5rem 4rem;max-width:1440px;}

.page-header{display:flex;align-items:baseline;gap:1.2rem;margin-bottom:1.8rem;
  border-bottom:1px solid var(--border);padding-bottom:1.2rem;}
.page-title{font-family:'DM Serif Display',serif;font-size:2.2rem;color:var(--green);margin:0;}

.section{font-size:0.63rem;letter-spacing:0.18em;text-transform:uppercase;
  color:var(--muted);font-family:'DM Mono',monospace;margin:1.6rem 0 0.7rem;}

.grid2{display:grid;grid-template-columns:repeat(2,1fr);gap:1rem;margin-bottom:1.2rem;}
.grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:1rem;margin-bottom:1.2rem;}

.card{background:var(--surface);border:1px solid var(--border);border-radius:12px;
  padding:1.3rem 1.5rem 1.1rem;position:relative;overflow:hidden;}
.card::before{content:'';position:absolute;top:0;left:0;right:0;height:3px;border-radius:12px 12px 0 0;}
.card.green::before{background:var(--green);}
.card.amber::before{background:var(--amber);}
.card.blue::before{background:var(--blue);}
.card.water::before{background:var(--water);}
.card.sky::before{background:var(--sky);}
.card.muted::before{background:var(--muted);}
.card.red::before{background:var(--red);}

.clabel{font-size:0.63rem;letter-spacing:0.14em;text-transform:uppercase;
  color:var(--dim);margin-bottom:0.5rem;font-family:'DM Mono',monospace;}
.cvalue{font-family:'DM Serif Display',serif;font-size:2.7rem;line-height:1;margin-bottom:0.4rem;}
.cunit{font-size:1.3rem;opacity:0.6;}
.cbadge{display:inline-flex;padding:0.18rem 0.7rem;border-radius:999px;
  font-size:0.68rem;font-weight:600;font-family:'DM Mono',monospace;
  letter-spacing:0.08em;text-transform:uppercase;margin-top:0.3rem;}
.ctrend{float:right;font-family:'DM Mono',monospace;font-size:0.7rem;color:var(--muted);}
.csub{font-size:0.72rem;color:var(--dim);font-family:'DM Mono',monospace;margin-top:0.35rem;}

.alert-box{display:flex;align-items:center;gap:0.9rem;padding:0.75rem 1.1rem;
  border-radius:8px;margin-bottom:0.5rem;font-size:0.88rem;font-weight:500;}
.alert-dry{background:#2a1a08;border:1px solid var(--amber);color:var(--amber);}
.alert-wet{background:#081a10;border:1px solid var(--green);color:var(--green);}
.alert-rain{background:#081020;border:1px solid var(--blue);color:var(--blue);}
.alert-wx{background:#0d1a2a;border:1px solid var(--sky);color:var(--sky);}

.pbar-bg{background:var(--surface2);border-radius:999px;height:9px;margin:0.4rem 0;overflow:hidden;}
.pbar-fill{height:9px;border-radius:999px;}

hr{border-color:var(--border)!important;margin:1.4rem 0!important;}
</style>
""", unsafe_allow_html=True)


# ── Static page header (renders once, never rerenders) ────────
st.markdown("""
<div class="page-header">
  <h1 class="page-title">🌿 Plant Monitor</h1>
</div>""", unsafe_allow_html=True)


# ── Data loaders ──────────────────────────────────────────────
def _load_sensors() -> pd.DataFrame:
    files = sorted(glob.glob(str(DATA_DIR / "readings_*.csv")))
    if not files:
        return pd.DataFrame()
    frames = [pd.read_csv(f) for f in files if Path(f).stat().st_size > 0]
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df["ts"] = pd.to_datetime(df["timestamp_iso"], utc=True, errors="coerce")
    return df.sort_values("ts").reset_index(drop=True)


def _load_irrigation() -> pd.DataFrame:
    rows = load_watering_log()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["timestamp_iso"], utc=True, errors="coerce")
    for col in ["duration_s", "ml_dispensed", "soil_pct_before", "weather_rain_prob"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    if "weather_blocked" not in df.columns:
        df["weather_blocked"] = False
    return df.sort_values("ts").reset_index(drop=True)


def _fetch_weather():
    if OWM_API_KEY == "YOUR_API_KEY_HERE":
        return None
    wx = WeatherService()
    return wx.get()


# ── Colour helpers ────────────────────────────────────────────
def sc(status):
    return {
        "dry": "#d4943a", "low": "#d4c43a",
        "optimal": "#5dbe7a", "wet": "#5b9bd5",
        "none": "#5a7060", "light": "#8bbfe8",
        "moderate": "#5b9bd5", "heavy": "#2d5c8a",
    }.get(status, "#5a7060")

def trend_arrow(t):
    return {"rising": "↑", "falling": "↓", "stable": "→"}.get(t, "→")

def card_class(soil_st):
    return "amber" if soil_st in ("dry", "low") else "green" if soil_st == "optimal" else "blue"


# ── Fragment — updates smoothly every N seconds ───────────────
# st.fragment rerenders ONLY this function, not the whole page.
# The header above stays completely still.
@st.fragment(run_every=DASHBOARD_REFRESH_SEC)
def live_dashboard():
    df     = _load_sensors()
    irr_df = _load_irrigation()
    wx     = _fetch_weather()

    if df.empty:
        st.warning("No sensor data yet. Start bridge.py and subscriber.py first.")
        st.code("python bridge.py --simulate\npython subscriber.py", language="bash")
        return

    last    = df.iloc[-1]
    soil_s  = float(last.get("soil_smoothed", 0))
    rain_s  = float(last.get("rain_smoothed",  0))
    soil_st = str(last.get("soil_status",  "—"))
    rain_st = str(last.get("rain_status",   "—"))
    soil_tr = str(last.get("soil_trend",    "stable"))
    alerts  = str(last.get("alerts", ""))
    sh      = sc(soil_st)
    rh      = sc(rain_st)

    # ── Alert banners ─────────────────────────────────────────
    if "TOO_DRY" in alerts:
        st.markdown('<div class="alert-box alert-dry">🏜 <b>TOO DRY</b> — Soil critically low. Auto-watering triggered if weather permits.</div>', unsafe_allow_html=True)
    if "TOO_WET" in alerts:
        st.markdown('<div class="alert-box alert-wet">💧 <b>TOO WET</b> — Risk of root rot. Watering paused.</div>', unsafe_allow_html=True)
    if "RAIN_HEAVY" in alerts:
        st.markdown('<div class="alert-box alert-rain">🌧 <b>HEAVY RAIN</b> — Rain sensor active. Watering blocked.</div>', unsafe_allow_html=True)
    if wx and wx.blocks_watering:
        st.markdown(f'<div class="alert-box alert-wx">🌦 <b>RAIN FORECAST</b> — {wx.max_rain_prob_pct:.0f}% chance of rain in next {WEATHER_LOOKAHEAD_H}h. Watering conserved.</div>', unsafe_allow_html=True)

    # ── Sensor cards ──────────────────────────────────────────
    st.markdown('<div class="section">Live Sensor Readings</div>', unsafe_allow_html=True)
    st.markdown(f"""
    <div class="grid2">
      <div class="card {card_class(soil_st)}">
        <div class="clabel">Soil Moisture</div>
        <div class="cvalue" style="color:{sh}">{soil_s:.1f}<span class="cunit">%</span></div>
        <span class="cbadge" style="background:{sh}18;color:{sh};border:1px solid {sh}44">{soil_st.upper()}</span>
        <span class="ctrend">{trend_arrow(soil_tr)} {soil_tr}</span>
      </div>
      <div class="card {'blue' if rain_s > 20 else 'muted'}">
        <div class="clabel">Rain Intensity</div>
        <div class="cvalue" style="color:{rh}">{rain_s:.1f}<span class="cunit">%</span></div>
        <span class="cbadge" style="background:{rh}18;color:{rh};border:1px solid {rh}44">{rain_st.upper()}</span>
      </div>
    </div>""", unsafe_allow_html=True)

    # Soil progress bar
    st.markdown(f"""
    <div style="margin-bottom:1.4rem">
      <div style="display:flex;justify-content:space-between;font-size:0.7rem;
           font-family:'DM Mono',monospace;color:var(--dim);margin-bottom:0.25rem">
        <span>0% DRY</span><span>Target {WATER_TARGET_PCT:.0f}%</span><span>100% WET</span>
      </div>
      <div class="pbar-bg">
        <div class="pbar-fill" style="width:{min(soil_s,100):.1f}%;background:{sh}"></div>
      </div>
      <div style="font-size:0.68rem;color:var(--dim);font-family:'DM Mono',monospace;margin-top:0.15rem">
        Water below {SOIL_ALERT_DRY:.0f}% &nbsp;·&nbsp; target {WATER_TARGET_PCT:.0f}%
      </div>
    </div>""", unsafe_allow_html=True)

    # ── Weather panel ─────────────────────────────────────────
    st.markdown('<div class="section">Weather Forecast (OpenWeatherMap)</div>', unsafe_allow_html=True)

    if OWM_API_KEY == "YOUR_API_KEY_HERE":
        st.info("Add your OpenWeatherMap API key to config.py to enable weather integration.")
    elif wx is None:
        st.warning("Weather data unavailable — check API key and internet connection.")
    else:
        wx_card_cls     = "red" if wx.blocks_watering else "sky"
        wx_status       = f"BLOCKED — {wx.max_rain_prob_pct:.0f}% rain prob" if wx.blocks_watering else "CLEAR TO WATER"
        wx_status_color = "#f87171" if wx.blocks_watering else "#5dbe7a"

        col_wx1, col_wx2 = st.columns(2)
        with col_wx1:
            st.markdown(f"""
            <div class="card {wx_card_cls}">
              <div class="clabel">Watering Status</div>
              <div style="font-family:'DM Serif Display',serif;font-size:1.3rem;
                   color:{wx_status_color};margin:0.5rem 0">{wx_status}</div>
              <div class="csub">{wx.location} &nbsp;·&nbsp; updated {wx.fetched_str}</div>
            </div>""", unsafe_allow_html=True)
        with col_wx2:
            st.markdown(f"""
            <div class="card sky">
              <div class="clabel">Max Rain Prob (next {WEATHER_LOOKAHEAD_H}h)</div>
              <div class="cvalue" style="color:var(--sky)">{wx.max_rain_prob_pct:.0f}<span class="cunit">%</span></div>
              <div class="csub">block threshold: {WEATHER_RAIN_BLOCK_PCT:.0f}%</div>
            </div>""", unsafe_allow_html=True)

    # ── Irrigation cards ──────────────────────────────────────
    st.markdown('<div class="section">Irrigation System</div>', unsafe_allow_html=True)

    today         = datetime.now().strftime("%Y-%m-%d")
    daily_ml      = 0.0
    sprays_today  = 0
    skipped_today = 0
    elapsed_s     = "No spray yet"
    last_dur      = last_ml = last_soil = 0.0

    if not irr_df.empty:
        today_irr     = irr_df[irr_df["ts"].dt.strftime("%Y-%m-%d") == today]
        actual        = today_irr[today_irr["weather_blocked"] == False]
        skipped       = today_irr[today_irr["weather_blocked"] == True]
        daily_ml      = actual["ml_dispensed"].sum()
        sprays_today  = len(actual)
        skipped_today = len(skipped)

        if not actual.empty:
            last_spray = actual.iloc[-1]
            last_ts    = last_spray["ts"]
            if hasattr(last_ts, "tzinfo") and last_ts.tzinfo is None:
                last_ts = last_ts.replace(tzinfo=timezone.utc)
            elapsed    = datetime.now(tz=timezone.utc) - last_ts
            mins       = int(elapsed.total_seconds() // 60)
            sec        = int(elapsed.total_seconds() % 60)
            elapsed_s  = f"{mins}m {sec}s ago"
            last_dur   = float(last_spray.get("duration_s", 0))
            last_ml    = float(last_spray.get("ml_dispensed", 0))
            last_soil  = float(last_spray.get("soil_pct_before", 0))

    st.markdown(f"""
    <div class="grid3">
      <div class="card water">
        <div class="clabel">Last Spray</div>
        <div style="font-family:'DM Serif Display',serif;font-size:1.45rem;
             color:var(--water);margin:0.4rem 0">{elapsed_s}</div>
        <div class="csub">{last_dur:.1f}s open &nbsp;·&nbsp; {last_ml:.0f}ml &nbsp;·&nbsp; soil was {last_soil:.1f}%</div>
      </div>
      <div class="card water">
        <div class="clabel">Water Used Today</div>
        <div class="cvalue" style="color:var(--water)">{daily_ml:.0f}<span class="cunit">ml</span></div>
        <div class="csub">{sprays_today} spray{"s" if sprays_today != 1 else ""} &nbsp;·&nbsp; {skipped_today} skipped by forecast</div>
      </div>
      <div class="card muted">
        <div class="clabel">Valve Config</div>
        <div style="font-family:'DM Mono',monospace;font-size:0.8rem;margin-top:0.4rem;line-height:2">
          <span style="color:var(--dim)">flow rate</span> &nbsp;<b>{PUMP_FLOW_RATE_ML_S:.0f} ml/s</b><br>
          <span style="color:var(--dim)">cooldown </span> &nbsp;<b>{WATER_COOLDOWN_S:.0f}s</b><br>
          <span style="color:var(--dim)">target   </span> &nbsp;<b>{WATER_TARGET_PCT:.0f}%</b>
        </div>
      </div>
    </div>""", unsafe_allow_html=True)


# ── Run ───────────────────────────────────────────────────────
live_dashboard()