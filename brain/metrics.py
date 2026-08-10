"""brain/metrics.py — professional trading business metrics (decision B4).

Calculates equity curves, profit factors, win rates, expectancies, drawdowns,
and rolling performance windows from the decided paper-trade log.

Expectancy formula:
  E = (win_rate * avg_win_R) - (loss_rate * abs(avg_loss_R))
"""
from __future__ import annotations

from data.database import SignalDB
from brain.risk_gate import effective_risk


def _equity_curve(db: SignalDB, start: float = 10_000.0) -> list[dict]:
    # exclude_sim: simulator walk-forward rows are calibration evidence with
    # backdated timestamps — the scorecard tracks YOUR paper book only.
    rows = db.decided_paper_rows(exclude_sim=True)
    risk = effective_risk()
    equity = start
    out = []
    for r in rows:
        rr = r.get("rr_achieved")
        if rr is None:
            continue
        dollar_risk = equity * (risk["risk_pct"] / 100.0)
        pnl = dollar_risk * rr
        equity = max(100.0, equity + pnl)
        out.append({
            "scan_id": r["scan_id"],
            "closed_ts": r["closed_ts"],
            "rr": rr,
            "pnl": pnl,
            "equity": round(equity, 2),
        })
    return out


def _metrics(rows: list[dict]) -> dict:
    if not rows:
        return {
            "n": 0, "wins": 0, "losses": 0, "win_rate": None,
            "avg_win_r": 0.0, "avg_loss_r": 0.0, "expectancy_r": 0.0,
            "profit_factor": None, "max_drawdown_pct": 0.0, "final_equity": 10_000.0,
            "win_streak": 0, "loss_streak": 0, "max_win_streak": 0,
            "max_loss_streak": 0,
        }
    wins = [r for r in rows if (r.get("rr_achieved") or 0) > 0]
    losses = [r for r in rows if (r.get("rr_achieved") or 0) < 0]
    n = len(rows)
    w_cnt, l_cnt = len(wins), len(losses)
    win_rate = w_cnt / n if n else 0.0
    loss_rate = l_cnt / n if n else 0.0
    avg_win = sum(r["rr_achieved"] for r in wins) / w_cnt if w_cnt else 0.0
    avg_loss = sum(r["rr_achieved"] for r in losses) / l_cnt if l_cnt else 0.0
    exp = (win_rate * avg_win) + (loss_rate * avg_loss)  # avg_loss is negative

    gross_profit = sum(r["rr_achieved"] for r in wins)
    gross_loss = abs(sum(r["rr_achieved"] for r in losses))
    pf = (gross_profit / gross_loss) if gross_loss > 0 else (99.0 if gross_profit > 0 else None)

    # Streaks
    cur_w, cur_l, max_w, max_l = 0, 0, 0, 0
    for r in rows:
        rr = r.get("rr_achieved") or 0
        if rr > 0:
            cur_w += 1
            cur_l = 0
            max_w = max(max_w, cur_w)
        elif rr < 0:
            cur_l += 1
            cur_w = 0
            max_l = max(max_l, cur_l)

    return {
        "n": n,
        "wins": w_cnt,
        "losses": l_cnt,
        "win_rate": round(win_rate, 3),
        "avg_win_r": round(avg_win, 2),
        "avg_loss_r": round(avg_loss, 2),
        "expectancy_r": round(exp, 3),
        "profit_factor": round(pf, 2) if pf is not None else None,
        "win_streak": cur_w,
        "loss_streak": cur_l,
        "max_win_streak": max_w,
        "max_loss_streak": max_l,
    }


def business_metrics(db: SignalDB, windows: tuple[int, ...] = (50, 100)) -> dict:
    """Full business scorecard: overall + rolling windows + execution.

    Runs on the real paper book only (exclude_sim=True): simulator
    walk-forward samples feed calibration/setup-proof, not your scorecard.
    """
    rows = db.decided_paper_rows(exclude_sim=True)
    overall = _metrics(rows)
    rolling = {}
    for w in windows:
        rolling[str(w)] = _metrics(rows[-w:]) if len(rows) >= w else _metrics(rows)
        rolling[f"last_{w}"] = rolling[str(w)]

    from brain.risk_gate import drawdown
    dw = drawdown(db)
    overall["max_drawdown_pct"] = dw["max_drawdown_pct"]
    overall["final_equity"] = dw.get("final_equity", dw.get("current_equity", 10_000.0))

    from brain.journal import violation_rate
    v = violation_rate(db)
    return {
        "overall": overall,
        "rolling": rolling,
        "execution": v,
        "equity_curve": _equity_curve(db),
    }


def format_metrics(metrics: dict) -> str:
    o = metrics["overall"]
    if not o.get("n"):
        return ("No decided paper trades yet — approve signals and run "
                "`python main.py paper --watch` to build your track record.")
    lines = [
        "=" * 66,
        f"BUSINESS SCORECARD  ({o['n']} decided paper trades)",
        "-" * 66,
        f"  win rate        {o['win_rate']*100:.1f}%  ({o['wins']}W / {o['losses']}L)",
        f"  avg winner      {o['avg_win_r']:+.2f}R    avg loser {o['avg_loss_r']:.2f}R",
        f"  expectancy      {o['expectancy_r']:+.3f}R per trade",
        f"  profit factor   {o['profit_factor'] if o['profit_factor'] is not None else 'n/a'}",
        f"  max drawdown    {o['max_drawdown_pct']:.2f}%   final equity {o['final_equity']:,.2f}",
        f"  streaks         {o['max_win_streak']}W / {o['max_loss_streak']}L",
    ]
    for w, r in metrics["rolling"].items():
        if r.get("n"):
            lines.append(f"  last {w:<4} trades: win {r['win_rate']*100:.0f}%  "
                         f"exp {r['expectancy_r']:+.3f}R  pf {r['profit_factor']}")
    e = metrics["execution"]
    if e.get("n"):
        lines.append(f"  execution       {e['violation_rate']*100:.0f}% rule violations "
                     f"({e['violations']}/{e['n']} journaled trades)")
    return "\n".join(lines)


def compute_business_metrics(db: SignalDB, windows: tuple[int, ...] = (50, 100)) -> dict:
    """Convenience alias for business_metrics."""
    return business_metrics(db, windows=windows)
