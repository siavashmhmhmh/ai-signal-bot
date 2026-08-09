"""
Signal Engine — combines multi-timeframe trend, momentum, volatility,
volume and market-structure factors into a single weighted confidence
score, then (if strong enough) builds a full trade plan: entry, stop
loss, and three take-profit targets expressed in risk multiples (R).

This is a transparent, rule-based "confluence" engine. It does not predict
the future — it quantifies how many independent, well-established
technical signals currently agree with each other.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Dict, Optional

import pandas as pd

import config
from indicators import add_core_indicators, nearest_support_resistance

log = logging.getLogger("signal_engine")


@dataclass
class Signal:
    symbol: str
    direction: str          # "LONG" or "SHORT"
    score: float             # 0-100 confidence
    entry: float
    stop_loss: float
    targets: list            # [tp1, tp2, tp3]
    risk_reward: list        # RR for each target
    factors: Dict[str, float] = field(default_factory=dict)
    snapshot: Dict[str, float] = field(default_factory=dict)  # for AI commentary


def _score_trend_alignment(mtf: Dict[str, pd.DataFrame]) -> float:
    """+1 = full bullish stack across all timeframes, -1 = full bearish."""
    votes = []
    for tf_key in ["entry", "trend_mid", "trend_high", "trend_macro"]:
        row = mtf[tf_key].iloc[-1]
        if row["ema20"] > row["ema50"] > row["ema200"]:
            votes.append(1)
        elif row["ema20"] < row["ema50"] < row["ema200"]:
            votes.append(-1)
        else:
            votes.append(0)
    return sum(votes) / len(votes)


def _score_momentum(mtf: Dict[str, pd.DataFrame]) -> float:
    row = mtf["entry"].iloc[-1]
    score = 0.0
    # RSI: reward healthy momentum, penalize extreme overbought/oversold
    if 50 < row["rsi14"] < 70:
        score += 0.5
    elif 30 < row["rsi14"] < 50:
        score -= 0.5
    elif row["rsi14"] >= 70:
        score += 0.15  # still bullish but getting stretched
    elif row["rsi14"] <= 30:
        score -= 0.15
    # MACD histogram direction
    prev_hist = mtf["entry"].iloc[-2]["macd_hist"]
    if row["macd_hist"] > 0 and row["macd_hist"] > prev_hist:
        score += 0.5
    elif row["macd_hist"] < 0 and row["macd_hist"] < prev_hist:
        score -= 0.5
    return max(-1.0, min(1.0, score))


def _score_volatility_position(mtf: Dict[str, pd.DataFrame]) -> float:
    row = mtf["entry"].iloc[-1]
    band_range = row["bb_upper"] - row["bb_lower"]
    if band_range <= 0:
        return 0.0
    position = (row["close"] - row["bb_lower"]) / band_range  # 0=lower band, 1=upper band
    # Favor price in the upper-mid band for longs, lower-mid for shorts
    if position >= 0.6:
        return min(1.0, (position - 0.5) * 2)
    if position <= 0.4:
        return -min(1.0, (0.5 - position) * 2)
    return 0.0


def _score_volume(mtf: Dict[str, pd.DataFrame]) -> float:
    row = mtf["entry"].iloc[-1]
    ratio = row["vol_ratio"]
    if pd.isna(ratio):
        return 0.0
    # A volume spike amplifies whatever direction price is already moving
    price_up = row["close"] >= mtf["entry"].iloc[-2]["close"]
    magnitude = min(1.0, max(0.0, (ratio - 1.0) / 2.0))
    return magnitude if price_up else -magnitude


def _score_structure(mtf: Dict[str, pd.DataFrame]) -> float:
    df = mtf["entry"]
    close = df["close"].iloc[-1]
    support, resistance = nearest_support_resistance(df)
    if pd.isna(support) and pd.isna(resistance):
        return 0.0
    score = 0.0
    if not pd.isna(support):
        dist_to_support = (close - support) / close
        if dist_to_support < 0.02:  # within 2% of support -> bullish structure
            score += 1.0 - (dist_to_support / 0.02) * 0.3
    if not pd.isna(resistance):
        dist_to_resistance = (resistance - close) / close
        if dist_to_resistance < 0.02:  # within 2% of resistance -> bearish structure
            score -= 1.0 - (dist_to_resistance / 0.02) * 0.3
    return max(-1.0, min(1.0, score))


def analyze_symbol(symbol: str, mtf_raw: Dict[str, pd.DataFrame]) -> Optional[Signal]:
    if not mtf_raw or any(df.empty or len(df) < 210 for df in mtf_raw.values()):
        return None

    mtf = {k: add_core_indicators(v) for k, v in mtf_raw.items()}

    factors = {
        "trend_alignment": _score_trend_alignment(mtf),
        "momentum": _score_momentum(mtf),
        "volatility_position": _score_volatility_position(mtf),
        "volume": _score_volume(mtf),
        "structure": _score_structure(mtf),
    }

    weighted = sum(factors[k] * config.FACTOR_WEIGHTS[k] for k in factors)
    # weighted is in [-1, 1] -> map to a 0-100 confidence score, direction from sign
    direction = "LONG" if weighted >= 0 else "SHORT"
    score = round(abs(weighted) * 100, 1)

    if score < config.MIN_SIGNAL_SCORE:
        return None

    entry_df = mtf["entry"]
    last = entry_df.iloc[-1]
    entry_price = float(last["close"])
    atr_val = float(last["atr14"])
    support, resistance = nearest_support_resistance(mtf_raw["entry"])

    if direction == "LONG":
        structural_stop = support if not pd.isna(support) else entry_price - atr_val * config.ATR_STOP_MULTIPLIER
        stop_loss = min(structural_stop, entry_price - atr_val * config.ATR_STOP_MULTIPLIER)
        risk = entry_price - stop_loss
        targets = [entry_price + risk * m for m in config.TP_R_MULTIPLES]
    else:
        structural_stop = resistance if not pd.isna(resistance) else entry_price + atr_val * config.ATR_STOP_MULTIPLIER
        stop_loss = max(structural_stop, entry_price + atr_val * config.ATR_STOP_MULTIPLIER)
        risk = stop_loss - entry_price
        targets = [entry_price - risk * m for m in config.TP_R_MULTIPLES]

    if risk <= 0:
        return None

    risk_reward = config.TP_R_MULTIPLES  # by construction, RR == the R multiple used

    snapshot = {
        "rsi_15m": round(float(last["rsi14"]), 1),
        "macd_hist_15m": round(float(last["macd_hist"]), 5),
        "ema_stack_1h": "bullish" if mtf["trend_mid"].iloc[-1]["ema20"] > mtf["trend_mid"].iloc[-1]["ema50"] else "bearish",
        "ema_stack_4h": "bullish" if mtf["trend_high"].iloc[-1]["ema20"] > mtf["trend_high"].iloc[-1]["ema50"] else "bearish",
        "ema_stack_1d": "bullish" if mtf["trend_macro"].iloc[-1]["ema20"] > mtf["trend_macro"].iloc[-1]["ema50"] else "bearish",
        "volume_ratio": round(float(last["vol_ratio"]), 2) if not pd.isna(last["vol_ratio"]) else None,
        "atr": round(atr_val, 6),
        "nearest_support": round(float(support), 6) if not pd.isna(support) else None,
        "nearest_resistance": round(float(resistance), 6) if not pd.isna(resistance) else None,
    }

    return Signal(
        symbol=symbol,
        direction=direction,
        score=score,
        entry=entry_price,
        stop_loss=round(stop_loss, 6),
        targets=[round(t, 6) for t in targets],
        risk_reward=risk_reward,
        factors={k: round(v, 2) for k, v in factors.items()},
        snapshot=snapshot,
    )
