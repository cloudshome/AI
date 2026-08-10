"""brain/risk_gate.py — the enforced risk & discipline gate (decisions B6, B7,
B9, B10).

Your roadmap's Phase 12-13: professionals have rules for *not* trading.
Previously the daily/weekly/drawdown limits were advisory text.  Now they are
enforced at the two places a trade can start:

  * the human approval queue (CLI ``approve`` / dashboard button), and
  * the paper runner (it will not enroll new trades while the gate is shut).

The gate combines:

  B6  daily loss limit → hard block;  weekly limit → reduced-activity flag;
      drawdown ladder → -5% reduce / -8% stop & review / -10% full review
  B7  trader state (angry / tired / revenge / chasing) → hard block
  B9  progression level → effective risk caps and unproven-setup approvals
  B10 setup-proven gate → unproven setups are research-only at the
      student/researcher levels

``evaluate()`` is a pure read of the DB + config; nothing here mutates state.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from config import (PROGRESSION, PROGRESSION_LEVELS, ENFORCE_RISK_LIMITS,
                    TRADER_STATE_BLOCK, DRAWDOWN_REDUCE_PCT, DRAWDOWN_STOP_PCT,
                    DRAWDOWN_REVIEW_PCT, RISK_PCT, MAX_DAILY_LOSS_PCT,
                    MAX_WEEKLY_LOSS_PCT, ACCOUNT_BALANCE)
from data.database import SignalDB


def progression() -> dict:
    """Effective caps for the current progression level (decision B9)."""
    level = PROGRESSION_LEVELS.get(PROGRESSION, PROGRESSION_LEVELS["student"])
    return {
        "level": PROGRESSION,
        "label": level["label"],
        "risk_pct": level["risk_pct"],
        "daily": level["daily"],
        "weekly": level["weekly"],
        "approve_unproven": level["approve_unproven"],
    }


def effective_risk() -> dict:
    """The risk numbers actually used for sizing/enforcement: progression wins
    over the generic env defaults when a progression level is active."""
    prog = progression()
    return {
        "risk_pct": prog["risk_pct"],
        "daily": prog["daily"],
        "weekly": prog["weekly"],
        "source": "progression",
        "env": {"risk_pct": RISK_PCT, "daily": MAX_DAILY_LOSS_PCT,
                "weekly": MAX_WEEKLY_LOSS_PCT},
    }


def _day_start_utc(now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(start.timestamp() * 1000)


def _week_start_utc(now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = start.replace(day=start.day - start.weekday())  # Monday
    return int(start.timestamp() * 1000)


def closed_pnl(db: SignalDB, since_ts: int, risk_pct: float) -> dict:
    """Sum of decided paper results since a timestamp, in R and in % equity.

    The live risk book excludes simulator walk-forward samples (exclude_sim):
    they are historical calibration evidence with backdated timestamps, not
    today's trading, so they must not trip daily/weekly loss limits.
    """
    rows = db.decided_paper_rows(since_ts=since_ts, exclude_sim=True)
    r_sum = round(sum(float(r["rr_achieved"] or 0.0) for r in rows), 3)
    return {"n": len(rows), "r_sum": r_sum,
            "pct": round(r_sum * risk_pct, 3)}


def daily_weekly(db: SignalDB) -> dict:
    """Today's and this week's P&L from decided paper trades (B6)."""
    risk = effective_risk()
    today = closed_pnl(db, _day_start_utc(), risk["risk_pct"])
    week = closed_pnl(db, _week_start_utc(), risk["risk_pct"])
    daily_blocked = ENFORCE_RISK_LIMITS and today["pct"] <= -risk["daily"]
    weekly_blocked = ENFORCE_RISK_LIMITS and week["pct"] <= -risk["weekly"]
    return {
        "today": today,
        "week": week,
        "daily_limit": risk["daily"],
        "weekly_limit": risk["weekly"],
        "daily_blocked": daily_blocked,
        "weekly_blocked": weekly_blocked,
        "reduced_activity": weekly_blocked,
    }


def drawdown(db: SignalDB) -> dict:
    """Equity-curve drawdown from decided paper trades (B6 ladder).

    Equity starts at the configured account balance (or $10,000) and applies
    each decided trade's ``rr_achieved * risk_pct`` in chronological order.
    Simulator samples (sim_key NOT NULL) are excluded: they are historical
    walk-forward rows, not the live book, and must not close the live gate.
    """
    rows = db.decided_paper_rows(exclude_sim=True)
    balance = ACCOUNT_BALANCE or 10_000.0
    risk = effective_risk()
    equity = balance
    peak = balance
    max_dd = 0.0
    for r in rows:
        equity += balance * (float(r["rr_achieved"] or 0.0) * risk["risk_pct"] / 100.0)
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak * 100.0 if peak else 0.0)
    if max_dd >= DRAWDOWN_REVIEW_PCT:
        level, blocked = "full_review", ENFORCE_RISK_LIMITS
    elif max_dd >= DRAWDOWN_STOP_PCT:
        level, blocked = "stop_and_review", ENFORCE_RISK_LIMITS
    elif max_dd >= DRAWDOWN_REDUCE_PCT:
        level, blocked = "reduce_risk", ENFORCE_RISK_LIMITS
    else:
        level, blocked = "normal", False
    return {
        "equity": round(equity, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "level": level,
        "blocked": blocked,
        "ladder": {"reduce": DRAWDOWN_REDUCE_PCT, "stop": DRAWDOWN_STOP_PCT,
                   "review": DRAWDOWN_REVIEW_PCT},
    }


def trader_state_blocked(db: SignalDB) -> dict:
    """Behavioral no-trade gate (decision B7)."""
    st = db.get_trader_state()
    if not TRADER_STATE_BLOCK:
        return {"blocked": False, "state": st, "flags": []}
    flags = [k for k in ("angry", "tired", "revenge", "chasing") if st.get(k)]
    return {"blocked": bool(flags), "state": st, "flags": flags}


def setup_proven(db: SignalDB, plan_type: str) -> dict:
    """Setup-proven gate (decisions A6/B10): >=100 backtest samples AND >=20
    decided paper samples AND positive expectancy."""
    st = db.setup_stats(plan_type or "")
    proven = bool(st["backtest_n"] >= 100 and st["paper_n"] >= 20 and
                  st["paper_expectancy"] > 0 and st["backtest_expectancy"] > 0)
    return {"proven": proven, **st}


def evaluate(db: SignalDB, symbol: str | None = None, plan_type: str | None = None,
             action: str | None = None) -> dict:
    """Full gate evaluation. ``allowed=False`` means: do not open a new trade.

    Returns everything the CLI/dashboard need to explain *why*.
    """
    prog = progression()
    risk = effective_risk()
    dw = drawdown(db)
    dws = daily_weekly(db)
    ts = trader_state_blocked(db)
    blocked_by: list[str] = []
    details: dict = {}

    if dws["daily_blocked"]:
        blocked_by.append(
            f"daily loss limit reached ({dws['today']['pct']:.2f}% ≤ -{risk['daily']}%)")
    if dws["weekly_blocked"]:
        blocked_by.append(
            f"weekly loss limit reached ({dws['week']['pct']:.2f}% ≤ -{risk['weekly']}%) — reduced activity")
    if dw["blocked"]:
        blocked_by.append(
            f"drawdown ladder: {dw['level']} at {dw['max_drawdown_pct']:.2f}% drawdown")
    if ts["blocked"]:
        blocked_by.append("trader state: " + ", ".join(ts["flags"]) +
                          " — no trading while these flags are set")

    proven_check = None
    if plan_type:
        proven_check = setup_proven(db, plan_type)
        details["setup_proven"] = proven_check
        if not proven_check["proven"] and not prog["approve_unproven"]:
            blocked_by.append(
                f"setup unproven ({plan_type}): {proven_check['backtest_n']} backtest / "
                f"{proven_check['paper_n']} paper samples — research only at "
                f"'{prog['level']}' level")

    details.update({
        "progression": prog,
        "effective_risk": risk,
        "daily_weekly": dws,
        "drawdown": dw,
        "trader_state": ts,
        "enforced": ENFORCE_RISK_LIMITS,
    })

    allowed = not blocked_by
    return {
        "allowed": allowed,
        "open": allowed,
        "blocked_by": blocked_by,
        "details": details,
        "progression": prog,
    }


def evaluate_risk_gate(db: SignalDB, symbol: str | None = None,
                       plan_type: str | None = None,
                       action: str | None = None) -> dict:
    """Convenience alias for evaluate."""
    return evaluate(db, symbol=symbol, plan_type=plan_type, action=action)


def gate_message(gate: dict) -> str:
    if gate["allowed"]:
        return "Risk gate OPEN — trade allowed"
    return "Risk gate CLOSED — " + "; ".join(gate["blocked_by"])


def status_text(db: SignalDB) -> str:
    """Human-readable status block for `python main.py risk`."""
    g = evaluate(db)
    d = g["details"]
    risk = d["effective_risk"]
    dw = d["drawdown"]
    dws = d["daily_weekly"]
    ts = d["trader_state"]
    prog = d["progression"]
    lines = [
        "=" * 66,
        f"RISK & DISCIPLINE GATE   progression={prog['level']}  ({prog['label']})",
        "-" * 66,
        f"  effective risk      {risk['risk_pct']}%/trade   {risk['daily']}%/day   {risk['weekly']}%/week",
        f"  today  {dws['today']['n']:>3} trades  {dws['today']['pct']:+.2f}%   (limit {risk['daily']}%)",
        f"  week   {dws['week']['n']:>3} trades  {dws['week']['pct']:+.2f}%   (limit {risk['weekly']}%)",
        f"  drawdown           {dw['max_drawdown_pct']:.2f}%  -> {dw['level']}   equity {dw['equity']:,.2f}",
        f"  trader state       {', '.join(ts['flags']) if ts['flags'] else 'clear'}",
        f"  enforced           {'yes' if d['enforced'] else 'no (advisory only)'}",
        "-" * 66,
        f"  GATE: {'OPEN — new trades allowed' if g['allowed'] else 'CLOSED — no new trades'}",
    ]
    if g["blocked_by"]:
        for b in g["blocked_by"]:
            lines.append(f"    ✗ {b}")
    if not g["allowed"] and ts["state"].get("note"):
        lines.append(f"    note: {ts['state']['note']}")
    return "\n".join(lines)
