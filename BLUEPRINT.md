# The Master Blueprint: From Paper Engine to Real Wealth in BTC, ETH & GOLD

> Our ultimate goal: **earning consistent profits and steadily accumulating
> real BTC, ETH, and GOLD** — with the precision and discipline of an
> institutional trading desk.
>
> Capital preservation always comes before capital growth. A trader who
> loses 50% needs a 100% gain just to break even. A trader who protects
> downside risk and executes high-expectancy setups with strict risk limits
> will mathematically compound wealth over time.

## 🧭 The System Map

```
                 AI TRADING INTELLIGENCE SYSTEM
                                 │
     ┌────────────┬──────────────┼──────────────┬─────────────┐
     ▼            ▼              ▼              ▼             ▼
 Market Feeds   Macro & News   Sentiment/OI   LLM Brain   Instant Alerts
 Binance/KuCoin Cointelegraph  Fear & Greed   Groq/OpenAI  Telegram Bot
 Tokenized Gold Econ Calendar  Funding, OI    Gemini       Discord Webhook
```

## 🔑 Essential APIs & Data Feeds

| Feed | Source | Key needed? | Status in repo |
|------|--------|-------------|----------------|
| Spot OHLCV (BTC/ETH/GOLD) | Binance public (`api.binance.com`) | No | ✅ integrated |
| Futures metrics (funding, OI, long/short) | Binance `fapi.binance.com` | No | ✅ best-effort |
| Multi-exchange verification | KuCoin + OKX public APIs | No | ✅ `agent health` cross-check |
| Crypto Fear & Greed Index | alternative.me | No | ✅ `brain/context.py` |
| BTC dominance, market cap, ETH/BTC | CoinGecko | Free tier | ✅ `brain/context.py` |
| DXY + spot gold (XAUUSD) | stooq CSV | No | ✅ `brain/context.py` (TwelveData/AlphaVantage optional — not needed) |
| Macro calendar / high-impact US data | stooq macro | No | ✅ `brain/context.py` |
| Financial news sentiment | CoinTelegraph/CoinDesk/Decrypt RSS | No | ✅ `data/sources/news.py` |
| LLM narrative brain | Groq (`gsk_…`) / OpenAI / Gemini | Free tier | ✅ `ai/llm_brain.py` |
| Telegram push alerts | @BotFather token + chat id | 2 min | ✅ `output/notifiers.py` |
| Discord signal cards | Webhook URL | 1 min | ✅ `output/notifiers.py` |

> **Tip:** run from a local computer or VPS in Dhaka/Asia — direct, unblocked,
> low-latency access to Binance.

## ⚙️ .env Configuration

Copy `.env.example` to `.env` and enable what you want:

```env
# ── Market Data ──────────────────────────────────────────────────────────
SYMBOLS=BTCUSDT,ETHUSDT,XAUUSD
TIMEFRAME=15m
BARS=500

# ── Progression Ladder (start with simulator) ────────────────────────────
PROGRESSION=simulator       # student | researcher | simulator | micro | consistent | scale

# ── LLM Reasoning Brain (optional) ───────────────────────────────────────
LLM_PROVIDER=openai         # auto | groq | openai | gemini | off
OPENAI_API_KEY=gsk_your_groq_or_openai_key_here
OPENAI_BASE_URL=https://api.groq.com/openai/v1
OPENAI_MODEL=llama-3.3-70b-versatile

# ── Instant Notifications (optional) ─────────────────────────────────────
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_telegram_chat_id
DISCORD_ANNOUNCE_WEBHOOK=https://discord.com/api/webhooks/...
```

## 📈 The 4-Step Path to Consistent Real Gains

```
[STEP 1: PAPER ACCUMULATION]      [STEP 2: SELF-IMPROVEMENT]
  Run paper monitor on live data    Run `learn` + `stats`
  Target: 100–200 paper trades      Confirm: Expectancy > +0.50R,
  (PROGRESSION=simulator)           Win Rate > 55%, PF > 1.5
            │                                   │
            ▼                                   ▼
[STEP 3: MICRO LIVE EXECUTION]    [STEP 4: SCALING & ACCUMULATION]
  PROGRESSION=micro                 Promote to consistent / scale
  0.5% risk per trade               Sweep profits into cold-storage
  -1.5% daily stop                  BTC, ETH and tokenized GOLD
```

### Step 1 — Accumulate 100–200 paper trades

```bash
python main.py paper --watch --interval 30
```

Proves whether the edge works in current live conditions without risking a
dollar. Review/approve signals on the dashboard (`python main.py web`) or via
`python main.py approve <id>`.

### Step 2 — Calibrate & prove the statistical edge

```bash
python main.py learn      # recompute setup calibration
python main.py stats      # business scorecard
python main.py simulator  # grind unique backtest + paper samples
python main.py agent ask "am i ready for micro?"   # the graduation gate
```

The graduation gate (implemented in `data/simulator.py`) promotes you to
Step 3 **only** when the primary setup family shows:

1. **Profit Factor ≥ 1.5**
2. **Expectancy ≥ +0.50R per trade**
3. **Win rate > 55%**
4. **Rule compliance ≥ 90%** (journaled trades)
5. **Sample proof:** ≥ 100 backtest + ≥ 20 paper per primary setup

### Step 3 — Graduate to real capital (`PROGRESSION=micro`)

Small live sizes: **0.5% risk per trade**, strict **-1.5% daily stop**.
Emotions cannot interfere with execution discipline.

### Step 4 — Systematically accumulate BTC, ETH & GOLD

Sweep a fixed portion of profits (e.g. 50%) into long-term spot reserves:

- **BTC** — digital gold, store of value
- **ETH** — the decentralized compute layer
- **GOLD / PAXG** — macro hedge against fiat inflation and volatility

## 🛡️ The 5 Golden Rules of the Trading Brain

1. **Protect capital above all** — a stopped-out trade with rules followed is
   a *good* trade; a random win that broke the rules is a *dangerous* trade.
2. **Enforce the risk gate** — never exceed 1.0% risk per trade; at the daily
   (-3.0%) or weekly (-6.0%) stop the desk stops trading immediately.
3. **Respect behavioral flags** — `python main.py tradestate --tired` locks
   you out until you reset.
4. **Follow the playbooks** —
   - **BTC:** 4H regime → 1H setup → 15M entry confirmation
   - **ETH:** BTC first, ETH second (never long ETH into a falling BTC)
   - **GOLD:** trade London/NY sessions; stand aside around high-impact US data
5. **Let the math compound** — trading is managing risk and letting positive
   statistical expectancy build wealth over time.

## 🎯 Ready to Run

```bash
python main.py web        # 1. all-in-one web dashboard
python main.py brief      # 2. daily morning briefing
python main.py agent all  # 3. one desk run: health + briefing + graduation
python main.py health     # 4. system health (+ KuCoin/OKX cross-check)
```
