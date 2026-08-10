"""tests/test_agents.py — Unit tests for autonomous desk agents and agent runs store."""
import pytest
from brain.agents import (MorningBriefAgent, WatchdogAgent, PaperReviewerAgent,
                          WeeklyReviewAgent, run_agent, run_all_agents)
from data.database import SignalDB


def test_agent_runs_db_recording(tmp_path):
    db_path = tmp_path / "test.db"
    with SignalDB(db_path) as db:
        run_id = db.record_agent_run("test_agent", "COMPLETED", summary="Test summary", payload={"k": "v"})
        assert run_id > 0
        latest = db.latest_agent_runs(limit=5)
        assert len(latest) == 1
        assert latest[0]["agent"] == "test_agent"
        assert latest[0]["status"] == "COMPLETED"
        assert latest[0]["payload"]["k"] == "v"


def test_morning_brief_agent(tmp_path):
    db_path = tmp_path / "test.db"
    with SignalDB(db_path) as db:
        agent = MorningBriefAgent(db=db)
        res = agent.run()
        assert res["agent"] == "morning_brief"
        assert res["status"] == "COMPLETED"
        assert "data" in res


def test_watchdog_agent(tmp_path):
    db_path = tmp_path / "test.db"
    with SignalDB(db_path) as db:
        agent = WatchdogAgent(db=db)
        res = agent.run()
        assert res["agent"] == "watchdog"
        assert res["status"] in ("COMPLETED", "WARN")


def test_paper_reviewer_agent(tmp_path):
    db_path = tmp_path / "test.db"
    with SignalDB(db_path) as db:
        agent = PaperReviewerAgent(db=db)
        res = agent.run()
        assert res["agent"] == "paper_reviewer"
        assert res["status"] == "COMPLETED"


def test_weekly_review_agent(tmp_path):
    db_path = tmp_path / "test.db"
    with SignalDB(db_path) as db:
        agent = WeeklyReviewAgent(db=db)
        res = agent.run()
        assert res["agent"] == "weekly_review"
        assert res["status"] == "COMPLETED"


def test_run_agent_dispatcher(tmp_path):
    db_path = tmp_path / "test.db"
    with SignalDB(db_path) as db:
        res1 = run_agent("morning", db=db)
        assert res1["status"] == "COMPLETED"
        res2 = run_agent("unknown_agent_xyz", db=db)
        assert res2["status"] == "ERROR"


def test_run_all_agents(tmp_path):
    db_path = tmp_path / "test.db"
    with SignalDB(db_path) as db:
        all_res = run_all_agents(db=db)
        assert all_res["status"] == "COMPLETED"
        assert len(all_res["agents"]) == 4
