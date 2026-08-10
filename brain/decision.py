"""brain/decision.py — the professional desk decision (decision A4).

Desk-first mode: every scan ends with ONE final decision in the shape the
roadmap demands:

    action: BUY | SELL | NO TRADE   (the only thing you may act on)
    blocked_by: [...]               (why the desk overrode the raw engine)
    gates: playbook / portfolio / risk  (each with check-level detail)

The raw engine signal stays in ``payload["signal"]`` as research.  The desk
decision is what the approval queue, CLI, dashboard and notifiers surface.

NO TRADE is a first-class, respectable answer.  Conflicting evidence,
portfolio exposure, risk limits, or a behavioral flag all produce NO TRADE.
"""
from __future__ import annotations

from datetime import datetime, timezone

from config import DESK_DEFAULT
from data.database import SignalDB

from . import playbooks as playbooks_mod
from . import portfolio as portfolio_mod
from . import risk_gate as risk_gate_mod


def _top_plan_type(payload: dict) -> str:
    plans = payload.get("plans") or []
    intel = payload.get("intelligence") or {}
    for p in plans:
        if p.get("primary", True):
            return p.get("type") or ""
    return (plans[0].get("type") if plans else "") or ""


def build_decision(payload: dict, symbol: str, db: SignalDB | None = None,
                   *, btc_bias: str | None = None,
                   eth_btc_slope: float | None = None,
                   df_1d=None, now: datetime | None = None) -> dict:
    """Compute the final desk decision for a full-analysis payload.

    ``btc_bias`` / ``eth_btc_slope`` come from the pipeline's ETH playbook
    context; ``df_1d`` supplies daily bars for gold PDH/PDL.
    """
    intel = payload.get("intelligence") or {}
    features = (payload.get("snapshot") or {}).get("features") or {}
    ctx = payload.get("context") or {}
    desk_action = intel.get("signal", "NO TRADE")
    if desk_action not in ("BUY", "SELL"):
        desk_action = "NO TRADE"
    plan_type = _top_plan_type(payload)

    owns_db = db is None
    db = db or SignalDB()
    try:
        return _build_decision_inner(payload, symbol, db, intel, features, ctx,
                                     desk_action, plan_type, btc_bias,
                                     eth_btc_slope, df_1d, now)
    finally:
        if owns_db:
            db.close()


def _build_decision_inner(payload: dict, symbol: str, db: SignalDB, intel: dict,
                          features: dict, ctx: dict, desk_action: str,
                          plan_type: str, btc_bias, eth_btc_slope, df_1d, now) -> dict:
    gates = {"desk": {"ok": desk_action in ("BUY", "SELL"),
                      "detail": f"desk filter says {desk_action}"}}

    # 1) Per-asset playbook (B1/B8)
    news_imminent = bool((ctx.get("macro") or {}).get("high_impact_imminent"))
    pb = playbooks_mod.apply_playbook(
        symbol, desk_action if desk_action != "NO TRADE" else None,
        now=now, news_imminent=news_imminent, df_1d=df_1d,
        btc_bias=btc_bias, eth_btc_slope=eth_btc_slope)
    gates["playbook"] = {
        "name": pb["playbook"],
        "ok": not pb["blocked"],
        "blocked_by": pb["blocking_checks"],
        "checks": pb["checks"],
        "note": pb["note"],
    }

    # 2) Portfolio / correlation veto (B2)
    pv = {"ok": True, "reasons": [], "exposure": None}
    if desk_action in ("BUY", "SELL"):
        _pv = portfolio_mod.portfolio_veto(db, symbol, desk_action)
        pv = {"ok": _pv["allowed"], "reasons": _pv["reasons"],
              "exposure": _pv["exposure"]}
    gates["portfolio"] = pv

    # 3) Risk & discipline gate (B6/B7/B9/B10)
    rg_full = risk_gate_mod.evaluate(db, symbol=symbol, plan_type=plan_type,
                                     action=desk_action if desk_action != "NO TRADE" else None)
    rg = {"ok": rg_full["allowed"], "blocked_by": rg_full["blocked_by"],
          "details": rg_full["details"]}
    gates["risk"] = rg

    blocked_by: list[str] = []
    if desk_action in ("BUY", "SELL"):
        if not gates["playbook"]["ok"]:
            blocked_by += [f"playbook({c})" for c in gates["playbook"]["blocked_by"]]
        if not gates["portfolio"]["ok"]:
            blocked_by += gates["portfolio"]["reasons"]
        if not gates["risk"]["ok"]:
            blocked_by += rg["blocked_by"]

    final = "NO TRADE" if blocked_by or desk_action == "NO TRADE" else desk_action
    return {
        "action": final,
        "desk_action": desk_action,
        "blocked_by": blocked_by,
        "gates": gates,
        "plan_type": plan_type,
        "regime": features.get("regime_name"),
        "regime_label": features.get("regime_label"),
        "desk_default": DESK_DEFAULT,
        "utc": (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M UTC"),
        "decision_text": ("WAIT — no trade" if final == "NO TRADE" else
                          f"TRADE {final} (desk-approved)"),
    }
