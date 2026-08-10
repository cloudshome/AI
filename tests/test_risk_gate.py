"""Tests for the enforced risk & discipline gate (decisions B6, B7, B9, B10)."""
from __future__ import annotations

import time

import pytest

import brain.risk_gate as rg
from data.database import SignalDB


def _decided(db: SignalDB, rr: float, n: int = 1, start_id: int = 9000,
             closed_ts: int | None = None) -> None:
    """Insert decided paper trades (TP_HIT/STOP_LOSS) directly."""
    ts = closed_ts or int(time.time() * 1000)
    for i in range(n):
        outcome = "TP_HIT" if rr > 0 else "STOP_LOSS"
        db.conn.execute(
            """INSERT INTO paper_trades(scan_id, signal_id, plan_type, symbol, timeframe,
                                        action, entry, stop_loss, take_profit, risk_reward,
                                        confidence_pct, status, created_ts, opened_ts,
                                        closed_ts, entry_price, exit_price, outcome,
                                        rr_achieved, close_reason)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (start_id + i, f"SIG{start_id+i}", "Buy Pullback", "BTCUSDT", "15m",
             "BUY", 100.0, 99.0, 102.0, 2.0, 70, "CLOSED", ts, ts, ts,
             100.0, 99.0 if rr < 0 else 102.0, outcome, rr, "test"))
    db.conn.commit()


def test_progression_levels():
    assert rg.progression()["level"] == "student"
    assert rg.progression()["risk_pct"] == 0.25
    assert rg.progression()["approve_unproven"] is False


def test_trader_state_blocks(tmp_path):
    """Decision B7: angry/tired/revenge/chasing close the gate."""
    db = SignalDB(tmp_path / "g.db")
    assert rg.evaluate(db)["allowed"] is True
    db.set_trader_state(angry=True, note="lost my daily limit, tilted")
    g = rg.evaluate(db)
    assert g["allowed"] is False
    assert any("trader state" in b for b in g["blocked_by"])
    assert "angry" in g["blocked_by"][0]
    db.set_trader_state(angry=False)
    assert rg.evaluate(db)["allowed"] is True
    db.close()


def test_daily_loss_limit_blocks(tmp_path):
    """Decision B6: hitting the daily loss limit halts new trades."""
    db = SignalDB(tmp_path / "g.db")
    risk = rg.effective_risk()
    # 4 losses of -1R at 0.25% risk = -1.0% today → daily limit (1.0%) reached
    _decided(db, -1.0, n=4)
    g = rg.evaluate(db)
    assert g["allowed"] is False
    assert any("daily loss limit" in b for b in g["blocked_by"])
    db.close()


def test_weekly_loss_limit_blocks(tmp_path):
    db = SignalDB(tmp_path / "g.db")
    risk = rg.effective_risk()
    # 9 losses = -2.25% this week → weekly limit (2.0%) reached
    _decided(db, -1.0, n=9)
    g = rg.evaluate(db)
    assert g["allowed"] is False
    assert any("weekly loss limit" in b for b in g["blocked_by"])
    db.close()


def test_drawdown_ladder(tmp_path):
    """Decision B6: -5% reduce, -8% stop & review, -10% full review."""
    db = SignalDB(tmp_path / "g.db")
    risk = rg.effective_risk()
    # 34 losses of -1R at 0.25% = -8.5% drawdown → stop & review
    _decided(db, -1.0, n=34)
    g = rg.evaluate(db)
    assert g["details"]["drawdown"]["level"] == "stop_and_review"
    assert g["allowed"] is False
    db.close()


def test_unproven_setup_blocked_at_student(tmp_path):
    """Decision B10: unproven setups are research-only at student level."""
    db = SignalDB(tmp_path / "g.db")
    g = rg.evaluate(db, symbol="BTCUSDT", plan_type="Buy Pullback", action="BUY")
    assert g["allowed"] is False
    assert any("unproven" in b for b in g["blocked_by"])
    db.close()


def test_setup_proven_requires_100_backtest_and_20_paper(tmp_path):
    db = SignalDB(tmp_path / "g.db")
    # 99 backtests + 19 paper → still unproven
    rows = [{"ts": i, "symbol": "BTCUSDT", "timeframe": "15m",
             "plan_type": "Buy Pullback", "action": "BUY", "confidence_pct": 80,
             "horizon_hours": 4.0, "outcome": "FULL_WIN", "rr_achieved": 2.0,
             "max_favorable": 1.0, "max_adverse": -1.0, "entry": 100.0,
             "trigger_level": None, "regime": "TRENDING_BULL"}
            for i in range(99)]
    db.save_backtest_rows(rows, run_id="proven")
    _decided(db, 2.0, n=19, start_id=5000)
    assert rg.setup_proven(db, "Buy Pullback")["proven"] is False
    # one more of each → proven (positive expectancy both sides)
    db.save_backtest_rows([{"ts": 999, "symbol": "BTCUSDT", "timeframe": "15m",
                            "plan_type": "Buy Pullback", "action": "BUY",
                            "confidence_pct": 80, "horizon_hours": 4.0,
                            "outcome": "FULL_WIN", "rr_achieved": 2.0,
                            "max_favorable": 1.0, "max_adverse": -1.0,
                            "entry": 100.0, "trigger_level": None,
                            "regime": "TRENDING_BULL"}], run_id="proven2")
    _decided(db, 2.0, n=1, start_id=5090)
    st = rg.setup_proven(db, "Buy Pullback")
    assert st["backtest_n"] >= 100 and st["paper_n"] >= 20
    assert st["proven"] is True
    db.close()


def test_gate_message(tmp_path):
    db = SignalDB(tmp_path / "g.db")
    db.set_trader_state(revenge=True)
    g = rg.evaluate(db)
    msg = rg.gate_message(g)
    assert "CLOSED" in msg and "revenge" in msg
    db.close()


def test_drawdown_and_daily_exclude_simulator_samples(tmp_path):
    """Simulator walk-forward rows are historical calibration evidence, not
    the live book: 200 losing sim samples must not trip the drawdown ladder
    or today's loss limit — but one REAL losing trade still moves the gate."""
    db = SignalDB(tmp_path / "sim_gate.db")
    ts = int(time.time() * 1000)

    def insert(rr: float, sim_key, i: int) -> None:
        outcome = "TP_HIT" if rr > 0 else "STOP_LOSS"
        db.conn.execute(
            """INSERT INTO paper_trades(scan_id, signal_id, plan_type, symbol, timeframe,
                                        action, entry, stop_loss, take_profit, risk_reward,
                                        confidence_pct, status, created_ts, opened_ts,
                                        closed_ts, entry_price, exit_price, outcome,
                                        rr_achieved, close_reason, sim_key)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (8000 + i, f"S{i}", "Sweep Reversal Buy", "BTCUSDT", "15m", "BUY",
             100.0, 99.0, 102.0, 2.0, 70, "CLOSED", ts, ts, ts,
             100.0, 99.0, outcome, rr, "test", sim_key))
    db.conn.commit()

    for i in range(200):                                    # 200 losing sim samples
        insert(-1.0, sim_key=f"pp:sim:{i}", i=i)
    d = rg.drawdown(db)
    assert d["level"] == "normal"
    assert d["max_drawdown_pct"] == 0.0
    assert rg.daily_weekly(db)["today"]["n"] == 0

    insert(-1.0, sim_key=None, i=500)                       # one REAL loss
    d2 = rg.drawdown(db)
    assert d2["max_drawdown_pct"] > 0.0
    assert rg.daily_weekly(db)["today"]["n"] == 1
    db.close()
