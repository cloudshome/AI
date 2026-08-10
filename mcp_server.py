#!/usr/bin/env python3
"""mcp_server.py — Minimal, dependency-free Model Context Protocol (MCP) server.

Implements JSON-RPC 2.0 over stdio for LLM integration (Claude Desktop, Cursor,
Arena, and MCP clients). Exposes grounded trading tools while strictly enforcing
a read-only permission map (no automatic order placement or gate bypass).
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from brain.ask import ask
from brain.brief import generate_morning_brief, post_trade_review
from brain.risk_gate import evaluate_risk_gate
from data.database import SignalDB
from data.symbols import normalize_symbol

SERVER_NAME = "cryptobrain-mcp"
SERVER_VERSION = "1.0.0"
PROTOCOL_VERSION = "2024-11-05"

# Read-only / research tools allowed. No order or approval bypass tools.
ALLOWED_TOOLS = {
    "ask": {
        "description": "Query grounded trading knowledge, setup expectancies, and playbooks with source citations.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Question to answer (e.g. 'which setups have positive expectancy in ranging markets?')"}
            },
            "required": ["query"],
        },
    },
    "tradestate": {
        "description": "View or update behavioral trader flags (angry, tired, revenge, chasing).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["get", "set", "clear"], "default": "get"},
                "angry": {"type": "boolean"},
                "tired": {"type": "boolean"},
                "revenge": {"type": "boolean"},
                "chasing": {"type": "boolean"},
                "note": {"type": "string"},
            },
        },
    },
    "risk": {
        "description": "Get current risk gate status, daily/weekly loss limits, and progression ladder.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    "health": {
        "description": "Run immune system health diagnostics and data freshness checks.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    "brief": {
        "description": "Generate cross-asset morning briefing across BTC, ETH, and GOLD.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbols": {"type": "array", "items": {"type": "string"}, "description": "List of symbols to brief"}
            },
        },
    },
    "postreview": {
        "description": "Generate post-trade review and MAE/MFE analytics for a scan ID.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "scan_id": {"type": "integer", "description": "Database scan ID to review"}
            },
            "required": ["scan_id"],
        },
    },
}


def handle_tool_call(name: str, args: dict[str, Any]) -> dict:
    if name not in ALLOWED_TOOLS:
        raise ValueError(f"Tool '{name}' is not permitted or does not exist. (Permission map enforced: read-only tools only)")

    if name == "ask":
        query = args.get("query", "")
        res = ask(query)
        return res

    elif name == "tradestate":
        action = args.get("action", "get")
        with SignalDB() as db:
            if action == "clear":
                db.set_trader_state(angry=False, tired=False, revenge=False, chasing=False, note="cleared via MCP")
            elif action == "set":
                db.set_trader_state(
                    angry=args.get("angry"),
                    tired=args.get("tired"),
                    revenge=args.get("revenge"),
                    chasing=args.get("chasing"),
                    note=args.get("note", "updated via MCP"),
                )
            return db.get_trader_state()

    elif name == "risk":
        with SignalDB() as db:
            return evaluate_risk_gate(db)

    elif name == "health":
        from brain.immune import run_health_check
        return run_health_check()

    elif name == "brief":
        syms = args.get("symbols")
        return generate_morning_brief(symbols=syms)

    elif name == "postreview":
        scan_id = int(args.get("scan_id", 0))
        return post_trade_review(scan_id)

    raise ValueError(f"Unhandled tool: {name}")


def process_message(msg: dict) -> Optional[dict]:
    msg_id = msg.get("id")
    method = msg.get("method")
    params = msg.get("params") or {}

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {
                    "tools": {},
                },
                "serverInfo": {
                    "name": SERVER_NAME,
                    "version": SERVER_VERSION,
                },
            },
        }

    elif method == "notifications/initialized":
        return None

    elif method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}

    elif method == "tools/list":
        tools_out = []
        for name, spec in ALLOWED_TOOLS.items():
            tools_out.append({
                "name": name,
                "description": spec["description"],
                "inputSchema": spec["inputSchema"],
            })
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": tools_out}}

    elif method == "tools/call":
        tool_name = params.get("name")
        tool_args = params.get("arguments") or {}
        try:
            res_data = handle_tool_call(tool_name, tool_args)
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [
                        {"type": "text", "text": json.dumps(res_data, indent=2, default=str)}
                    ]
                },
            }
        except Exception as exc:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {
                    "code": -32000,
                    "message": str(exc),
                },
            }

    else:
        if msg_id is not None:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {
                    "code": -32601,
                    "message": f"Method not found: {method}",
                },
            }
        return None


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
            resp = process_message(msg)
            if resp is not None:
                sys.stdout.write(json.dumps(resp) + "\n")
                sys.stdout.flush()
        except Exception as exc:
            err_resp = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"Parse error: {exc}"},
            }
            sys.stdout.write(json.dumps(err_resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
