# CryptoBrain × Your Professional-Trader Goal — Review & Decision List

**Status:** Review only. **No code has been changed.** You confirm decisions below; coding starts only after you say so.

**Date:** 2026-08-09 · **Reviewed against:** your 18-phase professional roadmap + 15-item rulebook for BTC + ETH + GOLD.

---

## 0. How to read this document

* **Part 1** — my condensed understanding of your goal (so we confirm we're aligned).
* **Part 2** — what your repo already delivers vs. the goal (✅ map).
* **Part 3** — the real gaps and conflicts (the honest review).
* **Part 4** — **THE DECISION LIST**: Section A = **CUT** ("self-mutilation" — remove/demote things that don't serve the goal), Section B = **IMPROVE** (build/upgrade what the goal demands).
* **Part 5** — how to confirm.

Legend: each decision has options `1 / 2 / 3` with **★ = my recommendation**. To confirm, reply with your choices, e.g.:

```
A1=1, A2=2, A3=1, B1=1, B2=1, B3=2, ...
```

Anything you don't list = I leave it as you currently have it.

---

## 1. Your goal, condensed (my understanding — correct me if wrong)

> **Objective:** build yourself into a *professional* BTC + ETH + Gold trader — defined as **positive expectancy over a large sample, with controlled drawdown and preserved capital** — supported by an AI system that **assists your decisions, never replaces your judgment**, and eventually scales you through: Student → Researcher → Simulator → Micro trader → Consistent → Scale.

The core discipline you laid out:
1. **One tested edge**, not many half-learned strategies.
2. **Per-asset playbooks** (BTC: 4H→1H→15M · ETH: BTC-first + ETH/BTC · Gold: D1→4H→1H/15M + sessions + macro).
3. **BTC + ETH = one correlated crypto-risk bucket** — never two independent positions.
4. **Risk before position**: risk % → stop distance → position size → leverage. Start at 0.25–0.5% per trade, 1–1.5% daily, 2–3% weekly.
5. **No fixed 3:1 R:R, no fixed EMA rules, no fixed leverage** — those are *hypotheses to test*, not rules.
6. **100–200 recorded samples per setup before trust**; measure win-rate, expectancy, profit factor, drawdown.
7. **Journal with "did I follow my system?" as the most important field.**
8. **A "no trade" system** — including behavioral states (angry/tired/revenge).
9. **News/macro/sentiment = context, not signals**; conflicting evidence → NO TRADE.
10. **AI = research analyst, risk checker, journal analyst, setup validator** — never a "LONG BTC 🚀" generator.

---

## 2. What already exists in your repo (✅ aligned with the goal)

Your codebase (`CryptoBrain` v2.0.0) is far ahead of a typical beginner setup. Verified against the code:

| Goal requirement | Where it exists today |
|---|---|
| Edge → Risk → Execution → Review loop | ✅ Backtester (`backtest --save`) → calibration (`learn`) → paper trading (`paper --watch`) → human approval gate (`approve/reject`) → stats |
| Multi-timeframe thinking (HTF bias → LTF execution) | ✅ `engine/mtf.py` — Monthly → 1M, alignment score, HTF contradiction filter |
| Market structure before indicators | ✅ `engine/structure.py` — BOS/CHOCH, order blocks, FVG, liquidity sweeps, premium/discount |
| Regime awareness (trend/range/vol expansion/compression/fakeouts) | ✅ `engine/regime.py` — incl. swing-failure patterns & liquidity-trap detection (used by the desk filter) |
| "AI assists, you approve" | ✅ Full human-in-the-loop state machine + paper runner that never sends real orders |
| Strict NO TRADE when unsure | ✅ `intelligence` desk filter: confidence <80% → NO TRADE; RR <1:2 → NO TRADE; news window / high volatility / sideways / HTF contradiction → NO TRADE |
| Macro context (Fed, CPI, NFP, FOMC, DXY, equities, fear&greed, halving, geopolitics) | ✅ `brain/context.py` (degrades gracefully offline) |
| Position sizing from stop distance | ✅ `_position_size()` — risk $ → stop distance → units/lots (advice-level) |
| Anti-spam / no whipsaw | ✅ `brain/state_memory.py` — cooldowns, state fingerprinting, whipsaw guard |
| Teaching layer | ✅ `coach` + full glossary |
| BTC + ETH + GOLD watchlist | ✅ with XAU routed via PAXG proxy |
| Self-improvement loop (measuring what actually works) | ✅ `brain/calibrator.py` — boosts positive-expectancy setups, dampens/filters negative ones |
| LLM brief + Discord/CryptoDada connectors + notifiers | ✅ (sources are cross-scored, not auto-trusted) |

**Honest verdict:** the *infrastructure* of a professional trading business already exists. What's missing is the *professional operating discipline* — and some of it is actively contradicted by current defaults.

---

## 3. The real gaps (the review you asked for)

These are the conflicts between your stated goal and the current system. Each one becomes a decision in Part 4.

1. **Breadth vs. depth.** The engine emits **10 plan types** from **~16 indicators**, and the dashboard's main surface is the raw engine (min confidence 55). Your roadmap says: *pick ONE primary setup and master it; a strategy is a sequence of conditions, not an indicator soup.* The current system is a radar; your goal requires a sniper — eventually.
2. **One generic pipeline for three different markets.** BTC, ETH, Gold all run the same rules at the same default timeframe (15m). Your goal: **separate playbooks** (BTC 4H→1H→15M; ETH gated by BTC first + ETH/BTC; Gold D1→4H→1H/15M + London/NY sessions + PDH/PDL + US data countdown). None of the playbook-specific gates exist.
3. **No correlation / portfolio risk.** There is **zero correlation logic** in the code. The system can happily present "long BTC" and "long ETH" side by side as if they were independent — exactly what your rulebook forbids ("BTC and ETH are correlated risk").
4. **Fixed rules you said should be hypotheses.** `INTELLIGENCE_MIN_RR = 2.0` (fixed R:R gate), `DEFAULT_RISK_REWARD = 2.0` (fixed TP1), `INTELLIGENCE_MIN_CONFIDENCE = 80`, `MIN_CONFIDENCE = 55`. Your goal: R:R and thresholds should be **setup-specific, derived from measured expectancy** — with fixed values only as safety floors.
5. **Calibration trusts data too early.** `CALIBRATE_MIN_N = 20` samples before the engine "believes" a setup. Your goal: **100–200** recorded samples before confidence; and filtering until then.
6. **Risk defaults are too aggressive for where you are.** `RISK_PCT = 1.0` per trade, `MAX_DAILY_LOSS_PCT = 3.0`, `MAX_WEEKLY_LOSS_PCT = 6.0`. Your roadmap's starting numbers: **0.25–0.5% / 1–1.5% / 2–3%**. Worse: these limits are **advisory only** — nothing actually stops new trades when a limit is hit.
7. **Backtest results are not regime-tagged.** The backtester records plan type, confidence, action — but **not the market regime** at signal time, and calibration is per setup only. Your goal's strategy statement is *"when condition X, on BTC, during regime Y, my expectancy is Z."* The Y dimension doesn't exist yet.
8. **The journal is missing its most important field.** `decisions` records your note; paper trades record outcome + R. There is **no "did I follow my system?", no emotion, no mistake, no screenshot, no MAE/MFE** for paper trades. Your goal explicitly says a profitable rule-breaking trade is a *terrible* trade — the system can't currently tell the difference.
9. **No business metrics.** `stats` shows win-rate / avg R / expectancy. Missing: **profit factor, max drawdown, consecutive losses/wins, rolling 50–100 trade evaluation, execution-violation rate** (your Phase 9).
10. **No behavioral "no-trade" gate.** Your Phase 12 lists *angry / tired / revenge / chasing* as no-trade conditions. Nothing in the system knows about them.
11. **The strict desk is opt-in, not the default.** `python main.py intelligence` gives the professional NO-TRADE-first report; the dashboard's main signal view shows the looser raw engine. Your goal: **capital-first output everywhere**, with "WAIT" as a respected answer.
12. **No progression mode.** Your Level 1→7 ladder (Student → … → Scale) doesn't exist as a mode that changes risk defaults and feature exposure.

---

## 4. THE DECISION LIST

### Section A — CUT ("self-mutilation": remove / demote what doesn't serve the goal)

| # | What | Current state | Proposed change | Options |
|---|------|---------------|-----------------|---------|
| **A1** | **Strategy breadth** | 10 plan types, all competing; dashboard shows the loosest view | Pick **ONE primary setup family to master first** (suggest: *liquidity sweep + trend continuation* — already exists as "Sweep Reversal" + "Buy/Sell Pullback"). Other plan types demoted to MONITOR-only until individually proven | **1★ = soft narrow:** chosen setup is the only one eligible for tradeable confidence; others appear as watch-items only. **2 = hard narrow:** engine emits only the chosen setup + its variants. **3 = keep breadth** (radar style), accept slower mastery |
| **A2** | **Fixed 2.0 R:R as a universal gate** | `INTELLIGENCE_MIN_RR = 2.0`; `DEFAULT_RISK_REWARD = 2.0` | R:R becomes **per-setup, per-regime, data-derived** (from backtests). Keep a low universal floor (e.g., 1.5) as safety only; TP1 targets become outputs of measured expectancy, not a constant | **1★ = data-driven targets + 1.5 floor.** **2 = keep 2.0 floor, data-driven above it.** **3 = keep fixed 2.0** |
| **A3** | **Risk defaults** | 1% / trade, 3% daily, 6% weekly (advisory) | Set your Phase-13 starting numbers: **0.25–0.5% per trade, 1–1.5% daily, 2–3% weekly** — and enforce them (see B6) | **1★ = adopt roadmap numbers (0.5 / 1.5 / 3).** **2 = 0.25 / 1 / 2 (ultra-conservative).** **3 = keep current** |
| **A4** | **"Signal generator" framing** | Raw engine is the main dashboard view; strict desk is a separate CLI command | **Capital-first becomes the default**: dashboard + notifiers present the desk-style report (NO TRADE is a first-class answer). Raw plans remain visible as research, not as the headline | **1★ = desk report is the default everywhere.** **2 = keep both views, desk first.** **3 = keep current** |
| **A5** | **Trusting private sources too early** | CryptoDada rows / Discord notes become "candidate signals," cross-scored — good — but there's no formal source-trust scoring | Formalize your Tier hierarchy: sources get trust scores; private signals can **never** be auto-actionable; "conflicting evidence → NO TRADE" becomes an explicit output line | **1★ = implement Tier-1..5 hierarchy + source scoring.** **2 = keep current behavior, document it.** **3 = drop private-source connectors** |
| **A6** | **20-sample calibration trust** | `CALIBRATE_MIN_N = 20` | Raise to **100** (your 100–200 rule); below that, a setup is labeled "not yet proven" and cannot reach tradeable confidence | **1★ = 100.** **2 = 50 (interim).** **3 = keep 20** |
| **A7** | **Untagged backtests** | Outcomes recorded without regime | (Move to B3 — improvement, not cut; listed here only so you see it's acknowledged) | — |

### Section B — IMPROVE (build / upgrade what the goal demands)

| # | What | Current state | Proposed change | Options |
|---|------|---------------|-----------------|---------|
| **B1** | **Per-asset playbooks** | One generic pipeline; default TF 15m for all three | Config-driven playbooks: **BTC = 4H→1H→15M** (regime → setup location → entry confirmation) · **ETH = BTC bias first, then ETH setup + ETH/BTC slope** · **GOLD = D1→4H→1H/15M + PDH/PDL + session windows + US-data countdown** | **1★ = implement all three playbooks.** **2 = BTC+GOLD first, ETH later.** **3 = keep generic pipeline** |
| **B2** | **Correlation / portfolio risk** | No correlation logic anywhere | BTC + ETH treated as **one crypto-risk bucket**: combined exposure cap, same-direction BTC+ETH positions vetoed or halved unless proven uncorrelated, ETH long blocked against strongly bearish BTC; portfolio risk engine can veto any signal | **1★ = implement bucket + veto engine.** **2 = implement display-only (warn, don't veto).** **3 = skip** |
| **B3** | **Regime-tagged learning** | Backtests don't record regime; calibration is per setup only | Record regime at signal time (already classified in `regime.py`), tag every backtest + paper trade, calibrate by **(setup × regime × asset)**; stats reports expectancy per regime | **1★ = full regime tagging + per-regime calibration.** **2 = tagging + reporting only (no per-regime calibration yet).** **3 = skip** |
| **B4** | **Business metrics** | win-rate / avg R / expectancy only | Add **profit factor, max drawdown, consecutive losses/wins, rolling 50–100 trade evaluation, execution-violation rate** to `stats` + dashboard learning panel | **1★ = add all.** **2 = add core four (PF, DD, streaks, rolling).** **3 = skip** |
| **B5** | **Journal upgrade** | decisions: note only; paper: outcome + R only | Pre-trade checklist (regime, HTF, setup, entry/SL/TP, risk %, news) + post-trade fields (**followed rules? emotion, mistake, screenshot, what to change**) + **MAE/MFE per paper trade**; "followed my system?" becomes the headline field; execution quality feeds coach feedback | **1★ = full journal + MAE/MFE.** **2 = post-trade fields only.** **3 = keep current** |
| **B6** | **Risk enforcement** | Limits are advisory text | **Enforced kill-switch**: daily loss limit hit → approval queue blocks new signals + paper runner pauses; weekly limit → reduced activity flag; drawdown ladder (−5% reduce / −8% stop & review / −10% full review) applied automatically | **1★ = enforce daily + weekly + drawdown ladder.** **2 = enforce daily only.** **3 = keep advisory** |
| **B7** | **Behavioral no-trade gate** | Doesn't exist | A daily "trader state" check (angry / tired / revenge-recovering / chasing) — set in dashboard; any flag = approval queue blocked + coach note; logged for your Phase-9 execution stats | **1★ = implement.** **2 = implement as journal question only (no blocking).** **3 = skip** |
| **B8** | **Gold session awareness** | No session logic; macro flags exist but generic | London/NY trading-window filters, PDH/PDL as explicit levels, **US data countdown → no-new-entry windows** for gold; gold volatility regime handling distinct from crypto | **1★ = implement (fold into B1 gold playbook).** **2 = PDH/PDL + data countdown only.** **3 = skip** |
| **B9** | **Progression mode** | No levels exist | A `PROGRESSION=student|researcher|simulator|micro|consistent|scale` setting that changes risk caps, which features are visible, and whether unproven setups can reach tradeable confidence | **1★ = implement all levels.** **2 = student/micro/consistent only.** **3 = skip** |
| **B10** | **Setup-proven gate** | Calibration exists but filter is opt-in (`CALIBRATE_FILTER=false`) | A plan type is **approvable only after ≥100 backtest samples with positive expectancy AND ≥20 live paper samples**; otherwise labeled "unproven — research only" | **1★ = gate approvals on proof.** **2 = gate with warning override.** **3 = keep current** |

---

## 5. Confirming

Reply with your picks, e.g.:

```
A1=1, A2=1, A3=1, A4=1, A5=1, A6=1, B1=1, B2=1, B3=1, B4=1, B5=1, B6=1, B7=1, B8=1, B9=1, B10=1
```

(or only the ones you want changed; everything else stays as-is). You can also say **"A1=2, B5=3, rest your call"** — I'll take my ★ recommendations for the rest.

**After confirmation**, I'll turn the approved list into a build plan (ordered by dependency, each item code-tested) and only then start coding. Until then — **no code touched**, as requested.

---

## Appendix — quick sanity check against your rulebook

| Your rule | Status |
|---|---|
| 1. Protect capital | ⚠️ Limits advisory → **A3/B6** |
| 2. Trade only a tested edge | ⚠️ Calibration exists but trusts 20 samples → **A6/B10** |
| 3. Risk a predetermined amount | ✅ exists (advice) → **B6** for enforcement |
| 4. Never move a stop | ⚠️ Not tracked → **B5** (journal field) |
| 5. Never revenge trade | ❌ Not tracked → **B7** |
| 6. Don't confuse leverage with edge | ✅ sizing-from-risk exists |
| 7. BTC & ETH correlated risk | ❌ **B2** |
| 8. Gold needs its own playbook | ❌ **B1/B8** |
| 9. News = context, not signal | ⚠️ Desk filter exists; default view is looser → **A4/A5** |
| 10. Every trade recorded | ✅ SQLite store; ❌ psychology fields → **B5** |
| 11. Evaluate over hundreds of trades | ⚠️ stats exist; thresholds low → **A6/B4** |
| 12. No setup → do nothing | ✅ NO TRADE desk filter → **A4** to make it default |
| 13. Scale only after evidence | ❌ No gate → **B9/B10** |
| 14. Never risk living-expense money | ⚠️ Advisory only → **A3** (your numbers) |
| 15. Survival + positive expectancy + controlled drawdown | ⚠️ Core loop exists → needs A+B to become the *operating system* |

---

# ✅ IMPLEMENTATION LOG — 2026-08-09 (all ★ recommendations, confirmed "do the best you think")

**No code was touched before your confirmation. After it, all of the above was built, tested (145 tests passing) and demonstrated.**

## What was implemented

| Decision | Built | Where |
|---|---|---|
| **A1** strategy breadth — soft narrow | Primary family `sweep_trend_continuation` (Sweep Reversal + OB/FVG Pullback) is the only setup eligible to become the best signal; others are watch-items. Plans carry `primary: true/false`. | `engine/rules.py`, `engine/signal_engine.py`, `brain/playbooks.py` |
| **A2** data-driven R:R | Per-setup TP targets derived from measured average winner, clamped 1.5–4.0R; `INTELLIGENCE_MIN_RR` is now a 1.5 safety floor. | `engine/rules.py`, `brain/calibrator.py` |
| **A3** risk defaults | Progression-driven: student = 0.25% / 1% / 2%; env defaults now 0.5 / 1.5 / 3.0. | `config.py`, `brain/risk_gate.py` |
| **A4** desk-first default | Every scan ends in ONE `decision` (`TRADE BUY/SELL` or `WAIT`); desk-vetoed signals never enter the approval queue; CLI + dashboard + paper runner all surface it. | `brain/decision.py`, `main.py`, `web/app.py` |
| **A5** information hierarchy | Source trust table (tier 1–5 + trust 0–1) with tier labels; private sources are context-only by design. | `data/database.py`, `main.py sourcetrust` |
| **A6** calibration trust | `CALIBRATE_MIN_N=100` (was 20). | `config.py` |
| **B1** per-asset playbooks | BTC 4H→1H→15M · ETH gated by BTC bias + ETH/BTC slope · GOLD D1→4H→1H/15M. | `brain/playbooks.py`, `brain/full_pipeline.py` |
| **B2** correlation/portfolio risk | BTC+ETH = one crypto bucket: same-direction veto, bucket cap (2), combined risk cap (1%), gold separate. | `brain/portfolio.py` |
| **B3** regime-tagged learning | Every frame/backtest/paper trade records its regime; calibration keyed `setup × regime`; stats report per regime. | `engine/signal_engine.py`, `data/backtester.py`, `data/database.py`, `brain/calibrator.py` |
| **B4** business metrics | Profit factor, max drawdown, streaks, rolling 50/100, violation rate, equity curve. | `brain/metrics.py`, `main.py stats` |
| **B5** journal upgrade | Pre-trade checklist auto-derived; post-trade fields with `followed_rules` headline; MAE/MFE per paper trade; execution-quality verdicts ("excellent loss" vs "terrible win"). | `brain/journal.py`, `data/paper_trading.py`, `main.py journal` |
| **B6** enforced risk limits | Daily → hard block, weekly → reduced activity, drawdown ladder −5/−8/−10; enforced at approval + paper runner; `--force` is the conscious override. | `brain/risk_gate.py`, `main.py approve`, `web/app.py /api/review` |
| **B7** behavioral no-trade gate | angry/tired/revenge/chasing flags block the queue until cleared. | `brain/risk_gate.py`, `main.py tradestate`, dashboard |
| **B8** gold session awareness | London/NY session windows, PDH/PDL from daily bars, US-data no-entry windows. | `brain/playbooks.py` |
| **B9** progression mode | student → researcher → simulator → micro → consistent → scale; changes risk caps + unproven-setup approvals. | `config.py`, `brain/risk_gate.py` |
| **B10** setup-proven gate | Proven = ≥100 backtests + ≥20 paper samples + positive expectancy; unproven = research-only at student/researcher. | `brain/risk_gate.py`, `data/database.py setup_stats` |

## Demonstrated (this session, offline + live)

1. **Backtest on 2000 bars** → regime-tagged edge map (`Sweep Reversal Buy::VOLATILITY_COMPRESSION +0.27R` boosted ×1.07; `Breakout Buy::LIQUIDITY_TRAP_CHOP -0.64R` dampened ×0.84; TP targets derived per setup).
2. **Desk-first scan** → engine `SELL`, desk `WAIT` ("setup unproven: 90 backtest / 0 paper samples — research only at 'student' level"), signal never entered the approval queue.
3. **Full happy path** (simulator level) → scan → desk `BUY` → PENDING_REVIEW → approve → paper runner enrolled → entry → TP_HIT +2.0R → CLOSED, MAE/MFE + regime recorded → journal "Excellent trade — followed the system and won".
4. **Gate enforcement** → `approve` blocked (exit 2) at student level; trader-state flags close the gate; daily/weekly/drawdown blocks unit-tested.
5. **Portfolio veto** → long ETH blocked while long BTC open ("correlated exposure, not a second edge").
6. **Business scorecard** → 8 paper trades: 62.5% WR, +0.875R expectancy, PF 3.33, max DD 0.73%, 11% violation rate.
7. **Dashboard** (live) → new "PROFESSIONAL DESK DECISION", "RISK & DISCIPLINE GATE" (flag toggles), business scorecard + journal in Learning, journal + MAE/MFE in the signal modal. Offline mode: `DEMO_MODE=1` serves the committed sample data.

## Try it

```bash
DEMO_MODE=1 python main.py web            # dashboard with sample data (no network needed)
python main.py scan --symbol BTC --tf 15m  # desk-first output
python main.py risk                        # gate status
python main.py tradestate --angry ...      # close the gate when tilted
python main.py journal <scan_id> --followed-rules 1 --emotion calm
python main.py stats                       # business scorecard
python main.py backtest --symbol BTCUSDT --tf 15m --bars 2000 --save && python main.py learn
```

**Next step (unconfirmed, not built):** run the same loop on real BTC/ETH/GOLD data,
gather 100–200 paper samples at `simulator` level, then move `PROGRESSION=micro`.
