"""
US Intraday ORB Web App – Flask server
Mobile-friendly table view of NASDAQ 100 ORB signals.
Port: 5002 (dev) / from ORB_PORT env (prod)
"""

import logging
import os
from datetime import datetime, date

import pandas as pd
import pandas_market_calendars as mcal
import pytz
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template

from scanner import run_scanner, ET

# Load config
load_dotenv("config/orb-prod.env")
load_dotenv("config/orb.local.env", override=True)

app = Flask(__name__)
log = logging.getLogger(__name__)

_cache: dict = {"df": None, "ts": None}
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "60"))
PORT = int(os.getenv("ORB_PORT", "5002"))
DEBUG = os.getenv("FLASK_DEBUG", "false").lower() == "true"

# Holiday cache: {year -> set of date objects that are NASDAQ/NYSE holidays}
_holiday_cache: dict[int, set] = {}
_NYSE_CAL = mcal.get_calendar("XNYS")


def _get_holidays(year: int) -> set:
    """Return set of NASDAQ/NYSE holiday dates for the given year.

    Holidays = weekdays (Mon–Fri) on which the exchange is closed.
    Computed once per year and cached in _holiday_cache.
    """
    if year not in _holiday_cache:
        schedule = _NYSE_CAL.schedule(
            start_date=f"{year}-01-01",
            end_date=f"{year}-12-31",
        )
        trading_days = set(d.date() for d in schedule.index)
        all_bdays = set(
            d.date()
            for d in pd.date_range(f"{year}-01-01", f"{year}-12-31", freq="B")
        )
        _holiday_cache[year] = all_bdays - trading_days
        log.info("NASDAQ holidays loaded for %d: %s", year, sorted(_holiday_cache[year]))
    return _holiday_cache[year]


def _is_market_hours(now_et: datetime) -> tuple[bool, str]:
    """Return (is_open, reason).

    Closed on: weekends, NYSE/NASDAQ holidays, outside 9:25 AM – 4:30 PM ET.
    """
    if now_et.weekday() >= 5:          # Sat=5, Sun=6
        return False, "weekend"
    if now_et.date() in _get_holidays(now_et.year):
        return False, "holiday"
    start = now_et.replace(hour=9,  minute=25, second=0, microsecond=0)
    end   = now_et.replace(hour=16, minute=30, second=0, microsecond=0)
    if start <= now_et < end:
        return True, "open"
    return False, "outside_hours"


def _get_data() -> pd.DataFrame:
    now = datetime.now(ET)
    if _cache["df"] is not None and _cache["ts"] is not None:
        age = (now - _cache["ts"]).total_seconds()
        if age < CACHE_TTL_SECONDS:
            return _cache["df"]
    df = run_scanner()
    _cache["df"] = df
    _cache["ts"] = now
    return df


@app.route("/")
def index():
    now_et = datetime.now(ET)
    return render_template(
        "index.html",
        server_time=now_et.strftime("%Y-%m-%d %H:%M:%S ET"),
        refresh_seconds=CACHE_TTL_SECONDS,
    )


@app.route("/api/scan")
def api_scan():
    now_et = datetime.now(ET)

    is_open, reason = _is_market_hours(now_et)
    if not is_open:
        _CLOSED_MESSAGES = {
            "weekend":       "Market closed — weekend. Resumes Monday 9:25 AM ET.",
            "holiday":       "Market closed — NYSE/NASDAQ holiday today.",
            "outside_hours": "Market closed. Scanning resumes at 9:25 AM ET (Mon–Fri).",
        }
        return jsonify({
            "status": "market_closed",
            "message": _CLOSED_MESSAGES.get(reason, "Market closed."),
            "server_time": now_et.strftime("%Y-%m-%d %H:%M:%S ET"),
            "rows": [],
        })

    orb_ready = now_et.replace(hour=9, minute=45, second=0, microsecond=0)
    if now_et < orb_ready:
        return jsonify({
            "status": "too_early",
            "message": "Market data available after 9:45 AM ET",
            "server_time": now_et.strftime("%Y-%m-%d %H:%M:%S ET"),
            "rows": [],
        })

    df = _get_data()
    rows = df.to_dict(orient="records") if not df.empty else []
    return jsonify({
        "status": "ok",
        "server_time": now_et.strftime("%Y-%m-%d %H:%M:%S ET"),
        "scan_time": _cache["ts"].strftime("%H:%M:%S ET") if _cache["ts"] else "--",
        "rows": rows,
    })


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "time": datetime.now(ET).isoformat()})


if __name__ == "__main__":
    from waitress import serve
    log.info("Starting ORB server on port %d", PORT)
    serve(app, host="0.0.0.0", port=PORT)
