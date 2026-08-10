"""tests/test_library_ask.py — Unit tests for RAG library indexing and query answering."""
import pytest
from brain.library import TradingLibrary
from brain.ask import ask
from data.database import SignalDB


def test_trading_library_indexing(tmp_path):
    db_path = tmp_path / "test.db"
    with SignalDB(db_path) as db:
        lib = TradingLibrary(db=db)
        assert len(lib.documents) > 0
        categories = {d["category"] for d in lib.documents}
        assert "backtest_results" in categories
        assert "playbooks" in categories
        assert "risk_rules" in categories
        assert "glossary" in categories


def test_trading_library_search(tmp_path):
    db_path = tmp_path / "test.db"
    with SignalDB(db_path) as db:
        lib = TradingLibrary(db=db)
        docs = lib.search("ranging positive expectancy", top_k=2)
        assert len(docs) > 0
        assert docs[0]["citation"] == "backtest_results"


def test_ask_expectancy_ranging(tmp_path):
    db_path = tmp_path / "test.db"
    with SignalDB(db_path) as db:
        res = ask("which setups have positive expectancy in ranging markets?", db=db)
        assert "Buy Pullback" in res["answer"]
        assert "exp=+0.88R" in res["answer"]
        assert "backtest_results" in res["citations"]


def test_ask_playbook_eth(tmp_path):
    db_path = tmp_path / "test.db"
    with SignalDB(db_path) as db:
        res = ask("what is the ETH playbook rule?", db=db)
        assert "BTC first, ETH second" in res["answer"]
        assert "playbooks/ETH" in res["citations"]


def test_ask_risk_rules(tmp_path):
    db_path = tmp_path / "test.db"
    with SignalDB(db_path) as db:
        res = ask("what are the daily and weekly loss limits?", db=db)
        assert "daily loss" in res["answer"].lower()
        assert "weekly loss" in res["answer"].lower()
        assert "risk_rules" in res["citations"]


def test_ask_empty_fallback(tmp_path):
    db_path = tmp_path / "test.db"
    with SignalDB(db_path) as db:
        res = ask("zzzz non existent concept xyqwk 999", db=db)
        assert "answer" in res
