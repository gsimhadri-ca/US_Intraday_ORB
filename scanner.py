"""
US Intraday ORB Scanner – NASDAQ 100 Top 27
15-Minute Opening Range Breakout with Black-Scholes Greeks (0-DTE)
Executes after 9:45 AM ET.
"""

import logging
import math
from datetime import datetime, date, timedelta

import numpy as np
import pandas as pd
import pytz
import yfinance as yf
from scipy.stats import norm

log = logging.getLogger(__name__)

TICKERS = [
    "NVDA", "AAPL", "MSFT", "AMZN", "META",
    "GOOGL", "GOOG", "TSLA", "AVGO", "NFLX",
    "COST", "TMUS", "AMD", "CSCO", "ADBE",
    "PEP", "TXN", "QCOM", "INTU", "AMAT",
    "ISRG", "BKNG", "AMGN", "ADP", "MRVL",
    "PANW", "KLAC",
]
ET = pytz.timezone("America/New_York")
MARKET_OPEN = {"hour": 9, "minute": 30}
ORB_END = {"hour": 9, "minute": 45}
RISK_FREE_RATE = 0.05  # 5% annualised
MAX_ROWS = 20


# ---------------------------------------------------------------------------
# Black-Scholes helpers
# ---------------------------------------------------------------------------

def _d1_d2(S: float, K: float, r: float, sigma: float, T: float):
    """Return (d1, d2). T in years."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return None, None
    sqrt_T = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T
    return d1, d2


def bs_delta(S: float, K: float, r: float, sigma: float, T: float, option_type: str = "call") -> float:
    d1, _ = _d1_d2(S, K, r, sigma, T)
    if d1 is None:
        return float("nan")
    return norm.cdf(d1) if option_type == "call" else norm.cdf(d1) - 1


def bs_theta_hourly(S: float, K: float, r: float, sigma: float, T: float, option_type: str = "call") -> float:
    """Theta per *hour* (not per day) in dollars."""
    d1, d2 = _d1_d2(S, K, r, sigma, T)
    if d1 is None:
        return float("nan")
    sqrt_T = math.sqrt(T)
    pdf_d1 = norm.pdf(d1)
    daily_theta = (
        -(S * pdf_d1 * sigma) / (2 * sqrt_T)
        - r * K * math.exp(-r * T) * (norm.cdf(d2) if option_type == "call" else norm.cdf(-d2))
    )
    # daily_theta is in $/year; convert to hourly
    return daily_theta / (365 * 24)


def estimate_iv(ticker_info: dict, fallback: float = 0.35) -> float:
    """Pull impliedVolatility from yfinance info dict; fallback to sector-typical value."""
    iv = ticker_info.get("impliedVolatility") or ticker_info.get("impliedvolatility")
    if iv and 0.05 < iv < 5.0:
        return float(iv)
    # Use annualised 30-day hist vol as proxy if available
    return fallback


# ---------------------------------------------------------------------------
# Volume helpers
# ---------------------------------------------------------------------------

def _avg_5d_volume(ticker: str) -> float:
    try:
        hist = yf.download(ticker, period="6d", interval="1d", progress=False, auto_adjust=True)
        vol = hist["Volume"].squeeze()
        if len(hist) >= 5:
            return float(vol.iloc[-6:-1].mean())
        return float(vol.mean()) if not hist.empty else 0.0
    except Exception as exc:
        log.warning("5-day volume fetch failed for %s: %s", ticker, exc)
        return 0.0


# ---------------------------------------------------------------------------
# Core scanner
# ---------------------------------------------------------------------------

def fetch_orb_data(ticker: str):
    """
    Returns dict with keys:
      orb_high, orb_low, current_price, current_volume, ticker
    or None on error.
    """
    try:
        df = yf.download(ticker, period="1d", interval="15m", progress=False, auto_adjust=True)
        if df is None or df.empty:
            log.warning("%s: empty intraday data", ticker)
            return None

        # Localise index to ET
        if df.index.tzinfo is None:
            df.index = df.index.tz_localize("UTC").tz_convert(ET)
        else:
            df.index = df.index.tz_convert(ET)

        # Opening range candle: 9:30 ET
        orb_candles = df[
            (df.index.hour == MARKET_OPEN["hour"]) & (df.index.minute == MARKET_OPEN["minute"])
        ]
        if orb_candles.empty:
            log.warning("%s: no 9:30 candle found", ticker)
            return None

        orb_row = orb_candles.iloc[0]
        orb_high = float(orb_row["High"].iloc[0] if isinstance(orb_row["High"], pd.Series) else orb_row["High"])
        orb_low  = float(orb_row["Low"].iloc[0]  if isinstance(orb_row["Low"],  pd.Series) else orb_row["Low"])

        # Most recent completed candle for current price & volume
        latest = df.iloc[-1]
        current_price  = float(latest["Close"].iloc[0] if isinstance(latest["Close"], pd.Series) else latest["Close"])
        current_volume = float(latest["Volume"].iloc[0] if isinstance(latest["Volume"], pd.Series) else latest["Volume"])

        return {
            "ticker": ticker,
            "orb_high": orb_high,
            "orb_low": orb_low,
            "current_price": current_price,
            "current_volume": current_volume,
            "df": df,
        }
    except Exception as exc:
        log.error("%s: fetch_orb_data error – %s", ticker, exc)
        return None


def _find_entry_time(df: pd.DataFrame, orb_high: float, orb_low: float, signal: str) -> str:
    """Return HH:MM ET of the first post-ORB candle that triggered the breakout."""
    post_orb = df[
        (df.index.hour > 9) |
        ((df.index.hour == 9) & (df.index.minute >= 45))
    ]
    close = post_orb["Close"].squeeze()
    if signal == "BUY CALL":
        triggered = post_orb[close > orb_high]
    elif signal == "BUY PUT":
        triggered = post_orb[close < orb_low]
    else:
        return "–"
    return triggered.index[0].strftime("%H:%M") if not triggered.empty else "–"


def run_scanner() -> pd.DataFrame:
    """Run ORB scan and return a sorted DataFrame (max MAX_ROWS rows)."""
    now_et = datetime.now(ET)
    log.info("ORB scan starting at %s ET", now_et.strftime("%H:%M:%S"))

    records = []
    for ticker in TICKERS:
        data = fetch_orb_data(ticker)
        if not data:
            continue

        orb_high      = data["orb_high"]
        orb_low       = data["orb_low"]
        current_price = data["current_price"]
        current_vol   = data["current_volume"]
        intraday_df   = data["df"]

        # Signal
        if current_price > orb_high:
            signal = "BUY CALL"
            entry_level = orb_high
            option_type = "call"
        elif current_price < orb_low:
            signal = "BUY PUT"
            entry_level = orb_low
            option_type = "put"
        else:
            signal = "NEUTRAL"
            entry_level = (orb_high + orb_low) / 2
            option_type = "call"

        entry_time = _find_entry_time(intraday_df, orb_high, orb_low, signal)

        # Greeks
        try:
            info = yf.Ticker(ticker).info
        except Exception:
            info = {}
        sigma = estimate_iv(info)

        # T = fraction of year remaining in today's trading session
        market_close_et = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
        minutes_left = max((market_close_et - now_et).total_seconds() / 60, 1)
        T = minutes_left / (365 * 390)  # 390 trading minutes/day

        S = current_price
        K = round(current_price)        # ATM strike (nearest dollar)

        delta = bs_delta(S, K, RISK_FREE_RATE, sigma, T, option_type)
        theta_hr = bs_theta_hourly(S, K, RISK_FREE_RATE, sigma, T, option_type)

        # Relative volume
        avg_vol = _avg_5d_volume(ticker)
        rel_vol = round(current_vol / avg_vol, 2) if avg_vol > 0 else 0.0

        records.append({
            "Ticker":         ticker,
            "Signal":         signal,
            "ORB High":       round(orb_high, 2),
            "ORB Low":        round(orb_low, 2),
            "Entry Level":    round(entry_level, 2),
            "Entry Time":     entry_time,
            "Current Price":  round(current_price, 2),
            "Diff":           round(current_price - entry_level, 2),
            "Delta":          round(delta, 3),
            "Theta/Hr":       round(theta_hr, 4),
            "IV":             round(sigma, 3),
            "Rel Vol":        rel_vol,
            "Curr Vol":       int(current_vol),
        })

    if not records:
        log.warning("No ORB records generated.")
        return pd.DataFrame()

    df = pd.DataFrame(records)

    # Exclude NEUTRAL tickers; sort by Entry Time asc (– values go last)
    df = df[df["Signal"] != "NEUTRAL"].copy()
    df["_et_sort"] = df["Entry Time"].apply(lambda t: t if t != "–" else "99:99")
    df = df.sort_values("_et_sort", ascending=True).drop(columns="_et_sort")
    df = df.head(MAX_ROWS).reset_index(drop=True)

    log.info("Scan complete – %d rows returned", len(df))
    return df


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # CLI: configure ET-aware logging to stderr
    class _ETFmt(logging.Formatter):
        def formatTime(self, record, datefmt=None):
            dt = datetime.fromtimestamp(record.created, tz=ET)
            if datefmt:
                return dt.strftime(datefmt)
            return dt.strftime("%Y-%m-%d %H:%M:%S") + ",%03d" % record.msecs

    _h = logging.StreamHandler()
    _h.setFormatter(_ETFmt("%(asctime)s %(levelname)s %(message)s"))
    logging.getLogger().addHandler(_h)
    logging.getLogger().setLevel(logging.INFO)

    result = run_scanner()
    if result.empty:
        print("No data returned.")
    else:
        pd.set_option("display.max_columns", None)
        pd.set_option("display.width", 160)
        print(result.to_string(index=False))
