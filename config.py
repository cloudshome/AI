"""CryptoBrain configuration — copy .env.example to .env to override."""
from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

from data.symbols import DEFAULT_WATCHLIST, normalize_symbol, parse_symbol_list

load_dotenv()

VERSION = "2.0.0"

ROOT = Path(__file__).parent

# ── Market data ──────────────────────────────────────────────────────────
SYMBOL = normalize_symbol(os.getenv("SYMBOL", "BTCUSDT"))
SYMBOLS = parse_symbol_list(os.getenv("SYMBOLS"), default=DEFAULT_WATCHLIST)
TIMEFRAME = os.getenv("TIMEFRAME", "15m")
BARS = int(os.getenv("BARS", "500"))

# Binance endpoints: the geo-friendly public market-data mirror is tried
# first, then the standard host. Futures endpoints geo-block some regions;
# the client degrades gracefully when they are unreachable.
BINANCE_HOSTS = ["https://data-api.binance.vision", "https://api.binance.com"]
BINANCE_FUTURES_HOSTS = ["https://fapi.binance.com", "https://fapi.binance.com"]

# ── Signal engine ────────────────────────────────────────────────────────
MIN_CONFIDENCE = int(os.getenv("MIN_CONFIDENCE", "55"))
DEFAULT_RISK_REWARD = float(os.getenv("DEFAULT_RISK_REWARD", "2.0"))
MAX_RISK_PCT = float(os.getenv("MAX_RISK_PCT", "1.0"))

# ── Institutional intelligence / risk desk ───────────────────────────────
# Stricter filters used by the AI Trading Intelligence System.  These do not
# remove the lower-threshold raw plans; they decide whether a professional
# desk-style report is allowed to say BUY/SELL or must protect capital with
# NO TRADE.  Per the professional roadmap, the R:R gate is a safety FLOOR
# (not a fixed target); setup-specific targets come from measured expectancy.
INTELLIGENCE_MIN_CONFIDENCE = int(os.getenv("INTELLIGENCE_MIN_CONFIDENCE", "80"))
INTELLIGENCE_MIN_RR = float(os.getenv("INTELLIGENCE_MIN_RR", "1.5"))
ACCOUNT_BALANCE = float(os.getenv("ACCOUNT_BALANCE", "0") or 0)  # optional; enables position sizing
RISK_PCT = max(0.0, min(MAX_RISK_PCT, float(os.getenv("RISK_PCT", "0.5"))))
MAX_DAILY_LOSS_PCT = float(os.getenv("MAX_DAILY_LOSS_PCT", "1.5"))
MAX_WEEKLY_LOSS_PCT = float(os.getenv("MAX_WEEKLY_LOSS_PCT", "3.0"))

# ── Professional operating mode (decision list A1/A4/B9) ─────────────────
# Desk-first: the strict capital-preservation report is the DEFAULT output of
# scans, dashboard and notifiers.  Raw engine plans remain visible as research.
DESK_DEFAULT = os.getenv("DESK_DEFAULT", "true").lower() in ("1", "true", "yes")

# Progression ladder (Phase 18): student → researcher → simulator → micro →
# consistent → scale.  Each level changes risk caps and whether unproven
# setups may be approved.  Unproven = fewer than 100 backtest samples, fewer
# than 20 decided paper samples, or non-positive expectancy (decision A6/B10).
PROGRESSION = os.getenv("PROGRESSION", "student").strip().lower()
PROGRESSION_LEVELS = {
    "student":    {"risk_pct": 0.25, "daily": 1.0, "weekly": 2.0, "approve_unproven": False, "label": "Learn: market structure + macro + risk"},
    "researcher": {"risk_pct": 0.25, "daily": 1.0, "weekly": 2.0, "approve_unproven": False, "label": "Backtest one strategy; no live approvals"},
    "simulator":  {"risk_pct": 0.50, "daily": 1.5, "weekly": 3.0, "approve_unproven": True,  "label": "Paper-trade 100–200 samples"},
    "micro":      {"risk_pct": 0.50, "daily": 1.5, "weekly": 3.0, "approve_unproven": True,  "label": "Very small real capital"},
    "consistent": {"risk_pct": 0.75, "daily": 2.0, "weekly": 4.0, "approve_unproven": True,  "label": "Proven expectancy + controlled drawdown"},
    "scale":      {"risk_pct": 1.00, "daily": 2.5, "weekly": 5.0, "approve_unproven": True,  "label": "Increase capital slowly"},
}
if PROGRESSION not in PROGRESSION_LEVELS:
    PROGRESSION = "student"

# Primary setup family (decision A1): only these plan types may reach
# tradeable confidence; every other setup is a watch-item until individually
# proven.  Default = the roadmap's suggested first edge: liquidity sweep +
# trend continuation (sweep reversals + OB/FVG pullbacks).
PRIMARY_SETUP_FAMILY = os.getenv("PRIMARY_SETUP_FAMILY", "sweep_trend_continuation").strip().lower()
PRIMARY_FAMILIES = {
    "sweep_trend_continuation": {
        "Sweep Reversal Buy", "Sweep Reversal Sell",
        "Buy Pullback", "Sell Pullback",
    },
    "all": None,  # explicit opt-out of narrowing (radar mode)
}

# Data-driven R:R targets (decision A2): TP distance comes from measured
# per-setup expectancy when available, clamped to this range.
TP_RR_MIN = float(os.getenv("TP_RR_MIN", "1.5"))
TP_RR_MAX = float(os.getenv("TP_RR_MAX", "4.0"))

# ── CryptoDada connector ─────────────────────────────────────────────────
CRYPTODADA_MODE = os.getenv("CRYPTODADA_MODE", "auto")          # auto|api|browser
CRYPTODADA_BASE_URL = os.getenv("CRYPTODADA_BASE_URL", "").rstrip("/")
CRYPTODADA_EMAIL = os.getenv("CRYPTODADA_EMAIL", "")
CRYPTODADA_PASSWORD = os.getenv("CRYPTODADA_PASSWORD", "")
CRYPTODADA_2FA = os.getenv("CRYPTODADA_2FA", "")

# ── Discord ──────────────────────────────────────────────────────────────
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
DISCORD_CHANNEL_IDS = [c.strip() for c in os.getenv("DISCORD_CHANNEL_IDS", "").split(",") if c.strip()]
DISCORD_ANNOUNCE_WEBHOOK = os.getenv("DISCORD_ANNOUNCE_WEBHOOK", "")

# ── LLM AI Brain ─────────────────────────────────────────────────────────
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "off")                 # auto|groq|openai|gemini|off
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# ── Notifiers ────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Web dashboard ────────────────────────────────────────────────────────
DASHBOARD_HOST = os.getenv("DASHBOARD_HOST", "0.0.0.0")
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "8050"))

# ── Signal database (learning store) ─────────────────────────────────────
DB_PATH = os.getenv("DB_PATH", str(ROOT / "data" / "cryptobrain.db"))

# ── Paper-trading runner (simulation only — never places exchange orders) ─
# The runner reads public OHLCV candles to fill approved paper trades and
# closes them at their planned SL / first TP.  Use `python main.py paper --watch`.
PAPER_POLL_SECONDS = int(os.getenv("PAPER_POLL_SECONDS", "30"))
PAPER_MAX_CANDLES_PER_CHECK = int(os.getenv("PAPER_MAX_CANDLES_PER_CHECK", "1000"))

# ── Backtester ───────────────────────────────────────────────────────────
BACKTEST_HORIZONS = [float(h) for h in os.getenv("BACKTEST_HORIZONS", "1,4,24").split(",")]
BACKTEST_MIN_BARS = int(os.getenv("BACKTEST_MIN_BARS", "120"))
BACKTEST_STEP = int(os.getenv("BACKTEST_STEP", "1"))

# ── Calibration (self-improvement) ───────────────────────────────────────
# Trust thresholds follow the roadmap: >=100 backtest samples and >=20 decided
# paper samples before a setup is treated as PROVEN (decisions A6/B10).
CALIBRATE_MIN_N = int(os.getenv("CALIBRATE_MIN_N", "100"))      # min backtest+paper samples to calibrate a setup
CALIBRATE_MIN_PAPER_N = int(os.getenv("CALIBRATE_MIN_PAPER_N", "20"))
CALIBRATE_GAIN = float(os.getenv("CALIBRATE_GAIN", "0.25"))   # expectancy -> multiplier sensitivity
CALIBRATE_MAX_MULT = float(os.getenv("CALIBRATE_MAX_MULT", "1.25"))
CALIBRATE_MIN_MULT = float(os.getenv("CALIBRATE_MIN_MULT", "0.6"))
CALIBRATE_FILTER = os.getenv("CALIBRATE_FILTER", "false").lower() in ("1", "true", "yes")
CALIBRATE_FILTER_THRESHOLD = float(os.getenv("CALIBRATE_FILTER_THRESHOLD", "-0.35"))  # R, negative

# ── Risk enforcement (decision B6) ───────────────────────────────────────
# When enabled, the approval queue and paper runner HALT new trades once the
# daily loss limit is reached, flag reduced activity at the weekly limit, and
# apply the drawdown ladder (-5% reduce / -8% stop & review / -10% full review).
ENFORCE_RISK_LIMITS = os.getenv("ENFORCE_RISK_LIMITS", "true").lower() in ("1", "true", "yes")
DRAWDOWN_REDUCE_PCT = float(os.getenv("DRAWDOWN_REDUCE_PCT", "5.0"))
DRAWDOWN_STOP_PCT = float(os.getenv("DRAWDOWN_STOP_PCT", "8.0"))
DRAWDOWN_REVIEW_PCT = float(os.getenv("DRAWDOWN_REVIEW_PCT", "10.0"))

# ── Trader-state no-trade gate (decision B7) ─────────────────────────────
# angry / tired / revenge / chasing flags block the approval queue until
# cleared.  Set with `python main.py tradestate --angry ...` or the dashboard.
TRADER_STATE_BLOCK = os.getenv("TRADER_STATE_BLOCK", "true").lower() in ("1", "true", "yes")

# ── Portfolio / correlation risk (decision B2) ───────────────────────────
# BTC + ETH are ONE correlated crypto-risk bucket: same-direction duplicates,
# ETH against a strongly bearish BTC, and bucket-size/risk caps veto new trades.
CRYPTO_BUCKET_MAX_TRADES = int(os.getenv("CRYPTO_BUCKET_MAX_TRADES", "2"))
MAX_CRYPTO_EXPOSURE_RISK_PCT = float(os.getenv("MAX_CRYPTO_EXPOSURE_RISK_PCT", "1.0"))
ETH_BTC_GATE = os.getenv("ETH_BTC_GATE", "true").lower() in ("1", "true", "yes")
GOLD_OWN_BUCKET = True  # gold exposure is tracked separately from crypto

# ── Gold playbook (decision B8) ──────────────────────────────────────────
# Sessions in UTC hours; PDH/PDL come from daily bars; US-data countdown is
# the existing macro calendar's high-impact window.  Mode: warn | block | off.
GOLD_SESSION_MODE = os.getenv("GOLD_SESSION_MODE", "warn").strip().lower()
GOLD_LONDON_WINDOW = (7, 16)      # UTC hours, inclusive start / exclusive end
GOLD_NY_WINDOW = (12, 21)         # UTC hours
GOLD_PDH_PDL = True
GOLD_NEWS_BLOCK = os.getenv("GOLD_NEWS_BLOCK", "true").lower() in ("1", "true", "yes")

# ── Information hierarchy / source trust (decision A5) ───────────────────
# Tier 1 market data, 2 macro, 3 positioning, 4 sentiment, 5 private signals.
# Private sources can never auto-trigger trades; conflicts force NO TRADE.
SOURCE_TIER_MAX_AUTO = int(os.getenv("SOURCE_TIER_MAX_AUTO", "2"))  # tiers <= this may contribute to a tradeable signal
SOURCE_TIER_NAMES = {1: "market_data", 2: "macro", 3: "positioning", 4: "sentiment", 5: "private"}

# ── State memory / signal stability (anti-spam, anti-whipsaw) ────────────
SIGNAL_COOLDOWN_MINUTES = int(os.getenv("SIGNAL_COOLDOWN_MINUTES", "30"))  # global floor
FLIP_PRICE_THRESHOLD_PCT = float(os.getenv("FLIP_PRICE_THRESHOLD_PCT", "0.8"))
MAX_FLIPS_PER_HOUR = int(os.getenv("MAX_FLIPS_PER_HOUR", "2"))
