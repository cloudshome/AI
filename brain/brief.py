"""brain/brief.py — Morning briefing & post-trade reasoning layer.

Provides:
  1. Morning Brief: Cross-asset MTF synthesis (BTC, ETH, GOLD), session checks,
     and overall desk bias (BULLISH / BEARISH / NEUTRAL).
  2. Post-Trade Review: Post-mortem on executed/paper trades covering R achieved,
     MAE/MFE excursion, rule compliance, and mentor takeaways.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from data.database import SignalDB
from data.symbols import normalize_symbol
from brain.playbooks import get_playbook, session_status, apply_playbook
from brain.risk_gate import evaluate_risk_gate
from brain.full_pipeline import analyze_full


def post_trade_review(scan_id: int, db: Optional[SignalDB] = None) -> dict:
    """Generate a post-mortem review for a specific scan / trade."""
    close_db = False
    if db is None:
        db = SignalDB()
        close_db = True

    try:
        scan = db.get_scan(scan_id)
        if not scan:
            return {
                "scan_id": scan_id,
                "error": f"Scan #{scan_id} not found in database.",
                "headline": f"SCAN #{scan_id} NOT FOUND",
            }

        paper = db.paper_trade_for_scan(scan_id) or {}
        journal = db.conn.execute(
            "SELECT * FROM journal_entries WHERE scan_id=?", (scan_id,)).fetchone()
        journal_dict = dict(journal) if journal else {}

        outcome = paper.get("outcome") or scan.get("status") or "UNKNOWN"
        rr = paper.get("rr_achieved")
        rr_str = f"{rr:+.2f}R" if rr is not None else "0.0R"
        if rr is not None and rr > 0:
            rr_str = f"{rr:.1f}R"

        mae = paper.get("mae")
        mfe = paper.get("mfe")
        mae_str = f"{mae:.1f}" if mae is not None else "0.0"
        mfe_str = f"{mfe:.1f}" if mfe is not None else "0.0"

        fr = journal_dict.get("followed_rules")
        fr_str = "YES" if fr == 1 else ("NO" if fr == 0 else "YES (auto)")

        headline = f"{outcome} · {rr_str} · MAE {mae_str} · MFE {mfe_str} · Followed rules: {fr_str}"

        # Key lessons / mentor feedback
        takeaways = []
        if outcome in ("WIN", "FULL_WIN", "TP_HIT"):
            takeaways.append("Target reached according to plan. Good discipline and patience.")
        elif outcome in ("LOSS", "SL_HIT"):
            takeaways.append("Stop loss protected capital. Loss is an ordinary cost of doing business.")
        else:
            takeaways.append("Trade concluded under monitored market conditions.")

        if mae is not None and mfe is not None and mae > 0:
            ratio = round(mfe / mae, 2) if mae else 0.0
            takeaways.append(f"Excursion ratio (MFE/MAE): {ratio:.2f} — entry precision evaluated.")

        if journal_dict.get("emotion"):
            takeaways.append(f"Recorded emotion: {journal_dict['emotion']}.")
        if journal_dict.get("mistake"):
            takeaways.append(f"Noted mistake: {journal_dict['mistake']}.")

        return {
            "scan_id": scan_id,
            "symbol": scan.get("symbol"),
            "action": scan.get("action"),
            "outcome": outcome,
            "rr_achieved": rr,
            "mae": mae,
            "mfe": mfe,
            "followed_rules": fr_str,
            "headline": headline,
            "takeaways": takeaways,
            "journal": journal_dict,
            "paper_trade": paper,
        }
    finally:
        if close_db:
            db.close()


def generate_morning_brief(symbols: list[str] | None = None,
                           db: Optional[SignalDB] = None) -> dict:
    """Cross-asset morning briefing for the trading desk."""
    target_symbols = symbols or ["BTCUSDT", "ETHUSDT", "XAUUSD"]
    target_symbols = [normalize_symbol(s) for s in target_symbols]

    close_db = False
    if db is None:
        db = SignalDB()
        close_db = True

    try:
        biases = {}
        notes = []
        now = datetime.now(timezone.utc)

        for sym in target_symbols:
            try:
                payload = analyze_full(sym, "15m", 150, with_context=False, with_memory=False)
                sig = payload.get("signal", {})
                act = sig.get("action", "NO TRADE")
                conf = sig.get("confidence", 50)
                mtf = payload.get("mtf", {})
                htf = mtf.get("htf_bias") or "neutral"
                ltf = mtf.get("ltf_bias") or "neutral"

                if "BUY" in act:
                    b_str = "bull"
                elif "SELL" in act:
                    b_str = "bear"
                else:
                    b_str = htf if htf != "neutral" else "neutral"

                # Check session status
                sess = session_status(sym, now.hour)
                sess_note = sess.get("label", "")

                biases[sym] = {
                    "action": act,
                    "confidence": conf,
                    "htf_bias": htf,
                    "ltf_bias": ltf,
                    "bias": b_str,
                    "session": sess.get("session"),
                    "session_label": sess_note,
                    "session_open": sess.get("open", True),
                }

                if sym == "XAUUSD" and not sess.get("open", True):
                    notes.append(f"Gold: {sess_note}")
            except Exception as exc:
                biases[sym] = {
                    "action": "NO TRADE",
                    "confidence": 0,
                    "bias": "neutral",
                    "session_label": "offline",
                    "error": str(exc),
                }

        # Derive desk stance
        btc_b = biases.get("BTCUSDT", {}).get("bias", "neutral")
        eth_b = biases.get("ETHUSDT", {}).get("bias", "neutral")
        gold_b = biases.get("XAUUSD", {}).get("bias", "neutral")

        distinct_biases = {b for b in (btc_b, eth_b, gold_b) if b != "neutral"}
        if len(distinct_biases) > 1:
            overall = "NEUTRAL"
            bias_summary = f"BTC {btc_b}/ETH {eth_b}/GOLD {gold_b} → NEUTRAL"
        elif len(distinct_biases) == 1:
            dominant = list(distinct_biases)[0]
            overall = "BULLISH" if dominant == "bull" else "BEARISH"
            bias_summary = f"Aligned {overall.lower()} across desk"
        else:
            overall = "NEUTRAL"
            bias_summary = "Ranging / neutral across assets"

        # Risk gate check
        rg = evaluate_risk_gate(db)
        risk_summary = f"Risk Gate: {'OPEN' if rg.get('open') else 'HALTED'} (Progression: {rg.get('progression', {}).get('level')})"

        return {
            "timestamp": int(now.timestamp()),
            "date": now.strftime("%Y-%m-%d %H:%M UTC"),
            "overall_bias": overall,
            "bias_summary": bias_summary,
            "biases": biases,
            "notes": notes,
            "risk_gate": rg,
            "risk_summary": risk_summary,
        }
    finally:
        if close_db:
            db.close()
