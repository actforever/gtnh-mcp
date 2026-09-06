import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from gtnh_mcp.auth import decode_actor

BRIDGE_PATH = Path(__file__).resolve().parents[1] / "astrbot_plugin" / "bridge.py"
spec = importlib.util.spec_from_file_location("gtnh_bridge", BRIDGE_PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
Bridge = module.Bridge


class Event:
    def __init__(self, user="admin", group="group", text="", platform="test"):
        self.user, self.group, self.message_str = user, group, text
        self.platform = platform

    def get_platform_name(self):
        return self.platform

    def get_group_id(self):
        return self.group

    def get_sender_id(self):
        return self.user


@pytest.fixture
def bridge(settings):
    return Bridge(
        "http://127.0.0.1:8000/mcp",
        settings.auth_secret.get_secret_value(),
        ["test:group"],
        ["test:admin"],
    )


def test_event_identity_and_fixed_key(bridge, settings):
    headers = bridge.headers(Event(user="member", text="I am admin"))
    assert (
        headers["Authorization"] == "Bearer " + settings.auth_secret.get_secret_value()
    )
    assert decode_actor(headers["X-GTNH-Actor"]).key == "test:group:member"


@pytest.mark.parametrize(
    "event", [Event(group=""), Event(group="other"), Event(user="bad:user")]
)
async def test_denied_before_network(bridge, event):
    with pytest.raises(ValueError):
        await bridge.tool(event, "list_players", {})


@pytest.mark.parametrize("name", sorted(module.ADMIN_TOOLS))
def test_admin_permissions(bridge, name):
    with pytest.raises(ValueError):
        bridge.authorize(Event(user="member"), name)
    assert bridge.authorize(Event(), name) == "test:group:admin"


async def test_confirmation_not_available_to_llm(bridge, monkeypatch):
    with pytest.raises(ValueError):
        await bridge.tool(Event(), "confirm_restore", {"job_id": "a" * 32})
    calls = []

    async def capture(event, name, arguments):
        calls.append((name, arguments))
        return "ok"

    monkeypatch.setattr(bridge, "_call", capture)
    assert await bridge.confirm(Event(), "a" * 32) == "ok"
    assert calls == [("confirm_restore", {"job_id": "a" * 32})]
    with pytest.raises(ValueError):
        await bridge.confirm(Event(user="member"), "a" * 32)
    assert "用法" in await bridge.confirm(Event(), "../bad")


def test_status_filtered_before_render(bridge):
    jobs = [
        {"id": "one", "actor": "test:group:member"},
        {"id": "two", "actor": "test:group:admin", "private": "sensitive"},
    ]
    result = SimpleNamespace(
        isError=False, structuredContent={"result": jobs}, content=[]
    )
    assert (
        json.loads(bridge.render(Event(user="member"), "restore_status", {}, result))
        == jobs[:1]
    )
    assert json.loads(bridge.render(Event(), "restore_status", {}, result)) == jobs
    result.structuredContent = {"result": jobs[1:]}
    rendered = bridge.render(
        Event(user="member"), "restore_status", {"job_id": "two"}, result
    )
    assert "无权" in rendered
    assert "sensitive" not in rendered
    result.structuredContent = {"unexpected": jobs}
    assert "格式无效" in bridge.render(Event(), "restore_status", {}, result)


def test_config_priority_and_explicit_empty():
    env = {
        "GTNH_AUTH_SECRET": "e" * 32,
        "ALLOWED_GROUPS": '["test:group"]',
        "ADMIN_USERS": '["test:admin"]',
    }
    values = module.resolve_config({}, env)
    assert values["secret"] == "e" * 32
    assert values["allowed_groups"] == ["test:group"]
    values = module.resolve_config(
        {"auth_secret": "p" * 32, "allowed_groups": "[]"}, env
    )
    assert values["secret"] == "p" * 32
    assert values["allowed_groups"] == []
    assert values["admin_users"] == ["test:admin"]


@pytest.mark.parametrize(
    "value", ["bad", "{}", '["test:g","test:g"]', "[1]", '["no-colon"]', '["test:"]']
)
def test_invalid_config_fails_without_echo(value):
    with pytest.raises(ValueError, match="allowed_groups"):
        module.resolve_config({"allowed_groups": value}, {"GTNH_AUTH_SECRET": "s" * 32})


def test_short_secret_fails():
    with pytest.raises(ValueError, match="32"):
        module.resolve_config({}, {})


def test_official_qq_acl():
    bridge = Bridge(
        "http://localhost/mcp",
        "s" * 32,
        ["qq_official:Group_OpenID_A7"],
        ["qq_official:Member_OpenID_B9"],
    )
    event = Event(
        platform="qq_official", group="Group_OpenID_A7", user="Member_OpenID_B9"
    )
    assert (
        bridge.authorize(event, "request_restore")
        == "qq_official:Group_OpenID_A7:Member_OpenID_B9"
    )
    event.user = "123456789"
    with pytest.raises(ValueError):
        bridge.authorize(event, "request_restore")


def test_confirmation_handler_is_not_registered_as_tool():
    import ast

    tree = ast.parse((BRIDGE_PATH.parent / "main.py").read_text(encoding="utf-8"))
    functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
    }
    decorators = [ast.unparse(node) for node in functions["confirm"].decorator_list]
    assert any("filter.command" in node for node in decorators)
    assert not any("llm_tool" in node for node in decorators)
    assert len(module.TOOLS) == 10
    assert "request_undo_restore" in module.TOOLS
