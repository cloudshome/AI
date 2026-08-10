"""brain/agent.py — the desk agent: health, morning briefing, natural-language ask.

This is the "agent layer" the professional trading mode exposes everywhere:

  * `python main.py agent morning`   — one briefing across the whole watchlist
                                     (desk decisions, risk gate, exposure, queue)
  * `python main.py agent health`    — system health (data feeds, DB, risk gate,
                                     learning store, MCP, LLM)
  * `python main.py agent ask "…"`   — natural-language question to the desk
  * MCP tools and dashboard panels   — /api/health, /api/agents, /api/ask render
                                     exactly the same data

Everything is best-effort: a blocked feed degrades to a note instead of an
error, so a morning briefing never fails because one context source is down.
"""
from __future__ import annotations

import json
import os
import platform
import time
from datetime import datetime, timezone

from config import (VERSION, SYMBOLS, TIMEFRAME, BARS, LLM_PROVIDER, DB_PATH,
                    PROGRESSION, PROGRESSION_LEVELS, CALIBRATE_MIN_N,
                    CALIBRATE_MIN_PAPER_N)
from data.symbols import parse_symbol_list
from data.sample_client import maybe_client


# ── health ────────────────────────────────────────────────────────────────

def _data_mode() -> dict:
    if os.getenv("DEMO_MODE", "0") in ("1", "true", "yes"):
        return {"mode": "demo", "provider": "sample client (synthetic/committed data)",
                "live": False,
                "note": "DEMO_MODE=1 — no live market data; set DEMO_MODE=0 for Binance"}
    return {"mode": "live", "provider": "binance", "live": True,
            "note": "live Binance market data"}


def _probe_data(client) -> dict:
    """Best-effort per-symbol data feed probe (one klines call each)."""
    out: dict[str, dict] = {}
    for symbol in SYMBOLS:
        try:
            df = client.klines(symbol, "15m", 60)
            ok = df is not None and len(df) >= 10
            out[symbol] = {"ok": bool(ok), "bars": 0 if df is None else len(df),
                           "last": None if df is None or not len(df) else
                           float(df["close"].iloc[-1])}
            if not ok:
                out[symbol]["error"] = "too few bars returned"
        except Exception as exc:
            out[symbol] = {"ok": False, "bars": 0, "error": f"{type(exc).__name__}: {exc}"}
    return out


# Public 15m-candle endpoints for the multi-exchange price cross-check
# (BLUEPRINT: "multi-exchange price verification"). No keys required.
_CROSS_EXCHANGES: dict[str, dict] = {
    "kucoin": {
        "url": "https://api.kucoin.com/api/v1/market/candles?type=15min&symbol={sym}",
        "symbols": {"BTCUSDT": "BTC-USDT", "ETHUSDT": "ETH-USDT",
                    "XAUUSD": "PAXG-USDT"},
        "close_idx": 2,  # [time, open, close, high, low, volume, turnover]
    },
    "okx": {
        "url": "https://www.okx.com/api/v5/market/candles?instId={sym}&bar=15m&limit=1",
        "symbols": {"BTCUSDT": "BTC-USDT", "ETHUSDT": "ETH-USDT",
                    "XAUUSD": "PAXG-USDT"},
        "close_idx": 4,  # [ts, o, h, l, c, vol, ...]
    },
}


def _cross_exchange_probe(binance_last: dict) -> dict:
    """Best-effort price cross-check: Binance vs KuCoin + OKX (public APIs).

    Flags deviations > 1%. Every exchange call is wrapped — an unreachable
    exchange degrades to a note instead of failing the health report.
    """
    import requests
    out = {"ok": True, "note": "multi-exchange cross-check (KuCoin + OKX public)",
           "threshold_pct": 1.0, "exchanges": {}}
    for name, cfg in _CROSS_EXCHANGES.items():
        ex: dict = {"ok": False, "symbols": {}}
        for our, theirs in cfg["symbols"].items():
            last = binance_last.get(our)
            if last is None:
                continue
            try:
                resp = requests.get(cfg["url"].format(sym=theirs), timeout=6)
                candles = (resp.json() or {}).get("data") or []
                if not candles:
                    ex["symbols"][our] = {"error": "no candles in response"}
                    continue
                price = float(candles[-1][cfg["close_idx"]])
                dev = round((price / last - 1.0) * 100.0, 3)
                ex["symbols"][our] = {"price": round(price, 4),
                                      "deviation_pct": dev,
                                      "flag": abs(dev) > 1.0}
                ex["ok"] = True
            except Exception as exc:
                ex["symbols"][our] = {"error": f"{type(exc).__name__}: {exc}"}
        if not ex["symbols"]:
            ex["note"] = "no symbols compared (no live Binance prices)"
        out["exchanges"][name] = ex
        if not ex["ok"]:
            out["ok"] = False
    return out


def health_report() -> dict:
    """One JSON-able health snapshot used by `agent health`, /api/health, MCP."""
    report: dict = {
        "ok": False,
        "version": VERSION,
        "python": platform.python_version(),
        "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "data": _data_mode(),
    }
    try:
        from data.database import SignalDB
        with SignalDB() as db:
            counts = db.conn.execute(
                "SELECT (SELECT COUNT(*) FROM scans) scans,"
                " (SELECT COUNT(*) FROM backtest_results) backtests,"
                " (SELECT COUNT(*) FROM paper_trades) paper,"
                " (SELECT COUNT(*) FROM calibration) calibration_entries"
            ).fetchone()
            report["database"] = {
                "ok": True, "path": str(db.path),
                "scans": counts["scans"], "backtest_samples": counts["backtests"],
                "paper_samples": counts["paper"],
                "calibration_entries": counts["calibration_entries"],
            }
    except Exception as exc:
        report["database"] = {"ok": False, "path": str(DB_PATH),
                              "error": f"{type(exc).__name__}: {exc}"}

    try:
        from brain.risk_gate import evaluate as gate_evaluate
        from brain.risk_gate import progression as progression_info
        with SignalDB() as db:
            gate = gate_evaluate(db)
            report["risk_gate"] = {
                "allowed": gate["allowed"],
                "blocked_by": gate.get("blocked_by", []),
                "progression": progression_info(),
                "details": {k: gate.get("details", {}).get(k) for k in
                            ("daily_weekly", "drawdown", "trader_state",
                             "effective_risk", "enforced")},
            }
    except Exception as exc:
        report["risk_gate"] = {"allowed": False, "blocked_by": [f"unavailable: {exc}"]}

    try:
        from data.database import SignalDB
        with SignalDB() as db:
            report["pending_reviews"] = len(db.pending_reviews())
            calib = db.load_calibration()
        proven = [k for k, v in calib.items() if v.get("proven")]
        report["learning"] = {
            "calibration_entries": len(calib),
            "proven_setups": proven,
        }
    except Exception:
        report["pending_reviews"] = 0
        report["learning"] = {"calibration_entries": 0, "proven_setups": []}

    try:
        client = maybe_client()
        report["data"]["probe"] = _probe_data(client)
        report["data"]["ok"] = any(v.get("ok") for v in report["data"]["probe"].values())
    except Exception as exc:
        report["data"]["probe"] = {}
        report["data"]["ok"] = False
        report["data"]["error"] = f"{type(exc).__name__}: {exc}"

    # Multi-exchange verification only makes sense against real Binance prices.
    if report["data"].get("mode") == "live":
        report["data"]["cross_exchange"] = _cross_exchange_probe({
            sym: v.get("last") for sym, v in report["data"]["probe"].items()
            if v.get("ok")})
    else:
        report["data"]["cross_exchange"] = {
            "ok": False,
            "note": "skipped — demo data (no live Binance prices to compare)"}

    # MCP + LLM availability (informational; neither is required for the engine)
    try:
        import mcp  # noqa: F401
        report["mcp"] = {"available": True, "note": "run `python main.py mcp`"}
    except Exception:
        report["mcp"] = {"available": False,
                         "note": "mcp package not installed (pip install mcp)"}
    report["llm"] = {"provider": LLM_PROVIDER, "enabled": False}
    try:
        from ai.llm_brain import LLMBrain
        report["llm"]["enabled"] = LLMBrain().enabled
    except Exception:
        pass

    report["ok"] = bool(report.get("database", {}).get("ok")) and \
        bool(report.get("data", {}).get("ok"))
    return report


def format_health(report: dict) -> str:
    """Human-readable block for `python main.py agent health` / `health`."""
    lines = ["=" * 66, f"HEALTH — CryptoBrain v{report.get('version')} "
                        f"({report.get('python')})", "-" * 66]
    data = report.get("data", {})
    lines.append(f"  data mode    : {data.get('mode')} — {data.get('provider')}")
    for sym, probe in (data.get("probe") or {}).items():
        if probe.get("ok"):
            lines.append(f"    {sym:<10} ok  {probe['bars']} bars, last "
                         f"{probe.get('last'):,.2f}" if probe.get("last") is not None
                         else f"    {sym:<10} ok  {probe['bars']} bars")
        else:
            lines.append(f"    {sym:<10} ✗  {probe.get('error', 'unreachable')}")
    cross = data.get("cross_exchange") or {}
    if cross.get("exchanges"):
        lines.append(f"  cross-exch    : {cross.get('note', '')} "
                     f"({cross.get('threshold_pct')}% flag threshold)")
        for name, ex in cross.get("exchanges", {}).items():
            bits = []
            for sym, info in ex.get("symbols", {}).items():
                if "deviation_pct" in info:
                    flag = " ⚠" if info.get("flag") else ""
                    bits.append(f"{sym} {info['deviation_pct']:+.2f}%{flag}")
                else:
                    bits.append(f"{sym} {info.get('error', 'unreachable')}")
            lines.append(f"    {name:<10} {'ok  ' if ex.get('ok') else '✗   '}"
                         + " · ".join(bits) if bits else f"    {name:<10} ✗  no data")
    elif cross.get("note"):
        lines.append(f"  cross-exch    : {cross.get('note')}")
    db_ = report.get("database", {})
    if db_.get("ok"):
        lines.append(f"  database     : ok  {db_['path']}")
        lines.append(f"    scans {db_['scans']} · backtest samples {db_['backtest_samples']}"
                     f" · paper samples {db_['paper_samples']}"
                     f" · calibration entries {db_['calibration_entries']}")
    else:
        lines.append(f"  database     : ✗  {db_.get('error', 'unreachable')}")
    gate = report.get("risk_gate", {})
    prog = gate.get("progression", {})
    lines.append(f"  risk gate    : {'OPEN' if gate.get('allowed') else 'CLOSED'}  "
                 f"progression {prog.get('level', '?')} — {prog.get('label', '')}")
    for b in gate.get("blocked_by", []):
        lines.append(f"    ✗ {b}")
    learning = report.get("learning", {})
    lines.append(f"  learning     : {learning.get('calibration_entries', 0)} "
                 f"calibration entries, {len(learning.get('proven_setups', []))} proven")
    lines.append(f"  pending      : {report.get('pending_reviews', 0)} review(s)")
    mcp = report.get("mcp", {})
    lines.append(f"  mcp          : {'ready' if mcp.get('available') else 'not installed'}"
                 f" — {mcp.get('note', '')}")
    llm = report.get("llm", {})
    lines.append(f"  llm          : provider {llm.get('provider')} "
                 f"({'enabled' if llm.get('enabled') else 'off'})")
    lines.append("-" * 66)
    lines.append(f"  VERDICT      : {'OK' if report.get('ok') else 'PROBLEMS FOUND'}")
    return "\n".join(lines)


# ── morning briefing ──────────────────────────────────────────────────────

def morning_briefing(symbols: list[str] | None = None, timeframe: str = TIMEFRAME,
                     bars: int = BARS, save: bool = False) -> dict:
    """The 9am desk run: every watchlist asset, desk decisions, gate, queue."""
    from brain.full_pipeline import analyze_full
    from data.database import SignalDB
    from brain.risk_gate import evaluate as gate_evaluate
    from brain.journal import violation_rate

    symbols = parse_symbol_list(symbols)
    assets = []
    for symbol in symbols:
        entry: dict = {"symbol": symbol, "timeframe": timeframe, "ok": True}
        try:
            payload = analyze_full(symbol, timeframe, bars, with_context=False,
                                   with_memory=True)
            sig = payload.get("signal", {})
            decision = payload.get("decision") or {}
            plans = payload.get("plans", [])
            plan_conf = [int(p.get("confidence") or 0) for p in plans]
            entry.update({
                "action": sig.get("action"),
                "confidence": sig.get("confidence"),
                "confidence_pct": max(plan_conf) if plan_conf else None,
                "n_plans": len(plans),
                "entry": sig.get("entry"),
                "stop_loss": sig.get("stop_loss"),
                "take_profit": sig.get("take_profit"),
                "risk_reward": sig.get("risk_reward"),
                "reason": sig.get("reason"),
                "signal_id": sig.get("signal_id"),
                "price": payload.get("snapshot", {}).get("features", {}).get("price"),
                "desk_action": decision.get("action", sig.get("action")),
                "blocked_by": decision.get("blocked_by", []),
                "playbook": (decision.get("gates") or {}).get("playbook", {}).get("name"),
                "regime": payload.get("snapshot", {}).get("features", {}).get("regime_name"),
                "lifecycle": payload.get("lifecycle", {}),
            })
            if save:
                from main import run_scan
                payload = run_scan(symbol, timeframe, bars, save_db=True)
                entry["scan_id"] = payload.get("scan_id")
        except Exception as exc:
            entry.update({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        assets.append(entry)

    briefing: dict = {
        "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "timeframe": timeframe,
        "assets": assets,
        "narrative": "",
    }
    try:
        with SignalDB() as db:
            gate = gate_evaluate(db)
            briefing["risk_gate"] = {
                "allowed": gate["allowed"], "blocked_by": gate.get("blocked_by", []),
                "progression": gate.get("details", {}).get("progression", {}),
            }
            briefing["pending_reviews"] = len(db.pending_reviews())
            briefing["open_exposure"] = db.open_exposure()
            briefing["paper_stats"] = db.paper_trade_stats()["overall"]
            try:
                briefing["journal"] = violation_rate(db)
            except Exception:
                briefing["journal"] = {"n": 0, "violations": 0}
            calib = db.load_calibration()
            briefing["learning"] = {
                "entries": len(calib),
                "proven": sorted(k for k, v in calib.items() if v.get("proven")),
            }
    except Exception as exc:
        briefing["risk_gate"] = {"allowed": False,
                                 "blocked_by": [f"gate unavailable: {exc}"]}

    briefing["narrative"] = _briefing_narrative(briefing)
    return briefing


def _briefing_narrative(b: dict) -> str:
    """Rule-based plain-English morning summary (always available offline)."""
    lines = []
    for a in b.get("assets", []):
        if not a.get("ok"):
            lines.append(f"• {a['symbol']}: data feed unavailable — {a.get('error', '')}")
            continue
        pct = a.get("confidence_pct")
        conf = f"{pct}%" if pct else f"{a.get('confidence') or '—'}"
        side = a.get("desk_action", a.get("action"))
        if side in ("BUY", "SELL"):
            rr = a.get("risk_reward")
            lines.append(f"• {a['symbol']} {a['timeframe']}: {side} {a.get('entry'):,.2f} "
                         f"(conf {conf}, R:R {rr}) — {a.get('reason', '')}")
            if a.get("blocked_by"):
                lines.append(f"    desk vetoed: {'; '.join(a['blocked_by'])}")
        else:
            lines.append(f"• {a['symbol']} {a['timeframe']}: NO TRADE "
                         f"(conf {conf}) — {a.get('reason', '')}")
    gate = b.get("risk_gate") or {}
    prog = gate.get("progression") or {}
    lines.append(f"• Risk gate {'OPEN' if gate.get('allowed') else 'CLOSED'} at "
                 f"progression '{prog.get('level', '?')}'"
                 + (f" — {', '.join(gate['blocked_by'])}" if gate.get("blocked_by") else ""))
    lines.append(f"• {b.get('pending_reviews', 0)} signal(s) awaiting human review.")
    paper = b.get("paper_stats") or {}
    if paper.get("n"):
        lines.append(f"• Paper book: {paper['n']} trades, {paper['wins']}W/{paper['losses']}L, "
                     f"avg {paper.get('avg_rr', 0):+.2f}R.")
    proven = (b.get("learning") or {}).get("proven", [])
    if proven:
        lines.append(f"• Proven setups: {', '.join(proven)}.")
    else:
        lines.append("• No setup proven yet — grind samples with "
                     "`python main.py simulator` (>=100 backtest, >=20 paper per setup).")
    return "\n".join(lines)


def format_briefing(b: dict) -> str:
    lines = ["=" * 66, f"MORNING BRIEFING — {b.get('time')}  ({b.get('timeframe')})",
             "-" * 66]
    for a in b.get("assets", []):
        if not a.get("ok"):
            lines.append(f"  {a['symbol']:<10} ✗ {a.get('error', 'unreachable')}")
            continue
        side = a.get("desk_action", a.get("action"))
        mark = "✓" if side in ("BUY", "SELL") else "·"
        entry = a.get("entry")
        entry_s = f"@{entry:,.2f}" if entry else ""
        pct = a.get("confidence_pct")
        conf = f"{pct}%" if pct else f"{a.get('confidence') or '—'}"
        lines.append(f"  {mark} {a['symbol']:<10} {side:<8} conf={conf} "
                     f"{entry_s}  SL {a.get('stop_loss')}  TP {a.get('take_profit')}")
        if a.get("blocked_by"):
            for reason in a["blocked_by"]:
                lines.append(f"      ✗ {reason}")
        elif a.get("playbook"):
            lines.append(f"      playbook: {a['playbook']} · regime: {a.get('regime') or '—'}")
    gate = b.get("risk_gate") or {}
    prog = gate.get("progression") or {}
    gate_state = "OPEN — new trades allowed" if gate.get("allowed") \
        else "CLOSED — no new trades"
    lines.append("-" * 66)
    lines.append(f"  RISK GATE: {gate_state}  progression={prog.get('level', '?')}")
    for reason in gate.get("blocked_by", []):
        lines.append(f"      ✗ {reason}")
    lines.append(f"  pending human review: {b.get('pending_reviews', 0)}")
    lines.append(f"  open exposure: {len(b.get('open_exposure', []))} paper trade(s)")
    paper = b.get("paper_stats") or {}
    if paper.get("n"):
        lines.append(f"  paper book: {paper['n']} tracked · {paper['wins']}W/{paper['losses']}L "
                     f"· avg {paper.get('avg_rr', 0):+.2f}R")
    learning = b.get("learning") or {}
    lines.append(f"  learning: {learning.get('entries', 0)} calibration entries · "
                 f"proven: {', '.join(learning.get('proven', [])) or 'none'}")
    lines.append("-" * 66)
    lines.append(b.get("narrative", ""))
    return "\n".join(lines)


# ── ask ───────────────────────────────────────────────────────────────────

_INTENT_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    # graduation must be checked first: "ready for micro" also contains
    # the generic "progression"/"micro" words.
    ("graduation", ("graduat", "am i ready", "ready for micro", "micro yet",
                    "real money", "promote me", "ready to trade real",
                    "step 3", "blueprint gate")),
    ("risk", ("risk", "gate", "limit", "drawdown", "daily", "weekly", "trader state",
              "stop trading", "allowed")),
    ("exposure", ("exposure", "portfolio", "position", "bucket", "correlation", "open trade")),
    ("pending", ("pending", "review", "approve", "queue", "waiting for")),
    ("journal", ("journal", "discipline", "rules", "followed", "violation", "emotion")),
    ("calibration", ("calibrat", "learn", "proven", "setup", "expectancy", "multiplier")),
    ("stats", ("stats", "backtest", "win rate", "learning", "scorecard")),
    ("paper", ("paper", "simulation", "simulated")),
    ("market", ("scan", "market", "price", "signal", "analyze", "trade now", "entry")),
    ("health", ("health", "status", "system", "database", "db", "feed", "up")),
    ("sources", ("source", "news", "trust", "tier", "discord", "telegram")),
    ("progression", ("progression", "level", "micro", "student", "simulator level",
                     "scale", "consistent")),
    ("morning", ("morning", "briefing", "brief", "today")),
]


def _detect_intent(question: str) -> str:
    q = question.lower()
    for intent, words in _INTENT_KEYWORDS:
        if any(w in q for w in words):
            return intent
    return "help"


def ask(question: str, symbol: str | None = None,
        timeframe: str = TIMEFRAME, bars: int = BARS) -> dict:
    """Answer a natural-language question from live DB + engine state."""
    intent = _detect_intent(question)
    from data.database import SignalDB
    with SignalDB() as db:
        if intent == "graduation":
            from brain.journal import violation_rate
            from data.simulator import (format_graduation, graduation_status,
                                        paper_progress)
            j = violation_rate(db)
            compliance = 1.0 - j["violation_rate"] if j["violation_rate"] is not None \
                else None
            g = graduation_status(paper_progress(db), compliance=compliance)
            return {"intent": intent, "question": question,
                    "answer": format_graduation(g).splitlines(),
                    "data": {"graduation": g, "journal": j}}
        if intent == "risk":
            from brain.risk_gate import evaluate as gate_evaluate
            from brain.risk_gate import status_text
            gate = gate_evaluate(db)
            return {"intent": intent, "question": question,
                    "answer": status_text(db).splitlines(),
                    "data": {"allowed": gate["allowed"],
                             "blocked_by": gate.get("blocked_by", []),
                             "progression": gate.get("details", {}).get("progression", {})}}
        if intent == "exposure":
            from brain.portfolio import bucket_state
            trades = db.open_exposure()
            lines = [f"Open exposure: {len(trades)} paper trade(s)"]
            buckets = {}
            for t in trades:
                bucket = "gold" if t.get("symbol") == "XAUUSD" else "crypto"
                buckets.setdefault(bucket, []).append(t)
            for bucket, rows in buckets.items():
                state = bucket_state(db, rows[0]["symbol"] or "BTCUSDT")
                lines.append(f"  {bucket} bucket: {state.n_open}/{state.max_trades} "
                             f"trades, directions {state.directions or ['flat']}")
                for t in rows:
                    lines.append(f"    {t.get('symbol')} {t.get('action')} — "
                                 f"{t.get('plan_type') or 'signal'} "
                                 f"({t.get('status', '')})")
            if not trades:
                lines.append("  The book is flat — no correlated risk.")
            return {"intent": intent, "question": question, "answer": lines,
                    "data": {"trades": trades,
                             "buckets": {k: {"n": len(v)} for k, v in buckets.items()}}}
        if intent == "pending":
            rows = db.pending_reviews()
            lines = [f"Pending human review: {len(rows)}"]
            for r in rows:
                lines.append(f"  #{r['id']} {r['symbol']} {r['action']} conf="
                             f"{r['confidence_label']} entry={r['entry']} — "
                             f"{r['reason'][:70]}")
            if not rows:
                lines.append("  Queue is empty.")
            return {"intent": intent, "question": question, "answer": lines,
                    "data": {"pending": rows}}
        if intent == "journal":
            from brain.journal import violation_rate
            j = violation_rate(db)
            rate = j["violation_rate"]
            if rate is None:
                lines = ["Journal discipline: no trades recorded yet.",
                         "  `python main.py journal <scan_id> --followed-rules 1 ...`"]
            else:
                lines = [f"Journal discipline: {j['violations']}/{j['n']} trades "
                         f"({rate * 100:.1f}%) violated the system",
                         "  The goal is 0% — a loss with rules followed is an excellent trade."]
            return {"intent": intent, "question": question, "answer": lines,
                    "data": j}
        if intent == "calibration":
            from brain.calibrator import describe
            profile = db.load_calibration()
            lines = describe(profile).splitlines() if profile else [
                "No calibration profile yet — the engine is neutral (all multipliers 1.0).",
                "Grind samples: `python main.py simulator` (>=100 backtest, >=20 paper)."]
            return {"intent": intent, "question": question, "answer": lines,
                    "data": {"calibration": profile,
                             "targets": {"backtest": CALIBRATE_MIN_N,
                                         "paper": CALIBRATE_MIN_PAPER_N}}}
        if intent == "stats":
            from brain.metrics import business_metrics
            bt = db.backtest_stats()
            overall = bt["overall"]
            bt_wr = f"{overall['win_rate'] * 100:.1f}%" if overall["win_rate"] is not None \
                else "n/a"
            lines = [f"Backtest learning: {overall['n']} graded | win-rate {bt_wr} "
                     f"| avgR {overall['avg_rr']}"]
            for r in bt["by_type"][:6]:
                wr = f"{r['win_rate'] * 100:.1f}%" if r["win_rate"] is not None else "n/a"
                lines.append(f"  {r['plan_type']:<24} n={r['n']:>4} win {wr} "
                             f"avgR {r['avg_rr']}")
            try:
                m = business_metrics(db)
                lines.append(f"Business scorecard: PF {m.get('profit_factor', 0):.2f}, "
                             f"maxDD {m.get('max_drawdown_pct', 0):.2f}%")
            except Exception:
                pass
            return {"intent": intent, "question": question, "answer": lines,
                    "data": {"backtest": bt}}
        if intent == "paper":
            p = db.paper_trade_stats()["overall"]
            lines = [f"Paper trading: {p['n']} tracked | waiting {p.get('waiting', 0)} | "
                     f"open {p.get('open', 0)} | closed {p.get('closed', 0)} | "
                     f"wins/losses {p['wins']}/{p['losses']} | avg "
                     f"{p['avg_rr']:+.2f}R"]
            if not p["n"]:
                lines.append("  Approve a signal, then run `python main.py paper --watch`.")
            return {"intent": intent, "question": question, "answer": lines,
                    "data": {"paper": p}}
        if intent == "market":
            from main import run_scan
            sym = symbol or SYMBOLS[0]
            payload = run_scan(sym, timeframe, bars, save_db=False)
            sig = payload.get("signal", {})
            decision = payload.get("decision") or {}
            entry = sig.get("entry")
            entry_s = f"@ {entry:,.2f}" if entry else ""
            plan_conf = [int(p.get("confidence") or 0)
                         for p in payload.get("plans", [])]
            conf = f"{max(plan_conf)}%" if plan_conf else \
                str(sig.get("confidence") or "—")
            lines = [f"{sig.get('asset')} {sig.get('timeframe')}: "
                     f"{decision.get('action', sig.get('action'))} "
                     f"conf={conf} {entry_s}",
                     f"  reason: {sig.get('reason')}"]
            for b in decision.get("blocked_by", []):
                lines.append(f"  desk veto: {b}")
            return {"intent": intent, "question": question, "answer": lines,
                    "data": {"signal": sig, "decision": decision}}
        if intent == "health":
            report = health_report()
            return {"intent": intent, "question": question,
                    "answer": format_health(report).splitlines(), "data": report}
        if intent == "sources":
            from config import SOURCE_TIER_NAMES
            rows = db.load_source_scores()
            lines = [f"Source trust table: {len(rows)} source(s)"]
            for s in rows:
                lines.append(f"  {s['source']:<22} tier {s['tier']} "
                             f"({SOURCE_TIER_NAMES.get(s['tier'], '?')}) trust "
                             f"{s['trust']:.2f} {s.get('note') or ''}")
            if not rows:
                lines.append("  None recorded — `python main.py sourcetrust "
                             "<source> <tier 1-5> <0-1>`")
            return {"intent": intent, "question": question, "answer": lines,
                    "data": {"sources": rows}}
        if intent == "progression":
            from brain.risk_gate import progression as progression_info
            prog = progression_info()
            order = list(PROGRESSION_LEVELS)
            idx = order.index(prog["level"]) if prog["level"] in order else 0
            lines = [f"Progression: {prog['level']} — {prog['label']}",
                     f"  effective risk {prog['risk_pct']}%/trade · "
                     f"{prog['daily']}%/day · {prog['weekly']}%/week",
                     f"  approve unproven setups: {'yes' if prog['approve_unproven'] else 'no'}"]
            if idx + 1 < len(order):
                nxt = PROGRESSION_LEVELS[order[idx + 1]]
                lines.append(f"  next level: {order[idx + 1]} — {nxt['label']}")
            else:
                lines.append("  you are at the top of the ladder.")
            return {"intent": intent, "question": question, "answer": lines,
                    "data": {"progression": prog, "ladder": order}}
        if intent == "morning":
            b = morning_briefing(symbols=[symbol] if symbol else None,
                                 timeframe=timeframe)
            return {"intent": intent, "question": question,
                    "answer": format_briefing(b).splitlines(), "data": b}

    # help / unknown
    intents = ", ".join(sorted({i for i, _ in _INTENT_KEYWORDS}))
    return {"intent": "help", "question": question,
            "answer": ["I can answer questions about: " + intents,
                       "Try: 'is the risk gate open?', 'what's pending?', "
                       "'how is my journal discipline?', 'scan BTC'."],
            "data": {"intents": intents}}


def graduation_report() -> dict:
    """Graduation gate + per-setup progress, as one JSON-able dict.

    Used by `agent all`, `simulator --json` and the dashboard.
    """
    from data.database import SignalDB
    from data.simulator import graduation_status, paper_progress
    with SignalDB() as db:
        progress = paper_progress(db)
        from brain.journal import violation_rate
        j = violation_rate(db)
        compliance = 1.0 - j["violation_rate"] if j["violation_rate"] is not None \
            else None
    return {"progress": progress,
            "graduation": graduation_status(progress, compliance=compliance),
            "journal": j}


def format_answer(result: dict) -> str:
    return "\n".join(result.get("answer", []))


# ── JSON-safe helpers for MCP / dashboard ─────────────────────────────────

def json_safe(value):
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        if isinstance(value, dict):
            return {k: json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [json_safe(v) for v in value]
        return str(value)
