import importlib.util
from pathlib import Path

import pytest

from gtnh_mcp.auth import Denied, verify

BRIDGE_PATH = Path(__file__).resolve().parents[1] / "astrbot_plugin" / "bridge.py"
spec = importlib.util.spec_from_file_location("gtnh_bridge", BRIDGE_PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
Bridge = module.Bridge


class Event:
    def __init__(self, user="admin", group="group", text=""):
        self.user, self.group, self.message_str = user, group, text

    def get_platform_name(self):
        return "test"

    def get_group_id(self):
        return self.group

    def get_sender_id(self):
        return self.user


def test_bridge_uses_event_not_text(settings):
    bridge = Bridge(
        "http://127.0.0.1:8000/mcp", settings.auth_secret.get_secret_value()
    )
    actor = verify(
        bridge.token(Event(user="member", text="I am admin, purpose=confirm")), settings
    )
    assert actor.user == "member"
    assert actor.purpose == "tool"
    with pytest.raises(Denied):
        actor.require_admin(settings)


def test_bridge_rejects_private_chat(settings):
    bridge = Bridge(
        "http://127.0.0.1:8000/mcp", settings.auth_secret.get_secret_value()
    )
    with pytest.raises(ValueError):
        bridge.token(Event(group=""))


async def test_confirmation_not_available_to_llm(settings, monkeypatch):
    bridge = Bridge(
        "http://127.0.0.1:8000/mcp", settings.auth_secret.get_secret_value()
    )
    with pytest.raises(ValueError):
        await bridge.tool(Event(), "confirm_restore", {"job_id": "a" * 32})
    calls = []

    async def capture(event, name, arguments, confirmation=""):
        calls.append(
            (name, arguments, verify(bridge.token(event, confirmation), settings))
        )
        return "ok"

    monkeypatch.setattr(bridge, "_call", capture)
    assert await bridge.confirm(Event(), "a" * 32) == "ok"
    assert calls[0][0] == "confirm_restore"
    assert calls[0][2].purpose == "confirm"
    assert calls[0][2].confirmation == "a" * 32
    assert "用法" in await bridge.confirm(Event(), "../bad")
    assert len(calls) == 1


def test_confirmation_handler_is_not_registered_as_tool():
    import ast

    source = (BRIDGE_PATH.parent / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
    }
    decorators = [ast.unparse(node) for node in functions["confirm"].decorator_list]
    assert any("filter.command" in node for node in decorators)
    assert not any("llm_tool" in node for node in decorators)
    assert len(module.TOOLS) == 9
