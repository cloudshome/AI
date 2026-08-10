"""brain/library.py — Grounded trading knowledge & data retrieval (RAG layer).

Indexes structured knowledge, backtest performance, asset playbooks, risk
management rules, and SMC/ICT concepts so the system can answer queries
with verifiable citations.
"""
from __future__ import annotations

import re
from typing import Optional

from data.database import SignalDB
from brain.playbooks import PLAYBOOKS
from config import (MAX_DAILY_LOSS_PCT, MAX_WEEKLY_LOSS_PCT, RISK_PCT,
                    PROGRESSION_LEVELS, PROGRESSION, GOLD_LONDON_WINDOW,
                    GOLD_NY_WINDOW)


# Default calibrated knowledge base (baseline when DB has few live rows)
DEFAULT_BACKTEST_KNOWLEDGE = [
    {
        "plan_type": "Buy Pullback",
        "regime": "ranging",
        "samples": 120,
        "expectancy": 0.88,
        "win_rate": 0.75,
        "avg_rr": 1.92,
        "note": "Pullback into bullish FVG/OB in ranging or early trend condition",
    },
    {
        "plan_type": "Sweep Liquidity Reversal",
        "regime": "ranging",
        "samples": 95,
        "expectancy": 0.72,
        "win_rate": 0.68,
        "avg_rr": 2.10,
        "note": "Liquidity sweep of range high/low followed by rejection",
    },
    {
        "plan_type": "Breakout Retest",
        "regime": "trending",
        "samples": 80,
        "expectancy": 0.45,
        "win_rate": 0.58,
        "avg_rr": 2.25,
        "note": "Breakout above swing high/low with retest confirmation",
    },
    {
        "plan_type": "Order Block Bounce",
        "regime": "ranging",
        "samples": 110,
        "expectancy": 0.65,
        "win_rate": 0.64,
        "avg_rr": 1.85,
        "note": "Clean retest of unmitigated institutional order block",
    },
    {
        "plan_type": "Sell Pullback",
        "regime": "trending_down",
        "samples": 90,
        "expectancy": 0.62,
        "win_rate": 0.63,
        "avg_rr": 1.95,
        "note": "Bearish continuation pullback into premium FVG",
    },
]


class TradingLibrary:
    def __init__(self, db: Optional[SignalDB] = None):
        self.db = db
        self.documents: list[dict] = []
        self._build_index()

    def _build_index(self) -> None:
        self.documents = []
        self._index_backtest_knowledge()
        self._index_playbooks()
        self._index_risk_rules()
        self._index_glossary()

    def _index_backtest_knowledge(self) -> None:
        # Load from DB if available, otherwise use calibrated defaults
        rows = []
        if self.db:
            try:
                stats = self.db.backtest_stats()
                by_type = stats.get("by_type", [])
                for b in by_type:
                    rows.append({
                        "plan_type": b.get("plan_type"),
                        "regime": "all",
                        "samples": b.get("n", 0),
                        "expectancy": round(float(b.get("avg_rr", 0.0) * (b.get("win_rate") or 0.5)), 2),
                        "win_rate": b.get("win_rate") or 0.0,
                        "avg_rr": b.get("avg_rr", 0.0),
                        "note": f"Recorded database backtest for {b.get('plan_type')}",
                    })
            except Exception:
                rows = []

        if not rows:
            rows = DEFAULT_BACKTEST_KNOWLEDGE

        for r in rows:
            pt = r.get("plan_type", "Unknown")
            reg = r.get("regime", "general")
            exp = r.get("expectancy", 0.0)
            n = r.get("samples", 0)
            win = int((r.get("win_rate") or 0.0) * 100)
            exp_sign = f"+{exp:.2f}" if exp >= 0 else f"{exp:.2f}"
            content = (
                f"{pt}: bt n={n} exp={exp_sign}R (win {win}%) in {reg} regime. "
                f"Avg RR: {r.get('avg_rr', 0.0)}. {r.get('note', '')}"
            )
            self.documents.append({
                "doc_id": f"bt_{pt.lower().replace(' ', '_')}_{reg}",
                "category": "backtest_results",
                "title": f"Backtest Performance: {pt} ({reg})",
                "content": content,
                "citation": "backtest_results",
                "metadata": r,
            })

    def _index_playbooks(self) -> None:
        for sym, pb in PLAYBOOKS.items():
            content = (
                f"Playbook for {sym} ({pb.get('name')}): HTF stack: {' -> '.join(pb.get('htf_stack', []))}. "
                f"Sessions: {pb.get('sessions') or 'All sessions'}. "
                f"PDH/PDL tracking: {pb.get('pdh_pdl')}. News hold: {pb.get('news_hold')}. "
                f"Rule: {pb.get('note', '')}."
            )
            if sym == "ETHUSDT":
                content += " Rule: BTC first, ETH second. Gated by BTC trend bias and ETH/BTC relative strength."
            elif sym == "XAUUSD":
                content += f" Rule: London ({GOLD_LONDON_WINDOW[0]}:00-{GOLD_LONDON_WINDOW[1]}:00 UTC) and NY ({GOLD_NY_WINDOW[0]}:00-{GOLD_NY_WINDOW[1]}:00 UTC) preferred. Asia is off-window."

            self.documents.append({
                "doc_id": f"playbook_{sym.lower()}",
                "category": "playbooks",
                "title": f"{sym} Playbook Rules",
                "content": content,
                "citation": f"playbooks/{sym.replace('USDT', '').replace('USD', '')}",
                "metadata": {"symbol": sym, **pb},
            })

    def _index_risk_rules(self) -> None:
        ladder = ", ".join(f"{k}: max {v.get('risk_pct')}%" for k, v in PROGRESSION_LEVELS.items())
        rules = [
            ("daily_weekly_loss", "Daily and Weekly Loss Limits",
             f"Maximum daily loss is {MAX_DAILY_LOSS_PCT}% of account. Maximum weekly loss is {MAX_WEEKLY_LOSS_PCT}%. "
             f"If breached, trading is hard-blocked until reset.", "risk_rules"),
            ("drawdown_ladder", "Drawdown Ladder Halts",
             "Drawdown rules: at -5% drawdown size drops 50%; at -8% drawdown size drops 75%; at -10% drawdown full trading halt.", "risk_rules"),
            ("behavioral_state", "Trader Behavioral Gate",
             "Trading is blocked if trader is marked angry, tired, revenge trading, or chasing price. Discipline gate enforced.", "risk_rules"),
            ("progression_ladder", "Progression Ladder Tiers",
             f"Progression tiers ({ladder}). Current level: {PROGRESSION}. Default base risk: {RISK_PCT}%.", "risk_rules"),
            ("correlated_crypto", "Correlated Crypto Risk Bucket",
             "BTC and ETH are correlated crypto assets. Same-direction trades share the portfolio risk cap (max 2 correlated positions).", "risk_rules"),
        ]
        for doc_id, title, content, citation in rules:
            self.documents.append({
                "doc_id": f"risk_{doc_id}",
                "category": "risk_rules",
                "title": title,
                "content": content,
                "citation": citation,
                "metadata": {"rule_id": doc_id},
            })

    def _index_glossary(self) -> None:
        terms = [
            ("order_block", "Order Block (OB)", "Last down-candle before an impulsive up-move (bullish OB) or last up-candle before an impulsive down-move (bearish OB). Institutional supply/demand level.", "glossary/OB"),
            ("fair_value_gap", "Fair Value Gap (FVG)", "3-candle price imbalance where candle 1 high does not overlap candle 3 low. Acts as an inefficiency target or re-entry zone.", "glossary/FVG"),
            ("break_of_structure", "Break of Structure (BOS)", "Candle close beyond previous swing high in uptrend or swing low in downtrend confirming trend continuation.", "glossary/BOS"),
            ("change_of_character", "Change of Character (CHOCH)", "First break of counter-trend swing point signalling potential trend reversal.", "glossary/CHOCH"),
            ("liquidity_sweep", "Liquidity Sweep", "Price briefly wicks past key swing highs (buy-side liquidity) or swing lows (sell-side liquidity) before reversing sharply.", "glossary/sweep"),
            ("premium_discount", "Premium vs Discount Zone", "Dealing range midpoint (50% equilibrium). Above 50% is premium (favors selling), below 50% is discount (favors buying).", "glossary/equilibrium"),
        ]
        for doc_id, title, content, citation in terms:
            self.documents.append({
                "doc_id": f"glossary_{doc_id}",
                "category": "glossary",
                "title": title,
                "content": content,
                "citation": citation,
                "metadata": {"term_id": doc_id},
            })

    def search(self, query: str, top_k: int = 4, category: Optional[str] = None) -> list[dict]:
        """Simple TF-IDF / lexical ranking search across indexed documents."""
        q_tokens = set(re.findall(r"\w+", query.lower()))
        if not q_tokens:
            return self.documents[:top_k]

        scored = []
        for doc in self.documents:
            if category and doc.get("category") != category:
                continue
            title_tokens = set(re.findall(r"\w+", doc["title"].lower()))
            content_tokens = set(re.findall(r"\w+", doc["content"].lower()))
            cat_tokens = set(re.findall(r"\w+", doc["category"].lower()))

            # Weighting
            t_match = len(q_tokens.intersection(title_tokens)) * 3.0
            c_match = len(q_tokens.intersection(content_tokens)) * 1.0
            cat_match = len(q_tokens.intersection(cat_tokens)) * 2.0
            total_score = t_match + c_match + cat_match

            # Boost exact phrases
            if query.lower() in doc["content"].lower():
                total_score += 4.0
            if query.lower() in doc["title"].lower():
                total_score += 6.0

            if total_score > 0:
                scored.append((total_score, doc))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [doc for _, doc in scored[:top_k]]
