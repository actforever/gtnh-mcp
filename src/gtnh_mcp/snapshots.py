"""Read retained pre-restore worlds without consuming the original snapshot."""

import hashlib
import json
import os
import stat
from pathlib import Path

from .backups import Backups
from .rcon import OperationError


class PreviousSnapshot:
    def __init__(self, settings, source_id: str, world_dirs):
        self.settings = settings
        self.source_id = source_id
        self.world_dirs = world_dirs
        self.root = settings.server_root / ".gtnh-restore" / source_id / "previous"

    def scan(self):
        # Check every parent before traversing a retained snapshot.
        for path in (self.root.parent.parent, self.root.parent, self.root):
            if path.is_symlink() or not path.is_dir():
                raise OperationError("回档前存档不存在或路径不安全，无法撤销")
        entries, total = [], 0

        def visit(path):
            nonlocal total
            info = path.lstat()
            directory = stat.S_ISDIR(info.st_mode)
            if (
                not (directory or stat.S_ISREG(info.st_mode))
                or info.st_dev != self.root.stat().st_dev
                or os.path.ismount(path)
            ):
                raise OperationError("保留存档包含链接、特殊文件或独立挂载")
            relative = path.relative_to(self.root).as_posix()
            entries.append((relative, directory, info.st_size if not directory else 0))
            if len(entries) > self.settings.max_archive_members:
                raise OperationError("保留存档超过条目数限制")
            if directory:
                for child in path.iterdir():
                    visit(child)
            else:
                total += info.st_size
                if total > self.settings.max_archive_bytes:
                    raise OperationError("保留存档超过展开大小限制")

        for name in self.world_dirs:
            path = self.root / name
            if path.is_symlink() or not path.is_dir():
                raise OperationError("回档前的两个存档目录不完整")
            visit(path)
        Backups(self.settings, self.world_dirs).check_space(total)
        return sorted(entries), total

    def fingerprint(self, destination: Path | None = None):
        entries, total = self.scan()
        digest = hashlib.sha256()
        if destination is not None:
            destination.mkdir(parents=True, exist_ok=False)
        for relative, directory, size in entries:
            digest.update(json.dumps([relative, directory, size]).encode() + b"\n")
            path = self.root / relative
            target = destination / relative if destination is not None else None
            if directory:
                if target is not None:
                    target.mkdir()
                continue
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(descriptor, "rb") as source:
                if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                    raise OperationError("保留存档文件类型发生变化")
                output = target.open("xb") if target is not None else None
                try:
                    actual = 0
                    file_digest = hashlib.sha256()
                    while chunk := source.read(min(1024 * 1024, size - actual + 1)):
                        actual += len(chunk)
                        if actual > size:
                            raise OperationError("保留存档正在变化，请重新申请")
                        file_digest.update(chunk)
                        if output is not None:
                            output.write(chunk)
                    if actual != size:
                        raise OperationError("保留存档正在变化，请重新申请")
                    digest.update(file_digest.digest())
                    if output is not None:
                        output.flush()
                        os.fsync(output.fileno())
                finally:
                    if output is not None:
                        output.close()
        if self.scan()[0] != entries:
            raise OperationError("保留存档目录正在变化，请重新申请")
        return digest.hexdigest(), total

    def preflight(self, backup_id: str):
        if backup_id != self.source_id:
            raise OperationError("撤销来源编号不一致")
        try:
            digest, total = self.fingerprint()
            return {
                "id": self.source_id,
                "name": f"before-{self.source_id}",
                "format": "retained-directory",
                "sha256": digest,
                "expanded_size": total,
            }
        except OSError as exc:
            raise OperationError("无法读取回档前存档，请检查保留目录") from exc

    def stage(self, backup_id: str, digest: str, destination: Path):
        if self.preflight(backup_id)["sha256"] != digest:
            raise OperationError("回档前存档已变化，请重新申请撤销")
        try:
            copied, _ = self.fingerprint(destination)
            if copied != digest or self.preflight(backup_id)["sha256"] != digest:
                raise OperationError("撤销暂存期间来源变化，未停服")
            for name in self.world_dirs:
                owner = (self.settings.server_root / name).stat()
                root = destination / name
                for path in [root, *root.rglob("*")]:
                    path.chmod(0o750 if path.is_dir() else 0o640)
                    if hasattr(os, "chown"):
                        os.chown(path, owner.st_uid, owner.st_gid)
        except OSError as exc:
            raise OperationError("无法暂存回档前存档，未停服") from exc
