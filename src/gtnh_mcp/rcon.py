"""mcrcon with socket deadlines, safe in worker threads on Linux."""

import re
import select
import socket
import struct
import time

from filelock import FileLock, Timeout
from mcrcon import MCRcon, MCRconException

from .config import Settings


class OperationError(ValueError):
    pass


class SocketRcon(MCRcon):
    def __init__(self, settings: Settings):
        # Upstream's constructor installs a main-thread-only signal handler.
        self.host, self.port = settings.rcon_host, settings.rcon_port
        self.password = settings.rcon_password.get_secret_value()
        self.timeout = settings.rcon_timeout
        self.socket = None

    def connect(self):
        self.socket = socket.create_connection((self.host, self.port), self.timeout)
        try:
            self._send(3, self.password)
        except BaseException:
            self.disconnect()
            raise

    def _read(self, length):
        data = bytearray()
        while len(data) < length:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("RCON deadline exceeded")
            self.socket.settimeout(remaining)
            part = self.socket.recv(length - len(data))
            if not part:
                raise ConnectionError("RCON connection closed")
            data.extend(part)
        return bytes(data)

    def _send(self, out_type, out_data):
        self.deadline = time.monotonic() + self.timeout
        payload = struct.pack("<ii", 0, out_type) + out_data.encode() + b"\0\0"
        self.socket.settimeout(self.timeout)
        self.socket.sendall(struct.pack("<i", len(payload)) + payload)
        responses = []
        total = 0
        while True:
            length = struct.unpack("<i", self._read(4))[0]
            if not 10 <= length <= 4 * 1024**2:
                raise MCRconException("Invalid packet length")
            packet = self._read(length)
            request_id, _ = struct.unpack("<ii", packet[:8])
            if request_id == -1:
                raise MCRconException("Login failed")
            if packet[-2:] != b"\0\0" or request_id != 0:
                raise MCRconException("Invalid RCON response")
            total += length
            if total > 4 * 1024**2:
                raise MCRconException("Response too large")
            responses.append(packet[8:-2])
            if not select.select(
                [self.socket],
                [],
                [],
                min(0.05, max(0, self.deadline - time.monotonic())),
            )[0] or not self.socket.recv(1, socket.MSG_PEEK):
                return b"".join(responses).decode("utf-8", errors="replace")


def command_for(action: str, value: str = "") -> str:
    fixed = {"players": "list", "save": "save-all", "whitelist_list": "whitelist list"}
    if action in fixed:
        if value:
            raise OperationError("此工具不接受参数")
        return fixed[action]
    if action == "announce":
        if (
            not value.strip()
            or len(value) > 300
            or any(ord(c) < 32 or ord(c) == 127 for c in value)
        ):
            raise OperationError("公告须为 1–300 字符的单行文本")
        return "say " + value
    if action in {"whitelist_add", "whitelist_remove"}:
        if not re.fullmatch(r"[A-Za-z0-9_]{1,16}", value):
            raise OperationError("玩家名须为 1–16 位英文字母、数字或下划线")
        return f"whitelist {action.removeprefix('whitelist_')} {value}"
    raise OperationError("未知操作")


class RconService:
    def __init__(self, settings: Settings):
        self.settings = settings

    def raw(self, command: str) -> str:
        try:
            with SocketRcon(self.settings) as client:
                return client.command(command)
        except (OSError, MCRconException) as exc:
            raise OperationError(
                "RCON 连接或命令失败；写操作结果可能未知，请查询后再操作"
            ) from exc

    def execute(self, action: str, value: str = "") -> str:
        command = command_for(action, value)
        try:
            with FileLock(self.settings.lock_path, timeout=0, mode=0o660):
                if (self.settings.runtime_dir / "maintenance.json").exists():
                    raise OperationError("恢复维护状态尚未解除，请查询任务状态")
                return self.raw(command)
        except Timeout as exc:
            raise OperationError("服务器正在执行其他操作或恢复，请稍后重试") from exc
