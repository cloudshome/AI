"""MCP server tests — a REAL MCP client talking to `python main.py mcp`.

Two client implementations are exercised:

  1. a raw stdio JSON-RPC 2.0 client (protocol-level, no SDK) — always runs;
  2. the official ``mcp`` SDK client (mcp.client.stdio.ClientSession) — runs
     whenever the SDK is installed (it is in requirements.txt).

Both spawn the server as a subprocess, exactly like Claude Desktop / Cursor
would, so the test doubles as the integration check for the handoff step
"real MCP client test (Claude Desktop/Cursor → python main.py mcp)".
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MAIN = ROOT / "main.py"

TOOL_NAMES = ["cryptobrain_health", "cryptobrain_morning", "cryptobrain_scan",
              "cryptobrain_desk", "cryptobrain_risk", "cryptobrain_ask",
              "cryptobrain_pending", "cryptobrain_stats", "cryptobrain_paper",
              "cryptobrain_learn"]


@pytest.fixture
def server_env(monkeypatch, tmp_path):
    monkeypatch.setenv("DEMO_MODE", "1")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "mcp.db"))
    env = dict(os.environ)
    env["DEMO_MODE"] = "1"
    env["DB_PATH"] = str(tmp_path / "mcp.db")
    env["PYTHONUNBUFFERED"] = "1"
    return env


class RawMcpClient:
    """Minimal newline-delimited JSON-RPC 2.0 MCP client over stdio."""

    def __init__(self, env: dict):
        self.proc = subprocess.Popen(
            [sys.executable, str(MAIN), "mcp"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, env=env,
            cwd=str(ROOT), bufsize=1,
        )
        self._id = 0

    def _send(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        msg = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            msg["params"] = params
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        assert line.strip(), "server closed stdout — stderr: " + self.proc.stderr.read()[-500:]
        resp = json.loads(line)
        assert resp.get("id") == self._id, resp
        assert "error" not in resp, resp
        return resp["result"]

    def initialize(self) -> dict:
        return self._send("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "raw-test-client", "version": "1.0"},
        })

    def notify_initialized(self) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        self.proc.stdin.flush()

    def list_tools(self) -> list[dict]:
        return self._send("tools/list")["tools"]

    def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        result = self._send("tools/call", {"name": name, "arguments": arguments or {}})
        assert result["isError"] in (True, False, None)
        text = result["content"][0]["text"]
        return json.loads(text)

    def close(self) -> None:
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()


def test_mcp_raw_protocol_handshake(server_env):
    """Raw JSON-RPC client: initialize → tools/list → tools/call."""
    client = RawMcpClient(server_env)
    try:
        init = client.initialize()
        assert "protocolVersion" in init
        assert init["serverInfo"]["name"] == "cryptobrain"
        client.notify_initialized()
        tools = client.list_tools()
        names = [t["name"] for t in tools]
        assert names == TOOL_NAMES
        # every tool advertises a JSON schema
        for t in tools:
            assert t["inputSchema"]["type"] == "object"

        health = client.call_tool("cryptobrain_health")
        assert health["ok"] is True
        assert health["data"]["mode"] == "demo"

        risk = client.call_tool("cryptobrain_risk")
        assert risk["allowed"] is True
        assert risk["details"]["progression"]["level"] == "student"

        ask = client.call_tool("cryptobrain_ask",
                               {"question": "what is pending?"})
        assert ask["intent"] == "pending"

        scan = client.call_tool("cryptobrain_scan", {"symbol": "BTCUSDT"})
        assert scan["signal"]["asset"] == "BTCUSDT"
    finally:
        client.close()


def test_mcp_unknown_tool_returns_error(server_env):
    client = RawMcpClient(server_env)
    try:
        client.initialize()
        result = client._send("tools/call", {"name": "cryptobrain_nope",
                                             "arguments": {}})
        text = result["content"][0]["text"]
        assert "unknown tool" in json.loads(text)["error"]
    finally:
        client.close()


def test_mcp_server_starts_without_sdk(server_env):
    """The CLI entry point prints a clear error when the SDK is missing."""
    import importlib.util
    if importlib.util.find_spec("mcp"):
        pytest.skip("mcp SDK is installed — fallback path not exercised")
    from ai.mcp_server import run  # noqa: F401  (import must not fail)
    proc = subprocess.run([sys.executable, str(MAIN), "mcp"], env=server_env,
                          capture_output=True, text=True, timeout=30,
                          cwd=str(ROOT))
    assert proc.returncode == 1
    assert "pip install mcp" in proc.stderr


@pytest.mark.skipif(importlib.util.find_spec("mcp") is None,
                    reason="mcp SDK not installed")
def test_mcp_official_sdk_client(server_env):
    """The REAL MCP client test: official SDK over stdio."""
    import asyncio
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def main():
        params = StdioServerParameters(
            command=sys.executable, args=[str(MAIN), "mcp"],
            env=server_env, cwd=str(ROOT),
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                assert init.server_info.name == "cryptobrain"
                tools = await session.list_tools()
                names = [t.name for t in tools.tools]
                assert names == TOOL_NAMES

                res = await session.call_tool("cryptobrain_health", {})
                data = json.loads(res.content[0].text)
                assert data["ok"] is True

                res = await session.call_tool("cryptobrain_ask",
                                              {"question": "is the risk gate open?"})
                data = json.loads(res.content[0].text)
                assert data["intent"] == "risk"
                assert data["data"]["allowed"] is True

                res = await session.call_tool("cryptobrain_morning", {})
                data = json.loads(res.content[0].text)
                assert len(data["assets"]) == 3

                res = await session.call_tool("cryptobrain_paper", {})
                assert "paper" in json.loads(res.content[0].text)

    asyncio.run(main())
