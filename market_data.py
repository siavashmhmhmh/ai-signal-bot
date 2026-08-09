""" Market data layer — talks to MEXC's public REST API (no API key needed for market data). Handles symbol discovery (the "whole market" scan) and OHLCV retrieval per timeframe. NOTE: Switched from Binance to MEXC because Binance blocks requests coming from US-based server IPs (HTTP 451). MEXC's spot REST API mirrors Binance's format closely, but NOT identically — two differences we've had to patch: 1) klines response has 8 columns instead of Binance's 12 (handled below by building the DataFrame dynamically from however many columns come back). 2) klines interval names differ for some timeframes (e.g. Binance "1h" is MEXC "60m"). Handled below via INTERVAL_MAP. """
from __future__ import annotations
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List

import pandas as pd
import requests

import config

log = logging.getLogger("market_data")

_session = requests.Session()

# Maps our internal/Binance-style interval strings to MEXC's own interval
# strings. Any interval not listed here is assumed to already match MEXC's
# format (e.g. "1m", "5m", "15m", "30m", "1d" are the same on both).
INTERVAL_MAP = {
    "1h": "60m",
    "2h": "120m",
    "4h": "4h",
    "6h": "8h",   # MEXC has no native 6h bucket; nearest is 8h — adjust in
                  # config.TIMEFRAMES if you need something closer to 6h.
    "8h": "8h",
    "12h": "12h",
    "1d": "1d",
    "1w": "1W",
    "1M": "1M",
}


def _mexc_interval(interval: str) -> str:
    return INTERVAL_MAP.get(interval, interval)


def _get(path: str, params: dict | None = None) -> dict:
    url = f"{config.EXCHANGE_BASE_URL}{path}"
    resp = _session.get(url, params=params, timeout=config.REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def get_liquid_usdt_symbols() -> List[str]:
    """ Returns every actively-traded <COIN>USDT spot symbol on MEXC, filtered by minimum 24h quote volume so the bot doesn't waste calls (or issue signals) on dead/illiquid markets. This is effectively "the whole market" in liquidity terms. """
    exchange_info = _get("/api/v3/exchangeInfo")
    tradable = {
        s["symbol"]
        for s in exchange_info["symbols"]
        if s.get("status") in ("TRADING", "ENABLED", "1")
        and s["quoteAsset"] == config.QUOTE_ASSET
        and s.get("isSpotTradingAllowed", True)
    }

    tickers = _get("/api/v3/ticker/24hr")
    liquid = [
        t for t in tickers
        if t["symbol"] in tradable
        and float(t.get("quoteVolume", 0) or 0) >= config.MIN_24H_QUOTE_VOLUME_USDT
    ]
    liquid.sort(key=lambda t: float(t.get("quoteVolume", 0) or 0), reverse=True)

    symbols = [t["symbol"] for t in liquid][: config.MAX_SYMBOLS_PER_CYCLE]
    log.info("Market scan: %d liquid USDT symbols selected (of %d tradable).",
              len(symbols), len(tradable))
    return symbols


def get_klines(symbol: str, interval: str, limit: int = 250) -> pd.DataFrame:
    mexc_interval = _mexc_interval(interval)
    raw = _get("/api/v3/klines", {
        "symbol": symbol, "interval": mexc_interval, "limit": limit,
    })
    # MEXC's klines response has fewer columns than Binance's (8 vs 12):
    # [open_time, open, high, low, close, volume, close_time, quote_volume]
    # We build the DataFrame dynamically from however many columns actually
    # come back, so this keeps working even if the exchange adds/drops a
    # trailing field.
    base_cols = ["open_time", "open", "high", "low", "close", "volume",
                 "close_time", "quote_volume", "trades",
                 "taker_buy_base", "taker_buy_quote", "ignore"]
    n = len(raw[0]) if raw else len(base_cols)
    cols = base_cols[:n]
    df = pd.DataFrame(raw, columns=cols)
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = df[c].astype(float)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    return df[["open_time", "open", "high", "low", "close", "volume"]]


def get_multi_timeframe_data(symbol: str) -> Dict[str, pd.DataFrame]:
    """Fetch every configured timeframe for one symbol."""
    data = {}
    for tf_key, interval in config.TIMEFRAMES.items():
        try:
            data[tf_key] = get_klines(symbol, interval, config.KLINES_LIMIT)
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed to fetch %s %s: %s", symbol, interval, exc)
            return {}
    return data


def fetch_all_symbols_data(symbols: List[str]) -> Dict[str, Dict[str, pd.DataFrame]]:
    """ Concurrently fetches multi-timeframe data for every symbol, respecting a bounded thread pool so we stay well under the exchange's rate limits. """
    results: Dict[str, Dict[str, pd.DataFrame]] = {}
    with ThreadPoolExecutor(max_workers=config.MAX_CONCURRENT_REQUESTS) as pool:
        future_to_symbol = {
            pool.submit(get_multi_timeframe_data, sym): sym for sym in symbols
        }
        for future in as_completed(future_to_symbol):
            sym = future_to_symbol[future]
            try:
                data = future.result()
                if data:
                    results[sym] = data
            except Exception as exc:  # noqa: BLE001
                log.warning("Error fetching %s: %s", sym, exc)
            time.sleep(0.05)
    return results
