"""ai/llm_brain.py

Optional LLM "narrative brain": turns the numeric snapshot + signals into a
plain-English briefing the way a human analyst would write it.

Providers (LLM_PROVIDER):
  off     -> disabled (default). The engine runs fully without an LLM.
  openai  -> any OpenAI-compatible endpoint (works with OpenAI, Groq, Together…)
  gemini  -> Google Gemini generateContent
  auto    -> openai first, gemini fallback

No key? The brain degrades to a deterministic rule-based narrative built from
the scored conditions, so the output is never empty.
"""
from __future__ import annotations

import json
from typing import Optional

import requests

from config import (LLM_PROVIDER, OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL,
                      GEMINI_API_KEY, GEMINI_MODEL)


def _rule_based_narrative(payload: dict) -> str:
    sig = payload.get("signal", {})
    snap = payload.get("snapshot", {})
    feats = snap.get("features", {})
    scores = snap.get("scores", {})
    lines = []
    action = sig.get("action", "NO TRADE")
    if action == "NO TRADE":
        lines.append("The engine sees no immediate edge — scores are below the trade threshold.")
    else:
        lines.append(f"{action} {sig.get('asset')} @ {sig.get('entry')} — confidence {sig.get('confidence')}.")
        lines.append(f"Setup: {sig.get('reason')}.")
    if feats.get("trend"):
        lines.append(f"Trend: {feats['trend']} (EMA stack), ADX {feats.get('adx')}, Supertrend {'bull' if feats.get('supertrend_bull') else 'bear'}.")
    rsi = feats.get("rsi")
    if rsi is not None:
        lines.append(f"RSI {rsi:.1f} — {'overbought' if rsi >= 70 else 'oversold' if rsi <= 30 else 'neutral'}.")
    pd_ = feats.get("premium_discount")
    if pd_:
        lines.append(f"Price sits in the {pd_} zone of the dealing range.")
    plans = payload.get("plans", [])
    if plans:
        lines.append("Conditional setups to watch:")
        for p in plans[:4]:
            lines.append(f"  • [{p['action']}] {p['type']} ({p['confidence']}%): {p['condition']}")
    if scores:
        lines.append(f"Bull score {scores.get('bull', {}).get('score', 0)}/100 · Bear score {scores.get('bear', {}).get('score', 0)}/100.")
    lines.append("Not financial advice.")
    return "\n".join(lines)


class LLMBrain:
    def __init__(self, provider: str = LLM_PROVIDER):
        self.provider = provider

    @property
    def enabled(self) -> bool:
        if self.provider == "off":
            return False
        if self.provider in ("openai", "auto") and OPENAI_API_KEY:
            return True
        if self.provider in ("gemini", "auto") and GEMINI_API_KEY:
            return True
        return False

    def _prompt(self, payload: dict) -> str:
        slim = {
            "signal": {k: payload.get("signal", {}).get(k) for k in
                       ("asset", "action", "entry", "stop_loss", "take_profit", "confidence", "reason")},
            "plans": [{k: p.get(k) for k in ("type", "action", "condition", "entry", "confidence")}
                      for p in payload.get("plans", [])],
            "features": {k: payload.get("snapshot", {}).get("features", {}).get(k) for k in
                         ("price", "trend", "rsi", "adx", "volume_ratio", "above_vwap",
                          "premium_discount", "event_kind", "rsi_divergence", "sweep")},
            "intelligence": {k: payload.get("intelligence", {}).get(k) for k in
                             ("signal", "confidence", "risk_reward", "news", "scenario_A",
                              "scenario_B", "scenario_C")},
        }
        return (
            "You are the narrative layer of an AI Trading Intelligence System for "
            "BTCUSDT, ETHUSDT and XAUUSD/Gold. Think like a professional analyst: "
            "capital preservation first, never force a trade. Summarise the following "
            "machine state in 3-6 short bullet points for a trader. Be specific with "
            "levels. Do NOT give financial advice, position sizes or leverage. JSON: "
            + json.dumps(slim)
        )

    def _call_openai(self, prompt: str) -> Optional[str]:
        r = requests.post(
            f"{OPENAI_BASE_URL.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            json={"model": OPENAI_MODEL, "messages": [{"role": "user", "content": prompt}],
                  "max_tokens": 400, "temperature": 0.4},
            timeout=20)
        if r.status_code != 200:
            return None
        return r.json()["choices"][0]["message"]["content"]

    def _call_gemini(self, prompt: str) -> Optional[str]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
        r = requests.post(url, params={"key": GEMINI_API_KEY},
                          json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=20)
        if r.status_code != 200:
            return None
        try:
            return r.json()["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError):
            return None

    def complete(self, prompt: str, system: Optional[str] = None) -> Optional[str]:
        """Direct text completion for arbitrary reasoning prompts."""
        if not self.enabled:
            return None
        full_prompt = f"{system}\n\n{prompt}" if system else prompt
        if self.provider in ("openai", "auto"):
            res = self._call_openai(full_prompt)
            if res:
                return res.strip()
        if self.provider in ("gemini", "auto"):
            res = self._call_gemini(full_prompt)
            if res:
                return res.strip()
        return None

    def generate(self, payload: dict) -> dict:
        """Returns {'provider': ..., 'narrative': str}. Never raises."""
        if self.enabled:
            prompt = self._prompt(payload)
            if self.provider in ("openai", "auto"):
                text = self._call_openai(prompt)
                if text:
                    return {"provider": f"openai:{OPENAI_MODEL}", "narrative": text.strip()}
            if self.provider in ("gemini", "auto"):
                text = self._call_gemini(prompt)
                if text:
                    return {"provider": f"gemini:{GEMINI_MODEL}", "narrative": text.strip()}
            return {"provider": "offline", "narrative": _rule_based_narrative(payload),
                    "note": "LLM call failed — fell back to rule-based narrative"}
        return {"provider": "offline", "narrative": _rule_based_narrative(payload)}
