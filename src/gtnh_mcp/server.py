"""Authenticated Streamable HTTP MCP tools; no model-provided identity fields."""

import asyncio
import logging

import httpx
import jwt
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AccessToken, TokenVerifier
from fastmcp.server.dependencies import get_access_token
from starlette.responses import JSONResponse

from .auth import Denied, verify
from .config import Settings
from .rcon import OperationError, RconService


class IdentityVerifier(TokenVerifier):
    def __init__(self, settings):
        super().__init__()
        self.settings = settings

    async def verify_token(self, token: str):
        try:
            actor = verify(token, self.settings)
        except Denied:
            return None
        claims = jwt.decode(token, options={"verify_signature": False})
        return AccessToken(
            token=token,
            client_id=actor.key,
            subject=actor.key,
            scopes=[],
            expires_at=int(claims["exp"]),
            claims=claims,
        )


class HelperClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    def connection(self):
        transport = httpx.AsyncHTTPTransport(
            uds=str(self.settings.socket_path), retries=0
        )
        return httpx.AsyncClient(
            transport=transport, base_url="http://helper", timeout=600
        )

    async def call(self, token: str, action: str, value: str = ""):
        try:
            async with self.connection() as client:
                response = await client.post(
                    "/rpc",
                    json={"action": action, "value": value},
                    headers={"Authorization": f"Bearer {token}"},
                )
                body = response.json()
                if response.is_error:
                    raise OperationError(body.get("error", "恢复服务拒绝了操作"))
                return body["result"]
        except (httpx.HTTPError, KeyError) as exc:
            raise OperationError(
                "恢复服务不可用或请求超时；请查询任务状态，不要重复提交"
            ) from exc


def create_server(settings: Settings, rcon=None, helper=None) -> FastMCP:
    server = FastMCP(
        "GTNH Operations", auth=IdentityVerifier(settings), mask_error_details=True
    )
    rcon = rcon or RconService(settings)
    helper = helper or HelperClient(settings)

    def identity(admin=False, confirm=False):
        access = get_access_token()
        if access is None:
            raise Denied("缺少身份凭据")
        actor = verify(access.token, settings)
        if admin:
            actor.require_admin(settings)
        if actor.purpose != ("confirm" if confirm else "tool"):
            raise Denied("凭据用途不匹配")
        return actor, access.token

    async def execute(action: str, value: str = "", admin=False):
        try:
            actor, _ = identity(admin=admin)
            result = await asyncio.to_thread(rcon.execute, action, value)
            logging.getLogger(__name__).info(
                "rcon actor=%s action=%s result=returned", actor.key, action
            )
            return {
                "response": result,
                "note": "这是服务端原始响应；命令是否成功请依据响应内容判断",
            }
        except (Denied, OperationError) as exc:
            raise ToolError(str(exc)) from None

    async def restore_call(action: str, value: str = "", admin=False, confirm=False):
        try:
            actor, token = identity(admin=admin, confirm=confirm)
            if confirm and actor.confirmation != value:
                raise Denied("确认编号与凭据不匹配")
            return await helper.call(token, action, value)
        except (Denied, OperationError) as exc:
            raise ToolError(str(exc)) from None

    @server.tool
    async def list_players() -> dict:
        """查询服务器在线玩家。"""
        return await execute("players")

    @server.tool
    async def announce(message: str) -> dict:
        """发送一条服务器公告，最多 300 字符。"""
        return await execute("announce", message)

    @server.tool
    async def save_world() -> dict:
        """保存当前世界；此操作不会创建备份。"""
        return await execute("save")

    @server.tool
    async def list_whitelist() -> dict:
        """管理员查询白名单。"""
        return await execute("whitelist_list", admin=True)

    @server.tool
    async def add_whitelist(player: str) -> dict:
        """管理员将指定玩家加入白名单。"""
        return await execute("whitelist_add", player, admin=True)

    @server.tool
    async def remove_whitelist(player: str) -> dict:
        """管理员将指定玩家移出白名单。"""
        return await execute("whitelist_remove", player, admin=True)

    @server.tool
    async def list_backups() -> list[dict]:
        """查询现有 ZIP/tar.gz 备份的 ID、文件名、格式、大小和修改时间。"""
        return await restore_call("backups")

    @server.tool
    async def request_restore(backup_id: str) -> dict:
        """管理员申请指定备份恢复。只生成十分钟有效的确认编号，不停服。"""
        return await restore_call("request", backup_id, admin=True)

    @server.tool
    async def confirm_restore(job_id: str) -> dict:
        """仅供可信插件确认指令调用；普通工具凭据不能执行。"""
        return await restore_call("confirm", job_id, admin=True, confirm=True)

    @server.tool
    async def restore_status(job_id: str = "") -> list[dict]:
        """查询恢复任务；留空列出本人任务，管理员可查看全部任务。"""
        return await restore_call("status", job_id)

    @server.custom_route("/health", methods=["GET"])
    async def health(request):
        return JSONResponse({"status": "ok"})

    return server


def main():
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    settings = Settings.from_env()
    create_server(settings).run(
        transport="http",
        host=settings.mcp_host,
        port=settings.mcp_port,
        path="/mcp",
        stateless_http=True,
        json_response=True,
        show_banner=False,
    )
