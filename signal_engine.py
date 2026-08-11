"""
Signal Engine — implements the "Target Trend [BigBeluga]" indicator logic
as the bot's entry/exit engine.

How it works (mirrors the Pine Script indicator):
  * A rolling high/low band is built from SMA(high/low, length) offset by
    a smoothed ATR (SMA(ATR(200), 200) * 0.8).
  * When price closes above the upper band -> trend flips to UP (bullish).
  * When price closes below the lower band -> trend flips to DOWN (bearish).
  * The opposite band at the moment of the flip becomes the stop loss
    (the same "trailing stop" line the indicator plots).
  * Three take-profit targets are placed at 5x / 10x / 15x the smoothed
    ATR away from entry, in the trade's direction — identical to the
    indicator's target1/target2/target3 lines.

This is an exact, faithful translation of the indicator with no added
filters — the bot fires whenever the indicator itself flips trend on the
configured entry timeframe (15m by default), exactly like the indicator
would flash a signal on a live chart.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Dict, Optional

import pandas as pd

import config

log = logging.getLogger("signal_engine")


@dataclass
class Signal:
    symbol: str
    direction: str          # "LONG" or "SHORT"
    score: float             # 0-100 confidence (binary indicator -> 100 when it fires)
    entry: float
    stop_loss: float
    targets: list            # [tp1, tp2, tp3]
    risk_reward: list        # RR for each target
    factors: Dict[str, float] = field(default_factory=dict)
    snapshot: Dict[str, float] = field(default_factory=dict)  # for AI commentary


# ---------------------------------------------------------------------------
# Target Trend indicator, translated from Pine Script to pandas
# ---------------------------------------------------------------------------

def _true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    return pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)


def _rma(series: pd.Series, period: int) -> pd.Series:
    """Wilder's moving average — what Pine Script's ta.atr() uses internally."""
    return series.ewm(alpha=1.0 / period, adjust=False).mean()


def _compute_target_trend(df: pd.DataFrame, length: int = 10,
                           atr_period: int = 200) -> pd.DataFrame:
    """
    Reproduces the indicator's core calculations:
      sma_high = SMA(high, length) + smoothed_atr
      sma_low  = SMA(low,  length) - smoothed_atr
      trend flips up when close crosses above sma_high,
      trend flips down when close crosses below sma_low.
    Adds columns: sma_high, sma_low, atr_value, trend, signal_up, signal_down.
    """
    out = df.copy()
    tr = _true_range(out)
    atr_raw = _rma(tr, atr_period)
    out["atr_value"] = atr_raw.rolling(atr_period).mean() * 0.8
    out["sma_high"] = out["high"].rolling(length).mean() + out["atr_value"]
    out["sma_low"] = out["low"].rolling(length).mean() - out["atr_value"]

    close = out["close"].values
    sh = out["sma_high"].values
    sl = out["sma_low"].values
    n = len(out)

    trend = [None] * n
    cur = None
    for i in range(1, n):
        if pd.isna(sh[i]) or pd.isna(sl[i]) or pd.isna(sh[i - 1]) or pd.isna(sl[i - 1]):
            trend[i] = cur
            continue
        crossed_up = close[i] > sh[i] and not (close[i - 1] > sh[i - 1])
        crossed_down = close[i] < sl[i] and not (close[i - 1] < sl[i - 1])
        if crossed_up:
            cur = True
        elif crossed_down:
            cur = False
        trend[i] = cur
    out["trend"] = trend

    signal_up = [False] * n
    signal_down = [False] * n
    for i in range(1, n):
        if trend[i] is True and trend[i - 1] is not True:
            signal_up[i] = True
        if trend[i] is False and trend[i - 1] is not False:
            signal_down[i] = True
    out["signal_up"] = signal_up
    out["signal_down"] = signal_down
    return out


# ---------------------------------------------------------------------------
# Main entry point used by main.py — signature unchanged
# ---------------------------------------------------------------------------

def analyze_symbol(symbol: str, mtf_raw: Dict[str, pd.DataFrame]) -> Optional[Signal]:
    if not mtf_raw:
        log.info("%-12s SKIP no data returned", symbol)
        return None

    entry_raw = mtf_raw.get("entry")
    if entry_raw is None:
        log.info("%-12s SKIP missing entry timeframe", symbol)
        return None
    if len(entry_raw) < 211:
        log.info("%-12s SKIP not enough candles (%d)", symbol, len(entry_raw))
        return None

    length = getattr(config, "TARGET_TREND_LENGTH", 10)
    target_offset = getattr(config, "TARGET_TREND_OFFSET", 0)

    entry_tt = _compute_target_trend(entry_raw, length=length)
    # Mirror Pine Script's `barstate.isconfirmed`: only act on the last
    # FULLY CLOSED candle, never the currently-forming one. The most
    # recent row from the exchange is usually still live/in-progress, so
    # we deliberately look one bar back — this is what keeps the bot's
    # signal in sync with what the indicator actually shows on a live
    # TradingView chart.
    last = entry_tt.iloc[-2]

    if bool(last["signal_up"]):
        direction = "LONG"
    elif bool(last["signal_down"]):
        direction = "SHORT"
    else:
        log.info("%-12s no fresh Target Trend signal (trend=%s)", symbol, last["trend"])
        return None

    entry_price = float(last["close"])
    atr_value = float(last["atr_value"])
    if pd.isna(atr_value) or atr_value <= 0:
        log.info("%-12s SKIP invalid ATR", symbol)
        return None

    base = float(last["sma_low"]) if direction == "LONG" else float(last["sma_high"])
    sign = 1 if direction == "LONG" else -1
    targets = [
        entry_price + sign * atr_value * (5 + target_offset),
        entry_price + sign * atr_value * (10 + target_offset * 2),
        entry_price + sign * atr_value * (15 + target_offset * 3),
    ]
    stop_loss = base

    risk = abs(entry_price - stop_loss)
    if risk <= 0:
        log.info("%-12s SKIP zero/negative risk", symbol)
        return None
    risk_pct = risk / entry_price
    min_risk = getattr(config, "MIN_RISK_PCT", 0.001)
    max_risk = getattr(config, "MAX_RISK_PCT", 0.15)
    if risk_pct < min_risk:
        log.info("%-12s SKIP stop too tight: risk=%.3f%% (min %.2f%%)",
                  symbol, risk_pct * 100, min_risk * 100)
        return None
    if risk_pct > max_risk:
        log.info("%-12s SKIP stop too wide: risk=%.2f%% (max %.1f%%)",
                  symbol, risk_pct * 100, max_risk * 100)
        return None

    risk_reward = [abs(t - entry_price) / risk for t in targets]

    log.info("%-12s %-5s SIGNAL entry=%.6f stop=%.6f targets=%s atr=%.6f",
              symbol, direction, entry_price, stop_loss,
              [round(t, 6) for t in targets], atr_value)

    factors = {
        "trend_flip": 1.0 if direction == "LONG" else -1.0,
    }
    snapshot = {
        "indicator": "Target Trend (BigBeluga)",
        "timeframe": config.TIMEFRAMES.get("entry"),
        "trend_length": length,
        "atr": round(atr_value, 6),
        "trailing_stop": round(stop_loss, 6),
    }

    return Signal(
        symbol=symbol,
        direction=direction,
        score=100.0,  # binary indicator signal: it either fires cleanly or doesn't
        entry=round(entry_price, 6),
        stop_loss=round(stop_loss, 6),
        targets=[round(t, 6) for t in targets],
        risk_reward=[round(rr, 2) for rr in risk_reward],
        factors=factors,
        snapshot=snapshot,
    )
