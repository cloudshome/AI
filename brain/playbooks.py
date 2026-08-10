"""brain/playbooks.py — per-asset professional playbooks (decisions B1, B8, A1).

Your roadmap's core correction: BTC, ETH and GOLD are NOT the same market, so
they must not run one generic pipeline.

  BTC  : 4H → 1H → 15M    (regime → setup location → entry confirmation)
  ETH  : BTC first → ETH second; ETH/BTC tells you whether ETH is
         outperforming or underperforming BTC (correlation is handled by
         brain/portfolio.py; direction gating lives here)
  GOLD : D1 → 4H → 1H/15M + previous-day high/low + London/NY sessions +
         US-data countdown (when NOT to trade)

This module only *derives* gates and playbook metadata.  Enforcing them is
done by brain/decision.py so the raw engine output always stays visible as
research.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from config import (GOLD_SESSION_MODE, GOLD_LONDON_WINDOW, GOLD_NY_WINDOW,
                    GOLD_PDH_PDL, GOLD_NEWS_BLOCK, PRIMARY_SETUP_FAMILY,
                    PRIMARY_FAMILIES, ETH_BTC_GATE)

# ── Playbook definitions ─────────────────────────────────────────────────
PLAYBOOKS: dict[str, dict] = {
    "BTCUSDT": {
        "name": "BTC playbook",
        "htf_stack": ["4h", "1h", "15m"],
        "sessions": None,
        "pdh_pdl": False,
        "news_hold": False,
        "note": "4H regime → 1H setup location → 15M entry confirmation",
    },
    "ETHUSDT": {
        "name": "ETH playbook",
        "htf_stack": ["4h", "1h", "15m"],
        "sessions": None,
        "pdh_pdl": False,
        "news_hold": False,
        "gate_btc": True,
        "note": "BTC first, ETH second — never fight a strongly bearish BTC",
    },
    "XAUUSD": {
        "name": "Gold playbook",
        "htf_stack": ["1d", "4h", "1h", "15m"],
        "sessions": ["london", "newyork"],
        "pdh_pdl": True,
        "news_hold": True,
        "note": "D1 bias → 4H location → 1H/15M entry; London/NY sessions; PDH/PDL; stand aside around US data",
    },
}

SESSION_LABELS = {
    "london": "London (07:00–16:00 UTC)",
    "newyork": "New York (12:00–21:00 UTC)",
}


def get_playbook(symbol: str) -> dict:
    """Playbook for a canonical symbol; unknown symbols get a neutral one."""
    return PLAYBOOKS.get(symbol, {
        "name": f"{symbol} playbook",
        "htf_stack": ["4h", "1h", "15m"],
        "sessions": None,
        "pdh_pdl": False,
        "news_hold": False,
        "note": "Generic playbook (no dedicated profile yet)",
    })


def primary_plan_types(symbol: str) -> set | None:
    """Set of plan-type names eligible to become the best signal (decision A1).

    ``None`` means no narrowing (radar mode, ``PRIMARY_SETUP_FAMILY=all``).
    """
    family = PRIMARY_FAMILIES.get(PRIMARY_SETUP_FAMILY)
    if family is None:
        return None
    return set(family)


# ── Session awareness (decision B8) ──────────────────────────────────────
def utc_hour(now: datetime | None = None) -> int:
    return (now or datetime.now(timezone.utc)).hour


def session_status(symbol: str, hour: int | None = None) -> dict:
    """Which trading session is open right now (UTC)."""
    hour = utc_hour() if hour is None else hour
    play = get_playbook(symbol)
    sessions = play.get("sessions") or []
    if "london" in sessions and GOLD_LONDON_WINDOW[0] <= hour < GOLD_LONDON_WINDOW[1]:
        return {"session": "london", "label": SESSION_LABELS["london"], "open": True}
    if "newyork" in sessions and GOLD_NY_WINDOW[0] <= hour < GOLD_NY_WINDOW[1]:
        return {"session": "newyork", "label": SESSION_LABELS["newyork"], "open": True}
    if sessions:
        return {"session": "asia", "label": "Asia / off-window", "open": False}
    return {"session": None, "label": "", "open": True}


def previous_day_levels(df_1d: pd.DataFrame) -> dict:
    """Previous day high / low / close from daily bars (decision B8)."""
    if df_1d is None or len(df_1d) < 2:
        return {"available": False, "pdh": None, "pdl": None, "pdc": None}
    prev = df_1d.iloc[-2]
    try:
        return {
            "available": True,
            "pdh": float(prev["high"]),
            "pdl": float(prev["low"]),
            "pdc": float(prev["close"]),
        }
    except (KeyError, TypeError, ValueError):
        return {"available": False, "pdh": None, "pdl": None, "pdc": None}


def gold_gates(now: datetime | None = None, news_imminent: bool = False,
               df_1d: pd.DataFrame | None = None) -> dict:
    """Gold-specific no-trade conditions. Returns a list of check results."""
    checks = []
    hour = utc_hour(now)
    session = session_status("XAUUSD", hour)
    play = get_playbook("XAUUSD")

    # Off-window session
    if play.get("sessions") and not session["open"] and GOLD_SESSION_MODE != "off":
        checks.append({
            "check": "session_window",
            "ok": GOLD_SESSION_MODE == "warn",
            "detail": f"Outside preferred sessions — currently {session['label']} (mode: {GOLD_SESSION_MODE})",
            "blocking": GOLD_SESSION_MODE == "block",
        })

    # US-data countdown — gold can move violently around US releases
    if play.get("news_hold") and news_imminent and GOLD_NEWS_BLOCK:
        checks.append({
            "check": "us_data_countdown",
            "ok": False,
            "detail": "High-impact US macro event imminent — gold no-entry window",
            "blocking": True,
        })

    # PDH/PDL context (informational)
    pd = previous_day_levels(df_1d) if (play.get("pdh_pdl") and GOLD_PDH_PDL) else \
        {"available": False}
    if pd.get("available"):
        checks.append({
            "check": "pdh_pdl",
            "ok": True,
            "detail": (f"Prev day high {pd['pdh']:,.2f} / low {pd['pdl']:,.2f} / "
                       f"close {pd['pdc']:,.2f}"),
            "blocking": False,
        })
    return {"checks": checks, "blocked": any(c["blocking"] for c in checks)}


def eth_gates(btc_bias: str | None = None, eth_btc_slope: float | None = None,
              action: str | None = None) -> dict:
    """ETH playbook: BTC first, ETH second (decision B1).

    * BTC strongly bearish → no ETH longs (unless ETH/BTC is strongly
      outperforming, i.e. relative strength is doing the heavy lifting).
    * BTC strongly bullish → no ETH shorts for the same reason in reverse.
    """
    checks = []
    if not ETH_BTC_GATE:
        return {"checks": checks, "blocked": False}
    if btc_bias and action == "BUY" and btc_bias == "bear":
        rel_ok = bool(eth_btc_slope is not None and eth_btc_slope > 0)
        checks.append({
            "check": "btc_direction_gate",
            "ok": rel_ok,
            "detail": ("BTC is strongly bearish — ETH long only if ETH/BTC shows "
                       "relative strength" if rel_ok else
                       "BTC is strongly bearish — ETH long blocked (BTC first, ETH second)"),
            "blocking": not rel_ok,
        })
    if btc_bias and action == "SELL" and btc_bias == "bull":
        rel_ok = bool(eth_btc_slope is not None and eth_btc_slope < 0)
        checks.append({
            "check": "btc_direction_gate",
            "ok": rel_ok,
            "detail": ("BTC is strongly bullish — ETH short only if ETH/BTC shows "
                       "relative weakness" if rel_ok else
                       "BTC is strongly bullish — ETH short blocked (BTC first, ETH second)"),
            "blocking": not rel_ok,
        })
    if eth_btc_slope is not None:
        checks.append({
            "check": "eth_btc_slope",
            "ok": True,
            "detail": f"ETH/BTC slope {eth_btc_slope:+.3f}% (relative strength "
                      f"{'outperforming' if eth_btc_slope > 0 else 'underperforming'})",
            "blocking": False,
        })
    return {"checks": checks, "blocked": any(c["blocking"] for c in checks)}


def apply_playbook(symbol: str, action: str | None, *, now: datetime | None = None,
                   news_imminent: bool = False, df_1d: pd.DataFrame | None = None,
                   btc_bias: str | None = None, eth_btc_slope: float | None = None) -> dict:
    """Full playbook gate for one asset/action. Pure function of the inputs —
    no DB access — so it is easy to test."""
    play = get_playbook(symbol)
    checks: list[dict] = []
    blocked = False

    if symbol == "XAUUSD":
        g = gold_gates(now=now, news_imminent=news_imminent, df_1d=df_1d)
        checks += g["checks"]
        blocked = blocked or g["blocked"]
    if symbol == "ETHUSDT":
        e = eth_gates(btc_bias=btc_bias, eth_btc_slope=eth_btc_slope, action=action)
        checks += e["checks"]
        blocked = blocked or e["blocked"]

    return {
        "playbook": play["name"],
        "htf_stack": play["htf_stack"],
        "note": play["note"],
        "checks": checks,
        "blocked": blocked,
        "blocking_checks": [c["check"] for c in checks if c.get("blocking")],
    }
