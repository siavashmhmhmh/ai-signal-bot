"""
Tiny persistent state store so the bot doesn't resend the same signal
over and over on every scan cycle.
"""
from __future__ import annotations
import json
import os
import time
from typing import Dict

import config


def _load() -> Dict:
    if not os.path.exists(config.STATE_FILE):
        return {}
    try:
        with open(config.STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(state: Dict) -> None:
    with open(config.STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f)


def should_send(symbol: str, direction: str) -> bool:
    state = _load()
    key = f"{symbol}:{direction}"
    last_sent = state.get(key)
    if last_sent is None:
        return True
    elapsed_hours = (time.time() - last_sent) / 3600
    return elapsed_hours >= config.SIGNAL_COOLDOWN_HOURS


def mark_sent(symbol: str, direction: str) -> None:
    state = _load()
    state[f"{symbol}:{direction}"] = time.time()
    _save(state)
