"""Persistent single-server restore jobs with write-ahead filesystem transitions."""

import json
import logging
import os
import re
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from filelock import FileLock, Timeout

from .auth import Actor, Denied
from .backups import LEGACY_WORLD_DIRS, Backups
from .config import Settings, validate_world_directory
from .rcon import OperationError, RconService
from .snapshots import PreviousSnapshot

logger = logging.getLogger(__name__)
TERMINAL = {"succeeded", "rolled_back", "failed", "manual_intervention"}


def sync_dir(path: Path) -> None:
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    sync_dir(path.parent)


def move(source: Path, destination: Path) -> None:
    if destination.exists() or destination.is_symlink():
        raise OperationError("目录切换目标已存在，停止自动恢复")
    source.rename(destination)
    sync_dir(source.parent)
    sync_dir(destination.parent)


class RestoreManager:
    def __init__(self, settings: Settings, control, rcon=None):
        self.settings = settings
        self.control = control
        self.rcon = rcon or RconService(settings)
        self.backups = Backups(settings)
        settings.state_dir.mkdir(parents=True, exist_ok=True)
        settings.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.mutex = threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="restore")
        self.futures = []

    @property
    def marker(self) -> Path:
        return self.settings.runtime_dir / "maintenance.json"

    def path(self, job_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{32}", job_id):
            raise OperationError("无效任务编号")
        return self.settings.state_dir / f"{job_id}.json"

    def load(self, job_id: str) -> dict:
        try:
            return json.loads(self.path(job_id).read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise OperationError("任务不存在") from exc

    def save(self, job: dict, phase: str | None = None, **values) -> None:
        with self.mutex:
            if phase:
                job["phase"] = phase
            job.update(values, updated=time.time())
            atomic_json(self.path(job["id"]), job)
            logger.info(
                "restore actor=%s job=%s backup=%s phase=%s",
                job["actor"],
                job["id"],
                job["backup"]["id"],
                job["phase"],
            )

    def public(self, job: dict) -> dict:
        keys = (
            "id",
            "actor",
            "backup",
            "phase",
            "created",
            "expires",
            "updated",
            "message",
            "undo_of",
            "world_dirs",
        )
        return {key: job[key] for key in keys if key in job}

    def status(self, actor: Actor, job_id: str = "") -> list[dict]:
        with self.mutex:
            jobs = (
                [self.load(job_id)]
                if job_id
                else [
                    json.loads(path.read_text(encoding="utf-8"))
                    for path in self.settings.state_dir.glob("*.json")
                ]
            )
            result = [
                self.public(job)
                for job in jobs
                if job["actor"] == actor.key or actor.is_admin(self.settings)
            ]
            if job_id and not result:
                raise Denied("无权查看此任务")
            return sorted(result, key=lambda job: job["created"], reverse=True)

    @staticmethod
    def job_dirs(job):
        names = job.get("world_dirs", list(LEGACY_WORLD_DIRS))
        if (
            not isinstance(names, list)
            or len(names) != 2
            or names[1] != "visualprospecting"
        ):
            raise OperationError("恢复任务的目录记录无效，需人工检查")
        try:
            validate_world_directory(names[0])
        except (ValueError, TypeError) as exc:
            raise OperationError("恢复任务的世界目录记录无效") from exc
        return tuple(names)

    def validate_layout(self, world_dirs=None):
        root = self.settings.server_root
        for name in world_dirs or self.settings.world_dirs:
            path = root / name
            if (
                path.is_symlink()
                or not path.is_dir()
                or path.stat().st_dev != root.stat().st_dev
                or os.path.ismount(path)
            ):
                raise OperationError(
                    "世界目录和 visualprospecting 必须是存档根目录同一文件系统内的真实目录，不能单独挂载"
                )
        workspace = root / ".gtnh-restore"
        if workspace.is_symlink():
            raise OperationError("恢复工作目录不能是符号链接")

    def request(self, actor: Actor, backup_id: str) -> dict:
        return self.create_request(actor, backup_id)

    def request_undo(self, actor: Actor, job_id: str) -> dict:
        """Request a new restore from a successful job's retained previous worlds."""
        actor.require_admin(self.settings)
        with self.mutex:
            source = self.load(job_id)
            if source["phase"] != "succeeded":
                raise OperationError("只能撤销已成功完成且保留旧存档的任务")
            return self.create_request(actor, job_id, source)

    def create_request(self, actor: Actor, backup_id: str, source=None) -> dict:
        actor.require_admin(self.settings)
        with self.mutex:
            if self.marker.exists():
                raise OperationError("已有恢复任务或需要人工检查")
            world_dirs = (
                self.job_dirs(source)
                if source is not None
                else self.settings.world_dirs
            )
            self.validate_layout(world_dirs)
            backups = (
                PreviousSnapshot(self.settings, source["id"], world_dirs)
                if source is not None
                else self.backups
            )
            backup = backups.preflight(backup_id)
            snapshot = self.control.snapshot()
            job = {
                "id": secrets.token_hex(16),
                "actor": actor.key,
                "backup": backup,
                "phase": "pending",
                "created": time.time(),
                "expires": time.time() + 600,
                "container": snapshot,
                "world_dirs": list(world_dirs),
                "message": "请在十分钟内发送 /gtnh_confirm 任务编号；恢复会替换当前存档",
            }
            if source is not None:
                job["undo_of"] = source["id"]
                job["message"] = (
                    "申请撤销回档：恢复到该任务执行前的存档，不合并后续进度；当前世界也会保留。请在十分钟内发送 /gtnh_confirm 任务编号"
                )
            self.save(job)
            return self.public(job)

    def confirm(self, actor: Actor, job_id: str) -> dict:
        actor.require_admin(self.settings)
        if actor.purpose != "confirm" or actor.confirmation != job_id:
            raise Denied("必须由管理员使用明确的恢复确认指令")
        with self.mutex:
            job = self.load(job_id)
            if job["actor"] != actor.key:
                raise Denied("只能由原申请人在原群确认")
            if job["phase"] != "pending":
                return self.public(job)
            if time.time() > job["expires"]:
                raise OperationError("确认已过期，请重新申请")
            try:
                with FileLock(self.settings.lock_path, timeout=0, mode=0o660):
                    if self.marker.exists():
                        raise OperationError("已有恢复任务或需要人工检查")
                    # Marker precedes queued state: an interrupted acceptance fails closed.
                    atomic_json(self.marker, {"job": job_id})
                    self.save(job, "queued")
            except Timeout as exc:
                raise OperationError("服务器正在执行其他操作，请稍后确认") from exc
            self.futures.append(self.executor.submit(self.run, job_id))
            return self.public(job)

    def clear_marker(self):
        self.marker.unlink(missing_ok=True)
        sync_dir(self.marker.parent)

    def recover(self):
        """Call before serving requests; interrupted switching always rolls back."""
        with FileLock(self.settings.lock_path, timeout=0, mode=0o660):
            jobs = [
                json.loads(path.read_text(encoding="utf-8"))
                for path in self.settings.state_dir.glob("*.json")
            ]
            unfinished = [
                job for job in jobs if job["phase"] not in TERMINAL | {"pending"}
            ]
            blocked = [job for job in jobs if job["phase"] == "manual_intervention"]
            if blocked:
                # Recreate the gate even if the runtime volume was lost.
                atomic_json(self.marker, {"job": blocked[0]["id"]})
                return
            if len(unfinished) > 1:
                raise OperationError("存在多个未完成恢复，需人工检查")
            for job in unfinished:
                atomic_json(self.marker, {"job": job["id"]})
                if job["phase"] in {"queued", "preparing"}:
                    self.save(
                        job,
                        "failed",
                        message="恢复服务中断；未停服，暂存内容保留，请重新申请",
                    )
                    self.clear_marker()
                else:
                    self.rollback(job, "恢复服务中断，已按持久化日志回滚")
            if not unfinished and self.marker.exists():
                job = self.load(json.loads(self.marker.read_text())["job"])
                if job["phase"] == "pending":
                    self.save(
                        job, "failed", message="确认提交中断，未执行恢复，请重新申请"
                    )
                if job["phase"] != "manual_intervention":
                    self.clear_marker()

    def run(self, job_id: str):
        with FileLock(self.settings.lock_path, timeout=-1, mode=0o660):
            job = self.load(job_id)
            try:
                self.save(job, "preparing")
                world_dirs = self.job_dirs(job)
                self.validate_layout(world_dirs)
                workspace = self.settings.server_root / ".gtnh-restore" / job_id
                if "undo_of" in job:
                    source = self.load(job["undo_of"])
                    if (
                        source["phase"] != "succeeded"
                        or self.job_dirs(source) != world_dirs
                    ):
                        raise OperationError("撤销来源任务已变化，请重新申请")
                    backups = PreviousSnapshot(self.settings, source["id"], world_dirs)
                else:
                    backups = Backups(self.settings, world_dirs)
                backups.stage(
                    job["backup"]["id"], job["backup"]["sha256"], workspace / "incoming"
                )
                (workspace / "previous").mkdir()
                (workspace / "failed").mkdir()
                sync_dir(workspace)
                self.control.check_id(job["container"])
                self.rcon.raw(
                    "say Server backup restoration is starting; please reconnect later."
                )
                self.save(job, "stopping")
                self.control.stop(job["container"])
                self.save(job, "switching")
                for name in world_dirs:
                    self.control.assert_stopped(job["container"])
                    move(
                        self.settings.server_root / name, workspace / "previous" / name
                    )
                    move(
                        workspace / "incoming" / name, self.settings.server_root / name
                    )
                self.save(job, "starting")
                self.control.start(job["container"])
                self.control.finish(job["container"])
                self.save(
                    job,
                    "succeeded",
                    message="备份恢复完成，RCON 已就绪；旧存档保留在 .gtnh-restore/任务编号/previous",
                )
                self.clear_marker()
            except Exception as exc:
                message = (
                    str(exc)
                    if isinstance(exc, OperationError)
                    else f"恢复失败 ({type(exc).__name__})"
                )
                if job["phase"] in {"queued", "preparing"}:
                    self.save(job, "failed", message=message + "；未修改当前存档")
                    self.clear_marker()
                else:
                    self.rollback(job, message)

    def rollback(self, job: dict, reason: str):
        try:
            world_dirs = self.job_dirs(job)
            self.save(job, "rolling_back", message=reason)
            self.control.stop(job["container"])
            workspace = self.settings.server_root / ".gtnh-restore" / job["id"]
            for name in world_dirs:
                previous = workspace / "previous" / name
                live = self.settings.server_root / name
                failed = workspace / "failed" / name
                self.control.assert_stopped(job["container"])
                # Directory presence is a durable journal of each atomic rename.
                if previous.exists():
                    if live.exists():
                        move(live, failed)
                    move(previous, live)
                if not live.is_dir() or live.is_symlink():
                    raise OperationError("旧存档目录缺失，停止自动回滚")
            self.save(job, "rollback_starting")
            self.control.start(job["container"])
            self.control.finish(job["container"])
            self.save(job, "rolled_back", message=reason + "；已恢复旧存档并启动")
            self.clear_marker()
        except Exception as exc:
            message = (
                str(exc) if isinstance(exc, OperationError) else type(exc).__name__
            )
            self.save(
                job,
                "manual_intervention",
                message=reason
                + "；自动回滚未完成："
                + message
                + "。保留现场，禁止其他写操作，需人工处理",
            )

    def close(self):
        self.executor.shutdown(wait=True)
