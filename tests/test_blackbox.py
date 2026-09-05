"""Independent processes, real HTTP/MCP/RCON and actual temporary world files."""

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from conftest import make_archive
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from test_auth_rcon import token
from test_bridge import Bridge, Event

pytestmark = pytest.mark.blackbox
ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def services(settings, tmp_path, monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    settings.mcp_port, settings.rcon_port = free_port(), free_port()
    settings.rcon_timeout = 1
    docker_port, helper_port = free_port(), free_port()
    env = {
        **os.environ,
        "PYTHONIOENCODING": "utf-8",
        "BLACKBOX_DOCKER_PORT": str(docker_port),
        "BLACKBOX_HELPER_PORT": str(helper_port),
    }
    for name in type(settings).model_fields:
        value = getattr(settings, name)
        if hasattr(value, "get_secret_value"):
            value = value.get_secret_value()
        env[name.upper()] = json.dumps(value) if isinstance(value, list) else str(value)
    make_archive(settings.backup_dir / "sample.tar.gz")
    processes, logs = {}, {}

    def launch(mode, health):
        log = (tmp_path / f"{mode}-{len(logs)}.log").open("w", encoding="utf-8")
        logs[log.name] = log
        process = subprocess.Popen(
            [sys.executable, str(ROOT / "tests" / "blackbox_runtime.py"), mode],
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        processes[mode] = process
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                pytest.fail(Path(log.name).read_text(encoding="utf-8"))
            try:
                if httpx.get(health, timeout=0.5).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        pytest.fail(
            "service failed to become healthy: "
            + Path(log.name).read_text(encoding="utf-8")
        )

    urls = {
        "upstream": f"http://127.0.0.1:{docker_port}/observations",
        "helper": f"http://127.0.0.1:{helper_port}/health",
        "mcp": f"http://127.0.0.1:{settings.mcp_port}/health",
    }
    try:
        for mode, url in urls.items():
            launch(mode, url)
        yield {
            "settings": settings,
            "url": f"http://127.0.0.1:{settings.mcp_port}/mcp",
            "docker": f"http://127.0.0.1:{docker_port}",
            "helper": f"http://127.0.0.1:{helper_port}",
            "processes": processes,
            "launch": launch,
            "health": urls,
            "logs": logs,
        }
    finally:
        for process in reversed(list(processes.values())):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        for log in logs.values():
            log.close()


async def call(services, name, arguments=None, **claims):
    async with streamablehttp_client(
        services["url"],
        headers={"Authorization": "Bearer " + token(services["settings"], **claims)},
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await session.call_tool(name, arguments or {})


def data(result):
    assert not result.isError, result
    value = result.structuredContent
    if value is None:
        value = json.loads(result.content[0].text)
    return value.get("result", value) if isinstance(value, dict) else value


async def await_job(services, job_id):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        job = data(await call(services, "restore_status", {"job_id": job_id}))[0]
        if job["phase"] in {
            "succeeded",
            "rolled_back",
            "failed",
            "manual_intervention",
        }:
            return job
        await asyncio.sleep(0.1)
    pytest.fail("restore job timed out")


async def test_full_blackbox(services):
    settings = services["settings"]
    # Auth applies to protocol discovery as well as tool calls.
    async with httpx.AsyncClient(trust_env=False) as client:
        for encoded in [
            "",
            "invalid",
            token(settings, group="wrong"),
            token(settings, exp=1),
        ]:
            response = await client.post(
                services["url"],
                headers={
                    **({"Authorization": "Bearer " + encoded} if encoded else {}),
                    "Accept": "application/json, text/event-stream",
                },
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {"name": "blackbox", "version": "1"},
                    },
                },
            )
            assert response.status_code in {401, 403}

    async with streamablehttp_client(
        services["url"], headers={"Authorization": "Bearer " + token(settings)}
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
            assert len(tools) == 11
            for tool in tools:
                assert not {"user", "role", "platform", "group", "token"} & set(
                    tool.inputSchema.get("properties", {})
                )

    bridge = Bridge(services["url"], settings.auth_secret.get_secret_value())
    assert "Alice" in await bridge.tool(Event(user="member"), "list_players", {})
    assert "OK: say hello" in await bridge.tool(
        Event(user="member"), "announce", {"message": "hello"}
    )
    assert (
        "save-all" in data(await call(services, "save_world", sub="member"))["response"]
    )
    assert (
        data(await call(services, "add_whitelist", {"player": "Alice"}))["response"]
        == "OK: whitelist add Alice"
    )
    assert (
        await call(services, "add_whitelist", {"player": "Mallory"}, sub="member")
    ).isError
    assert (await call(services, "announce", {"message": "hi\nstop"})).isError
    assert (await call(services, "execute_command", {"command": "stop"})).isError

    backup = data(await call(services, "list_backups", sub="member"))[0]
    assert (
        await call(
            services, "request_restore", {"backup_id": backup["id"]}, sub="member"
        )
    ).isError
    job = data(await call(services, "request_restore", {"backup_id": backup["id"]}))
    assert job["phase"] == "pending"
    assert (settings.server_root / "Worlds/data.txt").read_text() == "old"
    assert (await call(services, "confirm_restore", {"job_id": job["id"]})).isError
    assert (
        await call(
            services,
            "confirm_restore",
            {"job_id": job["id"]},
            purpose="confirm",
            confirmation="b" * 32,
        )
    ).isError
    assert (
        await call(services, "restore_status", {"job_id": job["id"]}, sub="member")
    ).isError
    await bridge.confirm(Event(), job["id"])
    assert (await await_job(services, job["id"]))["phase"] == "succeeded"
    for name in ("Worlds", "visualprospecting"):
        assert (settings.server_root / name / "data.txt").read_text() == "new"
        assert (
            settings.server_root
            / ".gtnh-restore"
            / job["id"]
            / "previous"
            / name
            / "data.txt"
        ).read_text() == "old"
    observations = httpx.get(services["docker"] + "/observations").json()
    assert observations["commands"].count("stop") == 1
    assert observations["policy"]["Name"] == "unless-stopped"
    await bridge.confirm(Event(), job["id"])
    assert (
        httpx.get(services["docker"] + "/observations").json()["commands"].count("stop")
        == 1
    )

    # Restart the actual helper process and observe persisted jobs through MCP.
    services["processes"]["helper"].terminate()
    services["processes"]["helper"].wait(timeout=5)
    services["launch"]("helper", services["health"]["helper"])
    assert (
        data(await call(services, "restore_status", {"job_id": job["id"]}))[0]["phase"]
        == "succeeded"
    )

    # A changed archive cancels before issuing stop.
    pending = data(await call(services, "request_restore", {"backup_id": backup["id"]}))
    make_archive(
        settings.backup_dir / "sample.tar.gz",
        [("Worlds/data.txt", b"third"), ("visualprospecting/data.txt", b"third")],
    )
    await bridge.confirm(Event(), pending["id"])
    assert (await await_job(services, pending["id"]))["phase"] == "failed"
    assert (
        httpx.get(services["docker"] + "/observations").json()["commands"].count("stop")
        == 1
    )

    # New-world startup fails at the Docker boundary; actual worker rolls back.
    httpx.post(services["docker"] + "/behavior", json={"fail_next_start": True})
    pending = data(await call(services, "request_restore", {"backup_id": backup["id"]}))
    await bridge.confirm(Event(), pending["id"])
    assert (await await_job(services, pending["id"]))["phase"] == "rolled_back"
    for name in ("Worlds", "visualprospecting"):
        assert (settings.server_root / name / "data.txt").read_text() == "new"
    assert not (settings.runtime_dir / "maintenance.json").exists()

    # An unconfirmed stop must leave files intact and hold the maintenance gate.
    httpx.post(services["docker"] + "/behavior", json={"ignore_stop": True})
    pending = data(await call(services, "request_restore", {"backup_id": backup["id"]}))
    await bridge.confirm(Event(), pending["id"])
    assert (await await_job(services, pending["id"]))["phase"] == "manual_intervention"
    assert (await call(services, "save_world")).isError
    assert (settings.server_root / "Worlds/data.txt").read_text() == "new"
    assert httpx.get(services["docker"] + "/observations").json()["running"]
    # Inspect logs for accidental secret disclosure.
    for path, log in services["logs"].items():
        log.flush()
        contents = Path(path).read_text(encoding="utf-8")
        assert settings.auth_secret.get_secret_value() not in contents
        assert settings.rcon_password.get_secret_value() not in contents


async def test_undo_blackbox(services):
    settings = services["settings"]
    bridge = Bridge(services["url"], settings.auth_secret.get_secret_value())
    backup = data(await call(services, "list_backups"))[0]
    original = data(
        await call(services, "request_restore", {"backup_id": backup["id"]})
    )
    await bridge.confirm(Event(), original["id"])
    assert (await await_job(services, original["id"]))["phase"] == "succeeded"
    assert (
        await call(
            services, "request_undo_restore", {"job_id": original["id"]}, sub="member"
        )
    ).isError
    response = json.loads(
        await bridge.tool(Event(), "request_undo_restore", {"job_id": original["id"]})
    )
    undo = response.get("result", response)
    assert undo["phase"] == "pending"
    assert undo["undo_of"] == original["id"]
    assert (settings.server_root / "Worlds/data.txt").read_text() == "new"
    assert (await call(services, "confirm_restore", {"job_id": undo["id"]})).isError
    await bridge.confirm(Event(), undo["id"])
    assert (await await_job(services, undo["id"]))["phase"] == "succeeded"
    for name in ("Worlds", "visualprospecting"):
        assert (settings.server_root / name / "data.txt").read_text() == "old"
        assert (
            settings.server_root
            / ".gtnh-restore"
            / undo["id"]
            / "previous"
            / name
            / "data.txt"
        ).read_text() == "new"
    await bridge.confirm(Event(), undo["id"])
    assert (
        httpx.get(services["docker"] + "/observations").json()["commands"].count("stop")
        == 2
    )
