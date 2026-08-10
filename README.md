# 🧠 CryptoBrain — AI Trading Brain (Signal Engine)

**Multi-source, multi-indicator, conditional-signal engine for BTC, ETH, and XAUUSD/GOLD (plus any Binance USDT pair).**

CryptoBrain is the engine behind the "AI Brain Agent Assistant" concept: instead
of following one indicator (or copying one signal service), it reads **many
indicators + market structure (ICT/SMC) at the same time**, scores them, and
emits **multiple conditional trade plans** — the way a professional
discretionary trader thinks:

> *IF price sweeps buy-side liquidity AND prints bearish CHOCH → SELL.*
>
> *IF price pulls back to the bullish order block AND rejects → BUY.*

market dashboard: that project is the *situational awareness screen* (macro,
news, funding, order-flow, LLM brief), this project is the *signal generation
brain* (indicators → structure → scoring → JSON signals + conditional plans),
with connectors for your **private CryptoDada website** and **Discord group**.

---

## ✨ What it does

| Capability | Description |
|---|---|
| **Indicator engine** | RSI, MACD, EMA/SMA stack, Supertrend, ADX, Stochastic, WaveTrend, Bollinger, ATR, ROC, VWAP (session-anchored), Volume Profile (POC), OBV, volume spike |
| **Structure engine (ICT/SMC)** | Fractal swings, **BOS / CHOCH**, **order blocks**, **fair value gaps** (filled/unfilled), **liquidity sweeps**, equal highs/lows, premium/discount zone |
| **Scoring brain** | Weighted condition scoring (Trend +15, Structure +15, OB/FVG +20, Liquidity +15, Volume +10, Divergence +10, Momentum +10, Location +5 = 100) → `HIGH / MEDIUM / LOW / NO TRADE` |
| **Multi-condition plans** | Immediate entry, pullback at OB/FVG, breakout, sweep-reversal, FVG retest — each with entry/SL/TP ladder, R:R, confidence, and a human-readable IF condition |
| **AI Trading Intelligence System** | Institutional desk report for BTC / ETH / XAUUSD: strict `NO TRADE` filter when confidence <80%, RR <1:2, HTF conflict, volatile/sideways conditions, or news risk; includes scenarios, risk management, IF/THEN logic, trade management and self-review. |
| **JSON signals** | Exact schema requested — `signal_id`, `timestamp`, `asset`, `action`, `entry`, `stop_loss`, `take_profit`, `risk_reward`, `confidence`, `timeframe`, `reason` — plus validation |
| **CryptoDada website** | Connector for the private membership site (volume-spike screener, market radar, analyst notes, historical signals) via hidden-API probe or Playwright login |
| **Discord group** | Channel reader (bot/self token) that parses analyst "market update" posts into structured bias notes + sentiment; webhook push for outbound alerts |
| **News** | RSS headlines with naive sentiment tally (CoinTelegraph, CoinDesk, Decrypt) |
| **LLM narrative** | Optional AI Brain briefing (OpenAI-compatible / Gemini) that turns the numbers into plain English; rule-based fallback when no key is configured |
| **Notifiers** | Telegram + Discord webhook push of signals |
| **Backtester** | Walk-forward grading of every plan at +1h/+4h/+24h → win-rate, avg R, expectancy by plan type / confidence / action |
| **Signal database** | SQLite learning store — every scan + every graded outcome, queried via `python main.py stats` |
| **Human approval gate** | Every actionable signal enters `PENDING_REVIEW`; you approve/reject/execute/close it with one command (or the dashboard buttons). Full audit trail per signal. |
| **Paper-trading runner** | Watches **approved** signals against live public Binance candles, simulates a planned entry, and auto-closes at SL / TP1. It records the outcome and R result — **never places a real exchange order**. |
| **Self-improvement** | `python main.py learn` recomputes a per-setup calibration profile from backtest outcomes **plus decided paper trades** — boosts positive-expectancy plans, dampens (or filters) negative ones. |
| **Coach (teaching)** | `python main.py coach` explains *why* the engine said what it said, mentors you through the top setup step-by-step, gives personal feedback on your own approvals/rejections, and ships a full trading glossary. |
| **CI** | GitHub Actions runs the offline test suite on every push |
| **Web dashboard** | **`python main.py`** opens the all-in-one dashboard: live signal + lifecycle badge, **candlestick chart**, **multi-timeframe table**, **what-the-market-offers styles grid**, **context panel** (news/macro/geopolitics/cycle/social/equities), **state memory panel** (signal stability), plans, human approval queue, a one-click **paper-trading runner** panel, recent signals, learning dashboard, coach, LLM narrative — auto-refreshing. |
| **Human-like thinking** | **Multi-timeframe** (Monthly/Weekly/Daily/4H/1H/30M/15M/5M/1M → HTF bias + LTF execution + alignment score), **full market context** (fear&greed, BTC dominance, S&P/Nasdaq/DXY, macro calendar FOMC/CPI/NFP, halving cycle, geopolitics, influencer/social pulse), **trading-style signals** (Scalp/Day/Swing/Momentum/Position — "what the market provides, we take"), and **state memory** so signals only change when the market state changes — no random 30s signals. |

---

## 🏗 Architecture

```
                    ┌──────────────────────────────────────────────┐
                    │                 SOURCES                      │
                    │  Binance (OHLCV, funding, OI, L/S, liq)      │
                    │  CryptoDada website (volume screener, radar, │
                    │    analyst, historical signals)              │
                    │  Discord (market updates, news, chat, polls) │
                    │  RSS news feeds                              │
                    └───────────────┬──────────────────────────────┘
                                    ▼
                    ┌──────────────────────────────────────────────┐
                    │            ENGINE (this repo)                │
                    │  indicators.py   → RSI MACD EMA VWAP ADX …   │
                    │  structure.py    → BOS/CHOCH OB FVG liquidity│
                    │  features.py     → labeled market snapshot   │
                    │  scorer.py       → weighted condition score  │
                    │  rules.py        → IF/THEN conditional plans │
                    │  signal_engine.py→ final JSON + best signal  │
                    └───────────────┬──────────────────────────────┘
                                    ▼
              ┌─────────────────────┼─────────────────────┐
              ▼                     ▼                     ▼
      ┌──────────────┐     ┌──────────────┐     ┌──────────────────┐
      │  CLI (main)  │     │ Web dashboard│     │ Notifiers        │
      │  scan/watch  │     │ /api/scan    │     │ Telegram/Discord │
      └──────────────┘     └──────────────┘     └──────────────────┘
```

```
Binance klines → add_all_indicators() → analyze_structure()
     → build_snapshot() → score_bullish() / score_bearish()
     → build_plans()    → build_best_signal()
     → build_intelligence() strict desk filter
     → JSON {signal, plans, intelligence, snapshot, market_context, validation}
```

---

## 🛡️ Professional operating mode (the roadmap, built-in)

CryptoBrain now ships the discipline your professional roadmap demands —
**desk-first, enforced risk, one tested edge at a time**. Everything is
configurable via `.env`; defaults are conservative.

| Decision | What it does | Control |
|---|---|---|
| **Desk-first** | Every scan ends in ONE `decision`: `TRADE BUY/SELL` or `WAIT — NO TRADE`. Desk-vetoed signals never enter the approval queue. Raw engine plans stay visible as research. | `DESK_DEFAULT=true` |
| **One primary setup family** | Only your chosen family (default: liquidity sweep + trend continuation = Sweep Reversal + OB/FVG Pullback) may become the best signal; everything else is a watch-item until proven. | `PRIMARY_SETUP_FAMILY=sweep_trend_continuation` |
| **Data-driven R:R** | TP targets come from measured per-setup expectancy (clamped 1.5–4.0R); the old fixed 2.0 gate is now a safety floor. | `TP_RR_MIN/MAX`, `INTELLIGENCE_MIN_RR=1.5` |
| **Per-asset playbooks** | BTC 4H→1H→15M · ETH gated by BTC bias + ETH/BTC slope · GOLD D1→4H→1H/15M + PDH/PDL + London/NY sessions + US-data no-entry windows. | `brain/playbooks.py` |
| **Correlation risk** | BTC+ETH = ONE crypto bucket: same-direction duplicates, full buckets and combined risk veto new trades; gold is tracked separately. | `CRYPTO_BUCKET_MAX_TRADES=2` |
| **Enforced risk limits** | Daily limit → hard block; weekly → reduced activity; drawdown ladder −5%/−8%/−10%. Enforced at the approval button AND the paper runner (`--force` is the conscious override). | `ENFORCE_RISK_LIMITS=true` |
| **Behavioral no-trade gate** | Angry / tired / revenge / chasing flags close the gate until cleared. | `python main.py tradestate --angry --note "..."` |
| **Setup-proven gate** | A setup is PROVEN only after ≥100 backtest + ≥20 decided paper samples with positive expectancy; unproven = research-only at student/researcher levels. | `CALIBRATE_MIN_N=100` |
| **Regime-tagged learning** | Every backtest/paper trade records its market regime; calibration works per `setup × regime`, stats report per regime. | `python main.py stats` |
| **Business scorecard** | Win rate, avg win/loss, expectancy, profit factor, max drawdown, streaks, rolling 50/100 windows, execution-violation rate. | `python main.py stats` |
| **Professional journal** | Post-trade fields with "did I follow my system?" as the headline; MAE/MFE recorded per paper trade. | `python main.py journal <scan_id> --followed-rules 1 ...` |

New CLI:

```bash
python main.py risk          # gate status: daily/weekly/drawdown/progression
python main.py tradestate --angry --note "tilted"   # close the gate
python main.py tradestate --clear                   # open it again
python main.py journal <scan_id> --followed-rules 1 --emotion calm --mistake none
python main.py journal        # discipline summary (violation rate)
python main.py sourcetrust discord_group 5 0.3 --note "private signals: context only"
python main.py health         # system health: data feeds, DB, risk gate, MCP, LLM
python main.py brief          # daily desk briefing (alias for `agent morning`)
python main.py agent morning  # desk morning briefing across BTC/ETH/GOLD
python main.py agent ask "is the risk gate open?"   # natural-language question
python main.py agent ask "am i ready for micro?"    # the graduation gate
python main.py agent all      # one desk run: health + briefing + graduation
python main.py simulator      # grind unique 100-backtest / 20-paper samples per setup
python main.py mcp            # MCP server for Claude Desktop / Cursor (stdio)
```

Progression levels (`PROGRESSION=student|researcher|simulator|micro|consistent|scale`)
map to your Phase-18 ladder and change risk caps + whether unproven setups
can be approved. Start at `simulator` (BLUEPRINT Step 1) and paper-trade the
100–200 sample dataset that proves your setups; promote to `micro` only when
the **graduation gate** passes (see `BLUEPRINT.md`).

#### Desk agent (`agent`, `health`)

* `python main.py agent morning` — one briefing for the whole watchlist: every
  asset with its final **desk decision** (desk-first), playbook + regime,
  risk-gate state, pending reviews, open exposure and paper-book stats, plus a
  plain-English narrative. `--save` also persists each scan.
* `python main.py agent ask "..."` — intent-based answers from live engine +
  DB state: risk gate, exposure, pending queue, journal discipline,
  calibration, stats, paper book, market scan, sources, progression, and the
  **graduation gate** (`"am i ready for micro?"`) — the BLUEPRINT Step 2→3
  checklist: expectancy ≥ +0.50R, win rate > 55%, profit factor ≥ 1.5,
  rule compliance ≥ 90%, plus the 100/20 sample proof.
* `python main.py agent all` — one desk run: health report + morning
  briefing + graduation gate (`--json` for the full machine-readable blob).
* `python main.py brief` — the daily briefing (alias for `agent morning`).
* `python main.py health` — probe every data feed per symbol, DB integrity,
  risk gate, learning store, MCP + LLM availability; exits non-zero on failure.
  In live mode it also cross-checks Binance prices against **KuCoin + OKX**
  public APIs and flags deviations > 1%.
  The dashboard renders the same report (`/api/health`) and the morning
  briefing (`/api/agents`), and `/api/ask` exposes the question desk.

#### Paper-sample grind (`simulator`)

`python main.py simulator` walks history with the engine, grades every plan
forward (SL/TP touch logic on real bars), and stores **unique** samples —
re-simulating the same window never double-counts.  It produces both halves of
the setup-proven proof (decisions A6/B10):

* backtest samples (`sim_key`-deduped rows in `backtest_results`), and
* decided paper samples (created-and-closed `paper_trades` rows with
  TP_HIT/STOP_LOSS outcomes + regime).

The progress table shows each setup against the 100-backtest / 20-paper /
positive-expectancy targets and tells you when the primary setup family is
ready for `PROGRESSION=micro`.  Below it, the **graduation gate** prints the
four BLUEPRINT criteria (expectancy / win rate / profit factor / rule
compliance) with the exact numbers — `simulator --json` includes them too.
Offline it runs on the committed sample + deterministic synthetic data
(`DEMO_MODE=1`).

> Note: simulator walk-forward samples count as **calibration evidence**
> (setup-proof, graduation gate) but are excluded from the **live risk book**
> — daily/weekly loss limits, the drawdown ladder and the business scorecard
> only see real paper trades (`decided_paper_rows(exclude_sim=True)`).

#### MCP server (`mcp`)

`python main.py mcp` exposes the desk to Claude Desktop / Cursor / any MCP
client over stdio (10 tools: health, morning briefing, scan, desk report,
risk gate, ask, pending, stats, paper, learn).  Add to your MCP client config:

```json
{ "mcpServers": { "cryptobrain": {
    "command": "python", "args": ["main.py", "mcp"], "cwd": "/path/to/AI" } } }
```

---

## 🚀 Quickstart


```bash
# 1. install
pip install -r requirements.txt

# 2. ALL-IN-ONE DASHBOARD — watch everything + click to decide (no commands needed)
python main.py              # opens http://localhost:8050
#    everything runs from the dashboard: BTC/ETH/XAU quick buttons, live
#    signal, chart, MTF, styles, context, approval queue, history, coach, and one-click
#    "⚡ Learn now" / "▶ Quick backtest + learn" buttons.
#    CLI is only for automation/advanced use.

# 3. one-shot scan (live Binance data, no keys needed)
python main.py scan --symbol BTCUSDT --tf 15m --json

# 3b. professional AI trading-desk report (JSON only; capital first)
python main.py intelligence --symbol XAUUSD --tf 15m

# 4. multi-asset watchlist (BTC + ETH + XAU/GOLD)
python main.py scan --symbols BTCUSDT,ETHUSDT,XAUUSD --tf 1h

# 5. scan a single added market (aliases work)
python main.py scan --symbol ETH --tf 15m
python main.py scan --symbol XAUUSD --tf 1h    # XAU/GOLD routes to PAXGUSDT spot candles

# 6. continuous watch + notify (once you configure .env)
python main.py watch --symbol ETH --interval 120 --notify

# 7. backtest: grade every plan at +1h/+4h/+24h and store the outcomes
python main.py backtest --symbol BTCUSDT --tf 15m --bars 300 --horizons 1,4,24 --save

# 8. what the engine has learned (scans + backtest win-rates)
python main.py stats

# 9. human approval gate — signals wait for YOUR yes/no
python main.py review                # list signals awaiting approval
python main.py approve 42 --note "clean setup"
python main.py reject 42 --note "chasing entry"
python main.py signal 42             # full detail + lifecycle trail

# 10. PAPER TRADING — safely monitor approved signals; NO real orders are sent
python main.py paper                 # one safe live-market check now
python main.py paper --watch         # keep checking every 30s (Ctrl+C to stop)
python main.py paper --watch --interval 60 --symbol XAUUSD
# Immediate plans paper-fill at their stated entry after approval.
# Conditional plans wait until a live candle reaches the planned entry.
# SL / TP1 closes are recorded automatically in SQLite and feed `learn`.

# Manual lifecycle controls remain available when you need them:
python main.py execute 42            # mark an approved trade as executed
python main.py close 42              # manually close it / record your own outcome note

# 11. FULL human-trader analysis (MTF + context + styles + memory)
python main.py analyze --symbol XAUUSD --tf 1h

# 12. what the AI remembers about this market (state memory)
python main.py state --symbol ETH --tf 15m

# 13. self-improvement — recalibrate from backtests + decided paper trades
python main.py learn

# 14. coach — teaching mode
python main.py coach --symbol ETH    # explain + mentor + personal feedback
python main.py glossary FVG          # quick term lookup

# 15. run tests
python -m pytest tests/ -q
```

### Supported markets / aliases

The default watchlist now includes **BTC**, **ETH**, and **XAUUSD/GOLD**:

| Input alias | Canonical signal asset | Candle source |
|---|---|---|
| `BTC`, `BTCUSDT` | `BTCUSDT` | Binance spot/futures BTCUSDT |
| `ETH`, `ETHUSDT` | `ETHUSDT` | Binance spot/futures ETHUSDT |
| `XAUUSD`, `XAU`, `GOLD`, `GOLDUSDT`, `PAXGUSDT` | `XAUUSD` | Binance spot `PAXGUSDT` (PAX Gold proxy) |

XAUUSD/Gold has no Binance futures/funding context in this project, so the dashboard
shows its provider as `PAXGUSDT` and marks futures metrics as unavailable while
all indicator, structure, MTF, backtest, approval, and paper-trading flows keep
working with the user-facing asset name `XAUUSD`.

### AI Trading Intelligence System

`python main.py intelligence --symbol XAUUSD --tf 15m` returns **only JSON** in
the professional desk style: trend, structure, SMC, supply/demand, price action,
indicator confirmation, fundamentals/sentiment, scenarios, IF/THEN logic,
trade management, position-size estimate, and an explicit self-review.  It is
stricter than the raw signal engine and will return `"signal":"NO TRADE"` when
capital-preservation filters fail:

* confidence below `INTELLIGENCE_MIN_CONFIDENCE` (default 80%)
* RR below `INTELLIGENCE_MIN_RR` (default 1:2)
* high-impact macro/news risk, sideways/volatile market, or HTF contradiction
* insufficient market data

Set `ACCOUNT_BALANCE`, `RISK_PCT`, `MAX_DAILY_LOSS_PCT`, and
`MAX_WEEKLY_LOSS_PCT` in `.env` to include sizing/risk limits in the report.

**Offline demo** (no network): use the committed sample dataset —

```python
import pandas as pd
from engine.signal_engine import analyze_frame

df = pd.read_csv("data_samples/btcusdt_15m_sample.csv")
out = analyze_frame(df, symbol="BTCUSDT", timeframe="15m")
print(out.best_signal)
```

---

## 📦 Output format

The engine emits **exactly** the requested signal schema, plus the conditional
plans array:

```json
{
  "signal": {
    "signal_id": "BTCUSDT_20260804_0654",
    "timestamp": 1785826483752,
    "asset": "BTCUSDT",
    "action": "SELL",
    "entry": 63670.0,
    "stop_loss": 63861.01,
    "take_profit": 63287.98,
    "risk_reward": 2.5,
    "confidence": "MEDIUM",
    "timeframe": "15m",
    "reason": "price below VWAP + Buyside liquidity swept + sellside targets below + Momentum aligned (RSI<50, MACD histogram falling)"
  },
  "plans": [
    {
      "id": "reversal_sell",
      "type": "Sweep Reversal Sell",
      "action": "SELL",
      "condition": "IF buyside liquidity was swept at 64023.61 AND price shows bearish CHOCH / rejection",
      "trigger_level": 64023.61,
      "entry": 63670.0,
      "stop_loss": 63861.01,
      "take_profits": [63287.98, 62905.96],
      "risk_reward": 2.5,
      "confidence": 62,
      "confidence_label": "MEDIUM",
      "reasons": ["Bearish structure (bos_down)", "Buyside stop hunt detected"],
      "status": "active"
    }
  ],
  "snapshot": { "features": { "...60 labeled conditions..." }, "scores": { "bull": {...}, "bear": {...} } },
  "market_context": { "funding_rate_pct": null, "open_interest": null, "long_short_ratio": null },
  "validation": { "ok": true, "errors": [], "warnings": [] }
}
```

`signal.signal_type` is `SIGNAL` when a plan crosses the confidence threshold,
otherwise `MONITOR` (read the `plans` array for setups to wait for).
See `examples/example_signal.json` for a full live capture.

---

## 🔌 Connecting your private sources

### CryptoDada website (screenshots 1–5)

Copy `.env.example` → `.env` and fill:

```
CRYPTODADA_MODE=auto                 # auto | api | browser
CRYPTODADA_BASE_URL=https://your-cryptodada-site
CRYPTODADA_EMAIL=you@example.com
CRYPTODADA_PASSWORD=********
```

* `api` — probes the dashboard's hidden JSON endpoints (find them in DevTools
  → Network tab; the connector tries `/api/signals`, `/api/volume-spikes`,
  `/api/radar`, `/api/analyst`, …). Fastest.
* `browser` — Playwright login + scrape (`pip install playwright &&
  playwright install chromium`).
* `auto` — try `api`, fall back to `browser`.

Then: `python main.py sources` → the volume-spike screener rows become
**candidate signals that the engine independently cross-scores** with funding /
OI / structure before you act on them.

### Discord group (screenshots 6–8)

* **Read** analyst posts / market updates:
  ```
  DISCORD_TOKEN=your_bot_or_self_token
  DISCORD_CHANNEL_IDS=123456789,987654321
  ```
  `python main.py sources` parses "Market Update" messages into `{bias, levels,
  raw}` notes and tallies chat sentiment.
  ⚠️ Automating a **user** account can violate Discord's ToS — prefer a bot
  account added by the server admin, and review the ToS yourself.
* **Push** signals into the group (safe, recommended):
  ```
  DISCORD_ANNOUNCE_WEBHOOK=https://discord.com/api/webhooks/...
  ```
  then `python main.py watch --notify`.

---

## 🧠 LLM AI Brain narrative (optional)

```
LLM_PROVIDER=openai        # auto | openai | gemini | off
OPENAI_API_KEY=sk-...
# any OpenAI-compatible endpoint works, e.g. Groq:
# OPENAI_BASE_URL=https://api.groq.com/openai/v1
# OPENAI_MODEL=openai/gpt-oss-120b
```

`python main.py scan --symbol BTCUSDT --llm` appends a plain-English analyst
brief to the JSON. With no key, a deterministic rule-based narrative is used —
the output is never empty.

---

## 🧪 Tests

```bash
python -m pytest tests/ -q      # 90 tests, fully offline (synthetic data)
```

Covers: indicator math & no-look-ahead, structure detection (BOS/CHOCH, FVG,
sweeps), score bounds, plan generation (SL below entry for BUY etc.), full
pipeline, JSON schema validation, the backtester grader, the database, the
signal lifecycle (approval gate transitions), the conservative paper-trading
runner (entry / SL / TP detection), the calibrator, and the coach.

On every push, GitHub Actions runs this suite automatically
(`.github/workflows/ci.yml`) plus an offline smoke test on the sample data.

---

## 📊 Backtester — the learning loop

`python main.py backtest --symbol BTCUSDT --tf 15m --bars 300 --horizons 1,4,24 --save`

Walks the engine forward bar-by-bar (data up to each bar only — no look-ahead),
then grades every plan it produced at each horizon:

* **WIN / PARTIAL_WIN / FULL_WIN** — TP1 (and TP2) hit before SL
* **LOSS** — SL hit before TP1
* **OPEN** — neither level touched within the horizon
* **NOT_TRIGGERED** — the conditional plan's entry level was never reached

Output aggregates **win-rate, average R and expectancy** by plan type, by
confidence bucket and by action. Example (real BTCUSDT 15m, 300 bars):

```
by plan type:
  Buy Pullback        exec 137  win 73.0%  avgR +1.50
  FVG Retest Buy      exec  80  win 60.0%  avgR +1.19
  Breakout Buy        exec 271  win  8.5%  avgR -0.70
```

This is how the engine discovers *its own* edge map — which setups to trust
and which to filter out. `--save` stores every graded outcome in the signal
database.

---

## 🧪 Paper trading — the live-decision learning loop

The paper runner turns **your approved decisions** into a measured, live-market
sample without connecting to a trading account or sending an order anywhere.

```bash
# One pass: enroll any approved signals, then check active paper trades.
python main.py paper

# Keep it alive for unattended monitoring (recommended while testing).
python main.py paper --watch --interval 30

# Inspect combined history and paper-specific summary.
python main.py stats
```

How it works:

1. You approve a signal in the dashboard or with `python main.py approve <id>`.
2. On its next runner pass, an **Immediate** plan is paper-filled at its stated
   entry. A **waiting/conditional** plan stays `WAITING_ENTRY` until a live
   candle trades through the planned entry level.
3. The runner reads public Binance OHLCV data and moves the original lifecycle
   from `APPROVED → EXECUTED → CLOSED` when an entry and then SL/TP1 are hit.
4. It stores the simulated fill, exit, outcome, achieved R, runner note and
   candle cursor in `paper_trades`. `python main.py learn` uses decided
   `TP_HIT` / `STOP_LOSS` paper outcomes alongside historical backtests.

### Safety and accuracy rules

* **Paper only.** It has no exchange-order code, no exchange API key setting,
  and never touches a real position or wallet.
* **SL and TP1 only.** The first target closes the simulated position; TP2 is
  not silently assumed filled.
* **Conservative same-candle rule.** OHLCV cannot reveal which level came
  first. If both stop and TP are inside one candle, the runner records a
  stop-loss rather than an optimistic win.
* The browser button runs one explicit check. For continuous monitoring after a
  browser tab closes, keep `python main.py paper --watch` running in Terminal.

---

## 🗄 Signal database — the memory

Every `scan` and `watch` tick is saved by default to `data/cryptobrain.db`
(SQLite, no extra deps) — signal, plans, feature snapshot, market context.
Backtest outcomes and approved paper-trade outcomes land in the same local
store.

```bash
python main.py stats    # scans + backtest learning + paper-trade summary
```

Tables: `scans`, `plans`, `backtest_results`, `paper_trades`, `decisions`.
Use `--no-save` to skip ordinary scan writes. This store is the foundation for
confidence calibration (e.g. dampen a plan type the engine has measured as
negative-expectancy) while keeping historical backtest and live paper results
visibly separate in the dashboard.

---

## 🔄 CI

`.github/workflows/ci.yml` runs on every push / PR to `main`:
Python 3.12 → install deps → `compileall` → `pytest` → offline smoke test on
`data_samples/btcusdt_15m_sample.csv`. All tests are network-free, so CI is
fast and deterministic.

---

## 🚦 Signal lifecycle — human approval gate

Every actionable signal now flows through a **human-in-the-loop** state machine
instead of being fired straight at you:

```
CREATED ─▶ PENDING_REVIEW ─▶ APPROVED ─▶ EXECUTED ─▶ CLOSED (outcome recorded)
               │                  │           │
               └─▶ REJECTED       └─▶ SKIPPED ┘
```

* `scan` / `watch` create signals in **PENDING_REVIEW** (unless `--auto-approve`).
* You decide: `python main.py approve <id>`, `reject <id>`, or click the
  buttons in the dashboard's **Human approval queue**.
* Every transition is logged in the `decisions` table with your note, so the
  Coach can later teach from *your* decision pattern.
* The paper runner may advance an approved simulation through
  `APPROVED → EXECUTED → CLOSED` with `reviewer=paper_runner`; its exact
  fill/exit evidence lives in the linked `paper_trades` row.
* `python main.py signal <id>` shows the full lifecycle trail.

## 🧠 Human-like thinking: MTF + context + styles + state memory

**How it thinks like a trader:**

1. **Multi-timeframe** (`engine/mtf.py`) — reads Monthly → Weekly → Daily → 4H →
   1H → 30M → 15M → 5M → 1M like a trader: the higher frames set the **bias**,
   the lower frames **time the entry**. Output: HTF/LTF bias, an alignment score
   (-100..+100), and support/resistance carried down from the higher frames.
2. **Market context** (`brain/context.py`) — everything that moves BTC:
   fear & greed, BTC/ETH dominance, S&P500 / Nasdaq / DXY / gold, macro
   calendar (FOMC, CPI, NFP — flags high-impact events within 48h), the
   halving cycle phase, geopolitical headline scan, influencer/social pulse.
   Every source degrades gracefully if unreachable.
3. **Trading styles** (`brain/styles.py`) — "what the market provides, we
   take": Scalp / Day / Swing / Momentum / Position signals, each with
   direction, confidence, horizon, and a plain reason. When nothing is clean,
   it says **stand aside** and why.
4. **State memory** (`brain/state_memory.py`) — the anti-spam brain. It
   fingerprints the market state; if nothing changed, the signal is
   **reaffirmed** (dashboard shows "stable since …", `reaffirms` counter), not
   re-emitted. New signals fire only on a real state change (HTF flip,
   structure event, style change), with per-style cooldowns (Scalp 15m …
   Position 24h) and a **whipsaw guard** that suppresses signals when the HTF
   flips too often. Memory persists in SQLite (`market_state` + `state_events`).

**Performance:** all external fetches run in parallel with short timeouts and
per-source degradation, so the first dashboard load is ~2-4s and refreshes are
instant (cached). A 60s timeout guard + Retry button prevents the dashboard
from ever hanging at "Loading…".

## 🧠 Self-improvement — the closed learning loop

1. **Backtest** (`backtest --save`) grades every historical plan → outcomes stored in DB.
2. **Paper runner** (`paper --watch`) closes approved simulated live decisions at SL/TP1 → outcomes stored separately in DB.
3. **Learn** (`learn`) recomputes per-setup expectancy from both decided sources → calibration profile:
   * Buy Pullback / FVG Retest Buy measured positive → **confidence boosted**
     (×1.25 in our first run)
   * Breakout Buy / Sweep Reversal Sell measured negative → **dampened**
     (×0.82 / ×0.64), and optionally filtered entirely if bad enough
4. The profile is loaded on every scan, so **future signals are already smarter**.
   With no data the profile is empty and nothing changes — calibration is
   strictly additive. Tune via `CALIBRATE_MIN_N`, `CALIBRATE_GAIN`,
   `CALIBRATE_FILTER` in `.env`.

## 🧑‍🏫 Coach — teaching you to trade better

`python main.py coach` (or the dashboard's "Explain & mentor me"):

* **Explains** the current signal in plain English — trend, RSI, VWAP, structure
  event, premium/discount, liquidity sweep — with glossary terms expanded inline.
* **Mentors** you through the top setup step-by-step: entry, stop, take-profit
  ladder, risk:reward, why each condition fired, plus the homework rule
  ("if the trigger doesn't happen, the plan is cancelled").
* **Gives personal feedback** from your own decisions:
  *"You tend to approve Breakout Buy — the engine's measured win-rate for that
  setup is 8% over 477 samples; consider filtering it."*
* Ships a **glossary** (`python main.py glossary [TERM]`) covering every term
  the engine uses — BOS, CHOCH, FVG, Order Block, Liquidity Sweep, Premium/
  Discount, VWAP, RSI divergence, Expectancy, and more.

---

## 📁 Project layout

```
crypto-brain/
├── main.py                  # CLI: scan / watch / paper / sources / backtest / stats / web
├── config.py                # env-driven configuration
├── engine/
│   ├── indicators.py        # RSI MACD EMA VWAP ADX BB Supertrend WaveTrend …
│   ├── structure.py         # swings, BOS/CHOCH, OB, FVG, liquidity, sweeps
│   ├── features.py          # labeled market snapshot (60 conditions)
│   ├── scorer.py            # weighted condition scoring → confidence
│   ├── rules.py             # IF/THEN conditional plan generator
│   ├── lifecycle.py         # signal state machine + human approval gate
│   ├── calibration_hook.py  # applies the self-improvement profile to plans
│   └── signal_engine.py     # orchestrator → final JSON
├── brain/
│   ├── coach.py             # teaching layer: explain / mentor / feedback / glossary
│   ├── calibrator.py        # self-improvement: expectancy → calibration profile
│   ├── context.py           # fear&greed, dominance, equities, macro, cycle, social, geopolitics
│   ├── styles.py            # trading-style classification (Scalp/Day/Swing/Momentum/Position)
│   ├── state_memory.py      # market-state memory + signal stability (anti-spam, whipsaw guard)
│   └── full_pipeline.py     # combines MTF + context + engine + styles + memory
├── engine/
│   ├── indicators.py        # RSI MACD EMA VWAP ADX BB Supertrend WaveTrend …
│   ├── structure.py         # swings, BOS/CHOCH, OB, FVG, liquidity, sweeps
│   ├── mtf.py               # multi-timeframe analysis (HTF bias → LTF execution)
│   ├── features.py          # labeled market snapshot (60 conditions)
│   ├── scorer.py            # weighted condition scoring → confidence
│   ├── rules.py             # IF/THEN conditional plan generator
│   ├── lifecycle.py         # signal state machine + human approval gate
│   ├── calibration_hook.py  # applies the self-improvement profile to plans
│   └── signal_engine.py     # orchestrator → final JSON
├── data/
│   ├── binance_client.py    # geo-aware Binance market data
│   ├── database.py          # SQLite store (scans/plans/decisions/backtests/paper trades)
│   ├── backtester.py        # walk-forward plan grader (+1h/+4h/+24h)
│   ├── paper_trading.py     # approved-signal live-market paper runner (SL / TP1)
│   └── sources/
│       ├── cryptodada_website.py  # private-site connector (api/browser)
│       ├── discord_reader.py      # Discord reader + webhook push
│       └── news.py                # RSS headlines + sentiment
├── ai/llm_brain.py          # optional LLM narrative (OpenAI/Gemini/offline)
├── output/
│   ├── signal_schema.py     # JSON validation
│   └── notifiers.py         # Telegram + Discord push
├── web/app.py               # Flask dashboard (+ approval queue + coach)
├── tests/                   # offline test-suite (52 tests)
├── .github/workflows/ci.yml # auto test-runner on push
├── examples/example_signal.json
└── data_samples/btcusdt_15m_sample.csv
```

---

## 👤 Ownership

| | |
|---|---|
| **Owner** | [Cloudslover](https://github.com/Cloudslover) |
| **Canonical repo** | https://github.com/Cloudslover/AI |
| **Companion dashboard** | https://github.com/Cloudslover/CryptoDashboard |
| **Upstream reference** | https://github.com/cloudshome/AI (read-only history source) |

## 📤 Publishing to GitHub

```bash
# Create an empty public repo named "AI" under Cloudslover, then:
cd AI
git remote add origin https://github.com/Cloudslover/AI.git   # already set in this workspace
git push -u origin main
```

This workspace is pre-wired:

* `origin`   → `https://github.com/Cloudslover/AI.git` (push target)
* `upstream` → `https://github.com/cloudshome/AI.git` (pull latest reference code)

---

## ⚠️ Disclaimer

This software is for **research and education**. Outputs are risk-advice only,
not financial advice. Crypto derivatives are high-risk; always use stop-losses
and never risk money you cannot afford to lose. Always respect the ToS and rate
limits of any service you connect to (Binance, Discord, CryptoDada, LLM APIs).
