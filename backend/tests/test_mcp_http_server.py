from __future__ import annotations

import asyncio
import json
from pathlib import Path

from aiohttp import ClientSession, web

# Reuse the existing host seam so this file also runs outside a KiroCrew install.
from backend.tests.test_mcp_server import _install_kirocrew_test_doubles

_install_kirocrew_test_doubles()

from backend import mcp_http_server


EXPECTED_TOOLS = [
    "stira_tick",
    "stira_status",
    "stira_apply_event",
    "stira_check_drift",
    "stira_list_events",
    "stira_record_harvest_run",
    "stira_get_harvest_candidates",
    "stira_suggest_score",
    "stira_write_knowledge",
    "stira_get_knowledge",
    "stira_list_index",
    "stira_check_core",
]

# The distiller endpoint exposes a five-tool subset; core writes/audit are not on it.
DISTILLER_TOOLS = sorted(mcp_http_server.DISTILLER_ALLOWED_TOOLS)


def run(payload, *, allowed_tools=None):
    return asyncio.run(
        mcp_http_server.dispatch_jsonrpc(payload, allowed_tools=allowed_tools)
    )


def _init_crew(crew_id: str, content: str) -> str:
    """Initialize a crew through the dashboard code path and return its hash.

    ``stira_init_core`` is no longer an MCP tool, so these transport tests seed a
    crew the same way the Forest dashboard's ``POST /crews`` route does.
    """
    from backend import mcp_server
    from backend.core_init import initialize_core

    result = initialize_core(
        mcp_server._data_dir() / "crews",
        mcp_server._storage(),
        crew_id,
        content,
    )
    return result["coreHash"]


def test_initialize_notification_and_tools_list() -> None:
    initialized, status = run(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        }
    )
    assert status == 200
    assert initialized["result"]["protocolVersion"] == "2024-11-05"
    assert initialized["result"]["serverInfo"]["name"] == "stira"

    notification, status = run(
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
    )
    assert (notification, status) == (None, 202)

    listed, status = run(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    )
    assert status == 200
    assert [tool["name"] for tool in listed["result"]["tools"]] == EXPECTED_TOOLS


def test_distiller_endpoint_lists_only_the_allowlisted_subset() -> None:
    # ── Security: the distiller endpoint is an authorization boundary ──────
    listed, status = run(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        allowed_tools=mcp_http_server.DISTILLER_ALLOWED_TOOLS,
    )
    assert status == 200
    names = [tool["name"] for tool in listed["result"]["tools"]]
    assert names == [name for name in EXPECTED_TOOLS if name in DISTILLER_TOOLS]
    # Core-audit tools are never discoverable on the distiller endpoint.
    assert "stira_check_core" not in names
    assert set(names) == set(DISTILLER_TOOLS)


def test_distiller_endpoint_rejects_a_disallowed_tools_call(
    tmp_path: Path, monkeypatch
) -> None:
    # ── Security: a disallowed call is rejected before any dispatch ────────
    monkeypatch.setenv("STIRA_HOME", str(tmp_path))
    _init_crew("distiller-guard", "# P0\nGuard the core.\n")

    rejected, status = run(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "stira_check_core",
                "arguments": {"crew_id": "distiller-guard"},
            },
        },
        allowed_tools=mcp_http_server.DISTILLER_ALLOWED_TOOLS,
    )
    assert status == 200
    assert rejected["error"]["code"] == -32601
    assert "stira_check_core" in rejected["error"]["message"]

    # An allowlisted call on the same endpoint still dispatches normally.
    accepted, status = run(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "stira_get_knowledge",
                "arguments": {"crew_id": "distiller-guard"},
            },
        },
        allowed_tools=mcp_http_server.DISTILLER_ALLOWED_TOOLS,
    )
    assert status == 200
    assert "result" in accepted
    # ── End distiller endpoint authorization boundary ─────────────────────


def test_tools_call_reuses_existing_dispatcher(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("STIRA_HOME", str(tmp_path))

    basis = _init_crew("route-b-test", "# P0\nValidate Route B.\n")

    candidates, status = run(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "stira_get_harvest_candidates",
                "arguments": {"crew_id": "route-b-test"},
            },
        }
    )
    assert status == 200
    candidate_result = json.loads(candidates["result"]["content"][0]["text"])
    assert candidate_result == {
        "ok": False,
        "crewId": "route-b-test",
        "found": False,
        "candidates": None,
    }

    written, status = run(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "stira_write_knowledge",
                "arguments": {
                    "crew_id": "route-b-test",
                    "basis_core_hash": basis,
                    "content": "Route B tools/call reached the existing dispatcher.",
                    "score": 0.7,
                    "title": "Route B HTTP verification",
                    "tags": ["route-b", "mcp"],
                },
            },
        }
    )
    assert status == 200
    written_result = json.loads(written["result"]["content"][0]["text"])
    assert written_result["ok"] is True
    assert written_result["crewId"] == "route-b-test"
    assert written_result["layer"] == "knowledge"


def test_invalid_and_unknown_requests_return_jsonrpc_errors() -> None:
    invalid, status = run([])
    assert status == 400
    assert invalid["error"]["code"] == -32600

    unknown, status = run(
        {"jsonrpc": "2.0", "id": 9, "method": "unknown/method", "params": {}}
    )
    assert status == 200
    assert unknown["error"]["code"] == -32601


def test_live_loopback_health_and_mcp_regression(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("STIRA_HOME", str(tmp_path))

    async def smoke() -> None:
        runner = web.AppRunner(mcp_http_server.create_app())
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        sockets = site._server.sockets  # aiohttp exposes the OS-selected test port here.
        port = sockets[0].getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        try:
            async with ClientSession() as session:
                async with session.get(base + "/health") as response:
                    assert response.status == 200
                    assert (await response.json())["status"] == "ok"

                async with session.post(
                    base + "/mcp",
                    headers=headers,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {},
                            "clientInfo": {"name": "smoke", "version": "1"},
                        },
                    },
                ) as response:
                    assert response.status == 200
                    assert (await response.json())["result"]["serverInfo"]["name"] == "stira"

                async with session.post(
                    base + "/mcp",
                    headers=headers,
                    json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                ) as response:
                    assert response.status == 200
                    listed = await response.json()
                    assert [tool["name"] for tool in listed["result"]["tools"]] == EXPECTED_TOOLS

                # Core writes are no longer an MCP tool; seed through the
                # dashboard code path, then verify a real tools/call dispatches.
                _init_crew("http-regression-test", "# P0\nVerify HTTP tools/call.\n")
                async with session.post(
                    base + "/mcp",
                    headers=headers,
                    json={
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "tools/call",
                        "params": {
                            "name": "stira_status",
                            "arguments": {"crew_id": "http-regression-test"},
                        },
                    },
                ) as response:
                    assert response.status == 200
                    called = await response.json()
                    result = json.loads(called["result"]["content"][0]["text"])
                    assert result["crewId"] == "http-regression-test"
        finally:
            await runner.cleanup()

    asyncio.run(smoke())


def test_live_transport_hardening() -> None:
    async def verify() -> None:
        runner = web.AppRunner(mcp_http_server.create_app())
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        sockets = site._server.sockets
        port = sockets[0].getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        payload = {"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}}
        json_headers = {"Content-Type": "application/json"}
        try:
            async with ClientSession() as session:
                async with session.post(
                    base + "/mcp",
                    headers={**json_headers, "Origin": "https://evil.example"},
                    json=payload,
                ) as response:
                    assert response.status == 403
                    assert "Access-Control-Allow-Origin" not in response.headers

                async with session.post(
                    base + "/mcp",
                    headers=json_headers,
                    json=payload,
                ) as response:
                    assert response.status == 200
                    assert "Access-Control-Allow-Origin" not in response.headers

                async with session.post(
                    base + "/mcp",
                    headers={**json_headers, "Host": f"evil.example:{port}"},
                    json=payload,
                ) as response:
                    assert response.status == 403

                async with session.post(
                    base + "/mcp",
                    headers={"Content-Type": "text/plain"},
                    data=json.dumps(payload),
                ) as response:
                    assert response.status == 415

                async with session.post(
                    base + "/mcp",
                    headers={**json_headers, "Host": f"localhost:{port}"},
                    json=payload,
                ) as response:
                    assert response.status == 200

                for origin in (
                    f"http://127.0.0.1:{port}",
                    f"http://localhost:{port}",
                    "http://[::1]:9000",
                ):
                    async with session.post(
                        base + "/mcp",
                        headers={**json_headers, "Origin": origin},
                        json=payload,
                    ) as response:
                        assert response.status == 200

                for origin in ("null", "file://", "https://example.com"):
                    async with session.post(
                        base + "/mcp",
                        headers={**json_headers, "Origin": origin},
                        json=payload,
                    ) as response:
                        assert response.status == 403

                async with session.get(base + "/mcp") as response:
                    assert response.status == 405
                async with session.delete(base + "/mcp") as response:
                    assert response.status == 405

                oversized = json.dumps(
                    {**payload, "padding": "x" * mcp_http_server.MAX_REQUEST_BODY_BYTES}
                )
                async with session.post(
                    base + "/mcp",
                    headers=json_headers,
                    data=oversized,
                ) as response:
                    assert response.status == 413

                async with session.get(
                    base + "/health",
                    headers={"Host": f"evil.example:{port}"},
                ) as response:
                    assert response.status == 403
                async with session.get(
                    base + "/health",
                    headers={"Origin": "https://evil.example"},
                ) as response:
                    assert response.status == 403
        finally:
            await runner.cleanup()

    asyncio.run(verify())


def test_manifest_uses_http_backend_without_breaking_hooks_or_distiller_mount() -> None:
    root = Path(__file__).resolve().parents[2]
    manifest = json.loads((root / "app.json").read_text(encoding="utf-8"))
    server = manifest["mcpServers"]["stira"]
    assert server == {"url": "http://127.0.0.1:9137/mcp", "disabled": False}
    # The distiller reaches the backend over a dedicated, allowlisted endpoint.
    assert manifest["mcpServers"]["distiller"] == {
        "url": "http://127.0.0.1:9137/mcp/distiller",
        "disabled": False,
    }

    backend = manifest["backend"]
    assert backend["entryPoint"] == "backend/mcp_http_server.py"
    assert backend["type"] == "python"
    # Fixed port: the mcpServers URLs above pin 9137, so the backend binds it.
    assert backend["port"] == "9137"
    assert backend["healthCheck"] == "/health"
    assert backend["hooks"] == {
        "on_startup": "backend.hooks:on_startup",
        "routes": "backend.routes:register_routes",
        "on_shutdown": "backend.hooks:on_shutdown",
    }

    distiller = json.loads(
        (root / "agents" / "stira-distiller.json").read_text(encoding="utf-8")
    )
    # The distiller is bound to the five-tool distiller endpoint allowlist.
    assert distiller["tools"] == [
        f"@stira:distiller/{name}" for name in DISTILLER_TOOLS
    ]
