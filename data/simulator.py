"""data/simulator.py — the paper-sample grind (decisions A6 / B10).

The professional ladder says a setup is PROVEN only after:

    >= 100 unique backtest samples          (CALIBRATE_MIN_N)
    >=  20 unique decided paper samples     (CALIBRATE_MIN_PAPER_N)
    positive expectancy (average R > 0)

`python main.py simulator` grinds those samples: it walks history with the
same engine, grades every plan forward (exactly like the backtester), stores
each unique graded sample in the learning store, and additionally turns the
best plan of every window into a fully-simulated *paper trade* (created and
closed on the spot) so the paper half of the proof is also satisfied.

Honesty rules:

  * every sample gets a ``sim_key`` (symbol:tf:window-ts:plan:action) and is
    inserted only once — re-running over the same history adds zero samples,
    so the 100/20 counts are UNIQUE samples, not row counts;
  * outcomes come from real forward bars (SL/TP touch logic), never random;
  * simulator samples are stored in the same tables as live backtests/paper
    trades and are graded by the same calibration code.

Offline (DEMO_MODE=1) this works on the committed BTC sample + deterministic
synthetic series, which is exactly how the grind runs in this sandbox.
"""
from __future__ import annotations

import time

from config import (SYMBOLS, TIMEFRAME, BARS, MIN_CONFIDENCE, BACKTEST_HORIZONS,
                    BACKTEST_MIN_BARS, CALIBRATE_MIN_N, CALIBRATE_MIN_PAPER_N,
                    PRIMARY_SETUP_FAMILY, PRIMARY_FAMILIES)
from data.symbols import parse_symbol_list
from data.sample_client import maybe_client
from data.backtester import _evaluate, GradedPlan
from engine.signal_engine import analyze_frame


WIN_OUTCOME_TO_PAPER = {
    "FULL_WIN": "TP_HIT",
    "PARTIAL_WIN": "TP_HIT",   # first target hit = a decided win at that target
    "LOSS": "STOP_LOSS",
}
_PAPER_OUTCOMES = set(WIN_OUTCOME_TO_PAPER)


def _bt_key(symbol: str, tf: str, plan: dict, ts: int, horizon: float) -> str:
    return f"bt:{symbol}:{tf}:{ts}:{plan.get('type', '?')}:{plan.get('action', '?')}:{horizon}"


def _pp_key(symbol: str, tf: str, plan: dict, ts: int) -> str:
    return f"pp:{symbol}:{tf}:{ts}:{plan.get('type', '?')}:{plan.get('action', '?')}"


def _walk_symbol(symbol: str, timeframe: str, bars: int, step: int,
                 min_confidence: int, horizons: list[float],
                 df, db, run_id: str, seen_bt: set[str], seen_pp: set[str],
                 save: bool) -> dict:
    """One symbol: walk history, grade plans, store unique samples.

    Returns counts added to the learning store (backtest + paper samples).
    """
    from data.database import SignalDB
    tf_ms = df.attrs.get("timeframe", timeframe)
    from data.binance_client import TIMEFRAME_TO_MS
    ms = TIMEFRAME_TO_MS.get(tf_ms, 900_000)

    bt_added = 0
    pp_added = 0
    bt_rows: list[dict] = []
    paper_samples: list[dict] = []  # (plan, graded) ready to persist
    plans_total = 0

    for i in range(BACKTEST_MIN_BARS, len(df), step):
        slice_df = df.iloc[: i + 1]
        out = analyze_frame(slice_df, symbol=symbol, timeframe=timeframe,
                            min_confidence=min_confidence)
        plans = out.plans
        if not plans:
            continue
        plans_total += len(plans)
        regime = (out.features or {}).get("regime_name", "")

        best_plan = None
        for h in horizons:
            horizon_bars = max(1, int(h * 3_600_000 / ms))
            for plan in plans:
                graded: GradedPlan = _evaluate(plan, df, i, horizon_bars, regime=regime)
                if graded.outcome not in ("FULL_WIN", "PARTIAL_WIN", "LOSS"):
                    continue
                row = graded.as_row()
                row["symbol"] = symbol
                row["timeframe"] = timeframe
                row["sim_key"] = _bt_key(symbol, timeframe, plan, graded.ts, h)
                if row["sim_key"] in seen_bt:
                    continue
                seen_bt.add(row["sim_key"])
                bt_rows.append(row)
                bt_added += 1
                # Track the strongest plan of this window (first horizon) as
                # the candidate paper sample.
                if h == horizons[0] and (best_plan is None
                                         or (plan.get("confidence") or 0) >
                                         (best_plan[0].get("confidence") or 0)):
                    best_plan = (plan, graded)

        # One decided paper sample per window: the highest-confidence plan.
        if best_plan is not None:
            plan, graded = best_plan
            outcome = WIN_OUTCOME_TO_PAPER.get(graded.outcome)
            key = _pp_key(symbol, timeframe, plan, graded.ts)
            if outcome is not None and key not in seen_pp:
                seen_pp.add(key)
                paper_samples.append({
                    "plan": plan, "graded": graded, "outcome": outcome,
                    "sim_key": key, "regime": regime,
                })
                pp_added += 1

    if save and bt_rows:
        with SignalDB() as sdb:
            sdb.save_backtest_rows(bt_rows, run_id)
    if save and paper_samples:
        _persist_paper_samples(symbol, timeframe, paper_samples, db)

    return {"symbol": symbol, "plans_generated": plans_total,
            "backtest_added": bt_added, "paper_added": pp_added}


def _persist_paper_samples(symbol: str, timeframe: str, samples: list[dict],
                           db) -> None:
    """Create + immediately close one simulated paper trade per sample."""
    from data.database import SignalDB
    ts = time.time()
    with SignalDB() as sdb:
        for s in samples:
            plan = s["plan"]
            graded = s["graded"]
            entry = plan.get("entry")
            sl = plan.get("stop_loss")
            tps = plan.get("take_profits") or []
            tp = tps[0] if tps else None
            if entry is None or sl is None or tp is None:
                continue
            action = plan.get("action")
            risk = abs(float(entry) - float(sl)) or 1.0
            rr = plan.get("risk_reward")
            try:
                rr = float(rr) if rr is not None else round(
                    abs(float(tp) - float(entry)) / risk, 3)
            except (TypeError, ValueError):
                rr = round(abs(float(tp) - float(entry)) / risk, 3)
            payload = {
                "signal": {
                    "signal_id": f"{symbol}_{graded.ts}_{plan.get('id', 'sim')}",
                    "timestamp": graded.ts, "asset": symbol,
                    "timeframe": timeframe, "action": action,
                    "entry": entry, "stop_loss": sl, "take_profit": tp,
                    "risk_reward": rr, "confidence": "HIGH" if graded.confidence_pct >= 80
                    else "MEDIUM" if graded.confidence_pct >= 60 else "LOW",
                    "confidence_pct": graded.confidence_pct,
                    "reason": "simulator paper sample",
                    "signal_type": plan.get("type"),
                },
                "plans": [plan],
                "snapshot": {"features": {"price": entry, "regime_name": s["regime"],
                                          "score_used": graded.confidence_pct}},
                "market_context": {},
            }
            scan_id = sdb.save_scan(payload, status_override="APPROVED")
            fields = {
                "scan_id": scan_id,
                "signal_id": payload["signal"]["signal_id"],
                "plan_id": plan.get("id", "signal"),
                "plan_type": plan.get("type", "Signal"),
                "symbol": symbol, "timeframe": timeframe, "action": action,
                "entry": entry, "stop_loss": sl, "take_profit": tp,
                "risk_reward": rr, "confidence_pct": graded.confidence_pct,
                "status": "OPEN", "created_ts": graded.ts,
                "opened_ts": graded.ts, "entry_price": entry,
                "regime": s["regime"], "sim_key": s["sim_key"],
            }
            trade, created = sdb.create_paper_trade(fields)
            if not created:
                continue
            exit_price = sl if s["outcome"] == "STOP_LOSS" else tp
            sdb.close_paper_trade(trade["id"], s["outcome"], float(exit_price),
                                  float(graded.rr_achieved), "simulator",
                                  graded.ts)


def simulate_round(symbols: list[str] | None = None, timeframe: str = TIMEFRAME,
                   bars: int = BARS, step: int = 3, min_confidence: int = MIN_CONFIDENCE,
                   horizons: list[float] | None = None, save: bool = True,
                   client=None, db=None) -> dict:
    """One grind round: walk history for every symbol, store unique samples."""
    from data.database import SignalDB
    symbols = parse_symbol_list(symbols)
    horizons = horizons or BACKTEST_HORIZONS
    client = client or maybe_client()
    run_id = f"sim_{time.strftime('%Y%m%d_%H%M%S')}"
    own_db = db is None
    with SignalDB() as sdb:
        seen_bt = sdb.sim_keys("backtest_results")
        seen_pp = sdb.sim_keys("paper_trades")
        results = []
        for symbol in symbols:
            try:
                df = client.klines(symbol, timeframe, bars)
                results.append(_walk_symbol(
                    symbol, timeframe, bars, step, min_confidence, horizons,
                    df, sdb, run_id, seen_bt, seen_pp, save))
            except Exception as exc:
                results.append({"symbol": symbol, "error": f"{type(exc).__name__}: {exc}",
                                "backtest_added": 0, "paper_added": 0})
    total_bt = sum(r.get("backtest_added", 0) for r in results)
    total_pp = sum(r.get("paper_added", 0) for r in results)
    return {"run_id": run_id, "symbols": results,
            "backtest_added": total_bt, "paper_added": total_pp,
            "saved": save, "horizons": horizons, "timeframe": timeframe}


def paper_progress(db) -> list[dict]:
    """Per-plan-type learning progress: unique backtest + paper samples.

    Counts are honest because the simulator dedupes by sim_key at insert time
    (re-runs over the same history add zero rows) and real backtest/paper
    rows (sim_key IS NULL) are unique by construction.
    """
    bt = db.conn.execute(
        """SELECT plan_type,
                  SUM(CASE WHEN sim_key IS NULL THEN 1 ELSE 0 END) + COUNT(DISTINCT sim_key) n,
                  SUM(CASE WHEN outcome IN ('FULL_WIN','PARTIAL_WIN') THEN 1 ELSE 0 END) wins,
                  SUM(CASE WHEN outcome='LOSS' THEN 1 ELSE 0 END) losses,
                  ROUND(AVG(rr_achieved), 3) expectancy,
                  ROUND(SUM(CASE WHEN outcome IN ('FULL_WIN','PARTIAL_WIN')
                                 THEN COALESCE(rr_achieved,0) ELSE 0 END), 3) win_r,
                  ROUND(SUM(CASE WHEN outcome='LOSS'
                                 THEN ABS(COALESCE(rr_achieved,0)) ELSE 0 END), 3) loss_r
           FROM backtest_results
           WHERE outcome IN ('FULL_WIN','PARTIAL_WIN','LOSS')
           GROUP BY plan_type ORDER BY n DESC""").fetchall()
    pp = db.conn.execute(
        """SELECT plan_type,
                  SUM(CASE WHEN sim_key IS NULL THEN 1 ELSE 0 END) + COUNT(DISTINCT sim_key) n,
                  SUM(CASE WHEN outcome='TP_HIT' THEN 1 ELSE 0 END) wins,
                  SUM(CASE WHEN outcome='STOP_LOSS' THEN 1 ELSE 0 END) losses,
                  ROUND(AVG(rr_achieved), 3) expectancy,
                  ROUND(SUM(CASE WHEN outcome='TP_HIT'
                                 THEN COALESCE(rr_achieved,0) ELSE 0 END), 3) win_r,
                  ROUND(SUM(CASE WHEN outcome='STOP_LOSS'
                                 THEN ABS(COALESCE(rr_achieved,0)) ELSE 0 END), 3) loss_r
           FROM paper_trades
           WHERE outcome IN ('TP_HIT','STOP_LOSS')
           GROUP BY plan_type ORDER BY n DESC""").fetchall()
    bt_map = {r["plan_type"]: dict(r) for r in bt}
    pp_map = {r["plan_type"]: dict(r) for r in pp}
    plan_types = sorted(set(bt_map) | set(pp_map))
    progress = []
    for pt in plan_types:
        b = bt_map.get(pt, {})
        p = pp_map.get(pt, {})
        b_n, p_n = b.get("n") or 0, p.get("n") or 0
        expectancy = ((b.get("expectancy") or 0.0) * b_n + (p.get("expectancy") or 0.0) * p_n) \
            / (b_n + p_n) if (b_n + p_n) else 0.0
        wins = (b.get("wins") or 0) + (p.get("wins") or 0)
        losses = (b.get("losses") or 0) + (p.get("losses") or 0)
        win_r = (b.get("win_r") or 0.0) + (p.get("win_r") or 0.0)
        loss_r = (b.get("loss_r") or 0.0) + (p.get("loss_r") or 0.0)
        decided = wins + losses
        progress.append({
            "plan_type": pt,
            "backtest_n": b_n,
            "paper_n": p_n,
            "backtest_target": CALIBRATE_MIN_N,
            "paper_target": CALIBRATE_MIN_PAPER_N,
            "n": b_n + p_n,
            "wins": wins,
            "losses": losses,
            "win_rate": round(wins / decided, 3) if decided else None,
            "pf": round(win_r / loss_r, 3) if loss_r > 0 else None,
            "win_r": round(win_r, 3),
            "loss_r": round(loss_r, 3),
            "expectancy": round(expectancy, 3),
            "proven": b_n >= CALIBRATE_MIN_N and p_n >= CALIBRATE_MIN_PAPER_N
                      and expectancy > 0,
        })
    return progress


def primary_plan_types() -> list[str]:
    """The setup family that must be proven before PROGRESSION=micro (A1)."""
    family = PRIMARY_FAMILIES.get(PRIMARY_SETUP_FAMILY)
    return sorted(family or ())


def grind_verdict(progress: list[dict]) -> dict:
    """Is the primary setup family proven enough for PROGRESSION=micro?"""
    primary = primary_plan_types()
    by_type = {p["plan_type"]: p for p in progress}
    needed = []
    ready = True
    for pt in primary:
        p = by_type.get(pt)
        if p is None or not p["proven"]:
            ready = False
            if p is None:
                needed.append({"plan_type": pt, "backtest_n": 0, "paper_n": 0,
                               "missing": "no samples yet"})
            else:
                parts = []
                if p["backtest_n"] < CALIBRATE_MIN_N:
                    parts.append("backtest")
                if p["paper_n"] < CALIBRATE_MIN_PAPER_N:
                    parts.append("paper")
                if p["expectancy"] <= 0:
                    parts.append("positive expectancy")
                needed.append({
                    "plan_type": pt, "backtest_n": p["backtest_n"],
                    "paper_n": p["paper_n"], "expectancy": p["expectancy"],
                    "missing": ", ".join(parts) or "all targets met",
                })
    return {"ready": ready, "primary_setup_family": PRIMARY_SETUP_FAMILY,
            "primary_plan_types": primary, "missing": needed,
            "targets": {"backtest": CALIBRATE_MIN_N, "paper": CALIBRATE_MIN_PAPER_N}}


# ── graduation gate (BLUEPRINT Step 2 → Step 3) ──────────────────────────
# The master blueprint promotes the desk to PROGRESSION=micro (real, small
# capital) only when the primary setup family shows a real statistical edge:
#   expectancy >= +0.50R per trade
#   win rate   >  55%
#   profit factor >= 1.5
#   rule compliance >= 90%  (from the journal)
# combined with the 100-backtest / 20-paper sample proof of grind_verdict.
GRADUATION_CRITERIA: dict[str, float] = {
    "expectancy": 0.50,   # average R per decided trade (backtest + paper)
    "win_rate": 0.55,     # fraction of decided trades that won
    "pf": 1.5,            # gross win R / gross loss R
    "compliance": 0.90,   # journal: 1 - violation_rate
}


def graduation_status(progress: list[dict], compliance: float | None = None) -> dict:
    """Blueprint graduation gate: is the desk ready for PROGRESSION=micro?

    Aggregates every decided sample (backtest + paper, deduped) across the
    primary setup family and checks the four blueprint criteria. `compliance`
    is 1 - journal violation_rate (None until real journal entries exist —
    an unproven criterion, never a pass).
    """
    primary = primary_plan_types()
    rows = [p for p in progress if p["plan_type"] in primary]
    n = sum(p["n"] for p in rows)
    wins = sum(p["wins"] for p in rows)
    losses = sum(p["losses"] for p in rows)
    win_r = sum(p.get("win_r") or 0.0 for p in rows)
    loss_r = sum(p.get("loss_r") or 0.0 for p in rows)
    decided = wins + losses
    expectancy = round((win_r - loss_r) / n, 3) if n else 0.0
    win_rate = round(wins / decided, 3) if decided else None
    pf = round(win_r / loss_r, 3) if loss_r > 0 else None
    stats = {"plan_types": primary, "n": n, "decided": decided,
             "expectancy": expectancy, "win_rate": win_rate, "pf": pf,
             "compliance": compliance}
    met = {
        "expectancy": expectancy >= GRADUATION_CRITERIA["expectancy"],
        "win_rate": win_rate is not None and win_rate > GRADUATION_CRITERIA["win_rate"],
        "pf": (pf is not None and pf >= GRADUATION_CRITERIA["pf"])
              or (pf is None and loss_r == 0 and win_r > 0),
        "compliance": compliance is not None
                      and compliance >= GRADUATION_CRITERIA["compliance"],
    }
    samples_ok = grind_verdict(progress)["ready"]
    return {"ready": bool(samples_ok and all(met.values())),
            "samples_proven": bool(samples_ok),
            "criteria": dict(GRADUATION_CRITERIA),
            "stats": stats, "met": met}


def format_graduation(g: dict) -> str:
    """Human-readable graduation gate block (simulator + `agent ask`)."""
    s = g["stats"]
    c = g["criteria"]
    lines = ["=" * 70, "GRADUATION GATE — ready for PROGRESSION=micro? "
                       "(BLUEPRINT Step 2 → 3)", "-" * 70]
    lines.append(f"  primary family : {', '.join(s['plan_types']) or 'none'} "
                 f"({s['n']} decided samples)")
    rows = [
        ("expectancy", f"{s['expectancy']:+.3f}R", "≥ +0.50R", g["met"]["expectancy"]),
        ("win rate", f"{s['win_rate'] * 100:.1f}%" if s["win_rate"] is not None
         else "n/a", "> 55%", g["met"]["win_rate"]),
        ("profit factor", f"{s['pf']:.2f}" if s["pf"] is not None else "n/a",
         "≥ 1.50", g["met"]["pf"]),
        ("rule compliance", f"{s['compliance'] * 100:.1f}%" if s["compliance"] is not None
         else "no journal entries yet", "≥ 90%", g["met"]["compliance"]),
    ]
    for name, value, target, ok in rows:
        mark = "✓" if ok else "✗"
        lines.append(f"  {mark} {name:<16}{value:<22}{target}")
    lines.append("-" * 70)
    proof = ("✓ 100 backtest + 20 paper per primary setup" if g["samples_proven"]
             else "✗ grind more history first")
    lines.append(f"  sample proof  : {proof}")
    if g["ready"]:
        lines.append("  ✅ GRADUATED — you may set PROGRESSION=micro in .env "
                     "and trade small live size (0.5% risk/trade, -1.5% daily stop).")
    else:
        lines.append("  ⏳ not graduated — keep paper-trading; re-run "
                     "`python main.py simulator` as new bars arrive, "
                     "journal every trade.")
    return "\n".join(lines)


def format_progress(progress: list[dict], verdict: dict) -> str:
    lines = ["=" * 70, "SIMULATOR GRIND — unique learning samples per setup",
             "-" * 70,
             f"  {'PLAN TYPE':<26}{'BACKTEST':>10}{'PAPER':>8}{'EXPECT':>10}  PROVEN"]
    for p in progress:
        mark = "✓" if p["proven"] else "·"
        lines.append(f"  {p['plan_type']:<26}{p['backtest_n']:>6}/{p['backtest_target']}"
                     f"{p['paper_n']:>5}/{p['paper_target']}{p['expectancy']:>+9.3f}R  {mark}")
    lines.append("-" * 70)
    lines.append(f"  primary setup family: {verdict['primary_setup_family']} "
                 f"({', '.join(verdict['primary_plan_types']) or 'none'})")
    if verdict["ready"]:
        lines.append("  ✅ PRIMARY SETUPS PROVEN — you may set PROGRESSION=micro "
                     "in .env (paper-trade 100–200 samples on small real capital).")
    else:
        lines.append("  ⏳ not proven yet:")
        for m in verdict["missing"]:
            lines.append(f"     {m['plan_type']}: missing {m['missing']} "
                         f"(backtest {m['backtest_n']}/{CALIBRATE_MIN_N}, "
                         f"paper {m['paper_n']}/{CALIBRATE_MIN_PAPER_N})")
        lines.append("     re-run `python main.py simulator` on live data as "
                     "new bars arrive, or use a longer window (--bars / --step 1).")
    return "\n".join(lines)
