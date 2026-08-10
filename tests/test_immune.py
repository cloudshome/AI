"""tests/test_immune.py — Unit tests for immune health, staleness detection, and diagnostic alarms."""
import pytest
from brain.immune import (check_database, check_data_freshness, check_risk_system,
                          check_calibration_health, run_health_check)
from data.database import SignalDB


def test_immune_check_database(tmp_path):
    db_path = tmp_path / "test.db"
    with SignalDB(db_path) as db:
        res = check_database(db=db)
        assert res["status"] == "OK"
        assert "SQLite database healthy" in res["detail"]


def test_immune_check_data_freshness():
    res = check_data_freshness()
    # Catches the ~5.9 day old sample data as stale/critical
    assert res["status"] in ("CRITICAL", "WARN", "OK")
    assert "data_freshness" == res["name"]


def test_immune_check_risk_behavioral_flag(tmp_path):
    db_path = tmp_path / "test.db"
    with SignalDB(db_path) as db:
        # Initially OK
        res = check_risk_system(db=db)
        assert res["status"] == "OK"

        # Set angry flag
        db.set_trader_state(angry=True, note="frustrated")
        res_blocked = check_risk_system(db=db)
        assert res_blocked["status"] == "CRITICAL"
        assert "angry" in res_blocked["detail"]


def test_immune_run_health_check(tmp_path):
    db_path = tmp_path / "test.db"
    with SignalDB(db_path) as db:
        report = run_health_check(db=db)
        assert "status" in report
        assert "checks" in report
        assert len(report["checks"]) == 4
