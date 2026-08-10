"""data/backtester.py

Walk-forward grader for the brain's conditional plans — the learning loop.

For every bar in a historical OHLCV series (from `min_bars` onward) the engine
is re-run on the data *up to that bar only* (no look-ahead), producing its
conditional plans. Each plan is then followed forward over the configured
horizons (default 1h / 4h / 24h) and graded:

  WIN / PARTIAL_WIN / FULL_WIN : price hit TP1 (and TP2) before SL
  LOSS                         : price hit SL before TP1
  OPEN                         : neither level touched within the horizon
  NOT_TRIGGERED                : price never reached the plan's entry level
                                 (a conditional plan that never fired)

Output aggregates win-rate, average R:R and expectancy by plan type, by
confidence bucket and by action, per horizon. Results can be saved into the
signal database (`--save`) so the engine learns which setups actually work.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from config import BACKTEST_HORIZONS, BACKTEST_MIN_BARS, BACKTEST_STEP
from data.symbols import normalize_symbol
from data.binance_client import TIMEFRAME_TO_MS
from engine.signal_engine import analyze_frame

OUTCOMES = ("FULL_WIN", "PARTIAL_WIN", "LOSS", "OPEN", "NOT_TRIGGERED")
WIN_SET = {"FULL_WIN", "PARTIAL_WIN"}


@dataclass
class GradedPlan:
    ts: int
    plan_type: str
    action: str
    confidence_pct: int
    entry: Optional[float]
    trigger_level: Optional[float]
    horizon_hours: float
    outcome: str = "OPEN"
    rr_achieved: float = 0.0
    max_favorable: float = 0.0
    max_adverse: float = 0.0
    regime: str = ""   # market regime at signal time (decision B3)

    def as_row(self) -> dict:
        return {
            "ts": self.ts, "plan_type": self.plan_type, "action": self.action,
            "confidence_pct": self.confidence_pct, "entry": self.entry,
            "trigger_level": self.trigger_level, "horizon_hours": self.horizon_hours,
            "outcome": self.outcome, "rr_achieved": self.rr_achieved,
            "max_favorable": self.max_favorable, "max_adverse": self.max_adverse,
            "regime": self.regime,
        }


def _evaluate(plan: dict, df: pd.DataFrame, i: int, horizon_bars: int,
              regime: str = "") -> GradedPlan:
    """Grade one plan produced at bar i over `horizon_bars` future bars."""
    entry = plan.get("entry")
    sl = plan.get("stop_loss")
    tps = plan.get("take_profits") or []
    tp1 = tps[0] if len(tps) > 0 else None
    tp2 = tps[1] if len(tps) > 1 else None
    side = plan.get("action")
    is_buy = side == "BUY"

    gp = GradedPlan(
        ts=int(df.iloc[i]["ts"]), plan_type=plan.get("type", "?"),
        action=side or "?", confidence_pct=plan.get("confidence", 0),
        entry=entry, trigger_level=plan.get("trigger_level"),
        horizon_hours=round(horizon_bars * TIMEFRAME_TO_MS.get(
            df.attrs.get("timeframe", "15m"), 900_000) / 3_600_000, 2),
        regime=regime,
    )
    if entry is None or sl is None or tp1 is None:
        gp.outcome = "NOT_TRIGGERED"
        return gp

    risk = abs(entry - sl)
    end = min(len(df), i + 1 + horizon_bars)
    window = df.iloc[i + 1:end]
    if window.empty:
        gp.outcome = "OPEN"
        return gp

    highs = window["high"].to_numpy()
    lows = window["low"].to_numpy()

    # Did price ever trade at the entry level? Only conditional (waiting) plans
    # need the trigger — immediate plans execute at the current bar.
    conditional = plan.get("status") == "waiting" or plan.get("trigger_level") is not None
    touched_entry = True
    if conditional:
        touched_entry = ((lows <= entry) & (highs >= entry)).any()
    if not touched_entry:
        gp.outcome = "NOT_TRIGGERED"
        return gp

    # Order of SL vs TP1 touches
    sl_mask = (lows <= sl) if is_buy else (highs >= sl)
    tp1_mask = (highs >= tp1) if is_buy else (lows <= tp1)

    def first(idx_arr) -> Optional[int]:
        idx = int(idx_arr[0]) if idx_arr.size else None
        return idx if idx is not None and 0 <= idx < len(window) else None

    first_sl = first(np.flatnonzero(sl_mask))
    first_tp1 = first(np.flatnonzero(tp1_mask))

    if first_sl is not None and (first_tp1 is None or first_sl < first_tp1):
        gp.outcome = "LOSS"
        gp.rr_achieved = -1.0
    elif first_tp1 is not None:
        if tp2 is not None:
            if is_buy:
                tp2_hit = bool((highs[first_tp1:] >= tp2).any())
            else:
                tp2_hit = bool((lows[first_tp1:] <= tp2).any())
            if tp2_hit:
                gp.outcome = "FULL_WIN"
                gp.rr_achieved = round((tp2 - entry) / risk, 3) if risk else 0.0
            else:
                gp.outcome = "PARTIAL_WIN"
                gp.rr_achieved = round((tp1 - entry) / risk, 3) if risk else 0.0
        else:
            gp.outcome = "PARTIAL_WIN"
            gp.rr_achieved = round((tp1 - entry) / risk, 3) if risk else 0.0
    else:
        gp.outcome = "OPEN"

    if is_buy:
        gp.max_favorable = round(float(highs.max() - entry), 2)
        gp.max_adverse = round(float(entry - lows.min()), 2)
    else:
        gp.max_favorable = round(float(entry - lows.min()), 2)
        gp.max_adverse = round(float(highs.max() - entry), 2)
    return gp


def _aggregate(graded: list[GradedPlan]) -> dict:
    if not graded:
        return {"executed": 0, "wins": 0, "losses": 0, "opens": 0,
                "not_triggered": 0, "win_rate": None, "avg_rr": 0.0,
                "expectancy": 0.0}
    executed = [g for g in graded if g.outcome != "NOT_TRIGGERED"]
    wins = [g for g in executed if g.outcome in WIN_SET]
    losses = [g for g in executed if g.outcome == "LOSS"]
    opens = [g for g in executed if g.outcome == "OPEN"]
    decided = wins + losses
    win_rate = round(len(wins) / len(decided), 3) if decided else None
    rr_vals = [g.rr_achieved for g in decided]
    avg_rr = round(sum(rr_vals) / len(rr_vals), 3) if rr_vals else 0.0
    # expectancy: average R per decided trade (wins positive, losses -1R)
    exp = round(sum(g.rr_achieved for g in decided) / len(decided), 3) if decided else 0.0
    return {
        "executed": len(executed), "wins": len(wins), "losses": len(losses),
        "opens": len(opens), "not_triggered": len(graded) - len(executed),
        "win_rate": win_rate, "avg_rr": avg_rr, "expectancy": exp,
    }


def group_by(graded: list[GradedPlan], key_fn) -> dict:
    out: dict = {}
    for g in graded:
        k = key_fn(g)
        out.setdefault(k, []).append(g)
    return {k: _aggregate(v) for k, v in out.items()}


def run_backtest(df: pd.DataFrame, symbol: str = "BTCUSDT", timeframe: str = "15m",
                 horizons: Optional[list[float]] = None,
                 min_bars: int = BACKTEST_MIN_BARS,
                 step: int = BACKTEST_STEP,
                 min_confidence: int = 55) -> dict:
    """Walk the engine over history and grade every plan at every horizon."""
    symbol = normalize_symbol(symbol)
    horizons = horizons or BACKTEST_HORIZONS
    tf_ms = TIMEFRAME_TO_MS.get(timeframe, 900_000)
    df = df.reset_index(drop=True).copy()
    df.attrs["timeframe"] = timeframe
    df.attrs["symbol"] = symbol

    all_graded: list[GradedPlan] = []
    scans_done = 0
    plans_total = 0
    start = time.time()

    for i in range(min_bars, len(df), step):
        slice_df = df.iloc[: i + 1]
        out = analyze_frame(slice_df, symbol=symbol, timeframe=timeframe,
                            min_confidence=min_confidence)
        plans = out.plans
        if not plans:
            continue
        scans_done += 1
        plans_total += len(plans)
        regime = (out.features or {}).get("regime_name", "")
        for h in horizons:
            horizon_bars = max(1, int(h * 3_600_000 / tf_ms))
            for plan in plans:
                all_graded.append(_evaluate(plan, df, i, horizon_bars, regime=regime))

    report = {
        "meta": {
            "symbol": symbol, "timeframe": timeframe,
            "bars_total": len(df), "bars_analyzed": scans_done,
            "plans_generated": plans_total,
            "horizons_hours": horizons,
            "min_bars": min_bars, "step": step,
            "runtime_seconds": round(time.time() - start, 2),
            "engine_min_confidence": min_confidence,
        },
        "summary": _aggregate([g for g in all_graded if g.horizon_hours == horizons[0]]),
        "per_horizon": {
            str(h): _aggregate([g for g in all_graded if g.horizon_hours == h])
            for h in horizons
        },
        "by_type": group_by(all_graded, lambda g: g.plan_type),
        "by_action": group_by(all_graded, lambda g: g.action),
        "by_confidence": group_by(
            all_graded,
            lambda g: "HIGH" if g.confidence_pct >= 80
                      else "MEDIUM" if g.confidence_pct >= 60 else "LOW",
        ),
        "by_regime": group_by(all_graded, lambda g: g.regime or "UNKNOWN"),
    }
    return {"report": report, "graded": all_graded}


def save_report(report: dict, path: str = "data_samples/backtest_report.json") -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(report, indent=2, default=str))


def print_report(report: dict) -> None:
    meta, s = report["meta"], report["summary"]
    print("=" * 66)
    print(f"BACKTEST  {meta['symbol']} {meta['timeframe']}  "
          f"bars={meta['bars_total']} analyzed={meta['bars_analyzed']} "
          f"plans={meta['plans_generated']} ({meta['runtime_seconds']}s)")
    print(f"primary horizon {meta['horizons_hours'][0]}h — "
          f"executed {s['executed']} | wins {s['wins']} | losses {s['losses']} | "
          f"open {s['opens']} | not-triggered {s['not_triggered']}")
    wr = s["win_rate"]
    print(f"win-rate {f'{wr*100:.1f}%' if wr is not None else 'n/a'}  "
          f"avg R {s['avg_rr']}  expectancy {s['expectancy']}R/trade")
    print("-" * 66)
    for h, agg in report["per_horizon"].items():
        wr = agg["win_rate"]
        print(f"  {h}h: win {f'{wr*100:.1f}%' if wr is not None else 'n/a'}"
              f" ({agg['wins']}/{agg['wins']+agg['losses']})  avgR {agg['avg_rr']}  "
              f"exp {agg['expectancy']}  exec {agg['executed']}")
    print("-" * 66)
    print("by plan type:")
    for t, agg in sorted(report["by_type"].items(), key=lambda kv: -(kv[1]["executed"] or 0)):
        wr = agg["win_rate"]
        print(f"  {t:<24} exec {agg['executed']:>4}  win "
              f"{f'{wr*100:.1f}%' if wr is not None else 'n/a'}  "
              f"avgR {agg['avg_rr']}  exp {agg['expectancy']}")
    print("by confidence:")
    for c, agg in report["by_confidence"].items():
        wr = agg["win_rate"]
        print(f"  {c:<8} exec {agg['executed']:>4}  win "
              f"{f'{wr*100:.1f}%' if wr is not None else 'n/a'}  "
              f"avgR {agg['avg_rr']}  exp {agg['expectancy']}")
    print("by regime (decision B3 — strategy = setup × regime × asset):")
    for r, agg in sorted(report.get("by_regime", {}).items(),
                         key=lambda kv: -(kv[1]["executed"] or 0)):
        wr = agg["win_rate"]
        print(f"  {r:<22} exec {agg['executed']:>4}  win "
              f"{f'{wr*100:.1f}%' if wr is not None else 'n/a'}  "
              f"avgR {agg['avg_rr']}  exp {agg['expectancy']}")
