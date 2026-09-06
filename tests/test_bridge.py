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
    def __init__(self, user="admin", group="group", text="", platform="test"):
        self.user, self.group, self.message_str = user, group, text
        self.platform = platform

    def get_platform_name(self):
        return self.platform

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


@pytest.mark.parametrize("platform", ["test", "qq_official"])
def test_bridge_rejects_private_chat(settings, platform):
    bridge = Bridge(
        "http://127.0.0.1:8000/mcp", settings.auth_secret.get_secret_value()
    )
    with pytest.raises(ValueError):
        bridge.token(Event(group="", platform=platform))


def test_official_qq_identity_and_acl(settings):
    settings.allowed_groups = ["qq_official:Group_OpenID_A7"]
    settings.admin_users = ["qq_official:Member_OpenID_B9"]
    bridge = Bridge(
        "http://127.0.0.1:8000/mcp", settings.auth_secret.get_secret_value()
    )
    event = Event(
        platform="qq_official", group="Group_OpenID_A7", user="Member_OpenID_B9"
    )
    actor = verify(bridge.token(event), settings)
    assert actor.key == "qq_official:Group_OpenID_A7:Member_OpenID_B9"
    actor.require_admin(settings)
    for user in ["Other_Member", "member_openid_b9", "123456789"]:
        event.user = user
        with pytest.raises(Denied):
            verify(bridge.token(event), settings).require_admin(settings)
    event.group = "123456789"
    with pytest.raises(Denied):
        verify(bridge.token(event), settings)


def test_official_qq_confirmation_bound_to_event(settings):
    from conftest import make_archive
    from test_restore import FakeControl, FakeRcon

    from gtnh_mcp.restore import RestoreManager

    settings.allowed_groups = ["qq_official:Group_A", "qq_official:Group_B"]
    settings.admin_users = ["qq_official:Member_A", "qq_official:Member_B"]
    bridge = Bridge(
        "http://127.0.0.1:8000/mcp", settings.auth_secret.get_secret_value()
    )
    event = Event(platform="qq_official", group="Group_A", user="Member_A")
    make_archive(settings.backup_dir / "sample.tar.gz")
    manager = RestoreManager(settings, FakeControl(), FakeRcon())
    try:
        job = manager.request(
            verify(bridge.token(event), settings), manager.backups.listing()[0]["id"]
        )
        for group, user in [("Group_B", "Member_A"), ("Group_A", "Member_B")]:
            other = Event(platform="qq_official", group=group, user=user)
            with pytest.raises(Denied):
                manager.confirm(
                    verify(bridge.token(other, job["id"]), settings), job["id"]
                )
        assert manager.control.calls == []
        manager.confirm(verify(bridge.token(event, job["id"]), settings), job["id"])
        for future in manager.futures:
            future.result(timeout=10)
        assert manager.load(job["id"])["phase"] == "succeeded"
    finally:
        manager.close()


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
    assert len(module.TOOLS) == 10
    assert "request_undo_restore" in module.TOOLS
