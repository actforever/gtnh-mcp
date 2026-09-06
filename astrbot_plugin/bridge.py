"""AstrBot-independent bridge, exercised by integration tests."""

import base64
import json
import re
from datetime import timedelta

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

TOOLS = {
    "list_players",
    "announce",
    "save_world",
    "list_whitelist",
    "add_whitelist",
    "remove_whitelist",
    "list_backups",
    "request_restore",
    "request_undo_restore",
    "restore_status",
}
ADMIN_TOOLS = {
    "list_whitelist",
    "add_whitelist",
    "remove_whitelist",
    "request_restore",
    "request_undo_restore",
    "confirm_restore",
}


def resolve_config(config, environ):
    """Page settings override environment; explicit [] denies all."""
    resolved = {"url": config.get("mcp_url", "http://127.0.0.1:8000/mcp")}
    for name, env_name in (
        ("auth_secret", "GTNH_AUTH_SECRET"),
        ("allowed_groups", "ALLOWED_GROUPS"),
        ("admin_users", "ADMIN_USERS"),
    ):
        value = config.get(name, "")
        if not isinstance(value, str):
            raise ValueError(f"{name} 必须是文本")
        if not value.strip():
            value = environ.get(env_name, "")
        if name == "auth_secret":
            if len(value) < 32:
                raise ValueError("auth_secret / GTNH_AUTH_SECRET 至少需要 32 个字符")
            resolved["secret"] = value
            continue
        try:
            entries = json.loads(value or "[]")
            if (
                not isinstance(entries, list)
                or any(
                    not isinstance(entry, str)
                    or len(entry.split(":")) != 2
                    or any(
                        not part or part != part.strip() for part in entry.split(":")
                    )
                    or any(ord(c) < 32 or ord(c) == 127 for c in entry)
                    for entry in entries
                )
                or len(set(entries)) != len(entries)
            ):
                raise ValueError
        except (ValueError, TypeError):
            raise ValueError(
                f"{name} 必须是无重复的 platform:id JSON 字符串数组"
            ) from None
        resolved[name] = entries
    return resolved


class Bridge:
    def __init__(self, url: str, secret: str, allowed_groups=(), admin_users=()):
        if len(secret) < 32:
            raise ValueError("GTNH_AUTH_SECRET must contain at least 32 characters")
        self.url, self.secret = url, secret
        self.allowed_groups = set(allowed_groups)
        self.admin_users = set(admin_users)

    def identity(self, event) -> str:
        platform = str(event.get_platform_name() or "")
        group = str(event.get_group_id() or "")
        user = str(event.get_sender_id() or "")
        if not group or any(
            not value
            or ":" in value
            or any(ord(c) < 32 or ord(c) == 127 for c in value)
            for value in (platform, group, user)
        ):
            raise ValueError("仅支持有可信发送者身份的群消息")
        key = f"{platform}:{group}:{user}"
        if len(key.encode("utf-8")) > 1024:
            raise ValueError("调用者标识过长")
        return key

    def is_admin(self, event):
        return (
            f"{event.get_platform_name()}:{event.get_sender_id()}" in self.admin_users
        )

    def authorize(self, event, name):
        key = self.identity(event)
        if (
            f"{event.get_platform_name()}:{event.get_group_id()}"
            not in self.allowed_groups
        ):
            raise ValueError("此群未获授权")
        if name in ADMIN_TOOLS and not self.is_admin(event):
            raise ValueError("此操作需要管理员权限")
        return key

    def headers(self, event):
        key = self.identity(event)
        return {
            "Authorization": f"Bearer {self.secret}",
            "X-GTNH-Actor": base64.urlsafe_b64encode(key.encode("utf-8"))
            .decode("ascii")
            .rstrip("="),
        }

    def render(self, event, name, arguments, result):
        if result.isError:
            return "操作被拒绝或失败"
        if name == "restore_status":
            value = result.structuredContent
            if value is None:
                try:
                    value = json.loads(
                        "\n".join(
                            item.text for item in result.content if item.type == "text"
                        )
                    )
                except (ValueError, TypeError):
                    return "任务状态响应格式无效"
            jobs = value.get("result", value) if isinstance(value, dict) else value
            if not isinstance(jobs, list) or any(
                not isinstance(job, dict) or not isinstance(job.get("actor"), str)
                for job in jobs
            ):
                return "任务状态响应格式无效"
            if not self.is_admin(event):
                jobs = [job for job in jobs if job["actor"] == self.identity(event)]
                if arguments.get("job_id") and not jobs:
                    return "无权查看此任务或任务不存在"
            return json.dumps(jobs, ensure_ascii=False)
        if result.structuredContent is not None:
            return json.dumps(result.structuredContent, ensure_ascii=False)
        return "\n".join(item.text for item in result.content if item.type == "text")

    async def _call(self, event, name: str, arguments: dict) -> str:
        self.authorize(event, name)
        try:
            # A fresh MCP session for every invocation prevents identity sharing.
            async with (
                httpx.AsyncClient(
                    headers=self.headers(event),
                    timeout=620,
                    trust_env=False,
                ) as http_client,
                streamable_http_client(self.url, http_client=http_client) as streams,
            ):
                read, write, _ = streams
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(
                        name, arguments, read_timeout_seconds=timedelta(seconds=620)
                    )
                    return self.render(event, name, arguments, result)
        except Exception:
            # Transport errors can include headers; never echo the exception.
            return "GTNH 服务连接失败或超时。请查询任务状态；不要盲目重复写操作。"

    async def tool(self, event, name: str, arguments: dict) -> str:
        if name not in TOOLS:
            raise ValueError("不允许此工具")
        self.authorize(event, name)
        return await self._call(event, name, arguments)

    async def confirm(self, event, job_id: str) -> str:
        if not re.fullmatch(r"[a-f0-9]{32}", job_id):
            return "用法：/gtnh_confirm 32位任务编号"
        self.authorize(event, "confirm_restore")
        return await self._call(event, "confirm_restore", {"job_id": job_id})
