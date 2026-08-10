"""brain/agents.py — Autonomous desk agents (Morning brief, Watchdog, Paper reviewer, Weekly review).

Runs periodic, specialized desk workflows and logs all actions into the SQLite
`agent_runs` store.
"""
from __future__ import annotations

import json
import time
from typing import Optional

from data.database import SignalDB
from brain.brief import generate_morning_brief, post_trade_review
from brain.risk_gate import evaluate_risk_gate
from brain.metrics import compute_business_metrics


class BaseAgent:
    name: str = "base"

    def __init__(self, db: Optional[SignalDB] = None):
        self.db = db

    def run(self) -> dict:
        raise NotImplementedError


class MorningBriefAgent(BaseAgent):
    name = "morning_brief"

    def run(self) -> dict:
        brief = generate_morning_brief(db=self.db)
        summary = f"{brief.get('bias_summary', 'Neutral')} — {brief.get('risk_summary', '')}"
        if brief.get("notes"):
            summary += f" ({'; '.join(brief['notes'])})"

        if self.db:
            self.db.record_agent_run(self.name, "COMPLETED", summary=summary, payload=brief)

        return {
            "agent": self.name,
            "status": "COMPLETED",
            "summary": summary,
            "data": brief,
            "ts": int(time.time()),
        }


class WatchdogAgent(BaseAgent):
    name = "watchdog"

    def run(self) -> dict:
        close_db = False
        db = self.db
        if db is None:
            db = SignalDB()
            close_db = True

        try:
            # Check open paper trades and risk gate
            open_trades = db.conn.execute(
                "SELECT * FROM paper_trades WHERE status IN ('WAITING_ENTRY', 'OPEN')").fetchall()
            rg = evaluate_risk_gate(db)

            issues = []
            if not rg.get("open"):
                issues.append("Risk gate closed: " + "; ".join(rg.get("blocked_by", [])))

            stale_count = 0
            now = int(time.time())
            for t in open_trades:
                created = t["created_ts"] or now
                if t["status"] == "WAITING_ENTRY" and (now - created) > 48 * 3600:
                    stale_count += 1

            if stale_count > 0:
                issues.append(f"{stale_count} stale pending trades awaiting entry >48h")

            status = "WARN" if issues else "COMPLETED"
            summary = f"Monitored {len(open_trades)} active paper trades. Risk gate: {'OPEN' if rg.get('open') else 'HALTED'}."
            if issues:
                summary += f" Alerts: {'; '.join(issues)}"

            payload = {
                "active_trades_count": len(open_trades),
                "stale_trades_count": stale_count,
                "risk_gate_open": rg.get("open"),
                "issues": issues,
            }

            db.record_agent_run(self.name, status, summary=summary, payload=payload)

            return {
                "agent": self.name,
                "status": status,
                "summary": summary,
                "data": payload,
                "ts": int(time.time()),
            }
        finally:
            if close_db:
                db.close()


class PaperReviewerAgent(BaseAgent):
    name = "paper_reviewer"

    def run(self) -> dict:
        close_db = False
        db = self.db
        if db is None:
            db = SignalDB()
            close_db = True

        try:
            # Query recent closed trades
            closed = db.conn.execute(
                "SELECT * FROM paper_trades WHERE status='CLOSED' ORDER BY closed_ts DESC LIMIT 10").fetchall()

            reviews = []
            for r in closed:
                scan_id = r["scan_id"]
                rev = post_trade_review(scan_id, db=db)
                reviews.append({
                    "scan_id": scan_id,
                    "symbol": r["symbol"],
                    "outcome": r["outcome"],
                    "rr_achieved": r["rr_achieved"],
                    "headline": rev.get("headline"),
                })

            summary = f"Reviewed {len(reviews)} closed paper trades."
            if reviews:
                wins = sum(1 for r in reviews if r["outcome"] in ("WIN", "FULL_WIN", "TP_HIT"))
                summary += f" Win rate: {wins}/{len(reviews)} ({int(wins/len(reviews)*100)}%)."

            payload = {"reviewed_count": len(reviews), "reviews": reviews}
            db.record_agent_run(self.name, "COMPLETED", summary=summary, payload=payload)

            return {
                "agent": self.name,
                "status": "COMPLETED",
                "summary": summary,
                "data": payload,
                "ts": int(time.time()),
            }
        finally:
            if close_db:
                db.close()


class WeeklyReviewAgent(BaseAgent):
    name = "weekly_review"

    def run(self) -> dict:
        close_db = False
        db = self.db
        if db is None:
            db = SignalDB()
            close_db = True

        try:
            metrics = compute_business_metrics(db=db)
            o = metrics.get("overall", {})
            e = metrics.get("execution", {})
            pf = o.get("profit_factor") or 0.0
            n_trades = o.get("n", 0)
            viol_rate = e.get("violation_rate", 0.0)
            rule_rate = int((1.0 - viol_rate) * 100) if viol_rate is not None else 100

            change_note = "Expectancy & discipline stable."
            if rule_rate < 80:
                change_note = "Warning: Rule compliance below 80% threshold."

            summary = (f"Weekly review: {n_trades} trades, PF {pf:.2f}, "
                       f"Rule compliance {rule_rate}%. {change_note}")

            payload = {"metrics": metrics, "change_detection": change_note}
            db.record_agent_run(self.name, "COMPLETED", summary=summary, payload=payload)

            return {
                "agent": self.name,
                "status": "COMPLETED",
                "summary": summary,
                "data": payload,
                "ts": int(time.time()),
            }
        finally:
            if close_db:
                db.close()


AGENTS = {
    "morning_brief": MorningBriefAgent,
    "morning": MorningBriefAgent,
    "watchdog": WatchdogAgent,
    "paper_reviewer": PaperReviewerAgent,
    "paper_review": PaperReviewerAgent,
    "weekly_review": WeeklyReviewAgent,
    "weekly": WeeklyReviewAgent,
}


def run_agent(name: str, db: Optional[SignalDB] = None) -> dict:
    """Run an agent by name."""
    cls = AGENTS.get(name.lower().replace("-", "_"))
    if not cls:
        return {
            "agent": name,
            "status": "ERROR",
            "summary": f"Unknown agent '{name}'. Available: {', '.join(sorted(set(AGENTS.keys())))}",
            "ts": int(time.time()),
        }
    return cls(db=db).run()


def run_all_agents(db: Optional[SignalDB] = None) -> dict:
    """Run all core desk agents."""
    results = {}
    for name, cls in (("morning_brief", MorningBriefAgent),
                      ("watchdog", WatchdogAgent),
                      ("paper_reviewer", PaperReviewerAgent),
                      ("weekly_review", WeeklyReviewAgent)):
        results[name] = cls(db=db).run()
    return {
        "status": "COMPLETED",
        "agents": results,
        "ts": int(time.time()),
    }
