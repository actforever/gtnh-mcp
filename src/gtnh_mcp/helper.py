"""Private Unix-socket API for the privileged restore worker."""

import asyncio
import logging
import os
import socket
from contextlib import asynccontextmanager
from functools import partial
from typing import Literal

import uvicorn
from filelock import FileLock
from pydantic import BaseModel, ConfigDict, ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .auth import ACTOR_HEADER, Denied, verify
from .config import Settings
from .container import ContainerControl
from .rcon import OperationError
from .restore import RestoreManager


class HelperCall(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["backups", "request", "undo", "confirm", "status"]
    value: str = ""


def create_app(settings: Settings, manager: RestoreManager) -> Starlette:
    @asynccontextmanager
    async def lifespan(app):
        await asyncio.to_thread(manager.recover)
        try:
            yield
        finally:
            await asyncio.to_thread(manager.close)

    async def rpc(request: Request):
        try:
            header = request.headers.get("authorization", "")
            if not header.startswith("Bearer "):
                raise Denied("缺少身份凭据")
            actor = verify(header[7:], settings, request.headers.get(ACTOR_HEADER))
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 8192:
                    return JSONResponse({"error": "请求过大"}, status_code=413)
            call = HelperCall.model_validate_json(body)
            if call.action == "confirm":
                operation = partial(manager.confirm, actor, call.value)
            elif call.action == "backups":
                operation = manager.backups.listing
            elif call.action == "request":
                operation = partial(manager.request, actor, call.value)
            elif call.action == "undo":
                operation = partial(manager.request_undo, actor, call.value)
            else:
                operation = partial(manager.status, actor, call.value)
            result = await asyncio.to_thread(operation)
            logging.getLogger(__name__).info(
                "helper actor=%s action=%s result=ok", actor.key, call.action
            )
            return JSONResponse({"result": result})
        except Denied as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        except ValidationError:
            return JSONResponse({"error": "无效请求"}, status_code=400)
        except OperationError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        except Exception as exc:
            logging.getLogger(__name__).error(
                "helper failed type=%s", type(exc).__name__
            )
            return JSONResponse(
                {"error": "恢复服务内部错误，请检查服务日志"}, status_code=500
            )

    async def health(request):
        return JSONResponse({"status": "ok"})

    return Starlette(
        routes=[Route("/rpc", rpc, methods=["POST"]), Route("/health", health)],
        lifespan=lifespan,
    )


def main():
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    settings = Settings.from_env()
    settings.runtime_dir.mkdir(parents=True, exist_ok=True)
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    # Compose runs this service with primary GID 10001, shared with the MCP user.
    os.chmod(settings.runtime_dir, 0o2770)
    os.chown(settings.runtime_dir, os.getuid(), os.getgid())
    os.umask(0o007)
    with FileLock(settings.state_dir / "helper.lock", timeout=0):
        manager = RestoreManager(settings, ContainerControl(settings))
        settings.socket_path.unlink(missing_ok=True)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(settings.socket_path))
        os.chmod(settings.socket_path, 0o660)
        try:
            uvicorn.Server(
                uvicorn.Config(create_app(settings, manager), access_log=False)
            ).run(sockets=[listener])
        finally:
            listener.close()
            settings.socket_path.unlink(missing_ok=True)
