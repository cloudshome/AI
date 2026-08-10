"""brain/ask.py — RAG query interface with grounded citations.

Answers trading, risk, playbook, and performance questions by retrieving
relevant knowledge chunks from TradingLibrary and citing the primary sources.
"""
from __future__ import annotations

import re
from typing import Optional

from data.database import SignalDB
from brain.library import TradingLibrary


def ask(query: str, top_k: int = 3, db: Optional[SignalDB] = None,
        use_llm: bool = False) -> dict:
    """Answer a user query using grounded RAG retrieval and source citations."""
    lib = TradingLibrary(db=db)
    docs = lib.search(query, top_k=top_k)

    if not docs:
        return {
            "query": query,
            "answer": "No directly matching information found in the trading library.",
            "citations": [],
            "sources": [],
        }

    citations = list(dict.fromkeys(d.get("citation", "knowledge_base") for d in docs))
    q_low = query.lower()

    # Domain-specific synthesis for precise answers
    lines = []
    if "expectancy" in q_low or "ranging" in q_low or "backtest" in q_low or "win rate" in q_low or "setup" in q_low:
        bt_docs = [d for d in docs if d.get("category") == "backtest_results"]
        if bt_docs:
            for d in bt_docs:
                m = d.get("metadata", {})
                pt = m.get("plan_type", "Setup")
                exp = m.get("expectancy", 0.0)
                exp_s = f"+{exp:.2f}" if exp >= 0 else f"{exp:.2f}"
                n = m.get("samples", 0)
                win = int((m.get("win_rate") or 0.0) * 100)
                lines.append(f"{pt}: bt n={n} exp={exp_s}R (win {win}%) — cited: {d['citation']}")
        else:
            for d in docs:
                lines.append(f"{d['content']} — cited: {d['citation']}")

    elif "eth" in q_low or "btc" in q_low or "gold" in q_low or "playbook" in q_low:
        for d in docs:
            lines.append(f"{d['content']} — cited: {d['citation']}")

    elif "risk" in q_low or "loss" in q_low or "drawdown" in q_low or "ladder" in q_low:
        for d in docs:
            lines.append(f"{d['content']} — cited: {d['citation']}")

    else:
        for d in docs:
            lines.append(f"{d['content']} — cited: {d['citation']}")

    answer = "\n".join(lines)

    if use_llm:
        from ai.llm_brain import LLMBrain
        brain = LLMBrain()
        if brain.enabled:
            llm_prompt = (
                f"You are the trading intelligence assistant. Answer the user question concisely "
                f"based ONLY on the retrieved facts below. Always include citations.\n\n"
                f"Question: {query}\n\nFacts:\n{answer}\n"
            )
            llm_ans = brain.complete(llm_prompt)
            if llm_ans:
                answer = llm_ans.strip()

    return {
        "query": query,
        "answer": answer,
        "citations": citations,
        "sources": [{"title": d["title"], "citation": d["citation"], "category": d["category"]}
                    for d in docs],
    }
