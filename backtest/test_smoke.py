"""
Smoke tests for US Intraday ORB Scanner
=========================================
Verifies:
  1. All required packages import correctly
  2. Black-Scholes Greeks produce sensible values
  3. ORB signal logic is correct for known inputs
  4. yfinance can fetch live 15-min data for one ticker
  5. Backtest simulate_day() handles a synthetic day correctly
  6. Flask app creates without error
Run: python backtest/test_smoke.py
"""

import sys
import os
import math
from datetime import datetime

import pytz

# Allow imports from repo root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

PASS = "\033[92m PASS\033[0m"
FAIL = "\033[91m FAIL\033[0m"
INFO = "\033[94m INFO\033[0m"

results = []

def check(name: str, condition: bool, detail: str = "") -> bool:
    tag = PASS if condition else FAIL
    suffix = f"  [{detail}]" if detail else ""
    print(f"{tag}  {name}{suffix}")
    results.append((name, condition))
    return condition


# ============================================================
# 1. Package imports
# ============================================================
print("\n-- Package imports --------------------------------------")

try:
    import flask; check("flask import", True, flask.__version__)
except ImportError as e:
    check("flask import", False, str(e))

try:
    import waitress; check("waitress import", True)
except ImportError as e:
    check("waitress import", False, str(e))

try:
    import yfinance as yf; check("yfinance import", True, yf.__version__)
except ImportError as e:
    check("yfinance import", False, str(e))

try:
    import pandas as pd; check("pandas import", True, pd.__version__)
except ImportError as e:
    check("pandas import", False, str(e))

try:
    import numpy as np; check("numpy import", True, np.__version__)
except ImportError as e:
    check("numpy import", False, str(e))

try:
    import scipy; check("scipy import", True, scipy.__version__)
except ImportError as e:
    check("scipy import", False, str(e))

try:
    import pytz; check("pytz import", True)
except ImportError as e:
    check("pytz import", False, str(e))

try:
    import dotenv; check("python-dotenv import", True)
except ImportError as e:
    check("python-dotenv import", False, str(e))


# ============================================================
# 2. Black-Scholes Greeks
# ============================================================
print("\n-- Black-Scholes Greeks ---------------------------------")

try:
    from scanner import bs_delta, bs_theta_hourly, _d1_d2

    # ATM call, 1-hour to expiry, IV=35%, S≈K → delta should be ~0.50-0.60
    S, K, r, sigma = 200.0, 200.0, 0.05, 0.35
    T_1hr = 1 / (365 * 24)

    delta_c = bs_delta(S, K, r, sigma, T_1hr, "call")
    delta_p = bs_delta(S, K, r, sigma, T_1hr, "put")
    theta_h = bs_theta_hourly(S, K, r, sigma, T_1hr, "call")

    check("ATM call delta 0.45–0.65", 0.45 <= delta_c <= 0.65, f"delta={delta_c:.4f}")
    check("Put delta is negative",    delta_p < 0,             f"delta={delta_p:.4f}")
    check("Theta/hr is negative",     theta_h < 0,             f"theta={theta_h:.6f}")

    # Deep ITM call → delta near 1
    delta_itm = bs_delta(200, 150, r, sigma, T_1hr, "call")
    check("Deep ITM call delta > 0.95", delta_itm > 0.95, f"{delta_itm:.4f}")

    # Edge case: T=0 → NaN (graceful)
    d = bs_delta(200, 200, r, sigma, 0, "call")
    check("T=0 returns NaN gracefully", math.isnan(d), f"{d}")

except Exception as exc:
    check("Black-Scholes suite", False, str(exc))


# ============================================================
# 3. ORB signal logic
# ============================================================
print("\n-- ORB signal logic -------------------------------------")

try:
    # Replicate the signal logic from scanner.py
    def get_signal(orb_high, orb_low, current_price):
        if current_price > orb_high:
            return "BUY CALL"
        elif current_price < orb_low:
            return "BUY PUT"
        return "NEUTRAL"

    check("Above ORB_High → BUY CALL",  get_signal(100, 98, 101)  == "BUY CALL")
    check("Below ORB_Low  → BUY PUT",   get_signal(100, 98, 97)   == "BUY PUT")
    check("Inside range   → NEUTRAL",   get_signal(100, 98, 99)   == "NEUTRAL")
    check("At ORB_High    → NEUTRAL",   get_signal(100, 98, 100)  == "NEUTRAL")
    check("At ORB_Low     → NEUTRAL",   get_signal(100, 98, 98)   == "NEUTRAL")

except Exception as exc:
    check("Signal logic suite", False, str(exc))


# ============================================================
# 4. yfinance connectivity (single ticker, 1 day)
# ============================================================
print("\n-- yfinance connectivity --------------------------------")

try:
    import yfinance as yf
    import pandas as pd

    df = yf.download("AAPL", period="5d", interval="15m", progress=False, auto_adjust=True)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]

    check("AAPL 15-min download non-empty", not df.empty, f"{len(df)} rows")
    check("OHLCV columns present", all(c in df.columns for c in ["Open","High","Low","Close","Volume"]))

    ET = pytz.timezone("America/New_York")
    if df.index.tzinfo is None:
        df.index = df.index.tz_localize("UTC").tz_convert(ET)
    else:
        df.index = df.index.tz_convert(ET)

    check("Index is timezone-aware", df.index.tzinfo is not None)

except Exception as exc:
    check("yfinance connectivity", False, str(exc))


# ============================================================
# 5. Backtest simulate_day() with synthetic data
# ============================================================
print("\n-- Backtest simulate_day() ------------------------------")

try:
    import pandas as pd
    import numpy as np
    from backtest import simulate_day

    ET = pytz.timezone("America/New_York")

    def make_bar(dt_str, open_, high, low, close, vol=1_000_000):
        idx = pd.DatetimeIndex([pd.Timestamp(dt_str, tz=ET)])
        return pd.DataFrame({"Open": open_, "High": high, "Low": low,
                              "Close": close, "Volume": vol}, index=idx)

    # ORB: 9:30 candle high=105, low=100
    bars = [
        make_bar("2026-02-20 09:30:00", 100, 105, 100, 103),   # ORB candle
        make_bar("2026-02-20 09:45:00", 103, 106, 102, 106),   # breakout above 105
        make_bar("2026-02-20 10:00:00", 106, 116, 105, 115),   # hits TP (105+10=115)
        make_bar("2026-02-20 10:15:00", 115, 116, 114, 115),
    ]
    day_df = pd.concat(bars).sort_index()
    trade = simulate_day("TEST", day_df, "2026-02-20")

    check("simulate_day returns dict",        trade is not None)
    check("Direction is LONG",               trade and trade["Direction"] == "LONG")
    check("Entry == ORB_High (105)",          trade and trade["Entry"] == 105.0)
    check("Exit reason == TP",               trade and trade["Exit Reason"] == "TP")
    check("TP hit at 115 (entry+2×range)",   trade and abs(trade["Target"] - 115.0) < 0.01)
    check("P&L pts == +10",                  trade and abs(trade["P&L pts"] - 10.0) < 0.01)

    # Short scenario: ORB low=100, price drops below → BUY PUT
    bars_short = [
        make_bar("2026-02-21 09:30:00", 103, 105, 100, 102),   # ORB: high=105, low=100
        make_bar("2026-02-21 09:45:00", 102,  99,  98,  98),   # breaks below 100
        make_bar("2026-02-21 10:00:00",  98,  98,  90,  91),   # hits TP (100-10=90)
        make_bar("2026-02-21 10:15:00",  91,  92,  90,  91),
    ]
    day_short = pd.concat(bars_short).sort_index()
    trade_s = simulate_day("TEST", day_short, "2026-02-21")

    check("Short: Direction == SHORT",        trade_s and trade_s["Direction"] == "SHORT")
    check("Short: Signal == BUY PUT",         trade_s and trade_s["Signal"] == "BUY PUT")
    check("Short: Exit reason == TP",         trade_s and trade_s["Exit Reason"] == "TP")

    # No-signal day (price stays in range all day)
    bars_flat = [
        make_bar("2026-02-22 09:30:00", 100, 105, 100, 103),
        make_bar("2026-02-22 09:45:00", 103, 104, 101, 102),
        make_bar("2026-02-22 10:00:00", 102, 103, 101, 102),
        make_bar("2026-02-22 10:15:00", 102, 104, 100, 101),
    ]
    day_flat = pd.concat(bars_flat).sort_index()
    trade_none = simulate_day("TEST", day_flat, "2026-02-22")
    check("No breakout → None returned",      trade_none is None)

except Exception as exc:
    check("simulate_day suite", False, str(exc))


# ============================================================
# 6. Flask app creation
# ============================================================
print("\n-- Flask app creation -----------------------------------")

try:
    # Prevent waitress from binding port during test
    import unittest.mock as mock
    with mock.patch("waitress.serve"):
        import importlib, types
        # We only need to verify the app object is created
        import app as orb_app
        check("Flask app object created",   orb_app.app is not None)
        check("/api/health route exists",   "/api/health" in [r.rule for r in orb_app.app.url_map.iter_rules()])
        check("/api/scan route exists",     "/api/scan"   in [r.rule for r in orb_app.app.url_map.iter_rules()])
        check("/ index route exists",       "/"           in [r.rule for r in orb_app.app.url_map.iter_rules()])

        with orb_app.app.test_client() as client:
            resp = client.get("/api/health")
            check("/api/health returns 200", resp.status_code == 200)

except Exception as exc:
    check("Flask app suite", False, str(exc))


# ============================================================
# Results summary
# ============================================================
print("\n" + "=" * 55)
passed = sum(1 for _, ok in results if ok)
failed = sum(1 for _, ok in results if not ok)
print(f"  Results: {passed} passed, {failed} failed  ({len(results)} total)")
print("=" * 55 + "\n")

if failed:
    sys.exit(1)
