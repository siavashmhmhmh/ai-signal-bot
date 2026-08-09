"""
Optional AI narrative layer.

Important, honest design note: the AI does NOT invent the signal or predict
price. The rule-based signal_engine already computed direction, entry, stop,
and targets from real indicator values. This module only asks Claude to turn
those already-computed numbers into a short, readable rationale — the kind
of "why" a human analyst would write next to a trade idea. If no API key is
configured, the bot skips this and still sends complete signals.
"""
from __future__ import annotations
import logging

import config
from signal_engine import Signal

log = logging.getLogger("ai_analysis")

_client = None
if config.ENABLE_AI_COMMENTARY:
    try:
        import anthropic
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not initialize Anthropic client, AI commentary disabled: %s", exc)
        _client = None


def generate_commentary(signal: Signal) -> str | None:
    if _client is None:
        return None

    prompt = f"""You are a professional crypto market analyst. Below are ALREADY-COMPUTED
technical readings for {signal.symbol} (do not invent any numbers beyond these).
Write a concise 2-3 sentence rationale (in Persian) explaining why this
{signal.direction} setup lines up, referencing the concrete data given.
Do not add price predictions beyond the given entry/stop/targets. Do not
give financial advice language like "you should buy" — describe the setup
objectively.

Direction: {signal.direction}
Confidence score: {signal.score}/100
Entry: {signal.entry}
Stop loss: {signal.stop_loss}
Targets: {signal.targets}
Factor scores (-1 to 1): {signal.factors}
Indicator snapshot: {signal.snapshot}
"""

    try:
        response = _client.messages.create(
            model=config.ANTHROPIC_MODEL,
            max_tokens=220,
            messages=[{"role": "user", "content": prompt}],
        )
        text_parts = [b.text for b in response.content if b.type == "text"]
        return "".join(text_parts).strip() or None
    except Exception as exc:  # noqa: BLE001
        log.warning("AI commentary failed for %s: %s", signal.symbol, exc)
        return None
