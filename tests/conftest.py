import io
import tarfile

import pytest

from gtnh_mcp.config import Settings


@pytest.fixture
def settings(tmp_path):
    result = Settings(
        rcon_password="test-password",
        auth_secret="test-key-" * 8,
        runtime_dir=tmp_path / "run",
        server_root=tmp_path / "server",
        backup_dir=tmp_path / "backup",
        state_dir=tmp_path / "state",
        world_directory="Worlds",  # Keep legacy tar.gz fixtures alongside ZIP tests.
        free_space_reserve=0,
        stop_timeout=1,
        startup_timeout=1,
    )
    for path in (
        result.runtime_dir,
        result.server_root,
        result.backup_dir,
        result.state_dir,
    ):
        path.mkdir()
    for name in ("Worlds", "visualprospecting"):
        path = result.server_root / name
        path.mkdir()
        (path / "data.txt").write_text("old", encoding="utf-8")
    return result


def make_archive(path, entries=None):
    if entries is None:
        entries = [("Worlds/data.txt", b"new"), ("visualprospecting/data.txt", b"new")]
    with tarfile.open(path, "w:gz") as archive:
        for name, data in entries:
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    return path
