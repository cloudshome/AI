"""brain/immune.py — System health, staleness detection, and diagnostic alarms.

Monitors database integrity, market data freshness (e.g. catching stale sample data),
risk gate violations, behavioral flags, and calibration health.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

from data.database import SignalDB
from brain.risk_gate import evaluate_risk_gate
from config import DB_PATH


def check_database(db: Optional[SignalDB] = None) -> dict:
    """Verify database existence, schema, and table integrity."""
    close_db = False
    if db is None:
        db = SignalDB()
        close_db = True

    try:
        tables = [r[0] for r in db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        required = ["scans", "plans", "backtest_results", "paper_trades",
                    "agent_runs", "journal_entries", "trader_state"]
        missing = [t for t in required if t not in tables]

        if missing:
            return {
                "name": "database_integrity",
                "status": "CRITICAL",
                "detail": f"Missing tables: {', '.join(missing)}",
            }

        scans_cnt = db.conn.execute("SELECT COUNT(*) FROM scans").fetchone()[0]
        return {
            "name": "database_integrity",
            "status": "OK",
            "detail": f"SQLite database healthy ({len(tables)} tables, {scans_cnt} scans recorded)",
        }
    except Exception as exc:
        return {
            "name": "database_integrity",
            "status": "CRITICAL",
            "detail": f"Database error: {exc}",
        }
    finally:
        if close_db:
            db.close()


def check_data_freshness() -> dict:
    """Check timestamp freshness of market data sample / feeds."""
    sample_file = Path("data_samples/btcusdt_15m_sample.csv")
    if not sample_file.exists():
        return {
            "name": "data_freshness",
            "status": "WARN",
            "detail": "Sample CSV data file not found",
        }

    try:
        df = pd.read_csv(sample_file)
        ts_col = "ts" if "ts" in df.columns else ("timestamp" if "timestamp" in df.columns else None)
        if ts_col and len(df) > 0:
            last_ts = df[ts_col].iloc[-1]
            try:
                val = float(last_ts)
                ts_sec = val / 1000 if val > 1e11 else val
                now_sec = time.time()
                age_hours = (now_sec - ts_sec) / 3600
                age_days = age_hours / 24

                if age_days > 2.0:
                    return {
                        "name": "data_freshness",
                        "status": "CRITICAL",
                        "detail": f"Sample candle data is ~{age_days:.1f} days old (stale) — live feed recommended for production execution",
                        "age_days": round(age_days, 1),
                    }
                elif age_hours > 4.0:
                    return {
                        "name": "data_freshness",
                        "status": "WARN",
                        "detail": f"Data is {age_hours:.1f} hours old",
                        "age_hours": round(age_hours, 1),
                    }
            except (ValueError, TypeError):
                pass

        return {
            "name": "data_freshness",
            "status": "OK",
            "detail": f"Sample candle data verified ({len(df)} bars)",
        }
    except Exception as exc:
        return {
            "name": "data_freshness",
            "status": "WARN",
            "detail": f"Unable to parse candle data timestamp: {exc}",
        }


def check_risk_system(db: Optional[SignalDB] = None) -> dict:
    """Check risk limits, drawdown halts, and emotional/behavioral flags."""
    close_db = False
    if db is None:
        db = SignalDB()
        close_db = True

    try:
        rg = evaluate_risk_gate(db)
        if not rg.get("open"):
            return {
                "name": "risk_system",
                "status": "CRITICAL",
                "detail": f"Risk gate HALTED: {'; '.join(rg.get('blocked_by', []))}",
            }

        # Check behavioral flags directly
        st = db.get_trader_state()
        active_flags = [k for k in ("angry", "tired", "revenge", "chasing") if st.get(k)]
        if active_flags:
            return {
                "name": "risk_system",
                "status": "CRITICAL",
                "detail": f"Behavioral flags active: {', '.join(active_flags)} (trading blocked)",
            }

        return {
            "name": "risk_system",
            "status": "OK",
            "detail": f"Risk gate OPEN (Level: {rg.get('progression', {}).get('level')}, Max risk: {rg.get('progression', {}).get('max_risk_pct')}%)",
        }
    finally:
        if close_db:
            db.close()


def check_calibration_health(db: Optional[SignalDB] = None) -> dict:
    """Check self-improvement calibration state."""
    close_db = False
    if db is None:
        db = SignalDB()
        close_db = True

    try:
        cal = db.load_calibration()
        proven = [k for k, v in cal.items() if v.get("proven")]
        return {
            "name": "calibration_health",
            "status": "OK",
            "detail": f"Calibration active: {len(cal)} setups profiled ({len(proven)} proven)",
        }
    except Exception as exc:
        return {
            "name": "calibration_health",
            "status": "WARN",
            "detail": f"Calibration check warning: {exc}",
        }
    finally:
        if close_db:
            db.close()


def run_health_check(db: Optional[SignalDB] = None) -> dict:
    """Aggregate all immune system checks into a single diagnostic report."""
    checks = [
        check_database(db=db),
        check_data_freshness(),
        check_risk_system(db=db),
        check_calibration_health(db=db),
    ]

    criticals = [c for c in checks if c.get("status") == "CRITICAL"]
    warns = [c for c in checks if c.get("status") == "WARN"]

    overall_status = "CRITICAL" if criticals else ("WARN" if warns else "OK")
    summary = f"Immune health: {overall_status} ({len(criticals)} critical, {len(warns)} warning, {len(checks) - len(criticals) - len(warns)} ok)"

    return {
        "status": overall_status,
        "summary": summary,
        "critical_count": len(criticals),
        "warn_count": len(warns),
        "checks": checks,
        "timestamp": int(time.time()),
    }
