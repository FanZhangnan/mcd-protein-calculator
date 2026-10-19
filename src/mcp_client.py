"""Minimal MCP (streamable HTTP) client for the McDonald's China menu service.

Token policy
------------
The token is NEVER stored in this file, in fixtures, in tests, or in the web UI.
It is resolved at runtime, in this order:

1. Environment variable ``MCD_MCP_TOKEN`` (may or may not carry a "Bearer " prefix)
2. ``~/.workbuddy/mcp.json`` -> mcpServers["mcd-mcp"].headers.Authorization
   (the local WorkBuddy config that already holds the connected server)

Nothing in this module logs or returns the token.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path.home() / ".workbuddy" / "mcp.json"
DEFAULT_SERVER_KEY = "mcd-mcp"
MCP_URL = "https://mcp.mcd.cn"


class McpError(RuntimeError):
    """Raised when the MCP transport or the business layer fails."""

    def __init__(self, message: str, *, stage: str = "", code: Any = None):
        super().__init__(message)
        self.stage = stage
        self.code = code


def _norm_bearer(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    return value if value.lower().startswith("bearer ") else f"Bearer {value}"


def resolve_token() -> str:
    """Return an Authorization header value, or "" when unavailable."""
    env = os.environ.get("MCD_MCP_TOKEN", "")
    if env.strip():
        return _norm_bearer(env)

    cfg_path = Path(os.environ.get("MCD_MCP_CONFIG", DEFAULT_CONFIG_PATH))
    key = os.environ.get("MCD_MCP_SERVER", DEFAULT_SERVER_KEY)
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text("utf-8"))
            headers = (cfg.get("mcpServers", {}).get(key, {}) or {}).get("headers", {}) or {}
            raw = headers.get("Authorization", "")
            return _norm_bearer(raw)
        except Exception:
            return ""
    return ""


def _parse_body(body: str) -> dict:
    body = body.strip()
    if not body:
        return {}
    if "data:" in body:
        for line in body.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                try:
                    return json.loads(line[5:].strip())
                except Exception:
                    continue
    try:
        return json.loads(body)
    except Exception:
        return {"_raw": body[:500]}


def _extract_payload(result: dict) -> dict:
    """Prefer structuredContent; fall back to the JSON tail of the text block."""
    if isinstance(result.get("structuredContent"), dict):
        return result["structuredContent"]
    for block in result.get("content", []) or []:
        if block.get("type") == "text":
            text = block.get("text", "")
            marker = "## Original Response"
            tail = text.split(marker, 1)[1].strip() if marker in text else text.strip()
            try:
                return json.loads(tail)
            except Exception:
                continue
    return {}


class McpClient:
    def __init__(self, url: str = MCP_URL, token: str | None = None, timeout: int = 30):
        self.url = url
        self._token = _norm_bearer(token) if token else resolve_token()
        self.timeout = timeout
        self._session_id: str | None = None

    @property
    def has_token(self) -> bool:
        return bool(self._token)

    def _post(self, payload: dict) -> tuple[int, dict, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": self._token,
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        req = urllib.request.Request(
            self.url, data=json.dumps(payload).encode(), headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.status, dict(resp.headers), resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read().decode("utf-8", "replace")
        except Exception as exc:  # network / dns / timeout
            raise McpError(f"transport error: {type(exc).__name__}", stage="transport") from exc

    def initialize(self) -> dict:
        if not self._token:
            raise McpError("no MCP token available", stage="auth")
        status, headers, body = self._post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "mai-mai-planner", "version": "1.0"},
                },
            }
        )
        if status != 200:
            payload = _parse_body(body)
            raise McpError(
                f"initialize failed: HTTP {status} {payload.get('msg', '')}".strip(),
                stage="initialize",
                code=payload.get("code"),
            )
        self._session_id = headers.get("Mcp-Session-Id") or headers.get("mcp-session-id")
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return _parse_body(body)

    def call(self, tool: str, arguments: dict | None = None) -> dict:
        """Call a tool and return the business payload ({success, code, data, ...})."""
        if not self._session_id:
            self.initialize()
        status, _headers, body = self._post(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": tool, "arguments": arguments or {}},
            }
        )
        if status != 200:
            payload = _parse_body(body)
            raise McpError(
                f"{tool} failed: HTTP {status} {payload.get('msg', '')}".strip(),
                stage=f"call:{tool}",
                code=payload.get("code"),
            )
        envelope = _parse_body(body)
        if "error" in envelope:
            err = envelope["error"] or {}
            raise McpError(
                f"{tool} rpc error: {err.get('message', 'unknown')}",
                stage=f"call:{tool}",
                code=err.get("code"),
            )
        result = envelope.get("result", {}) or {}
        if result.get("isError"):
            raise McpError(f"{tool} returned isError", stage=f"call:{tool}")
        return _extract_payload(result)

    def list_tools(self) -> list[dict]:
        if not self._session_id:
            self.initialize()
        status, _h, body = self._post(
            {"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}}
        )
        if status != 200:
            raise McpError(f"tools/list failed: HTTP {status}", stage="tools/list")
        return _parse_body(body).get("result", {}).get("tools", [])
