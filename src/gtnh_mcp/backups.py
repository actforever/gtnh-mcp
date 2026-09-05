"""ZIP and tar.gz backups with a shared, bounded two-directory restore policy."""

import hashlib
import os
import shutil
import stat
import tarfile
import zipfile
import zlib
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from .config import Settings
from .rcon import OperationError

# Only for decoding pre-WORLD_DIRECTORY restore journals.
LEGACY_WORLD_DIRS = ("Worlds", "visualprospecting")
ARCHIVE_ERRORS = (
    tarfile.TarError,
    zipfile.BadZipFile,
    EOFError,
    OSError,
    zlib.error,
    NotImplementedError,
)


class Backups:
    def __init__(self, settings: Settings, world_dirs=None):
        self.settings = settings
        self.world_dirs = tuple(world_dirs or settings.world_dirs)

    def listing(self) -> list[dict]:
        result = []
        for path in self.settings.backup_dir.iterdir():
            archive_format = (
                "zip"
                if path.name.endswith(".zip")
                else "tar.gz"
                if path.name.endswith(".tar.gz")
                else None
            )
            if archive_format is None:
                continue
            try:
                info = path.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISREG(info.st_mode):
                result.append(
                    {
                        "id": hashlib.sha256(path.name.encode()).hexdigest(),
                        "name": path.name,
                        "format": archive_format,
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

    @contextmanager
    def archive(self, stream, archive_format):
        stream.seek(0)
        try:
            if archive_format == "zip":
                with zipfile.ZipFile(stream) as archive:
                    yield archive
            else:
                with tarfile.open(fileobj=stream, mode="r:gz") as archive:
                    yield archive
        except ARCHIVE_ERRORS as exc:
            raise OperationError(
                "备份损坏、CRC 校验失败、压缩方式不支持或文件不可读取"
            ) from exc
        finally:
            stream.seek(0)

    def entries(self, archive):
        is_zip = isinstance(archive, zipfile.ZipFile)
        seen, portable_names, roots = {}, {}, set()
        entries, total = [], 0
        for count, member in enumerate(archive.infolist() if is_zip else archive, 1):
            if count > self.settings.max_archive_members:
                raise OperationError("归档超过条目数限制")
            if is_zip:
                name, size, directory = (
                    member.orig_filename,
                    member.file_size,
                    member.is_dir(),
                )
                mode = stat.S_IFMT(member.external_attr >> 16)
                if member.flag_bits & 1 or member.compress_type not in {
                    zipfile.ZIP_STORED,
                    zipfile.ZIP_DEFLATED,
                }:
                    raise OperationError(
                        "ZIP 必须未加密，且使用 Stored 或 Deflate 压缩"
                    )
                if mode not in {0, stat.S_IFDIR if directory else stat.S_IFREG} or (
                    directory and size
                ):
                    raise OperationError("ZIP 包含链接、特殊文件或类型异常条目")
            else:
                name, size, directory = member.name, member.size, member.isdir()
                if not (directory or member.isreg()):
                    raise OperationError("归档不允许链接或特殊文件")
            if name in {".", "./"} and directory:
                continue
            path = PurePosixPath(name)
            if (
                not path.parts
                or path.is_absolute()
                or ".." in path.parts
                or "\\" in name
                or path.parts[0] not in self.world_dirs
                or any(
                    ":" in p or any(ord(c) < 32 for c in p) or p.endswith((".", " "))
                    for p in path.parts
                )
                or (len(path.parts) == 1 and not directory)
            ):
                raise OperationError(
                    "归档只能包含配置的世界目录和 visualprospecting 下的安全路径"
                )
            normalized = path.as_posix()
            if normalized in seen:
                raise OperationError("归档包含重复或大小写冲突路径")
            for part in [path, *path.parents]:
                spelling = part.as_posix()
                key = spelling.casefold()
                if portable_names.setdefault(key, spelling) != spelling:
                    raise OperationError("归档包含重复或大小写冲突路径")
            seen[normalized] = directory
            roots.add(path.parts[0])
            total += size
            if size < 0 or total > self.settings.max_archive_bytes:
                raise OperationError("归档超过配置的展开大小限制")
            entries.append((member, path, directory, size))
        if roots != set(self.world_dirs):
            raise OperationError("归档必须同时包含配置的世界目录和 visualprospecting")
        for name in seen:
            if any(
                seen.get(parent.as_posix()) is False
                for parent in PurePosixPath(name).parents
            ):
                raise OperationError("归档中的文件与目录路径冲突")
        return entries, total

    @staticmethod
    def consume(archive, member, expected: int, output=None):
        source = (
            archive.open(member)
            if isinstance(archive, zipfile.ZipFile)
            else archive.extractfile(member)
        )
        with source:
            actual = 0
            while chunk := source.read(min(1024 * 1024, expected - actual + 1)):
                actual += len(chunk)
                if actual > expected:
                    raise OperationError("展开文件超过声明大小")
                if output is not None:
                    output.write(chunk)
            if actual != expected:
                raise OperationError("归档文件长度不一致")

    def check_space(self, expanded: int) -> None:
        if (
            shutil.disk_usage(self.settings.server_root).free
            < expanded + self.settings.free_space_reserve
        ):
            raise OperationError("存档磁盘可用空间不足，无法暂存备份并保留当前存档")

    def preflight(self, backup_id: str) -> dict:
        with self.open(backup_id) as (stream, item):
            digest = self.digest(stream)
            with self.archive(stream, item["format"]) as archive:
                entries, expanded = self.entries(archive)
                self.check_space(expanded)
                for member, _, directory, size in entries:
                    if not directory:
                        self.consume(archive, member, size)
            if self.digest(stream) != digest:
                raise OperationError("备份正在变化，请稍后重试")
            return {**item, "sha256": digest, "expanded_size": expanded}

    def stage(self, backup_id: str, digest: str, destination: Path) -> None:
        with self.open(backup_id) as (stream, item):
            if self.digest(stream) != digest:
                raise OperationError("备份内容已变化，请重新申请恢复")
            with self.archive(stream, item["format"]) as archive:
                entries, expanded = self.entries(archive)
                self.check_space(expanded)
                owners = {
                    name: (self.settings.server_root / name).stat()
                    for name in self.world_dirs
                }
                destination.mkdir(parents=True, exist_ok=False)
                for name in self.world_dirs:
                    (destination / name).mkdir()
                for member, path, directory, size in entries:
                    target = destination.joinpath(*path.parts)
                    if directory:
                        target.mkdir(parents=True, exist_ok=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with target.open("xb") as output:
                            self.consume(archive, member, size, output)
                            output.flush()
                            os.fsync(output.fileno())
            if self.digest(stream) != digest:
                raise OperationError("备份在暂存期间变化，已取消恢复")
            for name in self.world_dirs:
                root = destination / name
                for path in [root, *root.rglob("*")]:
                    path.chmod(0o750 if path.is_dir() else 0o640)
                    if hasattr(os, "chown"):
                        os.chown(path, owners[name].st_uid, owners[name].st_gid)
