"""engine/calibration_hook.py

Applies the self-improvement profile to one plan's confidence.

Profiles are stored per setup — and since the professional-mode learning loop
is regime-tagged (decision B3), a profile key may be either ``plan_type`` or
``plan_type::regime``.  The composite key wins when present, so the engine can
know *"this setup works in TRENDING_BULL but not in RANGING"*.
"""
from __future__ import annotations


def _composite(plan_type: str, regime: str) -> str:
    return f"{plan_type}::{regime}" if regime else plan_type


def apply_calibration(conf: int, plan_type: str, calibration: dict,
                      regime: str = "") -> tuple[int, bool]:
    """Return (adjusted_confidence, filtered_out)."""
    if not calibration:
        return conf, False
    entry = calibration.get(_composite(plan_type, regime))
    if entry is None:
        entry = calibration.get(plan_type)
    if not entry:
        return conf, False
    if entry.get("filtered"):
        return conf, True
    mult = entry.get("multiplier", 1.0)
    return max(5, min(100, int(round(conf * mult)))), False
