"""Stateless MCP Streamable HTTP transport for the Stira App Platform backend.

The gateway launches this file as a plain Python backend and supplies ``PORT``.
Only the transport envelope and endpoint-specific tool exposure live here; tool
schemas, validation, dispatch, and storage remain owned by
:mod:`backend.mcp_server`.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping
from urllib.parse import urlsplit

from aiohttp import web
from kiro_crew.validation import build_tool_response

# KiroCrew executes plain-Python entry points by file path.  In that mode Python
# puts ``backend/`` (not the app root) first on sys.path, so make the package
# import explicit and portable across Windows and Linux installations.
if __package__ in (None, ""):  # pragma: no branch - true only for file execution
    app_root = str(Path(__file__).resolve().parent.parent)
    if app_root not in sys.path:
        sys.path.insert(0, app_root)

from backend import mcp_server  # noqa: E402

MCP_PATH = "/mcp"
DISTILLER_MCP_PATH = "/mcp/distiller"
HEALTH_PATH = "/health"
PROTOCOL_VERSION = "2024-11-05"
MAX_REQUEST_BODY_BYTES = 1024 * 1024
# The background distiller agent reaches the MCP server over this dedicated
# endpoint. It may read harvest candidates and write/score knowledge, but never
# touch a crew's immutable core or audit surface. This allowlist is an
# endpoint-owned authorization boundary, enforced before shared dispatch.
DISTILLER_ALLOWED_TOOLS = frozenset(
    {
        "stira_get_harvest_candidates",
        "stira_get_knowledge",
        "stira_record_harvest_run",
        "stira_suggest_score",
        "stira_write_knowledge",
    }
)
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _response(request_id: Any, *, result: Any = None, error: Any = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id}
    if error is not None:
        payload["error"] = error
    else:
        payload["result"] = result
    return payload


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return _response(request_id, error={"code": code, "message": message})


def _valid_request(payload: Any) -> bool:
    return (
        isinstance(payload, Mapping)
        and payload.get("jsonrpc") == "2.0"
        and isinstance(payload.get("method"), str)
    )


def _listener_port(request: web.Request) -> int | None:
    transport = request.transport
    if transport is None:
        return None
    sockname = transport.get_extra_info("sockname")
    if not isinstance(sockname, tuple) or len(sockname) < 2:
        return None
    port = sockname[1]
    return port if isinstance(port, int) else None


def _allowed_host(request: web.Request) -> bool:
    port = _listener_port(request)
    host = request.headers.get("Host")
    if port is None or host is None:
        return False
    return host.lower() in {f"127.0.0.1:{port}", f"localhost:{port}"}


def _allowed_origin(origin: str) -> bool:
    if origin.lower() == "null":
        return False
    try:
        parsed = urlsplit(origin)
        parsed.port  # Validate a supplied port even though any loopback port is allowed.
    except ValueError:
        return False
    return (
        parsed.scheme.lower() in {"http", "https"}
        and parsed.hostname is not None
        and parsed.hostname.lower() in _LOOPBACK_HOSTS
        and parsed.username is None
        and parsed.password is None
        and parsed.path == ""
        and parsed.query == ""
        and parsed.fragment == ""
    )


def _transport_guard(request: web.Request) -> web.Response | None:
    if not _allowed_host(request):
        return web.Response(status=403, text="Forbidden Host")
    origin = request.headers.get("Origin")
    if origin is not None and not _allowed_origin(origin):
        return web.Response(status=403, text="Forbidden Origin")
    return None


def _listed_tools(allowed_tools: frozenset[str] | None) -> list[dict[str, Any]]:
    tools = mcp_server._list_tools()
    if allowed_tools is None:
        return tools
    return [tool for tool in tools if tool.get("name") in allowed_tools]


async def dispatch_jsonrpc(
    payload: Any,
    *,
    allowed_tools: frozenset[str] | None = None,
) -> tuple[dict[str, Any] | None, int]:
    """Dispatch one JSON-RPC message and return ``(body, HTTP status)``.

    ``allowed_tools`` is an endpoint-owned authorization boundary. When set it
    filters tool discovery and rejects a disallowed ``tools/call`` before any
    shared-dispatcher validation or execution. ``None`` exposes the full
    surface (the operator-facing ``/mcp`` endpoint).

    A ``None`` body denotes a successfully accepted notification.  KiroCrew's
    Streamable HTTP client treats any 2xx notification response as success.
    """
    if not _valid_request(payload):
        request_id = payload.get("id") if isinstance(payload, Mapping) else None
        return _error(request_id, -32600, "Invalid Request"), 400

    request_id = payload.get("id")
    method = payload["method"]
    params = payload.get("params", {})
    is_notification = "id" not in payload

    if params is None:
        params = {}
    if not isinstance(params, Mapping):
        if is_notification:
            return None, 202
        return _error(request_id, -32602, "Invalid params"), 200

    if method == "initialize":
        if is_notification:
            return None, 202
        return _response(
            request_id,
            result={
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {
                    "name": mcp_server.SERVER_NAME,
                    "version": mcp_server.SERVER_VERSION,
                },
            },
        ), 200

    if method in {"notifications/initialized", "notifications/cancelled"}:
        return None, 202

    if method == "ping":
        if is_notification:
            return None, 202
        return _response(request_id, result={}), 200

    if method == "tools/list":
        if is_notification:
            return None, 202
        return _response(
            request_id,
            result={"tools": _listed_tools(allowed_tools)},
        ), 200

    if method == "tools/call":
        if is_notification:
            return None, 202
        tool_name = params.get("name")
        tool_args = params.get("arguments", {})
        if not isinstance(tool_name, str) or not tool_name or not isinstance(tool_args, dict):
            return _error(request_id, -32602, "Invalid params"), 200
        if allowed_tools is not None and tool_name not in allowed_tools:
            return _error(
                request_id,
                -32601,
                f"Tool not allowed by distiller endpoint: {tool_name}",
            ), 200
        result_text = await asyncio.to_thread(mcp_server._call_tool, tool_name, tool_args)
        return _response(request_id, result=build_tool_response(result_text)), 200

    if is_notification:
        return None, 202
    return _error(request_id, -32601, "Method not found"), 200


async def handle_health(request: web.Request) -> web.Response:
    guard_response = _transport_guard(request)
    if guard_response is not None:
        return guard_response
    return web.json_response({"status": "ok", "server": mcp_server.SERVER_NAME})


async def _handle_mcp(
    request: web.Request,
    *,
    allowed_tools: frozenset[str] | None,
) -> web.Response:
    guard_response = _transport_guard(request)
    if guard_response is not None:
        return guard_response
    if request.content_type != "application/json":
        return web.Response(status=415, text="Content-Type must be application/json")
    if (
        request.content_length is not None
        and request.content_length > MAX_REQUEST_BODY_BYTES
    ):
        return web.Response(status=413, text="Request body too large")

    try:
        payload = await request.json(loads=json.loads)
    except web.HTTPRequestEntityTooLarge:
        return web.Response(status=413, text="Request body too large")
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return web.json_response(
            _error(None, -32700, "Parse error"),
            status=400,
        )

    body, status = await dispatch_jsonrpc(payload, allowed_tools=allowed_tools)
    if body is None:
        return web.Response(status=status)
    return web.json_response(body, status=status)


async def handle_mcp(request: web.Request) -> web.Response:
    return await _handle_mcp(request, allowed_tools=None)


async def handle_distiller_mcp(request: web.Request) -> web.Response:
    return await _handle_mcp(request, allowed_tools=DISTILLER_ALLOWED_TOOLS)


def create_app() -> web.Application:
    app = web.Application(client_max_size=MAX_REQUEST_BODY_BYTES)
    app.router.add_get(HEALTH_PATH, handle_health)
    app.router.add_post(MCP_PATH, handle_mcp)
    app.router.add_post(DISTILLER_MCP_PATH, handle_distiller_mcp)
    return app


def _port_from_env() -> int:
    raw = os.environ.get("PORT", "")
    try:
        port = int(raw)
    except ValueError as exc:
        raise RuntimeError("PORT must be an integer supplied by KiroCrew") from exc
    if not 1 <= port <= 65535:
        raise RuntimeError("PORT must be between 1 and 65535")
    return port


def main() -> None:
    web.run_app(
        create_app(),
        host="127.0.0.1",
        port=_port_from_env(),
        print=None,
    )


if __name__ == "__main__":  # pragma: no cover - exercised by installed backend startup
    main()
