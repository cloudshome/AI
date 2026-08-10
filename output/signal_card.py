"""output/signal_card.py — Institutional AI Signal Card v2.0 Box-Drawing Formatter.

Renders the fund-grade Institutional AI Signal Card in high-fidelity terminal box-drawing format
and provides helper methods for JSON export and web rendering.
"""
from __future__ import annotations

from typing import Any, Optional


def format_signal_card(intel: dict) -> str:
    """Format the intelligence report into an Institutional Signal Card v2.0 terminal string."""
    asset = intel.get("asset", "UNKNOWN")
    signal = intel.get("signal", "NO TRADE")
    card = intel.get("signal_card") or {}

    # Header styling
    if signal == "BUY":
        action_badge = "🟢 BUY (LONG)"
    elif signal == "SELL":
        action_badge = "🔴 SELL (SHORT)"
    else:
        action_badge = "⚪ WAIT / NO TRADE (PROTECT CAPITAL)"

    conf_idx = card.get("ai_confidence_index", f"{intel.get('confidence', 0):.1f}%")
    conf_delta = card.get("confidence_delta", "+0.0%")
    ips = card.get("institutional_probability_score", intel.get("confidence", 0))
    grade = card.get("trade_quality_grade", "N/A")

    entry_zone = card.get("entry_zone", "N/A")
    stop_loss = card.get("stop_loss_display", "N/A")
    tp_ladder = card.get("tp_ladder", [])
    rr = intel.get("risk_reward", "1:2.0")
    hold_time = card.get("expected_hold_time", "4–8 Hours")
    active_until = card.get("active_until_utc", "N/A")

    reasons = card.get("why_ai_took_trade") or intel.get("reason", [])[:4]
    invalidations = card.get("invalidation_conditions") or [
        f"15m close beyond stop loss level",
        f"HTF market structure flips against direction",
    ]
    alt_scenario = card.get("alternative_scenario") or intel.get("scenario_B", "Wait for confirmed structure break.")

    regime_label = (intel.get("regime") or {}).get("label", intel.get("market_structure", "Normal"))

    lines = []
    lines.append("╔══════════════════════════════════════════════════════════════════════════════════╗")
    lines.append(f"║                     INSTITUTIONAL AI TRADING PLATFORM v2.0                      ║")
    lines.append(f"║                     \"Enterprise-Grade Intelligence for Alpha Generation\"        ║")
    lines.append("╚══════════════════════════════════════════════════════════════════════════════════╝")
    lines.append("┌──────────────────────────────────────────────────────────────────────────────────┐")
    lines.append(f"│  ASSET: {asset:<16} TIMEFRAME: {intel.get('timeframe', '15M'):<12} REGIME: {regime_label[:28]:<28}│")
    lines.append("├──────────────────────────────────────────────────────────────────────────────────┤")
    lines.append(f"│   {action_badge:<78}│")
    lines.append("├──────────────────────────────────────────────────────────────────────────────────┤")
    lines.append(f"│   AI Confidence Index       : {str(conf_idx):<10} {conf_delta:<48}│")
    lines.append(f"│   Institutional Probability : {str(ips) + '/100':<10} Trade Quality Grade: {grade:<26}│")
    lines.append("├──────────────────────────────────────────────────────────────────────────────────┤")
    lines.append(f"│   Entry Zone                : {entry_zone:<49}│")
    lines.append(f"│   Stop Loss                 : {stop_loss:<49}│")
    for tp in tp_ladder[:3]:
        tp_str = f"{tp.get('target', 'TP')}: ${tp.get('price', 0):,} ({'+' if tp.get('gain_pct', 0) >= 0 else ''}{tp.get('gain_pct', 0):.2f}%) — {tp.get('allocation_pct', 0)}% size ({tp.get('management', '')})"
        lines.append(f"│   {tp_str:<78}│")
    lines.append(f"│   Risk-Reward Ratio         : {rr:<16} Expected Hold Time: {hold_time:<24}│")
    lines.append("├──────────────────────────────────────────────────────────────────────────────────┤")
    lines.append("│   Why AI Took This Trade:                                                        │")
    for r in reasons[:4]:
        lines.append(f"│   • {r[:74]:<76}│")
    lines.append("├──────────────────────────────────────────────────────────────────────────────────┤")
    lines.append("│   Invalidation Conditions:                                                       │")
    for inv in invalidations[:3]:
        lines.append(f"│   • {inv[:74]:<76}│")
    lines.append("├──────────────────────────────────────────────────────────────────────────────────┤")
    lines.append("│   Alternative Scenario:                                                          │")
    lines.append(f"│   {alt_scenario[:78]:<78}│")
    lines.append("├──────────────────────────────────────────────────────────────────────────────────┤")
    lines.append(f"│   ⏳ Signal Active Until: {active_until:<54}│")
    lines.append("└──────────────────────────────────────────────────────────────────────────────────┘")
    return "\n".join(lines)
