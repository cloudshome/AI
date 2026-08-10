"""data/database.py

SQLite learning store for the CryptoBrain engine.

Tables
------
scans            : one row per engine run (signal + reason + feature snapshot)
plans            : the conditional plans each scan produced
backtest_results : per-plan outcomes from the walk-forward backtester
paper_trades     : approved live-market paper simulations and their outcomes

The point of this store: accumulate every scan, historical grade, and approved
paper-trade outcome, then answer questions like
  * "Which plan types actually win most often?"
  * "Does confidence >= 80 beat confidence 55-60?"
  * "Do BUY setups on the 15m beat SELL setups?"

No extra dependencies — stdlib sqlite3.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Optional

import config as _config

SCHEMA = """
CREATE TABLE IF NOT EXISTS scans(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  signal_id TEXT, ts INTEGER, symbol TEXT, timeframe TEXT, price REAL,
  action TEXT, entry REAL, stop_loss REAL, take_profit REAL,
  risk_reward REAL, confidence_label TEXT, confidence_pct INTEGER,
  reason TEXT, signal_type TEXT,
  features_json TEXT, plans_json TEXT, context_json TEXT,
  status TEXT DEFAULT 'PENDING_REVIEW', lifecycle_ts INTEGER, approve_note TEXT,
  regime TEXT, created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS plans(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  scan_id INTEGER NOT NULL,
  plan_id TEXT, type TEXT, action TEXT, condition TEXT,
  trigger_level REAL, entry REAL, stop_loss REAL,
  tp1 REAL, tp2 REAL, risk_reward REAL,
  confidence_pct INTEGER, confidence_label TEXT, status TEXT,
  FOREIGN KEY(scan_id) REFERENCES scans(id)
);
CREATE TABLE IF NOT EXISTS backtest_results(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT, ts INTEGER, symbol TEXT, timeframe TEXT,
  plan_type TEXT, action TEXT, confidence_pct INTEGER,
  horizon_hours REAL, outcome TEXT,
  rr_achieved REAL, max_favorable REAL, max_adverse REAL,
  entry REAL, trigger_level REAL, regime TEXT
);
CREATE TABLE IF NOT EXISTS decisions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  scan_id INTEGER NOT NULL,
  from_state TEXT, to_state TEXT,
  reviewer TEXT DEFAULT 'human',
  note TEXT,
  ts INTEGER,
  FOREIGN KEY(scan_id) REFERENCES scans(id)
);
CREATE TABLE IF NOT EXISTS calibration(
  plan_type TEXT PRIMARY KEY,
  multiplier REAL, expectancy REAL, samples INTEGER,
  regime TEXT DEFAULT '', proven INTEGER DEFAULT 0, tp_rr REAL,
  backtest_samples INTEGER DEFAULT 0, paper_samples INTEGER DEFAULT 0,
  win_rate REAL,
  updated_at INTEGER
);
CREATE TABLE IF NOT EXISTS paper_trades(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  scan_id INTEGER NOT NULL UNIQUE,
  signal_id TEXT, plan_id TEXT, plan_type TEXT,
  symbol TEXT, timeframe TEXT, action TEXT,
  entry REAL, stop_loss REAL, take_profit REAL,
  risk_reward REAL, confidence_pct INTEGER,
  status TEXT NOT NULL DEFAULT 'WAITING_ENTRY',
  created_ts INTEGER, opened_ts INTEGER, closed_ts INTEGER,
  entry_price REAL, exit_price REAL,
  outcome TEXT, rr_achieved REAL, close_reason TEXT,
  last_candle_ts INTEGER, last_price REAL, checks INTEGER DEFAULT 0,
  error TEXT, regime TEXT, mae REAL, mfe REAL,
  FOREIGN KEY(scan_id) REFERENCES scans(id)
);
CREATE TABLE IF NOT EXISTS journal_entries(
  scan_id INTEGER PRIMARY KEY,
  followed_rules INTEGER, emotion TEXT, mistake TEXT,
  screenshot_path TEXT, would_change TEXT, notes TEXT, ts INTEGER,
  FOREIGN KEY(scan_id) REFERENCES scans(id)
);
CREATE TABLE IF NOT EXISTS trader_state(
  id INTEGER PRIMARY KEY CHECK (id=1),
  angry INTEGER DEFAULT 0, tired INTEGER DEFAULT 0,
  revenge INTEGER DEFAULT 0, chasing INTEGER DEFAULT 0,
  note TEXT, updated_ts INTEGER
);
CREATE TABLE IF NOT EXISTS source_scores(
  source TEXT PRIMARY KEY, tier INTEGER, trust REAL,
  last_seen INTEGER, note TEXT
);
CREATE TABLE IF NOT EXISTS agent_runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  agent TEXT NOT NULL,
  status TEXT NOT NULL,
  summary TEXT,
  payload_json TEXT,
  ts INTEGER,
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_plans_scan ON plans(scan_id);
CREATE INDEX IF NOT EXISTS idx_bt_type ON backtest_results(plan_type);
CREATE INDEX IF NOT EXISTS idx_bt_outcome ON backtest_results(outcome);
CREATE INDEX IF NOT EXISTS idx_bt_regime ON backtest_results(regime);
CREATE INDEX IF NOT EXISTS idx_decisions_scan ON decisions(scan_id);
CREATE INDEX IF NOT EXISTS idx_paper_scan ON paper_trades(scan_id);
CREATE INDEX IF NOT EXISTS idx_paper_status ON paper_trades(status);
CREATE INDEX IF NOT EXISTS idx_paper_symbol ON paper_trades(symbol);
"""


class SignalDB:
    def __init__(self, path: str | Path | None = None):
        # Read config.DB_PATH at construction time (not import time) so tests
        # and embedded callers can point the store at a temp file by patching
        # config.DB_PATH — production behaviour is unchanged.
        self.path = Path(path) if path else Path(_config.DB_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Thread-safe: the dashboard's watchdog thread, Flask request threads
        # and auto-refresh all write to this DB concurrently. WAL mode +
        # busy_timeout + check_same_thread=False prevent periodic
        # "database is locked" crashes on busy machines.
        self.conn = sqlite3.connect(str(self.path), timeout=30.0,
                                    check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        """Initialize pragmas and schema with retry for concurrent processes."""
        for attempt in range(10):
            try:
                self.conn.execute("PRAGMA journal_mode=WAL")
                self.conn.execute("PRAGMA busy_timeout=30000")
                self.conn.execute("PRAGMA synchronous=NORMAL")
                self.conn.executescript(SCHEMA)
                self._migrate()
                self.conn.commit()
                break
            except sqlite3.OperationalError as exc:
                if "locked" in str(exc).lower() and attempt < 9:
                    time.sleep(0.05 * (attempt + 1))
                else:
                    raise

    def _retry_write(self, fn, max_attempts: int = 8):
        """Execute a write transaction with automatic retry on locked database."""
        for attempt in range(max_attempts):
            try:
                return fn()
            except sqlite3.OperationalError as exc:
                if "locked" in str(exc).lower() and attempt < max_attempts - 1:
                    time.sleep(0.05 * (attempt + 1))
                else:
                    raise

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass

    # ── migration ────────────────────────────────────────────────────────
    def _migrate(self) -> None:
        """Add lifecycle + professional-mode columns to pre-existing DBs
        (idempotent)."""
        def _add(table: str, col: str, decl: str) -> None:
            cols = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            if col not in cols:
                try:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
                except sqlite3.OperationalError as exc:
                    # Another thread/process finished the same migration between
                    # our PRAGMA check and the ALTER — that is fine.
                    if "duplicate column" not in str(exc).lower():
                        raise

        _add("scans", "signal_id", "TEXT")
        _add("scans", "status", "TEXT DEFAULT 'PENDING_REVIEW'")
        _add("scans", "lifecycle_ts", "INTEGER")
        _add("scans", "approve_note", "TEXT")
        _add("scans", "regime", "TEXT")
        _add("backtest_results", "regime", "TEXT")
        # sim_key: identity of a simulator-generated sample (symbol:tf:ts:plan:action)
        # so the 100-backtest / 20-paper grind counts UNIQUE samples even when the
        # same history window is re-simulated in a later run (decision A6/B10).
        _add("backtest_results", "sim_key", "TEXT")
        _add("paper_trades", "sim_key", "TEXT")
        _add("paper_trades", "regime", "TEXT")
        _add("paper_trades", "mae", "REAL")
        _add("paper_trades", "mfe", "REAL")
        _add("calibration", "regime", "TEXT DEFAULT ''")
        _add("calibration", "proven", "INTEGER DEFAULT 0")
        _add("calibration", "tp_rr", "REAL")
        _add("calibration", "backtest_samples", "INTEGER DEFAULT 0")
        _add("calibration", "paper_samples", "INTEGER DEFAULT 0")
        _add("calibration", "win_rate", "REAL")

    # ── lifecycle ────────────────────────────────────────────────────────
    def update_status(self, scan_id: int, to_state: str, note: str = "",
                      reviewer: str = "human") -> Optional[str]:
        """Transition a scan's lifecycle state and log a decision row.
        Returns the new state, or None if the scan doesn't exist."""
        def _do_update():
            row = self.conn.execute("SELECT status FROM scans WHERE id=?", (scan_id,)).fetchone()
            if row is None:
                return None
            from engine.lifecycle import transition, LifecycleError
            try:
                new_state = transition(row["status"], to_state)
            except LifecycleError as exc:
                raise LifecycleError(exc.message) from None
            self.conn.execute(
                "UPDATE scans SET status=?, lifecycle_ts=? WHERE id=?",
                (new_state, int(time.time() * 1000), scan_id))
            self.conn.execute(
                "INSERT INTO decisions(scan_id, from_state, to_state, reviewer, note, ts) "
                "VALUES (?,?,?,?,?,?)",
                (scan_id, row["status"], new_state, reviewer, note, int(time.time() * 1000)))
            self.conn.commit()
            return new_state
        return self._retry_write(_do_update)

    def pending_reviews(self, symbol: str | None = None) -> list[dict]:
        """Signals awaiting human approval, newest first."""
        q = ("SELECT s.*, (SELECT COUNT(*) FROM plans p WHERE p.scan_id=s.id) n_plans "
             "FROM scans s WHERE s.status='PENDING_REVIEW' AND s.action IN ('BUY','SELL')")
        args: tuple = ()
        if symbol:
            q += " AND s.symbol=?"
            args = (symbol,)
        q += " ORDER BY s.ts DESC LIMIT 30"
        return [dict(r) for r in self.conn.execute(q, args).fetchall()]

    def decision_history(self, scan_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT from_state, to_state, reviewer, note, ts FROM decisions "
            "WHERE scan_id=? ORDER BY ts", (scan_id,)).fetchall()
        return [dict(r) for r in rows]

    def get_scan(self, scan_id: int) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM scans WHERE id=?", (scan_id,)).fetchone()
        return dict(row) if row else None

    # ── paper trades (live-market simulation; no exchange orders) ────────
    def paper_candidates(self, symbol: str | None = None) -> list[dict]:
        """Approved/executed scans that have not yet entered the paper runner."""
        q = """SELECT s.* FROM scans s
               LEFT JOIN paper_trades p ON p.scan_id=s.id
               WHERE p.id IS NULL AND s.status IN ('APPROVED','EXECUTED')
                 AND s.action IN ('BUY','SELL')"""
        args: tuple = ()
        if symbol:
            q += " AND s.symbol=?"
            args = (symbol,)
        q += " ORDER BY s.lifecycle_ts ASC, s.id ASC"
        return [dict(r) for r in self.conn.execute(q, args).fetchall()]

    def get_paper_trade(self, trade_id: int) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM paper_trades WHERE id=?", (trade_id,)).fetchone()
        return dict(row) if row else None

    def paper_trade_for_scan(self, scan_id: int) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM paper_trades WHERE scan_id=?", (scan_id,)).fetchone()
        return dict(row) if row else None

    def create_paper_trade(self, fields: dict) -> tuple[dict, bool]:
        """Insert one simulated trade once. Returns ``(row, created)``.

        ``scan_id`` is unique, which makes repeated runner passes and two
        accidentally-started runner processes idempotent.
        """
        cols = (
            "scan_id", "signal_id", "plan_id", "plan_type", "symbol", "timeframe", "action",
            "entry", "stop_loss", "take_profit", "risk_reward", "confidence_pct", "status",
            "created_ts", "opened_ts", "entry_price", "regime", "sim_key",
        )
        values = tuple(fields.get(c) for c in cols)
        cur = self.conn.execute(
            f"INSERT OR IGNORE INTO paper_trades ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
            values,
        )
        self.conn.commit()
        row = self.paper_trade_for_scan(int(fields["scan_id"]))
        if row is None:  # pragma: no cover - protects a malformed DB only
            raise RuntimeError("paper trade insert did not return a row")
        return row, bool(cur.rowcount)

    def active_paper_trades(self, symbol: str | None = None) -> list[dict]:
        q = "SELECT * FROM paper_trades WHERE status IN ('WAITING_ENTRY','OPEN')"
        args: tuple = ()
        if symbol:
            q += " AND symbol=?"
            args = (symbol,)
        q += " ORDER BY created_ts ASC, id ASC"
        return [dict(r) for r in self.conn.execute(q, args).fetchall()]

    def open_paper_trade(self, trade_id: int, entry_price: float, opened_ts: int,
                         last_candle_ts: int | None = None,
                         last_price: float | None = None,
                         mae: float | None = None, mfe: float | None = None) -> bool:
        """Claim a waiting conditional trade as filled. Returns True once."""
        cur = self.conn.execute(
            """UPDATE paper_trades
               SET status='OPEN', opened_ts=?, entry_price=?, last_candle_ts=?,
                   last_price=?, mae=COALESCE(?, mae), mfe=COALESCE(?, mfe),
                   checks=checks+1
               WHERE id=? AND status='WAITING_ENTRY'""",
            (opened_ts, entry_price, last_candle_ts, last_price,
             mae, mfe, trade_id),
        )
        self.conn.commit()
        return bool(cur.rowcount)

    def close_paper_trade(self, trade_id: int, outcome: str, exit_price: float,
                          rr_achieved: float, close_reason: str, closed_ts: int,
                          last_candle_ts: int | None = None,
                          last_price: float | None = None,
                          mae: float | None = None, mfe: float | None = None) -> bool:
        """Atomically close an active paper trade; only one runner can win."""
        cur = self.conn.execute(
            """UPDATE paper_trades
               SET status='CLOSED', outcome=?, exit_price=?, rr_achieved=?,
                   close_reason=?, closed_ts=?, last_candle_ts=?, last_price=?,
                   mae=COALESCE(?, mae), mfe=COALESCE(?, mfe),
                   checks=checks+1
               WHERE id=? AND status IN ('WAITING_ENTRY','OPEN')""",
            (outcome, exit_price, rr_achieved, close_reason, closed_ts,
             last_candle_ts, last_price, mae, mfe, trade_id),
        )
        self.conn.commit()
        return bool(cur.rowcount)

    def cancel_paper_trade(self, trade_id: int, reason: str, closed_ts: int) -> bool:
        """Cancel an unfinished simulation when a human ends its source scan."""
        cur = self.conn.execute(
            """UPDATE paper_trades
               SET status='CANCELLED', close_reason=?, closed_ts=?
               WHERE id=? AND status IN ('WAITING_ENTRY','OPEN')""",
            (reason, closed_ts, trade_id),
        )
        self.conn.commit()
        return bool(cur.rowcount)

    def touch_paper_trade(self, trade_id: int, last_candle_ts: int | None = None,
                          last_price: float | None = None,
                          checked_ts: int | None = None,
                          mae: float | None = None, mfe: float | None = None) -> None:
        """Persist the runner cursor/last seen price after a non-decisive pass."""
        _ = checked_ts
        self.conn.execute(
            """UPDATE paper_trades
               SET last_candle_ts=COALESCE(?, last_candle_ts),
                   last_price=COALESCE(?, last_price),
                   mae=COALESCE(?, mae), mfe=COALESCE(?, mfe),
                   checks=checks+1
               WHERE id=? AND status IN ('WAITING_ENTRY','OPEN')""",
            (last_candle_ts, last_price, mae, mfe, trade_id),
        )
        self.conn.commit()

    # ── professional journal (decision B5) ───────────────────────────────
    def save_journal(self, scan_id: int, *, followed_rules: int | None = None,
                     emotion: str = "", mistake: str = "", screenshot_path: str = "",
                     would_change: str = "", notes: str = "") -> None:
        """Record post-trade journal fields for a closed scan. The headline
        field is ``followed_rules``: did I follow my system, win or lose?"""
        def _do_save():
            self.conn.execute(
                """INSERT INTO journal_entries
                   (scan_id, followed_rules, emotion, mistake, screenshot_path,
                    would_change, notes, ts)
                   VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(scan_id) DO UPDATE SET
                     followed_rules=excluded.followed_rules,
                     emotion=excluded.emotion, mistake=excluded.mistake,
                     screenshot_path=excluded.screenshot_path,
                     would_change=excluded.would_change,
                     notes=excluded.notes, ts=excluded.ts""",
                (scan_id, followed_rules, emotion, mistake, screenshot_path,
                 would_change, notes, int(time.time() * 1000)),
            )
            self.conn.commit()
        return self._retry_write(_do_save)

    def get_journal(self, scan_id: int) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM journal_entries WHERE scan_id=?", (scan_id,)).fetchone()
        return dict(row) if row else None

    def journal_stats(self) -> dict:
        rows = self.conn.execute("SELECT * FROM journal_entries").fetchall()
        entries = [dict(r) for r in rows]
        n = len(entries)
        violations = sum(1 for e in entries if e.get("followed_rules") == 0)
        return {
            "n": n,
            "violations": violations,
            "violation_rate": round(violations / n, 3) if n else None,
            "recent": entries[-10:],
        }

    # ── trader state (decision B7) ───────────────────────────────────────
    def get_trader_state(self) -> dict:
        row = self.conn.execute("SELECT * FROM trader_state WHERE id=1").fetchone()
        if not row:
            return {"angry": False, "tired": False, "revenge": False,
                    "chasing": False, "note": "", "updated_ts": None, "any": False}
        d = dict(row)
        d["any"] = bool(d.get("angry") or d.get("tired") or d.get("revenge") or d.get("chasing"))
        return d

    def set_trader_state(self, *, angry: bool | None = None, tired: bool | None = None,
                         revenge: bool | None = None, chasing: bool | None = None,
                         note: str = "") -> dict:
        """Set/clear the behavioral no-trade flags. Returns the new state."""
        def _do_save():
            cur = self.conn.execute("SELECT * FROM trader_state WHERE id=1")
            row = cur.fetchone()
            if row is None:
                self.conn.execute(
                    "INSERT INTO trader_state(id, angry, tired, revenge, chasing, note, updated_ts) "
                    "VALUES (1,?,?,?,?,?,?)",
                    (1 if angry else 0, 1 if tired else 0, 1 if revenge else 0,
                     1 if chasing else 0, note, int(time.time() * 1000)))
            else:
                self.conn.execute(
                    """UPDATE trader_state SET
                         angry=?, tired=?, revenge=?, chasing=?, note=?, updated_ts=?
                       WHERE id=1""",
                    (angry if angry is not None else row["angry"],
                     tired if tired is not None else row["tired"],
                     revenge if revenge is not None else row["revenge"],
                     chasing if chasing is not None else row["chasing"],
                     note if note else row["note"], int(time.time() * 1000)))
            self.conn.commit()
        self._retry_write(_do_save)
        return self.get_trader_state()

    # ── information hierarchy / source trust (decision A5) ──────────────
    def save_source_score(self, source: str, tier: int, trust: float,
                          note: str = "") -> None:
        """Record a source's information tier (1-5) and trust score (0-1)."""
        def _do_save():
            self.conn.execute(
                """INSERT INTO source_scores(source, tier, trust, last_seen, note)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(source) DO UPDATE SET
                     tier=excluded.tier, trust=excluded.trust,
                     last_seen=excluded.last_seen, note=excluded.note""",
                (source, int(tier), max(0.0, min(1.0, float(trust))),
                 int(time.time() * 1000), note))
            self.conn.commit()
        return self._retry_write(_do_save)

    def load_source_scores(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM source_scores ORDER BY tier ASC, trust DESC").fetchall()
        return [dict(r) for r in rows]

    # ── exposure + decided outcomes for risk/portfolio engines ───────────
    def open_exposure(self) -> list[dict]:
        """Open/planned exposure: active paper trades plus approved scans not
        yet enrolled by the paper runner."""
        rows = self.conn.execute(
            """SELECT COALESCE(s.symbol, p.symbol) AS symbol,
                      COALESCE(s.action, p.action) AS action,
                      p.status AS status,
                      p.plan_type AS plan_type, p.risk_reward AS risk_reward,
                      COALESCE(p.id, 0) AS paper_id, s.id AS scan_id
               FROM paper_trades p LEFT JOIN scans s ON s.id = p.scan_id
               WHERE p.status IN ('WAITING_ENTRY','OPEN')
               UNION ALL
               SELECT s.symbol AS symbol, s.action AS action,
                      'APPROVED' AS status, NULL AS plan_type, NULL AS risk_reward,
                      0 AS paper_id, s.id AS scan_id
               FROM scans s LEFT JOIN paper_trades p ON p.scan_id = s.id
               WHERE p.id IS NULL AND s.status IN ('APPROVED','EXECUTED')
               ORDER BY scan_id""").fetchall()
        return [dict(r) for r in rows]

    def decided_paper_rows(self, since_ts: int | None = None,
                           symbol: str | None = None,
                           exclude_sim: bool = False) -> list[dict]:
        """Closed, decided paper trades (TP_HIT / STOP_LOSS), oldest first.

        exclude_sim=True drops simulator walk-forward samples (sim_key NOT
        NULL): they are historical evidence for calibration/setup-proof, but
        they are NOT the live book — the daily/weekly loss limits, drawdown
        ladder and business scorecard must only see real paper trades.
        """
        q = ("SELECT * FROM paper_trades "
             "WHERE outcome IN ('TP_HIT','STOP_LOSS')")
        args: list = []
        if exclude_sim:
            q += " AND sim_key IS NULL"
        if since_ts is not None:
            q += " AND closed_ts >= ?"
            args.append(since_ts)
        if symbol:
            q += " AND symbol=?"
            args.append(symbol)
        q += " ORDER BY COALESCE(closed_ts, opened_ts, created_ts) ASC"
        return [dict(r) for r in self.conn.execute(q, args).fetchall()]

    def setup_stats(self, plan_type: str) -> dict:
        """Decided-sample counts for the setup-proven gate (decision B10)."""
        bt = self.conn.execute(
            """SELECT COUNT(*) n,
                      SUM(CASE WHEN outcome IN ('FULL_WIN','PARTIAL_WIN') THEN 1 ELSE 0 END) wins,
                      ROUND(AVG(rr_achieved),3) avg_rr
               FROM backtest_results WHERE plan_type=?
                 AND outcome IN ('FULL_WIN','PARTIAL_WIN','LOSS')""",
            (plan_type,)).fetchone()
        pp = self.conn.execute(
            """SELECT COUNT(*) n,
                      SUM(CASE WHEN outcome='TP_HIT' THEN 1 ELSE 0 END) wins,
                      ROUND(AVG(rr_achieved),3) avg_rr
               FROM paper_trades WHERE plan_type=?
                 AND outcome IN ('TP_HIT','STOP_LOSS')""",
            (plan_type,)).fetchone()
        bt_n, bt_wins, bt_avg = (bt["n"] or 0, bt["wins"] or 0, bt["avg_rr"] or 0.0)
        pp_n, pp_wins, pp_avg = (pp["n"] or 0, pp["wins"] or 0, pp["avg_rr"] or 0.0)
        return {
            "backtest_n": bt_n, "backtest_wins": bt_wins, "backtest_expectancy": round(bt_avg, 3),
            "paper_n": pp_n, "paper_wins": pp_wins, "paper_expectancy": round(pp_avg, 3),
        }

    @staticmethod
    def _outcome_stats(rows: list[dict]) -> dict:
        """Normalise paper outcome aggregates in one place."""
        if not rows:
            return {"n": 0, "wins": 0, "losses": 0, "win_rate": None, "avg_rr": 0.0}
        row = rows[0]
        n = row.get("n") or 0
        wins, losses = row.get("wins") or 0, row.get("losses") or 0
        decided = wins + losses
        counts = {k: row.get(k) or 0 for k in ("waiting", "open", "closed", "cancelled")}
        return {
            **row, **counts, "n": n, "wins": wins, "losses": losses,
            "win_rate": round(wins / decided, 3) if decided else None,
            "avg_rr": row.get("avg_rr") or 0.0,
        }

    def paper_trade_stats(self, symbol: str | None = None, limit: int = 12) -> dict:
        """Paper-runner dashboard/CLI stats, kept separate from backtests."""
        where = ""
        args: tuple = ()
        if symbol:
            where = " WHERE symbol=?"
            args = (symbol,)
        overall = self._outcome_stats([dict(self.conn.execute(
            f"""SELECT COUNT(*) n,
                       SUM(CASE WHEN status='WAITING_ENTRY' THEN 1 ELSE 0 END) waiting,
                       SUM(CASE WHEN status='OPEN' THEN 1 ELSE 0 END) open,
                       SUM(CASE WHEN status='CLOSED' THEN 1 ELSE 0 END) closed,
                       SUM(CASE WHEN status='CANCELLED' THEN 1 ELSE 0 END) cancelled,
                       SUM(CASE WHEN outcome='TP_HIT' THEN 1 ELSE 0 END) wins,
                       SUM(CASE WHEN outcome='STOP_LOSS' THEN 1 ELSE 0 END) losses,
                       ROUND(AVG(CASE WHEN outcome IN ('TP_HIT','STOP_LOSS') THEN rr_achieved END), 3) avg_rr
                FROM paper_trades{where}""", args).fetchone())])
        by_type = [dict(r) for r in self.conn.execute(
            f"""SELECT plan_type, COUNT(*) n,
                       SUM(CASE WHEN outcome='TP_HIT' THEN 1 ELSE 0 END) wins,
                       SUM(CASE WHEN outcome='STOP_LOSS' THEN 1 ELSE 0 END) losses,
                       ROUND(AVG(CASE WHEN outcome IN ('TP_HIT','STOP_LOSS') THEN rr_achieved END), 3) avg_rr
                FROM paper_trades{where}
                GROUP BY plan_type ORDER BY n DESC""", args).fetchall()]
        by_type = [self._outcome_stats([r]) for r in by_type]
        recent = [dict(r) for r in self.conn.execute(
            f"SELECT * FROM paper_trades{where} ORDER BY COALESCE(closed_ts, opened_ts, created_ts) DESC LIMIT ?",
            args + (max(1, min(int(limit), 50)),)).fetchall()]
        return {"overall": overall, "by_type": by_type, "recent": recent}

    # ── calibration (self-improvement profile) ───────────────────────────
    def save_calibration(self, profile: dict) -> None:
        def _do_save():
            now = int(time.time() * 1000)
            for plan_type, entry in profile.items():
                self.conn.execute(
                    """INSERT INTO calibration(plan_type, multiplier, expectancy, samples,
                                               regime, proven, tp_rr, backtest_samples,
                                               paper_samples, win_rate, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(plan_type) DO UPDATE SET
                         multiplier=excluded.multiplier, expectancy=excluded.expectancy,
                         samples=excluded.samples, regime=excluded.regime,
                         proven=excluded.proven, tp_rr=excluded.tp_rr,
                         backtest_samples=excluded.backtest_samples,
                         paper_samples=excluded.paper_samples,
                         win_rate=excluded.win_rate, updated_at=excluded.updated_at""",
                    (plan_type, entry.get("multiplier", 1.0),
                     entry.get("expectancy"), entry.get("samples", 0),
                     entry.get("regime", ""), 1 if entry.get("proven") else 0,
                     entry.get("tp_rr"), entry.get("backtest_samples", 0),
                     entry.get("paper_samples", 0), entry.get("win_rate"), now))
            self.conn.commit()
        return self._retry_write(_do_save)

    def load_calibration(self) -> dict:
        rows = self.conn.execute("SELECT * FROM calibration").fetchall()
        return {r["plan_type"]: {
            "multiplier": r["multiplier"],
            "expectancy": r["expectancy"],
            "samples": r["samples"],
            "regime": r["regime"],
            "proven": bool(r["proven"]),
            "tp_rr": r["tp_rr"],
            "backtest_samples": r["backtest_samples"],
            "paper_samples": r["paper_samples"],
            "win_rate": r["win_rate"],
        } for r in rows}

    # ── scans ────────────────────────────────────────────────────────────
    def save_scan(self, payload: dict, status_override: str | None = None) -> int:
        """Persist one engine output (signal + plans + snapshot). Returns scan id.

        ``status_override`` lets the professional desk force ``CREATED`` when
        the final decision is blocked (desk-first mode), so a signal the
        system already vetoed never appears in the human approval queue.
        """
        def _do_save():
            sig = payload.get("signal", {})
            snap = payload.get("snapshot", {})
            features = snap.get("features", {})
            plans = payload.get("plans", [])
            from engine.lifecycle import reviewable
            if status_override:
                status = status_override
            else:
                status = "PENDING_REVIEW" if reviewable(sig) else "CREATED"
            cur = self.conn.execute(
                """INSERT INTO scans
                   (signal_id, ts, symbol, timeframe, price, action, entry, stop_loss, take_profit,
                    risk_reward, confidence_label, confidence_pct, reason, signal_type,
                    features_json, plans_json, context_json, status, lifecycle_ts, regime)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    sig.get("signal_id"),
                    sig.get("timestamp") or int(time.time() * 1000),
                    sig.get("asset", ""),
                    sig.get("timeframe", ""),
                    features.get("price"),
                    sig.get("action"),
                    sig.get("entry"),
                    sig.get("stop_loss"),
                    sig.get("take_profit"),
                    sig.get("risk_reward"),
                    sig.get("confidence"),
                    features.get("score_used"),
                    sig.get("reason", ""),
                    sig.get("signal_type", ""),
                    json.dumps(features, default=str),
                    json.dumps(plans, default=str),
                    json.dumps(payload.get("market_context", {}), default=str),
                    status,
                    int(time.time() * 1000),
                    features.get("regime_name"),
                ),
            )
            scan_id = cur.lastrowid
            for p in plans:
                tps = p.get("take_profits") or []
                self.conn.execute(
                    """INSERT INTO plans
                       (scan_id, plan_id, type, action, condition, trigger_level,
                        entry, stop_loss, tp1, tp2, risk_reward, confidence_pct,
                        confidence_label, status)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        scan_id, p.get("id"), p.get("type"), p.get("action"),
                        p.get("condition"), p.get("trigger_level"), p.get("entry"),
                        p.get("stop_loss"), tps[0] if len(tps) > 0 else None,
                        tps[1] if len(tps) > 1 else None, p.get("risk_reward"),
                        p.get("confidence"), p.get("confidence_label"), p.get("status"),
                    ),
                )
            self.conn.commit()
            return scan_id
        return self._retry_write(_do_save)

    def latest_scans(self, symbol: str | None = None, limit: int = 20) -> list[dict]:
        q = "SELECT * FROM scans"
        args: tuple = ()
        if symbol:
            q += " WHERE symbol = ?"
            args = (symbol,)
        q += " ORDER BY ts DESC LIMIT ?"
        rows = self.conn.execute(q, args + (limit,)).fetchall()
        return [dict(r) for r in rows]

    def plan_stats(self) -> list[dict]:
        """Plan-type distribution from real (live) scans."""
        rows = self.conn.execute(
            """SELECT type, action, COUNT(*) n,
                      ROUND(AVG(confidence_pct),1) avg_conf,
                      ROUND(AVG(risk_reward),2) avg_rr
               FROM plans GROUP BY type, action ORDER BY n DESC"""
        ).fetchall()
        return [dict(r) for r in rows]

    # ── backtest results ─────────────────────────────────────────────────
    def save_backtest_rows(self, rows: list[dict], run_id: str) -> int:
        def _do_save():
            n = 0
            for r in rows:
                self.conn.execute(
                    """INSERT INTO backtest_results
                       (run_id, ts, symbol, timeframe, plan_type, action,
                        confidence_pct, horizon_hours, outcome, rr_achieved,
                        max_favorable, max_adverse, entry, trigger_level, regime,
                        sim_key)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (run_id, r.get("ts"), r.get("symbol"), r.get("timeframe"),
                     r.get("plan_type"), r.get("action"), r.get("confidence_pct"),
                     r.get("horizon_hours"), r.get("outcome"), r.get("rr_achieved"),
                     r.get("max_favorable"), r.get("max_adverse"), r.get("entry"),
                     r.get("trigger_level"), r.get("regime"), r.get("sim_key")),
                )
                n += 1
            self.conn.commit()
            return n
        return self._retry_write(_do_save)

    def sim_keys(self, table: str) -> set[str]:
        """All simulator sample identities already stored in a table.

        Used by the paper-sample grind to keep counts honest: re-simulating
        the same history window must not double-count a sample.
        """
        if table not in ("backtest_results", "paper_trades"):
            raise ValueError(f"sim_keys: unsupported table {table!r}")
        rows = self.conn.execute(
            f"SELECT sim_key FROM {table} WHERE sim_key IS NOT NULL").fetchall()
        return {r["sim_key"] for r in rows}

    def backtest_stats(self) -> dict:
        """Win-rate learning: by plan type, by confidence bucket, by regime."""
        def agg(where: str = "") -> dict:
            q = f"""SELECT COUNT(*) n,
                           SUM(CASE WHEN outcome IN ('WIN','FULL_WIN','PARTIAL_WIN') THEN 1 ELSE 0 END) wins,
                           SUM(CASE WHEN outcome='LOSS' THEN 1 ELSE 0 END) losses,
                           SUM(CASE WHEN outcome='OPEN' THEN 1 ELSE 0 END) opens,
                           SUM(CASE WHEN outcome='NOT_TRIGGERED' THEN 1 ELSE 0 END) not_triggered,
                           ROUND(AVG(rr_achieved),2) avg_rr
                    FROM backtest_results {where}"""
            row = dict(self.conn.execute(q).fetchone())
            for k in ("wins", "losses", "opens", "not_triggered"):
                row[k] = row[k] or 0
            row["n"] = row["n"] or 0
            decided = row["wins"] + row["losses"]
            row["win_rate"] = round(row["wins"] / decided, 3) if decided else None
            row["avg_rr"] = row["avg_rr"] or 0.0
            return row

        by_type = [dict(r) for r in self.conn.execute(
            """SELECT plan_type, COUNT(*) n,
                      SUM(CASE WHEN outcome IN ('WIN','FULL_WIN','PARTIAL_WIN') THEN 1 ELSE 0 END) wins,
                      SUM(CASE WHEN outcome='LOSS' THEN 1 ELSE 0 END) losses,
                      ROUND(AVG(rr_achieved),2) avg_rr
               FROM backtest_results GROUP BY plan_type ORDER BY n DESC""").fetchall()]
        for r in by_type:
            r["n"] = r["n"] or 0
            r["wins"] = r["wins"] or 0
            r["losses"] = r["losses"] or 0
            r["avg_rr"] = r["avg_rr"] or 0.0
            decided = r["wins"] + r["losses"]
            r["win_rate"] = round(r["wins"] / decided, 3) if decided else None

        by_conf = [dict(r) for r in self.conn.execute(
            """SELECT CASE WHEN confidence_pct >= 80 THEN 'HIGH'
                           WHEN confidence_pct >= 60 THEN 'MEDIUM'
                           ELSE 'LOW' END bucket,
                      COUNT(*) n,
                      SUM(CASE WHEN outcome IN ('WIN','FULL_WIN','PARTIAL_WIN') THEN 1 ELSE 0 END) wins,
                      SUM(CASE WHEN outcome='LOSS' THEN 1 ELSE 0 END) losses,
                      ROUND(AVG(rr_achieved),2) avg_rr
               FROM backtest_results GROUP BY bucket""").fetchall()]
        for r in by_conf:
            r["n"] = r["n"] or 0
            r["wins"] = r["wins"] or 0
            r["losses"] = r["losses"] or 0
            r["avg_rr"] = r["avg_rr"] or 0.0
            decided = r["wins"] + r["losses"]
            r["win_rate"] = round(r["wins"] / decided, 3) if decided else None

        by_regime = [dict(r) for r in self.conn.execute(
            """SELECT regime, COUNT(*) n,
                      SUM(CASE WHEN outcome IN ('WIN','FULL_WIN','PARTIAL_WIN') THEN 1 ELSE 0 END) wins,
                      SUM(CASE WHEN outcome='LOSS' THEN 1 ELSE 0 END) losses,
                      ROUND(AVG(rr_achieved),2) avg_rr
               FROM backtest_results GROUP BY regime ORDER BY n DESC""").fetchall()]
        for r in by_regime:
            r["n"] = r["n"] or 0
            r["wins"] = r["wins"] or 0
            r["losses"] = r["losses"] or 0
            r["avg_rr"] = r["avg_rr"] or 0.0
            decided = r["wins"] + r["losses"]
            r["win_rate"] = round(r["wins"] / decided, 3) if decided else None

        return {"overall": agg(), "by_type": by_type, "by_confidence": by_conf,
                "by_regime": by_regime}

    # ── agent runs ───────────────────────────────────────────────────────
    def record_agent_run(self, agent: str, status: str, summary: str = "",
                         payload: dict | None = None) -> int:
        ts = int(time.time())
        payload_json = json.dumps(payload or {}, default=str)
        def _op():
            cur = self.conn.execute(
                "INSERT INTO agent_runs(agent, status, summary, payload_json, ts) VALUES(?,?,?,?,?)",
                (agent, status, summary, payload_json, ts))
            self.conn.commit()
            return cur.lastrowid
        return self._retry_write(_op)

    def latest_agent_runs(self, limit: int = 10, agent: str | None = None) -> list[dict]:
        if agent:
            rows = self.conn.execute(
                "SELECT * FROM agent_runs WHERE agent=? ORDER BY id DESC LIMIT ?",
                (agent, limit)).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM agent_runs ORDER BY id DESC LIMIT ?",
                (limit,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["payload"] = json.loads(d.get("payload_json") or "{}")
            except Exception:
                d["payload"] = {}
            out.append(d)
        return out

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
