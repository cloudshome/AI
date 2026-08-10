"""Offline tests for the approved-signal paper-trading runner."""
from __future__ import annotations

import time

import pandas as pd
import pytest

from brain.calibrator import compute_expectancy_by_type
from data.database import SignalDB
from data.paper_trading import (
    OUTCOME_STOP,
    OUTCOME_TARGET,
    PAPER_CLOSED,
    PAPER_OPEN,
    PAPER_WAITING,
    PaperTradingRunner,
    exit_event,
)
from tests.test_database import _payload


NOW = int(time.time() * 1000)


class CandleClient:
    """Tiny in-memory Binance stand-in; accepts the new cursor arguments."""

    def __init__(self, candles: list[dict]):
        self.frame = pd.DataFrame(candles)
        self.calls: list[dict] = []

    def klines(self, symbol="BTCUSDT", timeframe="15m", limit=500,
               start_time=None, end_time=None):
        self.calls.append({"symbol": symbol, "timeframe": timeframe,
                           "limit": limit, "start_time": start_time})
        return self.frame.copy()


def _candle(ts: int, *, low: float, high: float, close: float | None = None) -> dict:
    close = close if close is not None else (low + high) / 2
    return {"ts": ts, "open": close, "high": high, "low": low,
            "close": close, "volume": 100.0}


def _approve(db: SignalDB, payload: dict) -> int:
    sid = db.save_scan(payload)
    assert db.update_status(sid, "APPROVED", note="paper test") == "APPROVED"
    # Make deterministic candles line up with the approval moment rather than
    # the test suite's actual SQLite write timestamp.
    db.conn.execute("UPDATE scans SET lifecycle_ts=? WHERE id=?", (NOW, sid))
    db.conn.commit()
    return sid


def _waiting_payload() -> dict:
    payload = _payload()
    payload["signal"].update({
        "signal_id": "BTCUSDT_waiting", "entry": 100.0, "stop_loss": 95.0,
        "take_profit": 110.0, "risk_reward": 2.0,
    })
    payload["plans"] = [{
        "id": "buy_pullback", "type": "Buy Pullback", "action": "BUY",
        "condition": "wait for a pullback", "trigger_level": 100.0,
        "entry": 100.0, "stop_loss": 95.0, "take_profits": [110.0],
        "risk_reward": 2.0, "confidence": 84, "confidence_label": "HIGH",
        "status": "waiting",
    }]
    payload["snapshot"]["features"]["price"] = 103.0
    return payload


def test_empty_paper_stats_are_zeroed_for_cli_and_dashboard(tmp_path):
    db = SignalDB(tmp_path / "paper.db")
    overall = db.paper_trade_stats()["overall"]
    assert overall == {
        "n": 0, "waiting": 0, "open": 0, "closed": 0, "cancelled": 0,
        "wins": 0, "losses": 0, "win_rate": None, "avg_rr": 0.0,
    }
    db.close()


def test_immediate_approved_trade_auto_closes_at_target(tmp_path):
    db = SignalDB(tmp_path / "paper.db")
    sid = _approve(db, _payload())
    # The active top plan fills on approval. This later candle reaches only
    # TP1, so the result must be a paper win and lifecycle CLOSED.
    client = CandleClient([_candle(NOW + 1, low=61_000.0, high=62_300.0, close=62_200.0)])
    result = PaperTradingRunner(db, client, clock_ms=lambda: NOW, enforce_gate=False).run_once()

    trade = db.paper_trade_for_scan(sid)
    assert result.enrolled == 1
    assert result.opened == 1
    assert result.closed == 1
    assert trade["status"] == PAPER_CLOSED
    assert trade["outcome"] == OUTCOME_TARGET
    assert trade["rr_achieved"] > 0
    assert db.get_scan(sid)["status"] == "CLOSED"
    history = db.decision_history(sid)
    assert [h["to_state"] for h in history] == ["APPROVED", "EXECUTED", "CLOSED"]
    assert history[-1]["reviewer"] == "paper_runner"
    db.close()


def test_waiting_trade_enters_then_closes_on_same_candle(tmp_path):
    db = SignalDB(tmp_path / "paper.db")
    sid = _approve(db, _waiting_payload())
    # Low reaches the planned pullback entry; high then reaches TP. OHLCV
    # cannot give exact intrabar ordering, but only TP is touched after entry.
    client = CandleClient([_candle(NOW + 1, low=99.5, high=111.0, close=109.0)])
    result = PaperTradingRunner(db, client, clock_ms=lambda: NOW, enforce_gate=False).run_once()

    trade = db.paper_trade_for_scan(sid)
    assert result.enrolled == 1
    assert result.opened == 1
    assert result.closed == 1
    assert trade["status"] == PAPER_CLOSED
    assert trade["outcome"] == OUTCOME_TARGET
    assert trade["plan_type"] == "Buy Pullback"
    assert db.get_scan(sid)["status"] == "CLOSED"
    db.close()


def test_waiting_trade_stays_approved_until_entry_is_reached(tmp_path):
    db = SignalDB(tmp_path / "paper.db")
    sid = _approve(db, _waiting_payload())
    # Candle never reaches 100.0, so the scan must remain human-approved, not
    # falsely marked executed.
    client = CandleClient([_candle(NOW + 1, low=101.0, high=106.0, close=104.0)])
    result = PaperTradingRunner(db, client, clock_ms=lambda: NOW, enforce_gate=False).run_once()

    trade = db.paper_trade_for_scan(sid)
    assert result.enrolled == 1
    assert result.opened == 0 and result.closed == 0
    assert trade["status"] == PAPER_WAITING
    assert db.get_scan(sid)["status"] == "APPROVED"
    db.close()


def test_both_levels_in_one_candle_is_conservatively_a_stop():
    trade = {"action": "BUY", "entry": 100.0, "stop_loss": 95.0, "take_profit": 110.0}
    event = exit_event(trade, _candle(NOW, low=94.0, high=111.0))
    assert event is not None
    assert event["outcome"] == OUTCOME_STOP
    assert event["rr_achieved"] == -1.0
    assert event["ambiguous"] is True
    assert "conservative" in event["reason"]


def test_already_executed_conditional_scan_is_treated_as_open(tmp_path):
    db = SignalDB(tmp_path / "paper.db")
    sid = _approve(db, _waiting_payload())
    assert db.update_status(sid, "EXECUTED", note="manual fill") == "EXECUTED"
    # The monitor must honour the existing fill rather than re-waiting for it.
    client = CandleClient([_candle(NOW + 1, low=101.0, high=106.0, close=104.0)])
    result = PaperTradingRunner(db, client, clock_ms=lambda: NOW, enforce_gate=False).run_once()

    trade = db.paper_trade_for_scan(sid)
    assert result.enrolled == 1
    assert trade["status"] == PAPER_OPEN
    assert db.get_scan(sid)["status"] == "EXECUTED"
    db.close()


def test_paper_outcomes_feed_the_calibrator_but_not_backtest_table(tmp_path):
    db = SignalDB(tmp_path / "paper.db")
    sid = _approve(db, _waiting_payload())
    client = CandleClient([_candle(NOW + 1, low=99.5, high=111.0, close=109.0)])
    PaperTradingRunner(db, client, clock_ms=lambda: NOW, enforce_gate=False).run_once()

    learned = compute_expectancy_by_type(db)
    entry = learned["Buy Pullback"]
    assert entry["n"] == 1
    assert entry["paper_samples"] == 1
    assert entry["backtest_samples"] == 0
    assert entry["expectancy"] == pytest.approx(2.0)
    # Keep historical dashboard stats honest: paper outcomes have their own
    # table/card and do not masquerade as a walk-forward backtest.
    assert db.backtest_stats()["overall"]["n"] == 0
    stats = db.paper_trade_stats()
    assert stats["overall"]["wins"] == 1
    assert stats["overall"]["win_rate"] == 1.0
    db.close()


def test_runner_gate_blocks_unproven_enrollment(tmp_path):
    """Decisions B6/B10: at student level the runner refuses to enroll an
    unproven setup until the gate opens."""
    from tests.test_database import _payload
    db = SignalDB(tmp_path / "gate.db")
    sid = _approve(db, _payload())
    client = CandleClient([_candle(NOW + 1, low=99.5, high=111.0, close=109.0)])
    result = PaperTradingRunner(db, client, clock_ms=lambda: NOW).run_once()
    assert result.enrolled == 0
    assert any(e["event"] == "GATE_BLOCKED" for e in result.events)
    assert db.paper_trade_for_scan(sid) is None
    db.close()
