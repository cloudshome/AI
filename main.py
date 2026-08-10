#!/usr/bin/env python3
"""CryptoBrain — AI trading-brain signal engine.

Usage:
  python main.py scan --symbol BTCUSDT --tf 15m --json
  python main.py scan --symbols BTCUSDT,ETHUSDT,XAUUSD
  python main.py intelligence --symbol XAUUSD --tf 15m      # JSON-only desk report
  python main.py watch --symbol ETH --interval 120          # continuous loop
  python main.py web                                         # dashboard
  python main.py paper --watch                               # live-market paper monitor
  python main.py sources                                     # CryptoDada + Discord + news
  python main.py brief                                       # daily desk briefing (alias)
  python main.py agent morning                               # desk morning briefing
  python main.py agent ask "is the risk gate open?"          # natural-language question
  python main.py agent ask "am i ready for micro?"           # graduation gate
  python main.py agent all                                   # health + briefing + graduation
  python main.py health                                      # system health (+ KuCoin/OKX cross-check)
  python main.py simulator                                   # grind 100/20 sample proof
  python main.py mcp                                         # MCP server for Claude/Cursor
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (SYMBOL, SYMBOLS, TIMEFRAME, BARS, MIN_CONFIDENCE, DEFAULT_RISK_REWARD,
                    DASHBOARD_HOST, DASHBOARD_PORT, PAPER_POLL_SECONDS, VERSION)

from data.symbols import normalize_symbol, parse_symbol_list
from data.binance_client import BinanceClient
from data.sample_client import maybe_client
from engine.signal_engine import analyze_frame
from output.signal_schema import validate_output
from output.notifiers import notify_all


def _client() -> BinanceClient:
    return maybe_client()


def _sym(value: str | None) -> str:
    """Normalize BTC/ETH/XAU aliases before storing or filtering signals."""
    return normalize_symbol(value or SYMBOL)


def _symbols(value: str | None) -> list[str]:
    """Parse comma-separated assets using the configured BTC/ETH/XAU watchlist."""
    return parse_symbol_list(value, default=SYMBOLS)


def _load_calibration() -> dict:
    """Load the self-improvement profile from the DB (empty dict when unused)."""
    try:
        from data.database import SignalDB
        with SignalDB() as db:
            return db.load_calibration()
    except Exception:
        return {}


def run_scan(symbol: str, timeframe: str, bars: int, with_context: bool = True,
             with_llm: bool = False, save_db: bool = True,
             auto_approve: bool = False) -> dict:
    from brain.full_pipeline import analyze_full
    symbol = _sym(symbol)
    payload = analyze_full(symbol, timeframe, bars,
                           with_context=with_context, with_memory=True)

    if with_llm:
        from ai.llm_brain import LLMBrain
        payload["llm"] = LLMBrain().generate(payload)

    payload["validation"] = validate_output(payload)

    if save_db:
        from data.database import SignalDB
        from engine.lifecycle import reviewable
        with SignalDB() as db:
            sig = payload.get("signal", {})
            decision = payload.get("decision") or {}
            # Desk-first (decision A4): a signal the desk vetoed never enters
            # the human approval queue — it is recorded as a research scan.
            desk_ok = decision.get("action") in ("BUY", "SELL")
            status_override = None if desk_ok else "CREATED"
            existing = db.conn.execute(
                "SELECT id, status FROM scans WHERE signal_id=?",
                (sig.get("signal_id"),)).fetchone()
            if existing:
                scan_id = existing["id"]
                status = existing["status"]
            else:
                scan_id = db.save_scan(payload, status_override=status_override)
                status = "PENDING_REVIEW" if reviewable(sig) and desk_ok else "CREATED"
            payload["scan_id"] = scan_id
            if auto_approve and reviewable(sig) and desk_ok:
                db.update_status(scan_id, "APPROVED", note="auto-approve", reviewer="auto")
                payload["lifecycle"] = {"status": "APPROVED",
                                        "note": "auto-approved (--auto-approve)"}
            else:
                payload["lifecycle"] = {
                    "status": status,
                    "note": ("awaiting human approval — `python main.py review`"
                             if status == "PENDING_REVIEW" else
                             "monitor-only signal (no action required)" if status == "CREATED"
                             and not decision.get("blocked_by") else
                             "DESK BLOCKED — " + "; ".join(decision.get("blocked_by", []))
                             if status == "CREATED" and decision.get("blocked_by") else
                             f"current state: {status}"),
                }
    return payload


def cmd_scan(args) -> int:
    symbols = _symbols(args.symbols)
    all_payloads = []
    for sym in symbols:
        try:
            payload = run_scan(sym, args.tf, args.bars, with_llm=args.llm,
                               save_db=not args.no_save, auto_approve=args.auto_approve)
            all_payloads.append(payload)
            if not args.json:
                _print_human(payload)
        except ConnectionError as exc:
            print(f"[!] {sym}: {exc}", file=sys.stderr)
    if args.json:
        body = all_payloads[0] if len(all_payloads) == 1 else all_payloads
        print(json.dumps(body, indent=2, default=str))
    return 0 if all_payloads else 1


def _print_human(payload: dict) -> None:
    sig = payload["signal"]
    decision = payload.get("decision") or {}
    print("=" * 72)
    d = decision.get("action", sig.get("action"))
    if d != sig.get("action"):
        print(f"{'NO TRADE':>10}  {sig['asset']}  [{sig['timeframe']}]  "
              f"conf={sig['confidence']:<7} {sig['signal_id']}   ← DESK VETOED engine {sig['action']}")
        for b in decision.get("blocked_by", []):
            print(f"   ✗ {b}")
    else:
        print(f"{sig['action']:>10}  {sig['asset']}  [{sig['timeframe']}]  "
              f"conf={sig['confidence']:<7} {sig['signal_id']}")
    if sig.get("entry"):
        print(f"   entry {sig['entry']:>12,.2f}   SL {sig['stop_loss']:>10,.2f}   "
              f"TP {sig['take_profit']:>12,.2f}   RR {sig['risk_reward']:.2f}")
    print(f"   reason: {sig['reason']}")
    if decision:
        gates = decision.get("gates") or {}
        play = gates.get("playbook") or {}
        if play.get("name"):
            print(f"   playbook: {play['name']} — {play.get('note', '')}")
        for c in play.get("checks", []):
            mark = "✓" if c.get("ok") else ("✗" if c.get("blocking") else "·")
            print(f"     {mark} {c['detail']}")
        rg = gates.get("risk") or {}
        for b in rg.get("blocked_by", []):
            print(f"     ✗ risk: {b}")
        pv = gates.get("portfolio") or {}
        for r in pv.get("reasons", []):
            print(f"     ✗ portfolio: {r}")

    # Styles summary (what the market is offering)
    styles = payload.get("styles") or {}
    if styles:
        print("-" * 72)
        print("   STYLES:")
        for s in ("Scalp", "Day", "Swing", "Momentum", "Position"):
            v = styles.get("styles", {}).get(s, {})
            if v and v.get("available"):
                print(f"     ✓ {s:<10} {v.get('direction')} {v.get('confidence')}%  "
                      f"({v.get('horizon')})  {v.get('reason', '')[:70]}")
        if styles.get("stand_aside"):
            print(f"     · stand aside: {'; '.join(styles['stand_aside'])}")

    # Memory / stability
    mem = payload.get("memory") or {}
    if mem:
        st = mem.get("status")
        print("-" * 72)
        print(f"   STATE MEMORY: {st}" + (f"  (stable, reaffirmed ×{mem.get('reaffirms', 0)})" if st == "SAME" else ""))
        for c in mem.get("changes", [])[:4]:
            print(f"     · {c}")
        if mem.get("whipsaw"):
            print("     ⚠️ whipsaw guard active — signals suppressed")

    print("-" * 72)
    for p in payload.get("plans", []):
        print(f"   [{p['confidence']:>3}% {p['confidence_label']:<6}] {p['type']:<22} "
              f"{p['condition'][:80]}")
    scores = payload.get("snapshot", {}).get("scores", {})
    if scores:
        print(f"   scores → bull {scores.get('bull', {}).get('score', 0)}  |  "
              f"bear {scores.get('bear', {}).get('score', 0)}")

    mtf = payload.get("mtf") or {}
    if mtf:
        print("-" * 72)
        views = mtf.get("views", {})
        print("   MTF: " + "  ".join(
            f"{tf}:{v.get('trend', '?')[:1].upper()}" if v.get("available") else f"{tf}:—"
            for tf, v in views.items()))
        a = mtf.get("alignment", {})
        print(f"   HTF {mtf.get('htf_bias')} | LTF {mtf.get('ltf_bias')} | "
              f"alignment {a.get('score')} ({a.get('label')})")
        kl = mtf.get("key_levels", {})
        print(f"   support {kl.get('support', [])}  resistance {kl.get('resistance', [])}")

    ctx = payload.get("market_context", {})
    if ctx.get("data_symbol") and ctx.get("data_symbol") != sig.get("asset"):
        print(f"   data source {ctx.get('data_symbol')} ({ctx.get('provider')})")
    if ctx.get("futures"):
        print(f"   funding {ctx['funding_rate_pct']}%  OI {ctx['open_interest']:,.0f}  "
              f"L/S {ctx['long_short_ratio']:.2f}")
    else:
        print(f"   futures context unavailable — {ctx.get('note', 'not available')}")
    lc = payload.get("lifecycle")
    if lc:
        print(f"   lifecycle: {lc.get('status')} — {lc.get('note')}")
    print()


def cmd_watch(args) -> int:
    symbol = _sym(args.symbol)
    print(f"Watching {symbol} {args.tf} every {args.interval}s — Ctrl+C to stop")
    last_sig = None
    while True:
        try:
            payload = run_scan(symbol, args.tf, args.bars,
                               save_db=not args.no_save, auto_approve=args.auto_approve)
            sig = payload["signal"]
            if sig["action"] != "NO TRADE" and sig.get("signal_id") != last_sig:
                last_sig = sig.get("signal_id")
                _print_human(payload)
                if args.notify:
                    res = notify_all(sig, payload.get("plans"))
                    print(f"   notified: {res}")
            else:
                ts = time.strftime("%H:%M:%S")
                print(f"[{ts}] {sig['asset']} {sig['action']} conf={sig['confidence']} "
                      f"(score {sig.get('confidence')})")
        except Exception as exc:  # keep the loop alive
            print(f"[!] {exc}", file=sys.stderr)
        time.sleep(max(10, args.interval))


def cmd_sources(args) -> int:
    from data.sources.cryptodada_website import CryptoDadaConnector, summarize_cryptodada
    from data.sources.discord_reader import DiscordReader, summarize_discord
    from data.sources.news import fetch_news

    result = {}
    cd = CryptoDadaConnector()
    result["cryptodada"] = summarize_cryptodada(cd.fetch()) if cd.configured else {
        "configured": False, "message": "Set CRYPTODADA_* in .env"}
    dr = DiscordReader()
    result["discord"] = summarize_discord(dr.read_all()) if dr.can_read else {
        "configured": False, "message": "Set DISCORD_TOKEN + DISCORD_CHANNEL_IDS in .env"}
    result["news"] = fetch_news(limit=12)
    # Information hierarchy (decision A5): tier + trust per source.
    from data.database import SignalDB
    from config import SOURCE_TIER_NAMES
    with SignalDB() as db:
        result["source_trust"] = [
            {**s, "tier_name": SOURCE_TIER_NAMES.get(s["tier"], "?")}
            for s in db.load_source_scores()]
    if not result["source_trust"]:
        result["source_trust_note"] = (
            "No source scores yet — `python main.py sourcetrust <source> <tier 1-5> <0-1> "
            "[note]` (tier 5 = private signals, never auto-actionable)")
    print(json.dumps(result, indent=2, default=str))
    return 0


def cmd_backtest(args) -> int:
    from data.backtester import run_backtest, save_report, print_report
    from data.database import SignalDB

    client = _client()
    symbol = _sym(args.symbol)
    df = client.klines(symbol, args.tf, args.bars)
    horizons = [float(h) for h in args.horizons.split(",") if h.strip()]
    result = run_backtest(df, symbol=symbol, timeframe=args.tf,
                          horizons=horizons, min_confidence=args.min_conf)
    report = result["report"]
    save_report(report)
    print_report(report)

    if args.save:
        run_id = time.strftime("%Y%m%d_%H%M%S")
        rows = []
        for g in result["graded"]:
            r = g.as_row()
            r.update({"run_id": run_id, "symbol": symbol,
                      "timeframe": args.tf})
            rows.append(r)
        with SignalDB() as db:
            n = db.save_backtest_rows(rows, run_id)
        print(f"\nSaved {n} graded plans to the signal database (run {run_id}).")
        print("Run `python main.py stats` to see what the engine has learned.")
    return 0


def _print_review_row(r: dict) -> None:
    print(f"  #{r['id']:<5} {r['symbol']:<10} {r['timeframe']:<4} {r['action']:<5} "
          f"conf={r['confidence_label']:<7} entry={r['entry']}  "
          f"plans={r['n_plans']}  {r['reason'][:60]}")


def cmd_review(args) -> int:
    from data.database import SignalDB
    with SignalDB() as db:
        rows = db.pending_reviews(_sym(args.symbol) if args.symbol else None)
    print(f"Pending human approval: {len(rows)}")
    for r in rows:
        _print_review_row(r)
    print("\nApprove:  python main.py approve <id> [--note ...]")
    print("Reject:   python main.py reject <id> [--note ...]")
    print("Details:  python main.py signal <id>")
    return 0


def _decide(args, to_state: str) -> int:
    from data.database import SignalDB
    from engine.lifecycle import LifecycleError
    with SignalDB() as db:
        try:
            new = db.update_status(args.scan_id, to_state, note=args.note or "")
        except LifecycleError as exc:
            print(f"[!] {exc}", file=sys.stderr)
            return 1
        if new is None:
            print(f"[!] scan #{args.scan_id} not found", file=sys.stderr)
            return 1
        sig = db.get_scan(args.scan_id)
    print(f"scan #{args.scan_id} → {new}  ({sig['symbol']} {sig['action']} "
          f"{sig['entry']})  note: {args.note or '—'}")
    return 0


def cmd_approve(args) -> int:
    """Human approval — but the risk & discipline gate is ENFORCED here
    (decisions B6/B7/B9/B10).  `--force` is the explicit escape hatch."""
    from data.database import SignalDB
    from brain.risk_gate import evaluate as gate_evaluate, gate_message
    from data.paper_trading import _primary_plan
    with SignalDB() as db:
        scan = db.get_scan(args.scan_id)
        if scan is None:
            print(f"[!] scan #{args.scan_id} not found", file=sys.stderr)
            return 1
        plan_type = _primary_plan(scan).get("type")
        gate = gate_evaluate(db, symbol=scan.get("symbol"), plan_type=plan_type,
                             action=scan.get("action"))
    if not gate["allowed"] and not args.force:
        print("[!] Approval blocked by the risk & discipline gate.", file=sys.stderr)
        for b in gate["blocked_by"]:
            print(f"    ✗ {b}", file=sys.stderr)
        print("    (re-run with --force to override consciously)", file=sys.stderr)
        return 2
    rc = _decide(args, "APPROVED")
    if rc == 0 and not gate["allowed"]:
        print(f"    ⚠️ overridden with --force: {gate_message(gate)}")
    return rc


def cmd_reject(args) -> int:
    return _decide(args, "REJECTED")


def cmd_execute(args) -> int:
    return _decide(args, "EXECUTED")


def cmd_close(args) -> int:
    return _decide(args, "CLOSED")


def _print_paper_run(result: dict) -> None:
    """Human-readable summary for one safe paper-monitoring pass."""
    print("=" * 72)
    print("PAPER TRADING RUNNER — public market data only; no exchange orders")
    print(f"  enrolled {result.get('enrolled', 0)} | checked {result.get('checked', 0)} | "
          f"entries {result.get('opened', 0)} | closed {result.get('closed', 0)} | "
          f"cancelled {result.get('cancelled', 0)}")
    for event in result.get("events", []):
        extra = event.get("reason", "")
        if event.get("outcome"):
            extra = f"{event['outcome']} {event.get('rr_achieved', 0):+.2f}R — {extra}"
        if event.get("price") is not None:
            extra = f"@ {event['price']:,.8g} — {extra}"
        print(f"  #{event.get('trade_id', '?'):<4} {event.get('symbol', '?'):<10} "
              f"{event.get('action', '?'):<4} {event.get('event', '?'):<14} {extra}")
    for error in result.get("errors", []):
        print(f"  [!] {error.get('symbol', 'paper')} #{error.get('trade_id', error.get('scan_id', '?'))}: "
              f"{error.get('error')}", file=sys.stderr)


def cmd_paper(args) -> int:
    """Run the approved-signal paper monitor once, or keep it running."""
    from data.database import SignalDB
    from data.paper_trading import PaperTradingRunner

    client = _client()
    symbol = _sym(args.symbol) if args.symbol else None

    def one_pass() -> dict:
        with SignalDB() as db:
            runner = PaperTradingRunner(db=db, client=client)
            return runner.run_once(symbol=symbol, enroll=not args.no_enroll).as_dict()

    if args.watch:
        print(f"Paper runner watching {symbol or 'all approved symbols'} every "
              f"{args.interval}s — Ctrl+C to stop")
        try:
            while True:
                try:
                    result = one_pass()
                    if args.json:
                        print(json.dumps(result, default=str))
                    else:
                        _print_paper_run(result)
                except Exception as exc:  # keep unattended paper monitoring alive
                    print(f"[!] paper runner pass failed: {type(exc).__name__}: {exc}", file=sys.stderr)
                time.sleep(max(10, args.interval))
        except KeyboardInterrupt:
            print("\nPaper runner stopped.")
        return 0

    try:
        result = one_pass()
    except Exception as exc:
        print(f"[!] paper runner failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        _print_paper_run(result)
        from data.database import SignalDB
        with SignalDB() as db:
            stats = db.paper_trade_stats(symbol)["overall"]
        print(f"  total paper trades {stats['n']} | waiting {stats.get('waiting', 0)} | "
              f"open {stats.get('open', 0)} | wins/losses {stats['wins']}/{stats['losses']} | "
              f"avg R {stats['avg_rr']}")
    return 0


def cmd_signal(args) -> int:
    from data.database import SignalDB
    import json as _json
    with SignalDB() as db:
        scan = db.get_scan(args.scan_id)
        if scan is None:
            print(f"[!] scan #{args.scan_id} not found", file=sys.stderr)
            return 1
        history = db.decision_history(args.scan_id)
    print(f"scan #{scan['id']} — {scan['symbol']} {scan['timeframe']} {scan['action']} "
          f"({scan['created_at']})")
    print(f"  status: {scan['status']}  conf: {scan['confidence_label']}  "
          f"entry: {scan['entry']}  SL: {scan['stop_loss']}  TP: {scan['take_profit']}")
    print(f"  reason: {scan['reason']}")
    print("  lifecycle:")
    for h in history:
        print(f"    {h['from_state']} → {h['to_state']}  by {h['reviewer']}  "
              f"note: {h['note'] or '—'}")
    if scan.get("plans_json"):
        plans = _json.loads(scan["plans_json"])
        print(f"  plans ({len(plans)}):")
        for p in plans[:5]:
            print(f"    [{p['confidence']}%] {p['type']} — {p['condition'][:80]}")
    return 0


def cmd_learn(args) -> int:
    from brain.calibrator import learn, describe
    result = learn()
    print(describe(result["profile"]))
    if args.json:
        import json as _json
        print(_json.dumps(result["profile"], indent=2))
    return 0


def cmd_coach(args) -> int:
    from data.database import SignalDB
    from brain.coach import explain_signal, mentor, personal_feedback, GLOSSARY

    # 1) teach from the current market (fresh scan, no DB save)
    payload = run_scan(args.symbol, args.tf, args.bars, save_db=False)
    print("=" * 70)
    print("🧑‍🏫 COACH — what's happening right now")
    print("=" * 70)
    for line in explain_signal(payload):
        print(" ", line)
    print()
    print(mentor(payload))

    # 2) personal feedback from decision history
    with SignalDB() as db:
        fb = personal_feedback(db)
    print()
    print("📈 YOUR TRADING FEEDBACK")
    for f in fb:
        print(" ", f)

    # 3) optional glossary deep-dive
    if args.term:
        term = next((k for k in GLOSSARY if k.lower() == args.term.lower()), None)
        if term:
            meaning, why = GLOSSARY[term]
            print(f"\n📖 {term}: {meaning}\n   Why it matters: {why}")
        else:
            print(f"\n[!] unknown term '{args.term}' — try: {', '.join(GLOSSARY)}")
    return 0


def cmd_glossary(args) -> int:
    from brain.coach import GLOSSARY
    if args.term:
        term = next((k for k in GLOSSARY if k.lower() == args.term.lower()), None)
        if term:
            meaning, why = GLOSSARY[term]
            print(f"{term}: {meaning}\n  Why it matters: {why}")
        else:
            print(f"unknown term '{args.term}'")
        return 0
    for term, (meaning, why) in GLOSSARY.items():
        print(f"{term:<18} {meaning}")
    return 0


def _print_context(ctx: dict) -> None:
    print("   CONTEXT (what affects the market):")
    fng = ctx.get("fear_greed") or {}
    if fng.get("available"):
        print(f"     fear&greed: {fng['value']} ({fng['label']})")
    dom = ctx.get("dominance") or {}
    if dom.get("available"):
        print(f"     BTC dom {dom['btc_dominance']}% · ETH {dom['eth_dominance']}% · "
              f"total cap ${dom['total_market_cap_usd']/1e12:.2f}T "
              f"({dom['market_cap_change_24h_pct']:+.2f}% 24h)")
    eq = ctx.get("equities") or {}
    if eq.get("available"):
        cp = eq.get("change_pct", {})
        print(f"     S&P500 {cp.get('^spx')}% · Nasdaq {cp.get('^ndq')}% · "
              f"DXY {cp.get('dx.f')}% · Gold {cp.get('xauusd')}%")
    macro = ctx.get("macro") or {}
    if macro.get("available"):
        for e in macro.get("events", [])[:4]:
            flag = " ⚠️" if e.get("days_until", 99) <= 2 else ""
            print(f"     {e['date']} {e['name']} (in {e['days_until']}d){flag}")
    cyc = ctx.get("cycle") or {}
    if cyc.get("available"):
        print(f"     cycle: {cyc['phase']} · {cyc['days_since_halving']}d since halving · "
              f"{cyc.get('position_vs_200d')}")
    geo = ctx.get("geopolitics") or {}
    if geo.get("available") and geo.get("count"):
        print(f"     ⚠️ geopolitics: {geo['count']} headline hit(s) — {geo['hits'][0]['keyword']}")
    social = ctx.get("social") or {}
    if social.get("available") and social.get("count"):
        print(f"     social/influencer mentions: {social['count']} — "
              f"{social['hits'][0]['keyword']}")
    reg = ctx.get("risk_regime") or {}
    if reg.get("regime"):
        print(f"     risk regime: {reg['regime']} ({reg.get('score')}) — "
              f"{' · '.join(reg.get('parts', [])[:5])}")


def cmd_intelligence(args) -> int:
    """Strict professional desk report."""
    from brain.full_pipeline import analyze_full
    from output.signal_card import format_signal_card
    symbol = _sym(args.symbol)
    payload = analyze_full(symbol, args.tf, args.bars, with_context=True)
    intel = payload.get("intelligence", {})
    if getattr(args, "card", False):
        print(format_signal_card(intel))
    else:
        print(json.dumps(intel, indent=2, default=str))
    return 0


def cmd_card(args) -> int:
    """Print the Institutional AI Signal Card v2.0."""
    from brain.full_pipeline import analyze_full
    from output.signal_card import format_signal_card
    symbol = _sym(args.symbol)
    payload = analyze_full(symbol, args.tf, args.bars, with_context=True)
    intel = payload.get("intelligence", {})
    if args.json:
        print(json.dumps(intel.get("signal_card", intel), indent=2, default=str))
    else:
        print(format_signal_card(intel))
    return 0


def cmd_analyze(args) -> int:
    """Full 'human trader' analysis: MTF + context + styles + memory."""
    from brain.full_pipeline import analyze_full
    symbol = _sym(args.symbol)
    payload = analyze_full(symbol, args.tf, args.bars, with_context=True)
    sig = payload["signal"]
    print("=" * 72)
    print(f"🧠 FULL ANALYSIS — {symbol} {args.tf}")
    print(f"   {sig['action']} {sig['confidence']} — {sig['reason']}")
    mtf = payload.get("mtf", {})
    a = mtf.get("alignment", {})
    print(f"   HTF {mtf.get('htf_bias')} | LTF {mtf.get('ltf_bias')} | "
          f"alignment {a.get('score')} ({a.get('label')})")
    kl = mtf.get("key_levels", {})
    print(f"   support {kl.get('support')}  resistance {kl.get('resistance')}")
    print("-" * 72)
    _print_context(payload.get("context", {}))
    print("-" * 72)
    styles = payload.get("styles", {})
    print("   WHAT THE MARKET OFFERS:")
    if styles.get("market_offering"):
        for s in styles["market_offering"]:
            v = styles["styles"][s]
            print(f"     ✓ {s}: {v['direction']} {v['confidence']}% — {v['reason']}")
    else:
        print("     nothing clean right now — " + "; ".join(styles.get("stand_aside", [])))
    mem = payload.get("memory", {})
    if mem:
        print("-" * 72)
        print(f"   STATE MEMORY: {mem.get('status')}" +
              (f" — stable since {time.strftime('%H:%M', time.localtime(mem.get('stable_since', 0)/1000))}" if mem.get('stable_since') else ""))
        for c in mem.get("changes", [])[:5]:
            print(f"     · {c}")
    if args.json:
        import json as _json
        print(_json.dumps(payload, indent=2, default=str))
    return 0


def cmd_state(args) -> int:
    """Show the AI's remembered market state + event log."""
    from brain.state_memory import SignalMemory
    symbol = _sym(args.symbol)
    mem = SignalMemory()
    row = mem.get_state(symbol, args.tf)
    print("=" * 66)
    print(f"STATE MEMORY — {symbol} {args.tf}")
    if not row:
        print("No state recorded yet. Run `python main.py analyze` or `scan` first.")
        return 0
    print(f"  htf_bias     : {row['htf_bias']}")
    print(f"  alignment    : {row['alignment']}")
    print(f"  last event   : {row['last_event']}")
    print(f"  price        : {row['price']:,.2f}")
    print(f"  state hash   : {row['state_hash']}")
    print(f"  reaffirms    : {row['reaffirms']} (same-state refreshes)")
    print(f"  flips (1h)   : {row['flips_1h']}")
    print(f"  updated      : {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(row['updated_at']/1000))}")
    try:
        import json as _json
        st = _json.loads(row.get("styles_json") or "{}")
        print("  style memory :")
        for s in ("Scalp", "Day", "Swing", "Momentum", "Position"):
            v = st.get(s) or {}
            since = v.get("since_ts")
            print(f"    {s:<10} since={time.strftime('%H:%M', time.localtime(since/1000)) if since else '—'}  "
                  f"cooldown={v.get('cooldown_min', '?')}m")
    except Exception:
        pass
    print("-" * 66)
    print("  recent state events:")
    for e in mem.history(symbol, args.tf, limit=10):
        print(f"    {time.strftime('%m-%d %H:%M', time.localtime(e['ts']/1000))}  "
              f"{e['kind']:<8} {e['detail'][:80]}")
    return 0


def cmd_stats(args) -> int:
    from data.database import SignalDB

    symbol = _sym(args.symbol) if args.symbol else None
    with SignalDB() as db:
        scans = db.latest_scans(symbol, limit=15)
        plan_stats = db.plan_stats()
        bt = db.backtest_stats()
        paper = db.paper_trade_stats(symbol)

    print("=" * 66)
    print("SIGNAL DATABASE — learning store")
    if not scans:
        print("No scans recorded yet. Run `python main.py scan` (saves by default).")
        return 0
    print(f"latest {len(scans)} scans:")
    for s in scans:
        print(f"  {s['symbol']:<10} {s['timeframe']:<4} {s['action']:<8} "
              f"conf={s['confidence_label']:<7} entry={s['entry']}  {s['created_at']}")
    print("-" * 66)
    print("plan-type distribution (live scans):")
    for p in plan_stats:
        print(f"  {p['type']:<24} n={p['n']:>4} avgConf={p['avg_conf']}  avgRR={p['avg_rr']}")

    overall = bt["overall"]
    print("-" * 66)
    print("BACKTEST learning:")
    if overall["n"]:
        wr = overall["win_rate"]
        print(f"  overall: {overall['n']} graded | win-rate "
              f"{f'{wr*100:.1f}%' if wr is not None else 'n/a'} | "
              f"avgR {overall['avg_rr']} | wins {overall['wins']} losses {overall['losses']}")
        print("  by plan type:")
        for r in bt["by_type"]:
            wr = r["win_rate"]
            print(f"    {r['plan_type']:<24} n={r['n']:>4} win "
                  f"{f'{wr*100:.1f}%' if wr is not None else 'n/a'}  avgR {r['avg_rr']}")
        print("  by confidence bucket:")
        for r in bt["by_confidence"]:
            wr = r["win_rate"]
            print(f"    {r['bucket']:<8} n={r['n']:>4} win "
                  f"{f'{wr*100:.1f}%' if wr is not None else 'n/a'}  avgR {r['avg_rr']}")
    else:
        print("  none yet — run `python main.py backtest --save` to start learning.")

    p = paper["overall"]
    print("-" * 66)
    print("PAPER trading (approved live-market simulations):")
    if p["n"]:
        wr = p["win_rate"]
        print(f"  tracked {p['n']} | waiting {p.get('waiting', 0)} | open {p.get('open', 0)} | "
              f"closed {p.get('closed', 0)} | win-rate "
              f"{f'{wr*100:.1f}%' if wr is not None else 'n/a'} | avgR {p['avg_rr']}")
    else:
        print("  none yet — approve a signal, then run `python main.py paper --watch`.")

    # Business scorecard (decision B4) + regime learning (B3) + journal (B5)
    try:
        from brain.metrics import business_metrics, format_metrics
        with SignalDB() as db:
            metrics = business_metrics(db)
            print()
            print(format_metrics(metrics))
    except Exception as exc:  # never break stats on a metrics hiccup
        print(f"  (business metrics unavailable: {exc})")

    if bt.get("by_regime"):
        print("-" * 66)
        print("BACKTEST learning by regime (strategy = setup × regime × asset):")
        for r in bt["by_regime"]:
            wr = r["win_rate"]
            print(f"  {r['regime'] or 'UNKNOWN':<22} n={r['n']:>4} win "
                  f"{f'{wr*100:.1f}%' if wr is not None else 'n/a'}  avgR {r['avg_rr']}")

    try:
        from brain.journal import violation_rate
        with SignalDB() as db:
            j = violation_rate(db)
        if j["n"]:
            print("-" * 66)
            print(f"JOURNAL discipline: {j['violations']}/{j['n']} trades "
                  f"({j['violation_rate']*100:.1f}%) violated the system")
    except Exception:
        pass
    return 0


def cmd_web(args) -> int:
    from web.app import make_app, serve
    host = args.host or DASHBOARD_HOST
    port = args.port or DASHBOARD_PORT
    serve(make_app(), host, port)
    return 0


def cmd_sourcetrust(args) -> int:
    """Record/update a source's information tier (1-5) and trust (0-1)
    (decision A5).  Tier 5 = private signals: context only, never a trigger."""
    from data.database import SignalDB
    from config import SOURCE_TIER_NAMES
    with SignalDB() as db:
        db.save_source_score(args.source, args.tier, args.trust, note=args.note or "")
        rows = db.load_source_scores()
    print("source trust table:")
    for s in rows:
        print(f"  {s['source']:<22} tier {s['tier']} ({SOURCE_TIER_NAMES.get(s['tier'], '?')})  "
              f"trust {s['trust']:.2f}  {s['note'] or ''}")
    return 0


def cmd_risk(args) -> int:
    """Risk & discipline gate status: daily/weekly limits, drawdown ladder,
    trader state, progression level (decisions B6/B7/B9/B10)."""
    from brain.risk_gate import status_text
    from data.database import SignalDB
    with SignalDB() as db:
        print(status_text(db))
    return 0


def cmd_tradestate(args) -> int:
    """Set/clear the behavioral no-trade flags (decision B7)."""
    from data.database import SignalDB
    from brain.risk_gate import trader_state_blocked
    with SignalDB() as db:
        if any(v is not None for v in (args.angry, args.tired, args.revenge, args.chasing)) \
                or args.clear or args.note:
            if args.clear:
                db.set_trader_state(angry=False, tired=False, revenge=False,
                                    chasing=False, note=args.note or "cleared")
            else:
                db.set_trader_state(angry=args.angry, tired=args.tired,
                                    revenge=args.revenge, chasing=args.chasing,
                                    note=args.note or "")
            print("trader state updated:")
        st = db.get_trader_state()
        g = trader_state_blocked(db)
    for k in ("angry", "tired", "revenge", "chasing"):
        print(f"  {k:<8} {'✗ BLOCKED' if st.get(k) else 'clear'}")
    if st.get("note"):
        print(f"  note: {st['note']}")
    print(f"  gate: {'CLOSED — no new trades until cleared' if g['blocked'] else 'OPEN'}")
    return 0


def cmd_journal(args) -> int:
    """Professional trading journal (decision B5): record/view post-trade
    fields and execution quality for a closed scan."""
    from brain.journal import (save_journal, get_journal, execution_quality,
                               pre_trade_checklist, describe_entry,
                               violation_rate)
    from data.database import SignalDB
    with SignalDB() as db:
        if args.scan_id is not None:
            if args.followed_rules is not None or args.emotion or args.mistake \
                    or args.screenshot or args.would_change or args.notes:
                entry = save_journal(
                    db, args.scan_id,
                    followed_rules=args.followed_rules,
                    emotion=args.emotion, mistake=args.mistake,
                    screenshot_path=args.screenshot, would_change=args.would_change,
                    notes=args.notes)
                print(f"journal saved for scan #{args.scan_id}:")
                print(describe_entry(entry))
                q = execution_quality(db, args.scan_id)
                if q.get("recorded"):
                    print(f"  → {q['verdict']} (quality {q['quality']}, {q['outcome_r']:+.2f}R)")
            else:
                entry = get_journal(db, args.scan_id)
                print(f"journal for scan #{args.scan_id}:")
                print(describe_entry(entry))
                q = execution_quality(db, args.scan_id)
                if q.get("recorded"):
                    print(f"  → {q['verdict']} (quality {q['quality']})")
        else:
            stats = violation_rate(db)
            print("=" * 66)
            print("JOURNAL — execution discipline")
            print(f"  recorded trades   {stats['n']}")
            if stats["n"]:
                print(f"  rule violations   {stats['violations']} "
                      f"({stats['violation_rate']*100:.1f}%)")
                print("  (the goal is 0% — a loss with rules followed is an excellent trade)")
            else:
                print("  none yet — `python main.py journal <scan_id> --followed-rules 1 ...`")
    return 0


def cmd_agent(args) -> int:
    """Desk agent: morning briefing / health / natural-language ask / all."""
    from brain.agent import (ask, format_answer, format_briefing, format_health,
                             health_report, morning_briefing)
    if args.action == "morning":
        briefing = morning_briefing(symbols=args.symbols, timeframe=args.tf,
                                    bars=args.bars, save=args.save)
        if args.json:
            print(json.dumps(briefing, indent=2, default=str))
        else:
            print(format_briefing(briefing))
        return 0
    if args.action == "health":
        report = health_report()
        if args.json:
            print(json.dumps(report, indent=2, default=str))
        else:
            print(format_health(report))
        return 0
    if args.action == "ask":
        result = ask(args.question, symbol=args.symbol, timeframe=args.tf,
                     bars=args.bars)
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            print(format_answer(result))
        return 0
    if args.action == "all":
        # One desk run: health + morning briefing + graduation gate.
        from brain.agent import graduation_report
        from data.simulator import format_graduation
        report = health_report()
        briefing = morning_briefing(symbols=args.symbols, timeframe=args.tf,
                                    bars=args.bars, save=args.save)
        gate = graduation_report()
        if args.json:
            print(json.dumps({"health": report, "briefing": briefing,
                              "graduation": gate}, indent=2, default=str))
        else:
            print(format_health(report))
            print()
            print(format_briefing(briefing))
            print()
            print(format_graduation(gate["graduation"]))
        return 0
    print(f"[!] unknown agent action: {args.action}", file=sys.stderr)
    return 1


def cmd_brief(args) -> int:
    """Daily desk briefing — alias for `agent morning` (BLUEPRINT ready-to-run)."""
    from brain.agent import format_briefing, morning_briefing
    briefing = morning_briefing(symbols=args.symbols, timeframe=args.tf,
                                bars=args.bars, save=args.save)
    if args.json:
        print(json.dumps(briefing, indent=2, default=str))
    else:
        print(format_briefing(briefing))
    return 0


def cmd_health(args) -> int:
    """System health: data feeds, database, risk gate, learning, MCP, LLM."""
    from brain.agent import format_health, health_report
    report = health_report()
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(format_health(report))
    return 0 if report.get("ok") else 1


def cmd_simulator(args) -> int:
    """The paper-sample grind: unique backtest + paper samples per setup."""
    from data.database import SignalDB
    from data.simulator import (format_graduation, format_progress,
                                graduation_status, grind_verdict,
                                paper_progress, simulate_round)

    symbols = _symbols(args.symbols) if args.symbols else None
    total_bt = total_pp = 0
    rounds = []
    for r in range(max(1, args.rounds)):
        res = simulate_round(symbols=symbols, timeframe=args.tf, bars=args.bars,
                             step=args.step, min_confidence=args.min_conf,
                             horizons=[float(h) for h in args.horizons.split(",") if h],
                             save=not args.dry_run)
        rounds.append(res)
        total_bt += res["backtest_added"]
        total_pp += res["paper_added"]
        if not args.json:
            for s in res["symbols"]:
                if s.get("error"):
                    print(f"[!] {s['symbol']}: {s['error']}", file=sys.stderr)
            print(f"round {r + 1}: +{res['backtest_added']} backtest samples, "
                  f"+{res['paper_added']} paper samples (run {res['run_id']})")
        if args.dry_run:
            break  # dry-run: one pass, nothing stored

    with SignalDB() as db:
        progress = paper_progress(db)
        verdict = grind_verdict(progress)
        from brain.journal import violation_rate
        j = violation_rate(db)
        compliance = 1.0 - j["violation_rate"] if j["violation_rate"] is not None \
            else None
        gate = graduation_status(progress, compliance=compliance)
    if args.json:
        print(json.dumps({"rounds": rounds, "totals": {"backtest_added": total_bt,
                                                       "paper_added": total_pp},
                          "progress": progress, "verdict": verdict,
                          "graduation": gate, "journal": j},
                         indent=2, default=str))
    else:
        print()
        print(format_progress(progress, verdict))
        print()
        print(format_graduation(gate))
        print("\n(dry-run: nothing was stored)" if args.dry_run else "")
    return 0


def cmd_mcp(args) -> int:
    """Run the Model Context Protocol server (stdio) for MCP clients."""
    from ai.mcp_server import run as mcp_run
    return mcp_run()


def main() -> int:
    ap = argparse.ArgumentParser(description="CryptoBrain — AI trading-brain signal engine")
    sub = ap.add_subparsers(dest="cmd")

    p_scan = sub.add_parser("scan", help="one-shot signal scan")
    p_scan.add_argument("--symbol", default=SYMBOL, help="single asset (aliases: BTC, ETH, XAU/GOLD)")
    p_scan.add_argument("--symbols", default=None,
                        help=f"comma-separated watchlist (e.g. {','.join(SYMBOLS)}); overrides --symbol")
    p_scan.add_argument("--tf", default=TIMEFRAME)
    p_scan.add_argument("--bars", type=int, default=BARS)
    p_scan.add_argument("--json", action="store_true", help="raw JSON output")
    p_scan.add_argument("--llm", action="store_true", help="attach LLM narrative if configured")
    p_scan.add_argument("--no-save", action="store_true", help="do not write the scan to the signal database")
    p_scan.add_argument("--auto-approve", action="store_true",
                        help="skip the human approval gate (unattended mode)")
    p_scan.set_defaults(func=cmd_scan)

    p_watch = sub.add_parser("watch", help="continuous monitor loop")
    p_watch.add_argument("--symbol", default=SYMBOL, help="asset to watch (aliases: BTC, ETH, XAU/GOLD)")
    p_watch.add_argument("--tf", default=TIMEFRAME)
    p_watch.add_argument("--bars", type=int, default=BARS)
    p_watch.add_argument("--interval", type=int, default=120)
    p_watch.add_argument("--notify", action="store_true", help="push signals to Telegram/Discord")
    p_watch.add_argument("--no-save", action="store_true", help="do not write scans to the signal database")
    p_watch.add_argument("--auto-approve", action="store_true",
                        help="approve signals automatically (unattended mode)")
    p_watch.set_defaults(func=cmd_watch)

    p_paper = sub.add_parser("paper", help="monitor approved paper trades; never sends exchange orders")
    p_paper.add_argument("--symbol", default=None, help="optional symbol filter, e.g. BTCUSDT, ETH, XAUUSD")
    p_paper.add_argument("--watch", action="store_true", help="keep monitoring until Ctrl+C")
    p_paper.add_argument("--interval", type=int, default=PAPER_POLL_SECONDS,
                         help="seconds between checks in --watch mode")
    p_paper.add_argument("--no-enroll", action="store_true",
                         help="only check existing paper trades; do not enroll new approvals")
    p_paper.add_argument("--json", action="store_true", help="machine-readable run summary")
    p_paper.set_defaults(func=cmd_paper)

    p_src = sub.add_parser("sources", help="pull CryptoDada + Discord + news")
    p_src.set_defaults(func=cmd_sources)

    p_bt = sub.add_parser("backtest", help="walk-forward grade of engine plans")
    p_bt.add_argument("--symbol", default=SYMBOL, help="asset (aliases: BTC, ETH, XAU/GOLD)")
    p_bt.add_argument("--tf", default=TIMEFRAME)
    p_bt.add_argument("--bars", type=int, default=BARS)
    p_bt.add_argument("--horizons", default="1,4,24", help="comma-separated hours, e.g. 1,4,24")
    p_bt.add_argument("--min-conf", type=int, default=MIN_CONFIDENCE)
    p_bt.add_argument("--save", action="store_true", help="store graded outcomes in the signal database")
    p_bt.set_defaults(func=cmd_backtest)

    p_stats = sub.add_parser("stats", help="what the engine has learned (DB + backtests)")
    p_stats.add_argument("--symbol", default=None, help="optional asset filter (BTC, ETH, XAU/GOLD)")
    p_stats.set_defaults(func=cmd_stats)

    p_rev = sub.add_parser("review", help="list signals awaiting human approval")
    p_rev.add_argument("--symbol", default=None, help="optional asset filter (BTC, ETH, XAU/GOLD)")
    p_rev.set_defaults(func=cmd_review)

    p_app = sub.add_parser("approve", help="approve a pending signal (risk gate enforced)")
    p_app.add_argument("scan_id", type=int)
    p_app.add_argument("--note", default="")
    p_app.add_argument("--force", action="store_true",
                       help="override a closed risk gate consciously")
    p_app.set_defaults(func=cmd_approve)

    p_rej = sub.add_parser("reject", help="reject a pending signal")
    p_rej.add_argument("scan_id", type=int)
    p_rej.add_argument("--note", default="")
    p_rej.set_defaults(func=cmd_reject)

    p_exec = sub.add_parser("execute", help="mark an approved signal as executed")
    p_exec.add_argument("scan_id", type=int)
    p_exec.add_argument("--note", default="")
    p_exec.set_defaults(func=cmd_execute)

    p_close = sub.add_parser("close", help="close an executed signal (outcome recorded)")
    p_close.add_argument("scan_id", type=int)
    p_close.add_argument("--note", default="")
    p_close.set_defaults(func=cmd_close)

    p_sig = sub.add_parser("signal", help="show a signal's full detail + lifecycle")
    p_sig.add_argument("scan_id", type=int)
    p_sig.set_defaults(func=cmd_signal)

    p_learn = sub.add_parser("learn", help="recompute the self-improvement calibration profile")
    p_learn.add_argument("--json", action="store_true")
    p_learn.set_defaults(func=cmd_learn)

    p_coach = sub.add_parser("coach", help="teaching mode: explain + mentor + personal feedback")
    p_coach.add_argument("--symbol", default=SYMBOL, help="asset (aliases: BTC, ETH, XAU/GOLD)")
    p_coach.add_argument("--tf", default=TIMEFRAME)
    p_coach.add_argument("--bars", type=int, default=BARS)
    p_coach.add_argument("--term", default=None, help="explain a glossary term (e.g. FVG)")
    p_coach.set_defaults(func=cmd_coach)

    p_gl = sub.add_parser("glossary", help="list trading terms used by the engine")
    p_gl.add_argument("term", nargs="?", default=None)
    p_gl.set_defaults(func=cmd_glossary)

    p_intel = sub.add_parser("intelligence", help="professional AI trading desk report (JSON or card)")
    p_intel.add_argument("--symbol", default=SYMBOL, help="asset (aliases: BTC, ETH, XAU/GOLD)")
    p_intel.add_argument("--tf", default=TIMEFRAME)
    p_intel.add_argument("--bars", type=int, default=BARS)
    p_intel.add_argument("--card", action="store_true", help="render institutional terminal signal card")
    p_intel.set_defaults(func=cmd_intelligence)

    p_card = sub.add_parser("card", help="render Institutional AI Signal Card v2.0")
    p_card.add_argument("--symbol", default=SYMBOL, help="asset (aliases: BTC, ETH, XAU/GOLD)")
    p_card.add_argument("--tf", default=TIMEFRAME)
    p_card.add_argument("--bars", type=int, default=BARS)
    p_card.add_argument("--json", action="store_true", help="output card as raw JSON")
    p_card.set_defaults(func=cmd_card)

    p_an = sub.add_parser("analyze", help="full human-trader analysis (MTF + context + styles + memory)")
    p_an.add_argument("--symbol", default=SYMBOL, help="asset (aliases: BTC, ETH, XAU/GOLD)")
    p_an.add_argument("--tf", default=TIMEFRAME)
    p_an.add_argument("--bars", type=int, default=BARS)
    p_an.add_argument("--json", action="store_true")
    p_an.set_defaults(func=cmd_analyze)

    p_st = sub.add_parser("state", help="show the AI's remembered market state + event log")
    p_st.add_argument("--symbol", default=SYMBOL, help="asset (aliases: BTC, ETH, XAU/GOLD)")
    p_st.add_argument("--tf", default=TIMEFRAME)
    p_st.set_defaults(func=cmd_state)

    p_risk = sub.add_parser("risk", help="risk & discipline gate status (daily/weekly/drawdown/progression)")
    p_risk.set_defaults(func=cmd_risk)

    p_st2 = sub.add_parser("sourcetrust", help="record a source's information tier (1-5) + trust (0-1)")
    p_st2.add_argument("source")
    p_st2.add_argument("tier", type=int, choices=(1, 2, 3, 4, 5))
    p_st2.add_argument("trust", type=float)
    p_st2.add_argument("--note", default="")
    p_st2.set_defaults(func=cmd_sourcetrust)

    p_ts = sub.add_parser("tradestate", help="set/clear behavioral no-trade flags (angry/tired/revenge/chasing)")
    p_ts.add_argument("--angry", action="store_true", default=None)
    p_ts.add_argument("--tired", action="store_true", default=None)
    p_ts.add_argument("--revenge", action="store_true", default=None)
    p_ts.add_argument("--chasing", action="store_true", default=None)
    p_ts.add_argument("--clear", action="store_true", help="clear all flags")
    p_ts.add_argument("--note", default="")
    p_ts.set_defaults(func=cmd_tradestate)

    p_j = sub.add_parser("journal", help="professional trading journal (post-trade fields + execution quality)")
    p_j.add_argument("scan_id", nargs="?", type=int, default=None)
    p_j.add_argument("--followed-rules", type=int, choices=(0, 1), default=None,
                     help="1 = followed the system, 0 = broke the rules (the headline field)")
    p_j.add_argument("--emotion", default="", help="calm/anxious/euphoric/fearful/frustrated/greedy/...")
    p_j.add_argument("--mistake", default="")
    p_j.add_argument("--screenshot", default="", help="path to chart screenshot")
    p_j.add_argument("--would-change", default="")
    p_j.add_argument("--notes", default="")
    p_j.set_defaults(func=cmd_journal)

    p_web = sub.add_parser("web", help="run the web dashboard")
    p_web.add_argument("--host", default=None)
    p_web.add_argument("--port", type=int, default=None)
    p_web.set_defaults(func=cmd_web)

    p_agent = sub.add_parser("agent", help="desk agent: morning, health, ask, all")
    p_agent.set_defaults(func=cmd_agent)
    a_sub = p_agent.add_subparsers(dest="action", required=True)
    p_morning = a_sub.add_parser("morning", help="morning briefing across the watchlist")
    p_morning.add_argument("--symbols", default=None,
                           help="comma-separated watchlist (default: BTCUSDT,ETHUSDT,XAUUSD)")
    p_morning.add_argument("--tf", default=TIMEFRAME)
    p_morning.add_argument("--bars", type=int, default=BARS)
    p_morning.add_argument("--save", action="store_true",
                           help="also persist each scan to the signal database")
    p_morning.add_argument("--json", action="store_true")
    p_health_a = a_sub.add_parser("health", help="system health report")
    p_health_a.add_argument("--json", action="store_true")
    p_ask_a = a_sub.add_parser("ask", help="ask the desk a natural-language question")
    p_ask_a.add_argument("question")
    p_ask_a.add_argument("--symbol", default=None, help="asset context (BTC, ETH, GOLD)")
    p_ask_a.add_argument("--tf", default=TIMEFRAME)
    p_ask_a.add_argument("--bars", type=int, default=BARS)
    p_ask_a.add_argument("--json", action="store_true")
    p_all_a = a_sub.add_parser("all", help="one desk run: health + briefing + graduation")
    p_all_a.add_argument("--symbols", default=None,
                         help="comma-separated watchlist (default: BTCUSDT,ETHUSDT,XAUUSD)")
    p_all_a.add_argument("--tf", default=TIMEFRAME)
    p_all_a.add_argument("--bars", type=int, default=BARS)
    p_all_a.add_argument("--save", action="store_true",
                         help="also persist each scan to the signal database")
    p_all_a.add_argument("--json", action="store_true")

    p_brief = sub.add_parser("brief", help="daily desk briefing (alias for `agent morning`)")
    p_brief.add_argument("--symbols", default=None,
                         help="comma-separated watchlist (default: BTCUSDT,ETHUSDT,XAUUSD)")
    p_brief.add_argument("--tf", default=TIMEFRAME)
    p_brief.add_argument("--bars", type=int, default=BARS)
    p_brief.add_argument("--save", action="store_true",
                         help="also persist each scan to the signal database")
    p_brief.add_argument("--json", action="store_true")
    p_brief.set_defaults(func=cmd_brief)

    p_health = sub.add_parser("health", help="system health (data feeds, DB, risk gate, MCP)")
    p_health.add_argument("--json", action="store_true")
    p_health.set_defaults(func=cmd_health)

    p_sim = sub.add_parser("simulator",
                           help="grind unique backtest + paper samples per setup (A6/B10)")
    p_sim.add_argument("--rounds", type=int, default=1, help="rounds to run (default 1)")
    p_sim.add_argument("--symbols", default=None,
                       help="comma-separated watchlist (default: BTCUSDT,ETHUSDT,XAUUSD)")
    p_sim.add_argument("--tf", default=TIMEFRAME)
    p_sim.add_argument("--bars", type=int, default=600, help="history bars per symbol")
    p_sim.add_argument("--step", type=int, default=3, help="window step (1 = every bar)")
    p_sim.add_argument("--min-conf", type=int, default=MIN_CONFIDENCE)
    p_sim.add_argument("--horizons", default="1,4,24",
                       help="comma-separated forward horizons in hours")
    p_sim.add_argument("--dry-run", action="store_true",
                       help="compute what WOULD be added without storing anything")
    p_sim.add_argument("--json", action="store_true")
    p_sim.set_defaults(func=cmd_simulator)

    p_mcp = sub.add_parser("mcp", help="run the Model Context Protocol server (stdio)")
    p_mcp.set_defaults(func=cmd_mcp)

    args = ap.parse_args()
    if args.cmd == "scan" and args.symbols is None:
        args.symbols = args.symbol
    if args.cmd is None:
        # Default: open the all-in-one dashboard (watch everything + click to approve)
        from web.app import make_app, serve
        print("=" * 62)
        print(f"🧠 CryptoBrain v{VERSION} — all-in-one dashboard")
        print(f"   open  http://localhost:{DASHBOARD_PORT}   (watch + click approve/reject)")
        print("   everything runs from the dashboard — no commands needed")
        print("   advanced/automation: scan | intelligence | watch | paper | analyze | backtest |")
        print("                        learn | stats | coach | review | sources | state | glossary |")
        print("                        brief | agent (morning/health/ask/all) | health |")
        print("                        simulator | mcp")
        print("=" * 62)
        serve(make_app(), DASHBOARD_HOST, DASHBOARD_PORT)
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
