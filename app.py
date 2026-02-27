"""
US Intraday ORB Web App – Flask server
Mobile-friendly table view of NASDAQ 100 ORB signals.
Port: 5002 (dev) / from ORB_PORT env (prod)
"""

import logging
import os
from datetime import datetime

import pandas as pd
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


def _is_market_hours(now_et: datetime) -> bool:
    """Mon–Fri, 9:25 AM – 4:30 PM ET."""
    if now_et.weekday() >= 5:  # Sat=5, Sun=6
        return False
    start = now_et.replace(hour=9,  minute=25, second=0, microsecond=0)
    end   = now_et.replace(hour=16, minute=30, second=0, microsecond=0)
    return start <= now_et < end


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

    if not _is_market_hours(now_et):
        return jsonify({
            "status": "market_closed",
            "message": "Market closed. Scanning resumes at 9:25 AM ET (Mon–Fri).",
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
