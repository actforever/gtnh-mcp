"""Authenticated Streamable HTTP MCP tools; no model-provided identity fields."""

import asyncio
import logging

import httpx
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AccessToken, TokenVerifier
from fastmcp.server.dependencies import get_access_token, get_http_headers
from starlette.responses import JSONResponse

from .auth import ACTOR_HEADER, Actor, Denied, encode_actor, verify
from .config import Settings
from .rcon import OperationError, RconService


class KeyVerifier(TokenVerifier):
    def __init__(self, settings):
        super().__init__()
        self.settings = settings

    async def verify_token(self, token: str):
        try:
            verify(token, self.settings)
        except Denied:
            return None
        return AccessToken(
            token=token,
            client_id="gtnh-api-client",
            scopes=[],
        )


class HelperClient:
    # HTTPX requires an absolute HTTP URL. localhost supplies the Host header;
    # the UDS transport connects only to settings.socket_path, never TCP/DNS.
    rpc_url = "http://localhost/rpc"

    def __init__(self, settings: Settings):
        self.settings = settings

    def connection(self):
        transport = httpx.AsyncHTTPTransport(
            uds=str(self.settings.socket_path), retries=0
        )
        return httpx.AsyncClient(transport=transport, timeout=600)

    async def call(
        self, token: str, action: str, value: str = "", actor: Actor = Actor()
    ):
        try:
            async with self.connection() as client:
                response = await client.post(
                    self.rpc_url,
                    json={"action": action, "value": value},
                    headers={
                        "Authorization": f"Bearer {token}",
                        ACTOR_HEADER: encode_actor(actor),
                    },
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
        "GTNH Operations", auth=KeyVerifier(settings), mask_error_details=True
    )
    rcon = rcon or RconService(settings)
    helper = helper or HelperClient(settings)

    def identity():
        access = get_access_token()
        if access is None:
            raise Denied("缺少身份凭据")
        actor = verify(
            access.token, settings, get_http_headers().get(ACTOR_HEADER.lower())
        )
        return actor, access.token

    async def execute(action: str, value: str = ""):
        try:
            actor, _ = identity()
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

    async def restore_call(action: str, value: str = ""):
        try:
            actor, token = identity()
            return await helper.call(token, action, value, actor)
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
        """查询白名单；QQ 权限由插件检查。"""
        return await execute("whitelist_list")

    @server.tool
    async def add_whitelist(player: str) -> dict:
        """将指定玩家加入白名单；QQ 权限由插件检查。"""
        return await execute("whitelist_add", player)

    @server.tool
    async def remove_whitelist(player: str) -> dict:
        """将指定玩家移出白名单；QQ 权限由插件检查。"""
        return await execute("whitelist_remove", player)

    @server.tool
    async def list_backups() -> list[dict]:
        """查询现有 ZIP/tar.gz 备份的 ID、文件名、格式、大小和修改时间。"""
        return await restore_call("backups")

    @server.tool
    async def request_restore(backup_id: str) -> dict:
        """申请指定备份恢复。只生成十分钟有效的确认编号，不停服。"""
        return await restore_call("request", backup_id)

    @server.tool
    async def request_undo_restore(job_id: str) -> dict:
        """申请撤销一次成功回档，恢复该任务执行前的存档；须相同调用者确认，不合并进度。"""
        return await restore_call("undo", job_id)

    @server.tool
    async def confirm_restore(job_id: str) -> dict:
        """明确确认恢复或撤销任务，必须与申请人标识一致；插件仅通过人工命令调用。"""
        return await restore_call("confirm", job_id)

    @server.tool
    async def restore_status(job_id: str = "") -> list[dict]:
        """查询恢复任务；留空列出全部。QQ 任务可见性由插件过滤。"""
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
