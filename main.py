""" Entry point. Run with: python main.py Required env vars: TELEGRAM_BOT_TOKEN - from @BotFather TELEGRAM_CHAT_ID - chat/channel to post signals into Optional: ANTHROPIC_API_KEY - enables AI-written rationale per signal """
from __future__ import annotations
import logging

from telegram import Update
from telegram.ext import ContextTypes

import config
import market_data
import state
import telegram_bot
from ai_analysis import generate_commentary
from signal_engine import analyze_symbol

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
log = logging.getLogger("main")


async def run_market_scan(app) -> int:
    """One full scan cycle across the whole liquid USDT market. Returns the number of signals sent."""
    log.info("Starting market scan...")
    symbols = market_data.get_liquid_usdt_symbols()
    all_data = market_data.fetch_all_symbols_data(symbols)
    log.info("Fetched data for %d/%d symbols.", len(all_data), len(symbols))

    sent = 0
    for symbol, mtf_raw in all_data.items():
        try:
            signal = analyze_symbol(symbol, mtf_raw)
        except Exception as exc:  # noqa: BLE001
            log.warning("Analysis failed for %s: %s", symbol, exc)
            continue

        if signal is None:
            continue

        if not state.should_send(signal.symbol, signal.direction):
            log.info("Skipping %s %s â€” still in cooldown.", signal.symbol, signal.direction)
            continue

        log.info("Signal found: %s %s score=%.1f", signal.symbol, signal.direction, signal.score)
        commentary = generate_commentary(signal)
        delivered = await telegram_bot.send_signal(app, signal, commentary)
        if delivered:
            state.mark_sent(signal.symbol, signal.direction)
            sent += 1
        else:
            log.warning("%s %s scored %.1f but delivery FAILED â€” not marked as sent, "
                        "will retry next cycle.", signal.symbol, signal.direction, signal.score)

    log.info("Scan complete. %d signal(s) sent.", sent)
    return sent


async def job_scan_callback(context: ContextTypes.DEFAULT_TYPE) -> None:
    await run_market_scan(context.application)


async def command_scan_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("ðŸ”Ž Ø¯Ø± Ø­Ø§Ù„ Ø§Ø³Ú©Ù† Ú©Ù„ Ø¨Ø§Ø²Ø§Ø±... Ù…Ù…Ú©Ù† Ø§Ø³Øª Ú†Ù†Ø¯ Ø¯Ù‚ÛŒÙ‚Ù‡ Ø·ÙˆÙ„ Ø¨Ú©Ø´Ø¯.")
    sent = await run_market_scan(context.application)
    await update.message.reply_text(f"âœ… Ø§Ø³Ú©Ù† ØªÙ…Ø§Ù… Ø´Ø¯. {sent} Ø³ÛŒÚ¯Ù†Ø§Ù„ Ø¬Ø¯ÛŒØ¯ Ø§Ø±Ø³Ø§Ù„ Ø´Ø¯.")


def main() -> None:
    if not config.TELEGRAM_BOT_TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN ØªÙ†Ø¸ÛŒÙ… Ù†Ø´Ø¯Ù‡ Ø§Ø³Øª. Ù…ØªØºÛŒØ± Ù…Ø­ÛŒØ·ÛŒ Ø±Ø§ Ø³Øª Ú©Ù†ÛŒØ¯.")
    if not config.TELEGRAM_CHAT_ID:
        log.warning("TELEGRAM_CHAT_ID ØªÙ†Ø¸ÛŒÙ… Ù†Ø´Ø¯Ù‡ â€” Ø³ÛŒÚ¯Ù†Ø§Ù„â€ŒÙ‡Ø§ Ø§Ø±Ø³Ø§Ù„ Ù†Ø®ÙˆØ§Ù‡Ù†Ø¯ Ø´Ø¯ ØªØ§ ÙˆÙ‚ØªÛŒ ØªÙ†Ø¸ÛŒÙ… Ø´ÙˆØ¯.")

    app = telegram_bot.build_application(job_scan_callback, command_scan_callback)
    log.info("Bot starting... scan interval = %ss, min score = %s",
              config.SCAN_INTERVAL_SECONDS, config.MIN_SIGNAL_SCORE)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
