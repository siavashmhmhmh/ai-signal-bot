"""
Telegram integration — formats and sends signal messages, plus a couple of
handy commands (/start, /status, /scan) for manual control.
"""
from __future__ import annotations
import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

import config
from signal_engine import Signal

log = logging.getLogger("telegram_bot")


def format_signal_message(signal: Signal, ai_commentary: str | None) -> str:
    arrow = "🟢⬆️" if signal.direction == "LONG" else "🔴⬇️"
    targets_lines = "\n".join(
        f"   🎯 TP{i+1}: <code>{t}</code>  (R:R {rr}:1)"
        for i, (t, rr) in enumerate(zip(signal.targets, signal.risk_reward))
    )

    factors_line = " | ".join(f"{k}: {v:+.2f}" for k, v in signal.factors.items())

    msg = (
        f"{arrow} <b>{signal.direction} SIGNAL — {signal.symbol}</b>\n"
        f"اطمینان مدل: <b>{signal.score}/100</b>\n\n"
        f"🎯 <b>ورود (Entry):</b> <code>{signal.entry}</code>\n"
        f"🛑 <b>حد ضرر (Stop):</b> <code>{signal.stop_loss}</code>\n"
        f"{targets_lines}\n\n"
        f"📊 فاکتورها: {factors_line}\n"
    )

    if ai_commentary:
        msg += f"\n🧠 <b>تحلیل:</b> {ai_commentary}\n"

    msg += (
        "\n⚠️ این پیام سیگنال معاملاتی خودکار بر پایه تحلیل تکنیکال است و "
        "توصیه مالی نیست. مدیریت ریسک و حجم پوزیشن با خودتان است."
    )
    return msg


async def send_signal(app: Application, signal: Signal, ai_commentary: str | None) -> None:
    if not config.TELEGRAM_CHAT_ID:
        log.warning("TELEGRAM_CHAT_ID not set — cannot send signal for %s", signal.symbol)
        return
    text = format_signal_message(signal, ai_commentary)
    await app.bot.send_message(
        chat_id=config.TELEGRAM_CHAT_ID,
        text=text,
        parse_mode=ParseMode.HTML,
    )


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🤖 ربات سیگنال‌دهی هوشمند فعال است.\n"
        "این ربات کل بازار USDT بایننس را هر چند دقیقه یک‌بار اسکن می‌کند و "
        "سیگنال‌های خرید/فروش با نقطه ورود، حد ضرر و اهداف قیمتی صادر می‌کند.\n\n"
        "دستورات:\n"
        "/status — وضعیت فعلی ربات\n"
        "/scan — اجرای فوری یک اسکن دستی"
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        f"✅ در حال اجرا.\n"
        f"بازه اسکن: هر {config.SCAN_INTERVAL_SECONDS // 60} دقیقه\n"
        f"حداقل امتیاز سیگنال: {config.MIN_SIGNAL_SCORE}/100\n"
        f"تحلیل هوش مصنوعی: {'فعال' if config.ENABLE_AI_COMMENTARY else 'غیرفعال (کلید API تنظیم نشده)'}"
    )


def build_application(job_scan_callback, command_scan_callback) -> Application:
    """
    job_scan_callback: async function(context: ContextTypes.DEFAULT_TYPE) -> None
        used by the recurring background JobQueue.
    command_scan_callback: async function(update: Update, context) -> None
        used by the manual /scan command (python-telegram-bot requires the
        two signatures to be different, since CommandHandler passes `update`
        as the first argument and JobQueue does not).
    Both are wired in from main.py.
    """
    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("scan", command_scan_callback))

    # Recurring background scan job
    app.job_queue.run_repeating(
        job_scan_callback, interval=config.SCAN_INTERVAL_SECONDS, first=5
    )
    return app
