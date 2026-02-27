"""
ORB Backtest – NASDAQ 100 Top 10
=================================
Simulates the 15-minute Opening Range Breakout strategy over the
last 60 calendar days using yfinance 15-min data (maximum free lookback).

Strategy rules
--------------
  Entry  : First 15-min candle AFTER 9:45 ET whose CLOSE breaks above
            ORB_High (long) or below ORB_Low (short).
  Stop   : ORB range  (ORB_High - ORB_Low)
  Target : 2 × ORB range  (2:1 R:R)
  Exit   : Take-profit, stop-loss, or market-close (last 15-min candle ≤ 15:45)
  One    : At most one trade per ticker per day (first signal taken).

Outputs
-------
  - Per-trade log  : backtest/results/trades.csv
  - Daily summary  : backtest/results/daily_summary.csv
  - Ticker stats   : backtest/results/ticker_stats.csv
  - Console print  : summary table + overall stats
"""

import os
import sys
import logging
from datetime import datetime, timedelta

import pandas as pd
import numpy as np
import pytz
import yfinance as yf

# Allow running from repo root or backtest/ dir
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

ET = pytz.timezone("America/New_York")

TICKERS      = ["AAPL", "NVDA", "MSFT", "AMZN", "META", "TSLA", "GOOGL", "AVGO", "NFLX", "AMD"]
LOOKBACK     = "60d"           # yfinance max for 15-min data
INTERVAL     = "15m"
RR_RATIO     = 2.0             # Take-profit = RR × stop
RESULTS_DIR  = os.path.join(os.path.dirname(__file__), "results")


# ---------------------------------------------------------------------------
# Data fetch
# ---------------------------------------------------------------------------

def fetch_15m(ticker: str) -> pd.DataFrame:
    """Download 60 days of 15-min OHLCV, index in ET."""
    try:
        df = yf.download(ticker, period=LOOKBACK, interval=INTERVAL,
                         progress=False, auto_adjust=True)
        if df is None or df.empty:
            log.warning("%s: empty data", ticker)
            return pd.DataFrame()
        if df.index.tzinfo is None:
            df.index = df.index.tz_localize("UTC").tz_convert(ET)
        else:
            df.index = df.index.tz_convert(ET)

        # Flatten MultiIndex columns (yfinance ≥0.2.38 quirk)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0] for c in df.columns]

        return df
    except Exception as exc:
        log.error("%s: fetch error – %s", ticker, exc)
        return pd.DataFrame()


# ---------------------------------------------------------------------------
# Per-day simulation
# ---------------------------------------------------------------------------

def simulate_day(ticker: str, day_df: pd.DataFrame, trade_date: str) -> dict | None:
    """
    Given all 15-min bars for a single trading day, run the ORB strategy.
    Returns a trade dict or None if no signal / no entry candle.
    """
    # ORB candle: 9:30 bar
    orb = day_df[(day_df.index.hour == 9) & (day_df.index.minute == 30)]
    if orb.empty:
        return None

    orb_high = float(orb["High"].iloc[0])
    orb_low  = float(orb["Low"].iloc[0])
    orb_range = orb_high - orb_low
    if orb_range <= 0:
        return None

    stop_dist   = orb_range
    target_dist = orb_range * RR_RATIO

    # Candles from 9:45 onwards (index hour/minute filter)
    post_orb = day_df[
        (day_df.index.hour > 9) |
        ((day_df.index.hour == 9) & (day_df.index.minute >= 45))
    ]
    # Remove after-hours (keep ≤ 15:45 candle)
    post_orb = post_orb[
        (post_orb.index.hour < 16) |
        ((post_orb.index.hour == 15) & (post_orb.index.minute <= 45))
    ]
    if post_orb.empty:
        return None

    direction = None
    entry_price = None
    entry_time  = None

    for ts, row in post_orb.iterrows():
        close = float(row["Close"])
        if direction is None:
            # First breakout detection on close
            if close > orb_high:
                direction   = "LONG"
                entry_price = orb_high          # enter at ORB level
                entry_time  = ts
                break
            elif close < orb_low:
                direction   = "SHORT"
                entry_price = orb_low
                entry_time  = ts
                break

    if direction is None:
        return None   # No breakout today

    # --- Walk forward from entry to find exit ---
    entry_idx = post_orb.index.get_loc(entry_time)
    remaining = post_orb.iloc[entry_idx + 1:]  # bars after entry

    if direction == "LONG":
        tp = entry_price + target_dist
        sl = entry_price - stop_dist
    else:
        tp = entry_price - target_dist
        sl = entry_price + stop_dist

    exit_price  = None
    exit_reason = "EOD"

    for ts2, row2 in remaining.iterrows():
        high2  = float(row2["High"])
        low2   = float(row2["Low"])
        close2 = float(row2["Close"])

        if direction == "LONG":
            if high2 >= tp:
                exit_price  = tp
                exit_reason = "TP"
                break
            if low2 <= sl:
                exit_price  = sl
                exit_reason = "SL"
                break
        else:  # SHORT
            if low2 <= tp:
                exit_price  = tp
                exit_reason = "TP"
                break
            if high2 >= sl:
                exit_price  = sl
                exit_reason = "SL"
                break

    # EOD exit at last candle close
    if exit_price is None:
        last_bar = remaining.iloc[-1] if not remaining.empty else post_orb.iloc[-1]
        exit_price = float(last_bar["Close"])

    pnl_pts = (exit_price - entry_price) if direction == "LONG" else (entry_price - exit_price)
    pnl_pct = pnl_pts / entry_price * 100

    return {
        "Date":        trade_date,
        "Ticker":      ticker,
        "Direction":   direction,
        "Signal":      "BUY CALL" if direction == "LONG" else "BUY PUT",
        "ORB High":    round(orb_high, 2),
        "ORB Low":     round(orb_low, 2),
        "ORB Range":   round(orb_range, 2),
        "Entry":       round(entry_price, 2),
        "Stop":        round(sl, 2),
        "Target":      round(tp, 2),
        "Exit":        round(exit_price, 2),
        "Exit Reason": exit_reason,
        "P&L pts":     round(pnl_pts, 2),
        "P&L %":       round(pnl_pct, 3),
        "Win":         pnl_pts > 0,
    }


# ---------------------------------------------------------------------------
# Main backtest runner
# ---------------------------------------------------------------------------

def run_backtest(tickers: list = TICKERS) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Run backtest across all tickers.
    Returns (trades_df, daily_df, ticker_stats_df).
    """
    all_trades = []

    for ticker in tickers:
        log.info("Fetching %s ...", ticker)
        df = fetch_15m(ticker)
        if df.empty:
            continue

        # Group by calendar date
        dates = sorted(set(df.index.date))
        for d in dates:
            day_str = d.isoformat()
            day_df  = df[df.index.date == d].copy()
            # Skip days with fewer than 10 bars (early close / data gap)
            if len(day_df) < 10:
                continue
            trade = simulate_day(ticker, day_df, day_str)
            if trade:
                all_trades.append(trade)

    if not all_trades:
        log.warning("No trades generated.")
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    trades_df = pd.DataFrame(all_trades)

    # --- Daily summary ---
    daily_df = (
        trades_df.groupby("Date")
        .agg(
            Trades=("Ticker", "count"),
            Wins=("Win", "sum"),
            Total_PnL_pct=("P&L %", "sum"),
        )
        .reset_index()
    )
    daily_df["Win Rate"] = (daily_df["Wins"] / daily_df["Trades"] * 100).round(1)
    daily_df["Total_PnL_pct"] = daily_df["Total_PnL_pct"].round(3)

    # --- Ticker stats ---
    ticker_stats = (
        trades_df.groupby("Ticker")
        .agg(
            Trades=("Date", "count"),
            Wins=("Win", "sum"),
            Avg_PnL_pct=("P&L %", "mean"),
            Total_PnL_pct=("P&L %", "sum"),
            TP_count=("Exit Reason", lambda x: (x == "TP").sum()),
            SL_count=("Exit Reason", lambda x: (x == "SL").sum()),
            EOD_count=("Exit Reason", lambda x: (x == "EOD").sum()),
        )
        .reset_index()
    )
    ticker_stats["Win Rate %"] = (ticker_stats["Wins"] / ticker_stats["Trades"] * 100).round(1)
    ticker_stats["Avg_PnL_pct"]   = ticker_stats["Avg_PnL_pct"].round(3)
    ticker_stats["Total_PnL_pct"] = ticker_stats["Total_PnL_pct"].round(3)
    ticker_stats = ticker_stats.sort_values("Total_PnL_pct", ascending=False).reset_index(drop=True)

    return trades_df, daily_df, ticker_stats


# ---------------------------------------------------------------------------
# Print summary
# ---------------------------------------------------------------------------

def print_summary(trades_df: pd.DataFrame, ticker_stats: pd.DataFrame) -> None:
    total   = len(trades_df)
    wins    = trades_df["Win"].sum()
    losses  = total - wins
    win_pct = wins / total * 100 if total else 0
    avg_pnl = trades_df["P&L %"].mean()
    total_pnl = trades_df["P&L %"].sum()
    tp_count  = (trades_df["Exit Reason"] == "TP").sum()
    sl_count  = (trades_df["Exit Reason"] == "SL").sum()
    eod_count = (trades_df["Exit Reason"] == "EOD").sum()

    sep = "=" * 70
    print(f"\n{sep}")
    print("  NASDAQ ORB BACKTEST  –  15-min Opening Range Breakout  (60-day)")
    print(f"  Strategy: Break of 9:30–9:45 range  |  SL=range  TP=2×range")
    print(sep)
    print(f"  Total trades    : {total}")
    print(f"  Wins / Losses   : {wins} / {losses}")
    print(f"  Win rate        : {win_pct:.1f}%")
    print(f"  Avg P&L per trd : {avg_pnl:+.3f}%")
    print(f"  Total P&L (sum) : {total_pnl:+.3f}%")
    print(f"  TP hits / SL / EOD : {tp_count} / {sl_count} / {eod_count}")
    print(sep)
    print("\n  Per-Ticker Statistics:")
    print(f"  {'Ticker':<8} {'Trades':>6} {'Win%':>6} {'Avg P&L%':>10} {'Tot P&L%':>10} {'TP':>4} {'SL':>4} {'EOD':>4}")
    print("  " + "-" * 62)
    for _, r in ticker_stats.iterrows():
        print(f"  {r['Ticker']:<8} {r['Trades']:>6} {r['Win Rate %']:>5.1f}% "
              f"{r['Avg_PnL_pct']:>+10.3f} {r['Total_PnL_pct']:>+10.3f} "
              f"{int(r['TP_count']):>4} {int(r['SL_count']):>4} {int(r['EOD_count']):>4}")
    print(sep + "\n")


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    os.makedirs(RESULTS_DIR, exist_ok=True)

    trades_df, daily_df, ticker_stats = run_backtest()

    if trades_df.empty:
        print("No trades generated – check data connectivity.")
        sys.exit(1)

    # Save CSVs
    trades_path = os.path.join(RESULTS_DIR, "trades.csv")
    daily_path  = os.path.join(RESULTS_DIR, "daily_summary.csv")
    stats_path  = os.path.join(RESULTS_DIR, "ticker_stats.csv")

    trades_df.to_csv(trades_path, index=False)
    daily_df.to_csv(daily_path, index=False)
    ticker_stats.to_csv(stats_path, index=False)

    log.info("Results saved → %s", RESULTS_DIR)
    print_summary(trades_df, ticker_stats)

    # Show last 10 trades
    print("  Recent trades (last 10):")
    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 160)
    print(trades_df.tail(10).to_string(index=False))
    print()
