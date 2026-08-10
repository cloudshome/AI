"""ai/mcp_server.py — Model Context Protocol (MCP) server for CryptoBrain.

Lets Claude Desktop, Cursor, or any MCP client ask the desk directly:

    {
      "mcpServers": {
        "cryptobrain": {
          "command": "python",
          "args": ["main.py", "mcp"],
          "cwd": "/path/to/AI"
        }
      }
    }

Tools exposed (all offline-capable; DEMO_MODE=1 uses sample data):

  * cryptobrain_health      system health (data feeds, DB, risk gate, MCP, LLM)
  * cryptobrain_morning     the morning briefing across the watchlist
  * cryptobrain_scan        one fresh scan of a symbol
  * cryptobrain_desk        strict professional desk report (intelligence)
  * cryptobrain_risk        risk & discipline gate status
  * cryptobrain_ask         natural-language question to the desk
  * cryptobrain_pending     signals awaiting human approval
  * cryptobrain_stats       what the engine has learned (backtests/calibration)
  * cryptobrain_paper       paper-trade book stats
  * cryptobrain_learn       recompute the calibration profile

The server speaks the standard MCP protocol over stdio (JSON-RPC 2.0).  It
requires the official `mcp` package (pip install mcp) — a clear error is
printed otherwise.
"""
from __future__ import annotations

import asyncio
import json
import sys

from config import VERSION


def _json_lines(obj: dict) -> str:
    return json.dumps(obj, indent=2, default=str)


def _scan(symbol: str, timeframe: str, bars: int) -> dict:
    from main import run_scan
    payload = run_scan(symbol, timeframe, bars, save_db=False)
    return {"signal": payload.get("signal", {}),
            "decision": payload.get("decision", {}),
            "plans": payload.get("plans", [])[:6],
            "snapshot": {"features": payload.get("snapshot", {}).get("features", {})},
            "lifecycle": payload.get("lifecycle", {})}


def _desk(symbol: str, timeframe: str, bars: int) -> dict:
    from brain.full_pipeline import analyze_full
    payload = analyze_full(symbol, timeframe, bars, with_context=True)
    return payload.get("intelligence", {})


def _health() -> dict:
    from brain.agent import health_report
    return health_report()


def _morning(symbols: list[str] | None, timeframe: str, bars: int) -> dict:
    from brain.agent import morning_briefing
    return morning_briefing(symbols=symbols, timeframe=timeframe, bars=bars)


def _risk() -> dict:
    from data.database import SignalDB
    from brain.risk_gate import evaluate
    with SignalDB() as db:
        gate = evaluate(db)
        return {"allowed": gate["allowed"], "blocked_by": gate.get("blocked_by", []),
                "details": gate.get("details", {})}


def _ask(question: str, symbol: str | None, timeframe: str) -> dict:
    from brain.agent import ask
    return ask(question, symbol=symbol, timeframe=timeframe)


def _pending() -> dict:
    from data.database import SignalDB
    with SignalDB() as db:
        return {"pending": db.pending_reviews()}


def _stats() -> dict:
    from data.database import SignalDB
    from brain.metrics import business_metrics
    with SignalDB() as db:
        out = {"backtest": db.backtest_stats(),
               "calibration": db.load_calibration(),
               "plan_stats": db.plan_stats()}
        try:
            out["metrics"] = business_metrics(db)
        except Exception:
            out["metrics"] = {}
        return out


def _paper() -> dict:
    from data.database import SignalDB
    with SignalDB() as db:
        return {"paper": db.paper_trade_stats()["overall"]}


def _learn() -> dict:
    from brain.calibrator import learn
    result = learn()
    return {"profile": result["profile"]}


TOOLS = [
    {
        "name": "cryptobrain_health",
        "description": "CryptoBrain system health: data feeds, database, risk gate, "
                       "learning store, MCP and LLM availability.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "cryptobrain_morning",
        "description": "Morning desk briefing: every watchlist asset with the final "
                       "desk decision (desk-first), risk gate, pending reviews, "
                       "exposure and paper-book stats.",
        "inputSchema": {"type": "object", "properties": {
            "symbols": {"type": "array", "items": {"type": "string"},
                        "description": "optional watchlist, e.g. [BTCUSDT, ETHUSDT, XAUUSD]"},
            "timeframe": {"type": "string", "default": "15m"},
        }, "additionalProperties": False},
    },
    {
        "name": "cryptobrain_scan",
        "description": "Fresh scan of one symbol: signal, desk decision, plans.",
        "inputSchema": {"type": "object", "properties": {
            "symbol": {"type": "string", "default": "BTCUSDT"},
            "timeframe": {"type": "string", "default": "15m"},
        }, "required": ["symbol"], "additionalProperties": False},
    },
    {
        "name": "cryptobrain_desk",
        "description": "Strict professional desk report (intelligence) for one symbol: "
                       "signal card, self-review, risk, scenario B.",
        "inputSchema": {"type": "object", "properties": {
            "symbol": {"type": "string", "default": "BTCUSDT"},
            "timeframe": {"type": "string", "default": "15m"},
        }, "required": ["symbol"], "additionalProperties": False},
    },
    {
        "name": "cryptobrain_risk",
        "description": "Risk & discipline gate: daily/weekly limits, drawdown ladder, "
                       "trader-state flags, progression level.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "cryptobrain_ask",
        "description": "Ask the desk a natural-language question (risk, exposure, "
                       "pending reviews, journal, calibration, stats, market scan, …).",
        "inputSchema": {"type": "object", "properties": {
            "question": {"type": "string"},
            "symbol": {"type": "string", "default": None},
        }, "required": ["question"], "additionalProperties": False},
    },
    {
        "name": "cryptobrain_pending",
        "description": "Signals awaiting human approval.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "cryptobrain_stats",
        "description": "What the engine has learned: backtest stats, calibration "
                       "profile, plan distribution, business scorecard.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "cryptobrain_paper",
        "description": "Paper-trading book: tracked/open/closed trades, win rate, avg R.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "cryptobrain_learn",
        "description": "Recompute the self-improvement calibration profile from "
                       "stored backtest + paper outcomes.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]


def _call_tool(name: str, arguments: dict | None) -> dict:
    args = arguments or {}
    if name == "cryptobrain_health":
        return _health()
    if name == "cryptobrain_morning":
        return _morning(args.get("symbols"), args.get("timeframe", "15m"), 500)
    if name == "cryptobrain_scan":
        return _scan(args.get("symbol", "BTCUSDT"), args.get("timeframe", "15m"), 500)
    if name == "cryptobrain_desk":
        return _desk(args.get("symbol", "BTCUSDT"), args.get("timeframe", "15m"), 500)
    if name == "cryptobrain_risk":
        return _risk()
    if name == "cryptobrain_ask":
        return _ask(args.get("question", ""), args.get("symbol"), "15m")
    if name == "cryptobrain_pending":
        return _pending()
    if name == "cryptobrain_stats":
        return _stats()
    if name == "cryptobrain_paper":
        return _paper()
    if name == "cryptobrain_learn":
        return _learn()
    raise ValueError(f"unknown tool: {name}")


def _handle_call_tool(name: str, arguments: dict | None) -> CallToolResult:
    from mcp.types import CallToolResult, TextContent
    try:
        result = _call_tool(name, arguments)
        text = _json_lines(result)
    except Exception as exc:
        text = json.dumps({"ok": False,
                           "error": f"{type(exc).__name__}: {exc}"},
                          default=str)
    return CallToolResult(content=[TextContent(type="text", text=text)])


async def _serve() -> None:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import ListToolsResult, Tool

    async def list_tools(_ctx, _params) -> ListToolsResult:
        return ListToolsResult(tools=[
            Tool(name=t["name"], description=t["description"],
                 inputSchema=t["inputSchema"]) for t in TOOLS])

    async def call_tool(_ctx, params) -> CallToolResult:
        return _handle_call_tool(params.name, params.arguments)

    server = Server("cryptobrain", version=VERSION,
                    on_list_tools=list_tools, on_call_tool=call_tool)

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream,
                         server.create_initialization_options())


def run() -> int:
    """Entry point for `python main.py mcp`."""
    try:
        import mcp  # noqa: F401
    except Exception:
        print("mcp package not installed.  Install it with:  pip install mcp",
              file=sys.stderr)
        return 1
    try:
        asyncio.run(_serve())
        return 0
    except KeyboardInterrupt:
        return 0
