""" Central configuration for the AI Crypto Signal Bot. Everything sensitive (tokens, keys) is read from environment variables — never hard-code secrets in this file. """
import os

# ---------------------------------------------------------------------------
# TELEGRAM
# ---------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
# Chat/channel ID where signals are posted (can be your own user id, a group,
# or a channel where the bot is an admin). Get it from @userinfobot or by
# calling /getUpdates after messaging your bot once.
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ---------------------------------------------------------------------------
# OPTIONAL AI NARRATIVE LAYER (Claude)
# ---------------------------------------------------------------------------
# If set, every signal gets a short natural-language rationale written by
# Claude based on the ACTUAL computed indicator values (not invented data).
# If left empty, the bot still works perfectly using the rule-based engine.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
ENABLE_AI_COMMENTARY = bool(ANTHROPIC_API_KEY)

# ---------------------------------------------------------------------------
# MARKET SCAN
# ---------------------------------------------------------------------------
EXCHANGE_BASE_URL = "https://api.mexc.com"
# NOTE: was api.binance.com — Binance returns HTTP 451 (geo-block) from
# Railway's US-based IPs, which silently kills every symbol fetch every
# single cycle. MEXC's public REST API is used instead (see market_data.py
# for the small format differences this required: column count + interval
# names).
QUOTE_ASSET = "USDT"                 # scan every <coin>/USDT pair
MIN_24H_QUOTE_VOLUME_USDT = 5_000_000  # skip illiquid / low-volume coins
MAX_SYMBOLS_PER_CYCLE = 80           # respects exchange rate limits per scan
SCAN_INTERVAL_SECONDS = 15 * 60      # how often the whole market is rescanned
REQUEST_TIMEOUT = 10
MAX_CONCURRENT_REQUESTS = 8          # thread pool size for klines fetching

# Timeframes used for multi-timeframe confluence (fast -> slow)
TIMEFRAMES = {
    "entry": "15m",   # timing the entry
    "trend_mid": "1h",
    "trend_high": "4h",
    "trend_macro": "1d",
}
KLINES_LIMIT = 250  # candles fetched per timeframe (enough for EMA200)

# ---------------------------------------------------------------------------
# SIGNAL ENGINE THRESHOLDS
# ---------------------------------------------------------------------------
# Each factor contributes a weighted score in [-1, +1]. Total is normalized
# to 0-100. A signal only fires above MIN_SIGNAL_SCORE.
MIN_SIGNAL_SCORE = 30
# NOTE: lowered from 68. With 5 weighted factors in [-1,1] summing to a
# max magnitude of 1.0, a score of 68+ requires almost every factor to
# align strongly and simultaneously — this rarely happens in real market
# data, which is why zero signals were firing even once data was flowing.
# Start around 30, watch the per-symbol score logs (signal_engine.py now
# logs every symbol's score each cycle), and raise this once you've seen
# what realistic scores look like for your actual factor weights.
FACTOR_WEIGHTS = {
    "trend_alignment": 0.25,   # EMA20/50/200 stacking across timeframes
    "momentum": 0.20,          # RSI + MACD
    "volatility_position": 0.15,  # Bollinger Band position / squeeze
    "volume": 0.15,            # volume spike vs average
    "structure": 0.25,         # proximity to support/resistance / swing points
}

# Risk parameters for entry / stop / targets
ATR_LENGTH = 14
ATR_STOP_MULTIPLIER = 1.5       # stop distance = ATR * multiplier (beyond structure)
TP_R_MULTIPLES = [1.0, 2.0, 3.5]  # TP1 / TP2 / TP3 expressed in "R" (risk units)

# Avoid spamming: don't resend the same symbol/direction within this window
# unless the setup meaningfully changes.
SIGNAL_COOLDOWN_HOURS = 6

# ---------------------------------------------------------------------------
# MISC
# ---------------------------------------------------------------------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
STATE_FILE = os.getenv("STATE_FILE", "bot_state.json")  # tracks sent signals
