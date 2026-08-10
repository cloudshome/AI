"""Tests for the SQLite signal database (offline, tmp_path)."""
from __future__ import annotations

from data.database import SignalDB


def _payload() -> dict:
    return {
        "signal": {
            "signal_id": "BTCUSDT_20260804_1452", "timestamp": 1785826521000,
            "asset": "BTCUSDT", "action": "BUY", "entry": 61250.0,
            "stop_loss": 60700.0, "take_profit": 62200.0, "risk_reward": 2.1,
            "confidence": "HIGH", "timeframe": "15m", "reason": "test",
            "signal_type": "SIGNAL",
        },
        "plans": [
            {"id": "imm_buy", "type": "Immediate Buy", "action": "BUY",
             "condition": "enter now", "trigger_level": None, "entry": 61250.0,
             "stop_loss": 60700.0, "take_profits": [62200.0, 63200.0],
             "risk_reward": 2.1, "confidence": 90, "confidence_label": "HIGH",
             "status": "active"},
            {"id": "buy_pullback", "type": "Buy Pullback", "action": "BUY",
             "condition": "pullback to OB", "trigger_level": 61000.0,
             "entry": 61000.0, "stop_loss": 60600.0, "take_profits": [61900.0],
             "risk_reward": 2.0, "confidence": 84, "confidence_label": "HIGH",
             "status": "waiting"},
        ],
        "snapshot": {"features": {"price": 61250.0, "trend": "bullish"}},
        "market_context": {"funding_rate_pct": 0.01},
    }


def test_save_and_read_scan(tmp_path):
    db = SignalDB(tmp_path / "test.db")
    scan_id = db.save_scan(_payload())
    assert scan_id > 0
    latest = db.latest_scans(limit=10)
    assert len(latest) == 1
    assert latest[0]["symbol"] == "BTCUSDT"
    assert latest[0]["action"] == "BUY"
    db.close()


def test_plans_persisted(tmp_path):
    db = SignalDB(tmp_path / "test.db")
    db.save_scan(_payload())
    rows = db.conn.execute("SELECT * FROM plans").fetchall()
    assert len(rows) == 2
    types = {r["type"] for r in rows}
    assert types == {"Immediate Buy", "Buy Pullback"}
    db.close()


def test_plan_stats(tmp_path):
    db = SignalDB(tmp_path / "test.db")
    db.save_scan(_payload())
    stats = db.plan_stats()
    assert len(stats) == 2
    by_type = {s["type"]: s for s in stats}
    assert by_type["Immediate Buy"]["n"] == 1
    assert by_type["Immediate Buy"]["avg_conf"] == 90.0
    db.close()


def test_db_wal_and_busy_timeout_enabled(tmp_path):
    """Concurrent dashboard threads write to the same DB; WAL + busy timeout
    must be on so periodic 'database is locked' crashes never happen."""
    db = SignalDB(tmp_path / "t.db")
    mode = db.conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"
    busy = db.conn.execute("PRAGMA busy_timeout").fetchone()[0]
    assert busy >= 15000
    db.close()


def test_db_can_be_written_from_multiple_threads(tmp_path):
    """The dashboard's background threads each open their OWN SignalDB (like
    the real code does). WAL + busy_timeout must make concurrent writes safe —
    no 'database is locked' errors."""
    import threading
    errors = []

    def worker():
        try:
            with SignalDB(tmp_path / "t.db") as db:
                db.save_scan(_payload())
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []


def test_backtest_stats_empty_db_does_not_crash(tmp_path):
    """Regression: a fresh/empty DB used to crash backtest_stats with
    'None + None' (SUM returns NULL over an empty table)."""
    db = SignalDB(tmp_path / "t.db")
    stats = db.backtest_stats()
    o = stats["overall"]
    assert o["n"] == 0
    assert o["wins"] == 0 and o["losses"] == 0
    assert o["win_rate"] is None
    assert o["avg_rr"] == 0.0
    assert stats["by_type"] == []
    assert stats["by_confidence"] == []
    db.close()


def test_backtest_rows_and_stats(tmp_path):
    db = SignalDB(tmp_path / "test.db")
    rows = [
        {"ts": 1, "symbol": "BTCUSDT", "timeframe": "15m", "plan_type": "Immediate Buy",
         "action": "BUY", "confidence_pct": 85, "horizon_hours": 4.0,
         "outcome": "FULL_WIN", "rr_achieved": 2.0, "max_favorable": 300.0,
         "max_adverse": -50.0, "entry": 60000.0, "trigger_level": None},
        {"ts": 2, "symbol": "BTCUSDT", "timeframe": "15m", "plan_type": "Immediate Buy",
         "action": "BUY", "confidence_pct": 70, "horizon_hours": 4.0,
         "outcome": "LOSS", "rr_achieved": -1.0, "max_favorable": 20.0,
         "max_adverse": -400.0, "entry": 60500.0, "trigger_level": None},
    ]
    n = db.save_backtest_rows(rows, run_id="test_run")
    assert n == 2
    stats = db.backtest_stats()
    assert stats["overall"]["n"] == 2
    assert stats["overall"]["win_rate"] == 0.5
    assert stats["by_type"][0]["plan_type"] == "Immediate Buy"
    # confidence buckets: 85 -> HIGH, 70 -> MEDIUM
    buckets = {b["bucket"]: b for b in stats["by_confidence"]}
    assert buckets["HIGH"]["win_rate"] == 1.0
    assert buckets["MEDIUM"]["win_rate"] == 0.0
    db.close()


def test_decided_paper_rows_exclude_sim(tmp_path):
    """exclude_sim=True drops simulator walk-forward samples (sim_key NOT
    NULL): they are calibration evidence, not the live paper book."""
    import time
    db = SignalDB(tmp_path / "ex.db")
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
            (9000 + i, f"S{i}", "Buy Pullback", "BTCUSDT", "15m", "BUY",
             100.0, 99.0, 102.0, 2.0, 70, "CLOSED", ts, ts, ts,
             100.0, 102.0 if rr > 0 else 99.0, outcome, rr, "test", sim_key))
    db.conn.commit()

    insert(2.0, sim_key=None, i=0)                                  # real trade
    insert(-1.0, sim_key="pp:btc:15m:1:buy_pullback:BUY", i=1)      # sim sample

    assert len(db.decided_paper_rows()) == 2
    real = db.decided_paper_rows(exclude_sim=True)
    assert len(real) == 1
    assert real[0]["rr_achieved"] == 2.0
    assert real[0]["sim_key"] is None
    db.close()
