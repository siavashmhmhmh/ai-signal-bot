""" Self-contained technical indicator library built on pandas/numpy only — no ta-lib or external TA package required, so setup stays simple. """
from __future__ import annotations
import numpy as np
import pandas as pd


def ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()


def sma(series: pd.Series, length: int) -> pd.Series:
    return series.rolling(length).mean()


def rsi(series: pd.Series, length: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / length, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out.fillna(50)


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    macd_line = ema(series, fast) - ema(series, slow)
    signal_line = ema(macd_line, signal)
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / length, adjust=False).mean()


def adx(df: pd.DataFrame, length: int = 14) -> pd.Series:
    """ Average Directional Index (Wilder's method) — measures trend STRENGTH, not direction. Low ADX = choppy/ranging market where most signal engines (rule-based or "AI") produce false signals. Used as a gate: only accept a signal when the underlying trend is strong enough to trust the direction call. """
    high, low, close = df["high"], df["low"], df["close"]
    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    atr_smooth = tr.ewm(alpha=1 / length, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=1 / length, adjust=False).mean() / atr_smooth.replace(0, np.nan))
    minus_di = 100 * (minus_dm.ewm(alpha=1 / length, adjust=False).mean() / atr_smooth.replace(0, np.nan))

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / length, adjust=False).mean().fillna(0)


def bollinger_bands(series: pd.Series, length: int = 20, mult: float = 2.0):
    basis = sma(series, length)
    dev = series.rolling(length).std()
    upper = basis + mult * dev
    lower = basis - mult * dev
    bandwidth = (upper - lower) / basis.replace(0, np.nan)
    return basis, upper, lower, bandwidth


def volume_spike_ratio(volume: pd.Series, length: int = 20) -> pd.Series:
    avg_vol = volume.rolling(length).mean()
    return volume / avg_vol.replace(0, np.nan)


def swing_points(df: pd.DataFrame, left: int = 5, right: int = 5):
    """ Simple fractal-based swing high/low detector. Returns two boolean Series: is_swing_high, is_swing_low. """
    high, low = df["high"], df["low"]
    n = len(df)
    is_high = pd.Series(False, index=df.index)
    is_low = pd.Series(False, index=df.index)
    for idx in range(left, n - right):
        window_high = high.iloc[idx - left: idx + right + 1]
        window_low = low.iloc[idx - left: idx + right + 1]
        if high.iloc[idx] == window_high.max():
            is_high.iloc[idx] = True
        if low.iloc[idx] == window_low.min():
            is_low.iloc[idx] = True
    return is_high, is_low


def nearest_support_resistance(df: pd.DataFrame, left: int = 5, right: int = 5, lookback: int = 150):
    """ Returns (nearest_support, nearest_resistance) price levels below/above the last close, based on recent swing points. """
    recent = df.tail(lookback).copy()
    is_high, is_low = swing_points(recent, left, right)
    last_close = recent["close"].iloc[-1]

    resistances = recent.loc[is_high, "high"]
    resistances = resistances[resistances > last_close]
    supports = recent.loc[is_low, "low"]
    supports = supports[supports < last_close]

    nearest_res = resistances.min() if not resistances.empty else np.nan
    nearest_sup = supports.max() if not supports.empty else np.nan
    return nearest_sup, nearest_res


def add_core_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Adds every indicator this bot needs onto a copy of the OHLCV frame."""
    out = df.copy()
    out["ema20"] = ema(out["close"], 20)
    out["ema50"] = ema(out["close"], 50)
    out["ema200"] = ema(out["close"], 200)
    out["rsi14"] = rsi(out["close"], 14)
    macd_line, signal_line, hist = macd(out["close"])
    out["macd"] = macd_line
    out["macd_signal"] = signal_line
    out["macd_hist"] = hist
    out["atr14"] = atr(out, 14)
    out["adx14"] = adx(out, 14)
    basis, upper, lower, bandwidth = bollinger_bands(out["close"], 20, 2.0)
    out["bb_basis"] = basis
    out["bb_upper"] = upper
    out["bb_lower"] = lower
    out["bb_bandwidth"] = bandwidth
    out["vol_ratio"] = volume_spike_ratio(out["volume"], 20)
    return out
