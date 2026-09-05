"""AstrBot-independent bridge, exercised by integration tests."""

import json
import re
import time
from datetime import timedelta

import httpx
import jwt
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


class Bridge:
    def __init__(self, url: str, secret: str):
        if len(secret) < 32:
            raise ValueError("GTNH_AUTH_SECRET must contain at least 32 characters")
        self.url, self.secret = url, secret

    def token(self, event, confirmation: str = "") -> str:
        platform = str(event.get_platform_name() or "")
        group = str(event.get_group_id() or "")
        user = str(event.get_sender_id() or "")
        if not group or any(
            not value or ":" in value or any(ord(c) < 32 for c in value)
            for value in (platform, group, user)
        ):
            raise ValueError("仅支持有可信发送者身份的群消息")
        now = int(time.time())
        claims = {
            "iss": "astrbot-gtnh",
            "aud": "gtnh-mcp",
            "iat": now,
            "exp": now + 60,
            "platform": platform,
            "group": group,
            "sub": user,
            "purpose": "confirm" if confirmation else "tool",
        }
        if confirmation:
            claims["confirmation"] = confirmation
        return jwt.encode(claims, self.secret, algorithm="HS256")

    async def _call(
        self, event, name: str, arguments: dict, confirmation: str = ""
    ) -> str:
        token = self.token(event, confirmation)
        try:
            # A fresh MCP session for every invocation prevents identity sharing.
            async with (
                httpx.AsyncClient(
                    headers={"Authorization": f"Bearer {token}"},
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
                    if result.isError:
                        return "操作被拒绝或失败：" + "\n".join(
                            item.text for item in result.content if item.type == "text"
                        )
                    if result.structuredContent is not None:
                        return json.dumps(result.structuredContent, ensure_ascii=False)
                    return "\n".join(
                        item.text for item in result.content if item.type == "text"
                    )
        except Exception:
            # Transport errors can include headers; never echo the exception.
            return "GTNH 服务连接失败或超时。请查询任务状态；不要盲目重复写操作。"

    async def tool(self, event, name: str, arguments: dict) -> str:
        if name not in TOOLS:
            raise ValueError("不允许此工具")
        return await self._call(event, name, arguments)

    async def confirm(self, event, job_id: str) -> str:
        if not re.fullmatch(r"[a-f0-9]{32}", job_id):
            return "用法：/gtnh_confirm 32位任务编号"
        return await self._call(
            event, "confirm_restore", {"job_id": job_id}, confirmation=job_id
        )
