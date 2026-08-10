"""Tests for the portfolio/correlation risk engine (decision B2)."""
from __future__ import annotations

from data.database import SignalDB
from brain.portfolio import portfolio_veto, bucket_state


def _open_trade(db: SignalDB, symbol: str, action: str, status: str = "OPEN",
                scan_id: int = 1000) -> int:
    """Insert an open paper trade directly (fast, deterministic)."""
    cur = db.conn.execute(
        """INSERT INTO paper_trades(scan_id, signal_id, plan_type, symbol, timeframe,
                                    action, entry, stop_loss, take_profit, risk_reward,
                                    confidence_pct, status, created_ts, opened_ts, entry_price)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (scan_id, f"SIG{scan_id}", "Buy Pullback", symbol, "15m", action,
         100.0, 99.0, 102.0, 2.0, 70, status, 1_780_000_000_000,
         1_780_000_000_001, 100.0))
    db.conn.commit()
    return cur.lastrowid


def test_same_direction_duplicate_vetoed(tmp_path):
    """Long BTC + long ETH = correlated risk, not two edges."""
    db = SignalDB(tmp_path / "p.db")
    _open_trade(db, "BTCUSDT", "BUY")
    res = portfolio_veto(db, "ETHUSDT", "BUY")
    assert res["allowed"] is False
    assert any("correlated" in r for r in res["reasons"])
    assert res["exposure"]["bucket"] == "crypto"
    assert res["exposure"]["n_open"] == 1
    db.close()


def test_opposite_direction_allowed(tmp_path):
    db = SignalDB(tmp_path / "p.db")
    _open_trade(db, "BTCUSDT", "BUY")
    res = portfolio_veto(db, "ETHUSDT", "SELL")
    assert res["allowed"] is True
    db.close()


def test_bucket_cap(tmp_path):
    """The crypto bucket has a max number of concurrent positions."""
    db = SignalDB(tmp_path / "p.db")
    _open_trade(db, "BTCUSDT", "BUY", scan_id=1)
    _open_trade(db, "ETHUSDT", "BUY", scan_id=2)
    res = portfolio_veto(db, "BTCUSDT", "SELL")
    assert res["allowed"] is False
    assert any("full" in r for r in res["reasons"])
    db.close()


def test_gold_is_its_own_bucket(tmp_path):
    """Gold exposure does not count against the crypto bucket."""
    db = SignalDB(tmp_path / "p.db")
    _open_trade(db, "XAUUSD", "BUY", scan_id=3)
    res = portfolio_veto(db, "BTCUSDT", "BUY")
    assert res["allowed"] is True
    assert res["exposure"]["bucket"] == "crypto"
    assert res["exposure"]["n_open"] == 0
    g = bucket_state(db, "XAUUSD")
    assert g.market == "gold"
    db.close()
