"""Strict two-directory archives; caller-visible IDs never become paths."""

import hashlib
import os
import shutil
import stat
import tarfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from .config import Settings
from .rcon import OperationError

WORLD_DIRS = ("Worlds", "visualprospecting")


class Backups:
    def __init__(self, settings: Settings):
        self.settings = settings

    def listing(self) -> list[dict]:
        result = []
        for path in self.settings.backup_dir.glob("*.tar.gz"):
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode):
                continue
            result.append(
                {
                    "id": hashlib.sha256(path.name.encode()).hexdigest(),
                    "name": path.name,
                    "size": info.st_size,
                    "modified": info.st_mtime,
                }
            )
        return sorted(
            result, key=lambda item: (item["modified"], item["name"]), reverse=True
        )

    @contextmanager
    def open(self, backup_id: str):
        item = next((item for item in self.listing() if item["id"] == backup_id), None)
        if item is None:
            raise OperationError("备份不存在；请重新查询备份列表")
        path = self.settings.backup_dir / item["name"]
        if path.is_symlink():
            raise OperationError("不允许符号链接备份")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise OperationError("备份必须是普通文件")
            yield stream, item

    @staticmethod
    def digest(stream) -> str:
        stream.seek(0)
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
        stream.seek(0)
        return digest

    def inspect(self, stream) -> int:
        stream.seek(0)
        total = 0
        seen = {}
        roots = set()
        try:
            with tarfile.open(fileobj=stream, mode="r:gz") as archive:
                for member in archive:
                    name = member.name
                    path = PurePosixPath(name)
                    if name in {".", "./"} and member.isdir():
                        continue
                    if (
                        path.is_absolute()
                        or ".." in path.parts
                        or "\\" in name
                        or not path.parts
                        or path.parts[0] not in WORLD_DIRS
                        or any(
                            ":" in p or any(ord(c) < 32 for c in p) for p in path.parts
                        )
                        or not (member.isdir() or member.isreg())
                        or (len(path.parts) == 1 and not member.isdir())
                    ):
                        raise OperationError(
                            "归档只能包含 Worlds 和 visualprospecting 下的普通文件及目录"
                        )
                    normalized = path.as_posix()
                    if normalized in seen:
                        raise OperationError("归档包含重复路径")
                    seen[normalized] = member.isdir()
                    roots.add(path.parts[0])
                    total += member.size
                    if (
                        member.size < 0
                        or total > self.settings.max_archive_bytes
                        or len(seen) > self.settings.max_archive_members
                    ):
                        raise OperationError("归档超过配置的展开大小或文件数限制")
            if roots != set(WORLD_DIRS):
                raise OperationError("归档必须同时包含 Worlds 和 visualprospecting")
            for name in seen:
                if any(
                    seen.get(parent.as_posix()) is False
                    for parent in PurePosixPath(name).parents
                ):
                    raise OperationError("归档中的文件与目录路径冲突")
        except (tarfile.TarError, EOFError, OSError) as exc:
            raise OperationError("备份压缩包损坏或不可读取") from exc
        finally:
            stream.seek(0)
        return total

    def check_space(self, expanded: int) -> None:
        if (
            shutil.disk_usage(self.settings.server_root).free
            < expanded + self.settings.free_space_reserve
        ):
            raise OperationError("存档磁盘可用空间不足，无法暂存备份并保留当前存档")

    def preflight(self, backup_id: str) -> dict:
        with self.open(backup_id) as (stream, item):
            digest = self.digest(stream)
            expanded = self.inspect(stream)
            if self.digest(stream) != digest:
                raise OperationError("备份正在变化，请稍后重试")
            self.check_space(expanded)
            return {**item, "sha256": digest, "expanded_size": expanded}

    def stage(self, backup_id: str, digest: str, destination: Path) -> None:
        with self.open(backup_id) as (stream, _):
            if self.digest(stream) != digest:
                raise OperationError("备份内容已变化，请重新申请恢复")
            self.check_space(self.inspect(stream))
            destination.mkdir(parents=True, exist_ok=False)
            owners = {
                name: (self.settings.server_root / name).stat() for name in WORLD_DIRS
            }
            for name in WORLD_DIRS:
                (destination / name).mkdir()
            with tarfile.open(fileobj=stream, mode="r:gz") as archive:
                # inspect() already rejected links and every non-regular entry.
                # Revalidate each entry too: the source could be externally modified.
                total = 0
                seen = set()
                for member in archive:
                    if member.name in {".", "./"} and member.isdir():
                        continue
                    path = PurePosixPath(member.name)
                    if (
                        path.is_absolute()
                        or ".." in path.parts
                        or "\\" in member.name
                        or not path.parts
                        or path.parts[0] not in WORLD_DIRS
                        or not (member.isdir() or member.isreg())
                        or (len(path.parts) == 1 and not member.isdir())
                        or any(
                            ":" in p or any(ord(c) < 32 for c in p) for p in path.parts
                        )
                        or path.as_posix() in seen
                    ):
                        raise OperationError("归档在暂存期间变化")
                    seen.add(path.as_posix())
                    total += member.size
                    if (
                        member.size < 0
                        or total > self.settings.max_archive_bytes
                        or len(seen) > self.settings.max_archive_members
                    ):
                        raise OperationError("归档在暂存期间超过配置限制")
                    target = destination.joinpath(*path.parts)
                    if member.isdir():
                        target.mkdir(parents=True, exist_ok=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with (
                            archive.extractfile(member) as source,
                            target.open("xb") as output,
                        ):
                            shutil.copyfileobj(source, output, length=1024 * 1024)
                            output.flush()
                            os.fsync(output.fileno())
            if self.digest(stream) != digest:
                raise OperationError("备份在暂存期间变化，已取消恢复")
            # Do not trust archived UID/GID or special permission bits.
            for name in WORLD_DIRS:
                root = destination / name
                for path in [root, *root.rglob("*")]:
                    path.chmod(0o750 if path.is_dir() else 0o640)
                    if hasattr(os, "chown"):
                        os.chown(path, owners[name].st_uid, owners[name].st_gid)
