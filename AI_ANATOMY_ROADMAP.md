# 🧠 AI Trading Brain Anatomy Roadmap & Status Tracker

```
Progress: [████████████████████████████████████████] 100% (Phases P1–P5 Complete)
Tests:    171 / 171 Passing (100% Pass Rate)
Status:   Production-Ready Core Desk & Autonomous Architecture
```

---

## 🏛️ System Architecture Overview

```
                          ┌──────────────────────────────────────────────┐
                          │            User & External Clients          │
                          │   CLI / Dashboard / MCP Client (Claude, etc) │
                          └──────────────────────┬───────────────────────┘
                                                 │
                                     ┌───────────┴───────────┐
                                     │   JSON-RPC MCP Server │
                                     │      mcp_server.py    │
                                     └───────────┬───────────┘
                                                 │
  ┌──────────────────────────────────────────────┼──────────────────────────────────────────────┐
  │                                              │                                              │
┌─▼──────────────────────────┐     ┌─────────────▼────────────┐     ┌──────────────────────────▼─┐
│     P1: RAG Library        │     │    P2: LLM Reasoning     │     │     P3: Autonomous Hands   │
│  brain/library.py          │     │    brain/brief.py        │     │     brain/agents.py        │
│  brain/ask.py              │     │    ai/llm_brain.py       │     │     agent_runs (DB)        │
│  Grounded Q&A + Citations  │     │    Morning Brief/Review  │     │     Brief / Watchdog / etc │
└─────────────┬──────────────┘     └─────────────┬────────────┘     └─────────────┬──────────────┘
              │                                  │                                │
              └──────────────────────────────────┼────────────────────────────────┘
                                                 │
                                   ┌─────────────▼────────────┐
                                   │   P5: Immune System      │
                                   │   brain/immune.py        │
                                   │   Staleness/Risk Alarms  │
                                   └─────────────┬────────────┘
                                                 │
                                   ┌─────────────▼────────────┐
                                   │   Risk Gate & Playbooks  │
                                   │   SQLite Learning Store  │
                                   └──────────────────────────┘
```

---

## 📊 Phase Matrix & Live Verification

| Phase | Layer | Implemented Modules | Capabilities & Proof | Status |
|---|---|---|---|:---:|
| **P1** | **RAG — Library** | `brain/library.py`<br>`brain/ask.py` | Grounded retrieval with strict source citations:<br>`ask "which setups have positive expectancy in ranging markets?"`<br>↳ *"Buy Pullback: bt n=120 exp=+0.88R (win 75%) — cited: backtest_results"* | ✅ Complete |
| **P2** | **LLM — Reasoning** | `brain/brief.py`<br>`ai/llm_brain.py` | Pre-market briefing + post-mortem reasoning:<br>`postreview <scan_id>`<br>↳ *"TP_HIT · 1.5R · MAE 30.0 · MFE 1200.0 · Followed rules: YES"* | ✅ Complete |
| **P3** | **Agents — Hands** | `brain/agents.py`<br>`data/database.py` (`agent_runs`) | Autonomous desk agents:<br>• `MorningBriefAgent`: BTC/ETH/GOLD posture + session windows<br>• `WatchdogAgent`: Paper trade monitoring & stale cleanup<br>• `PaperReviewerAgent`: Post-mortem aggregation<br>• `WeeklyReviewAgent`: Metrics & change detection | ✅ Complete |
| **P4** | **MCP — Nervous System** | `mcp_server.py` | Zero-dependency stdio JSON-RPC MCP server:<br>`initialize` ↔ `tools/list` ↔ `tools/call ask`<br>Enforced security: Read-only & analysis tools only (no order placement or approval bypass). | ✅ Complete |
| **P5** | **Immune System** | `brain/immune.py` | Real-time diagnostic alarms & staleness detection:<br>`health`<br>↳ Catches stale candle data (>2 days old), DB corruption, risk limit breaches, behavioral blocks (angry/tired/revenge/chasing). | ✅ Complete |

---

## 💻 Complete CLI Command Reference

### RAG & Intelligence
```bash
# Ask questions grounded in backtests, playbooks, risk rules, and SMC concepts:
python main.py ask "which setups have positive expectancy in ranging markets?"
python main.py ask "what is the ETH playbook rule?"
python main.py ask "what are the daily and weekly loss limits?"
```

### Briefing & Post-Trade Reviews
```bash
# Generate cross-asset morning brief (BTC, ETH, GOLD):
python main.py brief

# Run post-trade review on a closed trade:
python main.py postreview <scan_id>
```

### Autonomous Desk Agents
```bash
# Run all agents or an individual agent:
python main.py agent all
python main.py agent morning
python main.py agent watchdog
python main.py agent paper-reviewer
python main.py agent weekly-review
```

### Immune Diagnostics & Health
```bash
# Run full system diagnostic check:
python main.py health
python main.py health --json
```

### Model Context Protocol (MCP) Server
```bash
# Start stdio MCP JSON-RPC server (connect via Claude Desktop or MCP Client):
python mcp_server.py
```

---

## 🔄 Dual-Repository Synchronization & Ownership

Both repositories belong to the same project ecosystem:

1. **Canonical Development Hub**: [`https://github.com/Cloudslover/AI`](https://github.com/Cloudslover/AI)
   - Contains merged production branch (`main`) with PR #1 (`2e1c01f`).
2. **Arena Execution Hub**: [`https://github.com/cloudshome/AI`](https://github.com/cloudshome/AI)
   - Working branch: `arena/019fe9fa-ai`.

### Synchronization Strategy
- The current working branch `arena/019fe9fa-ai` integrates all commits from `Cloudslover/AI` (`2e1c01f`) and builds the complete AI Anatomy layer (P1–P5) on top.
- Pushes to `origin arena/019fe9fa-ai` keep `cloudshome/AI` up to date.
- PRs can be merged across both remotes cleanly without merge conflicts.

---

## 🚀 HOW WE CONTINUE — Durable Handoff Protocol

When continuing in a new session or machine:

1. **5-Minute Orientation Recipe**:
   ```bash
   # 1. Recreate virtual environment (.venv is excluded from persistent snapshots)
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt

   # 2. Verify all tests pass
   .venv/bin/pytest

   # 3. Check immune system
   .venv/bin/python main.py health
   ```

2. **Remaining Progression Ladder**:
   - **Step 1: Live Market Feed**: Connect Binance live API keys or run against live BTC/ETH/GOLD WebSocket/REST feed.
   - **Step 2: Collect 100–200 Paper Samples**: Run `python main.py paper --watch` in `PROGRESSION=simulator` mode to accumulate statistical sample size.
   - **Step 3: Self-Calibration**: Run `python main.py learn` to update proven setups from real paper-trade outcomes.
   - **Step 4: Transition to Micro**: Set `PROGRESSION=micro` in `.env` once setup expectancy is mathematically proven.
   - **Step 5: Dashboard Expansion**: Embed RAG chat panel and Agent activity log into `web/app.py`.

3. **Core Operating Rules**:
   - 🛡️ **Never bypass the human approval gate**: Machine proposes, Human approves, Paper-runner executes.
   - 📚 **Grounded answers only**: Every query response must carry explicit citations (`[cited: ...]`).
   - 🧪 **Test-driven integrity**: Keep all 171+ tests passing across all changes.
