""" Market data layer — talks to MEXC's public REST API (no API key needed for market data). Handles symbol discovery (the "whole market" scan) and OHLCV retrieval per timeframe. NOTE: Switched from Binance to MEXC because Binance blocks requests coming from US-based server IPs (HTTP 451). MEXC's spot REST API mirrors Binance's format very closely, so almost nothing else in the bot needs to change. """
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
    raw = _get("/api/v3/klines", {
        "symbol": symbol, "interval": interval, "limit": limit,
    })
    cols = ["open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trades",
            "taker_buy_base", "taker_buy_quote", "ignore"]
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
            # small pacing delay to be a good API citizen
            time.sleep(0.05)
    return results
