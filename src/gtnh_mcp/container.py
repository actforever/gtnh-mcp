"""Only the configured Docker container can be controlled."""

import time

import docker

from .config import Settings
from .rcon import OperationError, RconService


class ContainerControl:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = docker.DockerClient(
            base_url="unix:///var/run/docker.sock", timeout=15
        )
        self.rcon = RconService(settings)

    def container(self):
        return self.client.containers.get(self.settings.container_name)

    def snapshot(self) -> dict:
        container = self.container()
        if container.status != "running":
            raise OperationError("GTNH 容器必须正在运行才能申请恢复")
        return {
            "id": container.id,
            "restart_policy": container.attrs["HostConfig"]["RestartPolicy"],
        }

    def check_id(self, snapshot: dict):
        container = self.container()
        if container.id != snapshot["id"]:
            raise OperationError("GTNH 容器已被替换，需人工检查恢复状态")
        return container

    def stop(self, snapshot: dict) -> None:
        container = self.check_id(snapshot)
        # Disable auto-restart before requesting a graceful RCON shutdown.
        container.update(restart_policy={"Name": "no"})
        container.reload()
        if container.status in {"exited", "created"}:
            return
        try:
            self.rcon.raw("stop")
        except OperationError:
            # A successful stop commonly closes RCON before returning a reply.
            pass
        deadline = time.monotonic() + self.settings.stop_timeout
        while time.monotonic() < deadline:
            container.reload()
            if container.status in {"exited", "created"}:
                return
            time.sleep(1)
        raise OperationError("停服未确认，未强杀进程；需要人工检查")

    def assert_stopped(self, snapshot: dict) -> None:
        if self.check_id(snapshot).status not in {"exited", "created"}:
            raise OperationError("容器未停止，拒绝替换存档")

    def start(self, snapshot: dict) -> None:
        container = self.check_id(snapshot)
        container.start()
        deadline = time.monotonic() + self.settings.startup_timeout
        while time.monotonic() < deadline:
            container.reload()
            if container.status in {"exited", "dead"}:
                raise OperationError("GTNH 容器启动后退出")
            try:
                self.rcon.raw("list")
                return
            except OperationError:
                time.sleep(2)
        raise OperationError("GTNH 未在规定时间内恢复 RCON 服务")

    def finish(self, snapshot: dict) -> None:
        self.check_id(snapshot).update(restart_policy=snapshot["restart_policy"])
