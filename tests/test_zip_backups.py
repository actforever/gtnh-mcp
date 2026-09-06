import stat
import zipfile

import pytest
from test_restore import FakeControl, FakeRcon, confirm, request

from gtnh_mcp.backups import Backups
from gtnh_mcp.config import Settings, validate_world_directory
from gtnh_mcp.rcon import OperationError
from gtnh_mcp.restore import RestoreManager


@pytest.fixture
def zip_settings(settings):
    (settings.server_root / "Worlds").rename(settings.server_root / "World")
    settings.world_directory = "World"
    return settings


def make_zip(settings, entries=None, compression=zipfile.ZIP_DEFLATED):
    path = settings.backup_dir / "2026-09-06-01-19-40.zip"
    with zipfile.ZipFile(path, "w", compression=compression) as archive:
        for name, content in entries or [
            ("World/data.txt", b"new"),
            ("visualprospecting/data.txt", b"new"),
        ]:
            archive.writestr(name, content)
    return path


@pytest.mark.parametrize("rollback", [False, True])
def test_zip_restore_and_rollback_freeze_world_names(zip_settings, rollback):
    make_zip(zip_settings)
    control = FakeControl()
    control.start_failures = int(rollback)
    manager = RestoreManager(zip_settings, control, FakeRcon())
    try:
        assert manager.backups.listing()[0]["format"] == "zip"
        job = request(manager)
        zip_settings.world_directory = "ChangedAfterRequest"
        confirm(manager, job)
        for name in ("World", "visualprospecting"):
            assert (zip_settings.server_root / name / "data.txt").read_text() == (
                "old" if rollback else "new"
            )
        assert not (zip_settings.server_root / "Worlds").exists()
        assert not (zip_settings.server_root / "ChangedAfterRequest").exists()
    finally:
        manager.close()


def test_corrupt_zip_rejected_before_stop(zip_settings):
    path = make_zip(zip_settings, compression=zipfile.ZIP_STORED)
    data = path.read_bytes()
    path.write_bytes(data.replace(b"new", b"bad", 1))
    control = FakeControl()
    manager = RestoreManager(zip_settings, control, FakeRcon())
    try:
        with pytest.raises(OperationError):
            request(manager)
        assert control.calls == []
        assert (zip_settings.server_root / "World/data.txt").read_text() == "old"
    finally:
        manager.close()


@pytest.mark.parametrize(
    "name",
    ["../escape", "/World/a", "World/../a", "World/a\\b", "Worlds/a", "World/a:b"],
)
def test_zip_unsafe_paths(zip_settings, name):
    # ZipInfo normalizes Windows backslashes while writing; patch both headers
    # to exercise the literal archive name a remote producer could supply.
    stored_name = name.replace("\\", "/")
    path = make_zip(
        zip_settings, [(stored_name, b"bad"), ("visualprospecting/a", b"ok")]
    )
    if stored_name != name:
        path.write_bytes(path.read_bytes().replace(stored_name.encode(), name.encode()))
    backups = Backups(zip_settings)
    with pytest.raises(OperationError):
        backups.preflight(backups.listing()[0]["id"])


def test_zip_symlink_rejected(zip_settings):
    link = zipfile.ZipInfo("World/link")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    make_zip(zip_settings, [(link, b"../../escape"), ("visualprospecting/a", b"ok")])
    backups = Backups(zip_settings)
    with pytest.raises(OperationError):
        backups.preflight(backups.listing()[0]["id"])


def test_zip_unsupported_compression(zip_settings):
    make_zip(zip_settings, compression=zipfile.ZIP_BZIP2)
    backups = Backups(zip_settings)
    with pytest.raises(OperationError):
        backups.preflight(backups.listing()[0]["id"])


def test_legacy_journal_directory_names():
    assert RestoreManager.job_dirs({}) == ("Worlds", "visualprospecting")
    assert RestoreManager.job_dirs({"world_dirs": ["World", "visualprospecting"]}) == (
        "World",
        "visualprospecting",
    )
    with pytest.raises(OperationError):
        RestoreManager.job_dirs({"world_dirs": ["../escape", "visualprospecting"]})


@pytest.mark.parametrize(
    "name", ["../World", "World/a", "visualprospecting", "backups", "World.", ""]
)
def test_invalid_world_directory(name):
    with pytest.raises(ValueError):
        validate_world_directory(name)


def test_default_world_directory():
    assert Settings.model_fields["world_directory"].default == "World"
    assert "allowed_groups" not in Settings.model_fields
    assert "admin_users" not in Settings.model_fields


def test_encrypted_zip_rejected(zip_settings):
    path = make_zip(zip_settings)
    content = bytearray(path.read_bytes())
    for signature, offset in [(b"PK\x03\x04", 6), (b"PK\x01\x02", 8)]:
        start = 0
        while (index := content.find(signature, start)) != -1:
            content[index + offset] |= 1
            start = index + len(signature)
    path.write_bytes(content)
    backups = Backups(zip_settings)
    with pytest.raises(OperationError):
        backups.preflight(backups.listing()[0]["id"])


@pytest.mark.parametrize("limit", ["max_archive_bytes", "max_archive_members"])
def test_zip_limits(zip_settings, limit):
    make_zip(zip_settings)
    setattr(zip_settings, limit, 1)
    backups = Backups(zip_settings)
    with pytest.raises(OperationError):
        backups.preflight(backups.listing()[0]["id"])


def test_zip_implicit_parent_case_collision(zip_settings):
    make_zip(
        zip_settings,
        [("World/A/x", b"x"), ("World/a/y", b"y"), ("visualprospecting/z", b"z")],
    )
    backups = Backups(zip_settings)
    with pytest.raises(OperationError):
        backups.preflight(backups.listing()[0]["id"])
