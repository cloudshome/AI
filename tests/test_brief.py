"""tests/test_brief.py — Unit tests for post-trade review & morning brief."""
import pytest
from brain.brief import post_trade_review, generate_morning_brief
from data.database import SignalDB


def test_post_trade_review_not_found(tmp_path):
    db_path = tmp_path / "test.db"
    with SignalDB(db_path) as db:
        rev = post_trade_review(999, db=db)
        assert rev["scan_id"] == 999
        assert "NOT FOUND" in rev["headline"]


def test_post_trade_review_populated(tmp_path):
    db_path = tmp_path / "test.db"
    with SignalDB(db_path) as db:
        # Create scan
        cur = db.conn.execute(
            "INSERT INTO scans(signal_id, symbol, timeframe, action, price, status) VALUES(?,?,?,?,?,?)",
            ("sig_100", "BTCUSDT", "15m", "BUY", 65000.0, "EXECUTED")
        )
        scan_id = cur.lastrowid

        # Create paper trade
        db.conn.execute(
            """INSERT INTO paper_trades(scan_id, signal_id, symbol, timeframe, action,
                                        status, outcome, rr_achieved, mae, mfe)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (scan_id, "sig_100", "BTCUSDT", "15m", "BUY", "CLOSED", "TP_HIT", 1.5, 30.0, 1200.0)
        )

        # Create journal entry
        db.conn.execute(
            "INSERT INTO journal_entries(scan_id, followed_rules, emotion, notes) VALUES(?,?,?,?)",
            (scan_id, 1, "calm", "Followed 15m entry rules perfectly")
        )
        db.conn.commit()

        rev = post_trade_review(scan_id, db=db)
        assert rev["outcome"] == "TP_HIT"
        assert rev["rr_achieved"] == 1.5
        assert rev["followed_rules"] == "YES"
        assert "TP_HIT · 1.5R · MAE 30.0 · MFE 1200.0 · Followed rules: YES" in rev["headline"]
        assert len(rev["takeaways"]) > 0


def test_generate_morning_brief(tmp_path):
    db_path = tmp_path / "test.db"
    with SignalDB(db_path) as db:
        brief = generate_morning_brief(symbols=["BTCUSDT", "ETHUSDT", "XAUUSD"], db=db)
        assert "overall_bias" in brief
        assert "biases" in brief
        assert "BTCUSDT" in brief["biases"]
        assert "XAUUSD" in brief["biases"]
