import tarfile
import time

import pytest
from conftest import make_archive

from gtnh_mcp.auth import Actor, Denied
from gtnh_mcp.backups import LEGACY_WORLD_DIRS as WORLD_DIRS
from gtnh_mcp.backups import Backups
from gtnh_mcp.rcon import OperationError
from gtnh_mcp.restore import RestoreManager, atomic_json, move

ADMIN = Actor("test:group:admin")
MEMBER = Actor("test:group:member")


class FakeControl:
    def __init__(self):
        self.stopped = False
        self.stop_fails = False
        self.start_failures = 0
        self.calls = []

    def snapshot(self):
        return {"id": "fixed", "restart_policy": {"Name": "unless-stopped"}}

    def check_id(self, snapshot):
        assert snapshot["id"] == "fixed"

    def stop(self, snapshot):
        self.calls.append("stop")
        if self.stop_fails:
            raise OperationError("stop failed")
        self.stopped = True

    def assert_stopped(self, snapshot):
        assert self.stopped, "filesystem mutation before stop"

    def start(self, snapshot):
        self.calls.append("start")
        self.stopped = False
        if self.start_failures:
            self.start_failures -= 1
            raise OperationError("startup failed")

    def finish(self, snapshot):
        self.calls.append("finish")


class FakeRcon:
    def raw(self, command):
        return "ok"


@pytest.fixture
def manager(settings):
    make_archive(settings.backup_dir / "sample.tar.gz")
    manager = RestoreManager(settings, FakeControl(), FakeRcon())
    yield manager
    manager.close()


def request(manager):
    return manager.request(ADMIN, manager.backups.listing()[0]["id"])


def confirm(manager, job):
    result = manager.confirm(ADMIN, job["id"])
    for future in manager.futures:
        future.result(timeout=10)
    return result


def assert_live(settings, expected):
    for name in WORLD_DIRS:
        assert (settings.server_root / name / "data.txt").read_text() == expected


@pytest.mark.parametrize(
    "name",
    [
        "../escape",
        "/tmp/escape",
        "Worlds/../../escape",
        "other/data",
        "worlds/data",
        "Worlds/a:b",
        "Worlds/a\\b",
    ],
)
def test_unsafe_archive(settings, name):
    make_archive(
        settings.backup_dir / "bad.tar.gz",
        [(name, b"bad"), ("visualprospecting/a", b"data")],
    )
    backups = Backups(settings)
    with pytest.raises(OperationError):
        backups.preflight(backups.listing()[0]["id"])


@pytest.mark.parametrize(
    "kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE]
)
def test_special_archive_entries(settings, kind):
    with tarfile.open(settings.backup_dir / "bad.tar.gz", "w:gz") as archive:
        member = tarfile.TarInfo("Worlds/link")
        member.type, member.linkname = kind, "../../escape"
        archive.addfile(member)
    backups = Backups(settings)
    with pytest.raises(OperationError):
        backups.preflight(backups.listing()[0]["id"])


@pytest.mark.parametrize(
    "entries",
    [
        [("Worlds/a", b"a")],
        [("Worlds", b"a"), ("visualprospecting/a", b"a")],
        [("Worlds/a", b"a"), ("Worlds/a", b"a"), ("visualprospecting/a", b"a")],
        [("Worlds/a", b"a"), ("Worlds/a/b", b"a"), ("visualprospecting/a", b"a")],
    ],
)
def test_invalid_structure(settings, entries):
    make_archive(settings.backup_dir / "bad.tar.gz", entries)
    backups = Backups(settings)
    with pytest.raises(OperationError):
        backups.preflight(backups.listing()[0]["id"])


def test_archive_limits_and_space(settings):
    make_archive(settings.backup_dir / "sample.tar.gz")
    backups = Backups(settings)
    backup_id = backups.listing()[0]["id"]
    settings.max_archive_bytes = 1
    with pytest.raises(OperationError):
        backups.preflight(backup_id)
    settings.max_archive_bytes = 100
    settings.free_space_reserve = 10**30
    with pytest.raises(OperationError):
        backups.preflight(backup_id)


def test_corrupt_archive_and_unknown_id(settings):
    (settings.backup_dir / "bad.tar.gz").write_bytes(b"not gzip")
    backups = Backups(settings)
    with pytest.raises(OperationError):
        backups.preflight(backups.listing()[0]["id"])
    with pytest.raises(OperationError):
        backups.preflight("../../server/Worlds")


def test_success_preserves_old_and_idempotent_confirmation(manager, settings):
    job = request(manager)
    assert_live(settings, "old")
    confirm(manager, job)
    assert manager.load(job["id"])["phase"] == "succeeded"
    assert_live(settings, "new")
    for name in WORLD_DIRS:
        assert (
            settings.server_root
            / ".gtnh-restore"
            / job["id"]
            / "previous"
            / name
            / "data.txt"
        ).read_text() == "old"
    count = len(manager.control.calls)
    confirm(manager, job)
    assert len(manager.control.calls) == count
    assert not manager.marker.exists()


def test_confirmation_owner_and_expiry(manager):
    job = request(manager)
    for actor in [
        MEMBER,
        Actor("test:other:admin"),
        Actor(),
    ]:
        with pytest.raises(Denied):
            manager.confirm(actor, job["id"])
    stored = manager.load(job["id"])
    manager.save(stored, expires=time.time() - 1)
    with pytest.raises(OperationError):
        confirm(manager, job)
    assert manager.control.calls == []


def test_backup_changed_after_request(manager, settings):
    job = request(manager)
    make_archive(
        settings.backup_dir / "sample.tar.gz",
        [("Worlds/other", b"changed"), ("visualprospecting/a", b"a")],
    )
    confirm(manager, job)
    assert manager.load(job["id"])["phase"] == "failed"
    assert manager.control.calls == []
    assert_live(settings, "old")


def test_stop_failure_blocks_without_modifying_world(manager, settings):
    job = request(manager)
    manager.control.stop_fails = True
    confirm(manager, job)
    assert manager.load(job["id"])["phase"] == "manual_intervention"
    assert manager.marker.exists()
    assert_live(settings, "old")


def test_start_failure_rolls_back(manager, settings):
    job = request(manager)
    manager.control.start_failures = 1
    confirm(manager, job)
    assert manager.load(job["id"])["phase"] == "rolled_back"
    assert_live(settings, "old")
    assert not manager.marker.exists()


def test_rollback_start_failure_requires_manual_recovery(manager, settings):
    job = request(manager)
    manager.control.start_failures = 2
    confirm(manager, job)
    assert manager.load(job["id"])["phase"] == "manual_intervention"
    assert_live(settings, "old")
    assert manager.marker.exists()


def test_partial_switch_failure_rolls_back(manager, settings, monkeypatch):
    import gtnh_mcp.restore as restore

    original = restore.move
    calls = 0

    def faulty_move(source, destination):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("injected rename failure")
        original(source, destination)

    monkeypatch.setattr(restore, "move", faulty_move)
    job = request(manager)
    confirm(manager, job)
    assert manager.load(job["id"])["phase"] == "rolled_back"
    assert_live(settings, "old")


@pytest.mark.parametrize("moves", [0, 1, 2, 3, 4])
@pytest.mark.parametrize("legacy", [False, True])
def test_interrupted_switch_recovery(manager, settings, moves, legacy):
    job = request(manager)
    stored = manager.load(job["id"])
    workspace = settings.server_root / ".gtnh-restore" / job["id"]
    manager.backups.stage(
        stored["backup"]["id"], stored["backup"]["sha256"], workspace / "incoming"
    )
    (workspace / "previous").mkdir()
    (workspace / "failed").mkdir()
    transitions = []
    for name in WORLD_DIRS:
        transitions.extend(
            [
                (settings.server_root / name, workspace / "previous" / name),
                (workspace / "incoming" / name, settings.server_root / name),
            ]
        )
    if legacy:
        stored.pop("world_dirs")
        settings.world_directory = "World"
    manager.save(stored, "switching")
    atomic_json(manager.marker, {"job": job["id"]})
    for source, destination in transitions[:moves]:
        move(source, destination)
    manager.recover()
    assert manager.load(job["id"])["phase"] == "rolled_back"
    assert_live(settings, "old")
    assert not manager.marker.exists()


def test_status_available_to_authenticated_clients(manager):
    job = request(manager)
    assert manager.status(MEMBER)[0]["id"] == job["id"]
    assert manager.status(MEMBER, job["id"])[0]["id"] == job["id"]
    assert manager.status(ADMIN)[0]["id"] == job["id"]
