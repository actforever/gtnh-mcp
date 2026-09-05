import pytest
from conftest import make_archive
from test_restore import ADMIN, MEMBER, FakeControl, FakeRcon, confirm, request

from gtnh_mcp.auth import Actor, Denied
from gtnh_mcp.rcon import OperationError
from gtnh_mcp.restore import RestoreManager, atomic_json, move


@pytest.fixture
def manager(settings):
    make_archive(settings.backup_dir / "sample.tar.gz")
    instance = RestoreManager(settings, FakeControl(), FakeRcon())
    yield instance
    instance.close()


def successful_restore(manager):
    job = request(manager)
    confirm(manager, job)
    assert manager.load(job["id"])["phase"] == "succeeded"
    return job


def assert_world(manager, content):
    for name in manager.settings.world_dirs:
        assert (manager.settings.server_root / name / "data.txt").read_text() == content


def test_undo_and_redo_preserve_both_worlds(manager):
    original = successful_restore(manager)
    # Include post-restore progress in the state retained by the undo job.
    for name in manager.settings.world_dirs:
        (manager.settings.server_root / name / "data.txt").write_text("played")
    stops = manager.control.calls.count("stop")
    undo = manager.request_undo(ADMIN, original["id"])
    assert undo["undo_of"] == original["id"]
    assert manager.control.calls.count("stop") == stops
    assert_world(manager, "played")
    confirm(manager, undo)
    assert manager.load(undo["id"])["phase"] == "succeeded"
    assert_world(manager, "old")
    for name in manager.settings.world_dirs:
        root = manager.settings.server_root / ".gtnh-restore"
        assert (
            root / original["id"] / "previous" / name / "data.txt"
        ).read_text() == "old"
        assert (
            root / undo["id"] / "previous" / name / "data.txt"
        ).read_text() == "played"
    confirm(manager, undo)
    assert manager.control.calls.count("stop") == stops + 1
    redo = manager.request_undo(ADMIN, undo["id"])
    confirm(manager, redo)
    assert_world(manager, "played")


def test_undo_failure_restores_current_world(manager):
    original = successful_restore(manager)
    undo = manager.request_undo(ADMIN, original["id"])
    manager.control.start_failures = 1
    confirm(manager, undo)
    assert manager.load(undo["id"])["phase"] == "rolled_back"
    assert_world(manager, "new")


def test_undo_changed_snapshot_fails_before_stop(manager):
    original = successful_restore(manager)
    undo = manager.request_undo(ADMIN, original["id"])
    path = (
        manager.settings.server_root
        / ".gtnh-restore"
        / original["id"]
        / "previous"
        / "Worlds/data.txt"
    )
    path.write_text("changed")
    stops = manager.control.calls.count("stop")
    confirm(manager, undo)
    assert manager.load(undo["id"])["phase"] == "failed"
    assert manager.control.calls.count("stop") == stops
    assert_world(manager, "new")


def test_undo_permissions_and_pending_source(manager):
    pending = request(manager)
    with pytest.raises(Denied):
        manager.request_undo(MEMBER, pending["id"])
    with pytest.raises(OperationError):
        manager.request_undo(ADMIN, pending["id"])
    confirm(manager, pending)
    undo = manager.request_undo(ADMIN, pending["id"])
    with pytest.raises(Denied):
        manager.confirm(ADMIN, undo["id"])
    with pytest.raises(Denied):
        manager.confirm(
            Actor("test", "other", "admin", "confirm", undo["id"]), undo["id"]
        )


def test_undo_missing_snapshot_rejected(manager):
    original = successful_restore(manager)
    path = (
        manager.settings.server_root
        / ".gtnh-restore"
        / original["id"]
        / "previous"
        / "Worlds"
    )
    path.rename(path.with_name("missing"))
    stops = manager.control.calls.count("stop")
    with pytest.raises(OperationError):
        manager.request_undo(ADMIN, original["id"])
    assert manager.control.calls.count("stop") == stops


def test_undo_survives_helper_restart(manager):
    original = successful_restore(manager)
    undo = manager.request_undo(ADMIN, original["id"])
    manager.close()
    restarted = RestoreManager(manager.settings, manager.control, FakeRcon())
    try:
        restarted.recover()
        confirm(restarted, undo)
        assert_world(restarted, "old")
    finally:
        restarted.close()


def test_interrupted_undo_switch_recovers_to_pre_undo_world(manager):
    from gtnh_mcp.snapshots import PreviousSnapshot

    original = successful_restore(manager)
    undo = manager.request_undo(ADMIN, original["id"])
    stored = manager.load(undo["id"])
    workspace = manager.settings.server_root / ".gtnh-restore" / undo["id"]
    PreviousSnapshot(
        manager.settings, original["id"], manager.settings.world_dirs
    ).stage(original["id"], stored["backup"]["sha256"], workspace / "incoming")
    (workspace / "previous").mkdir()
    (workspace / "failed").mkdir()
    manager.save(stored, "switching")
    atomic_json(manager.marker, {"job": undo["id"]})
    name = manager.settings.world_directory
    move(manager.settings.server_root / name, workspace / "previous" / name)
    move(workspace / "incoming" / name, manager.settings.server_root / name)
    manager.close()
    restarted = RestoreManager(manager.settings, manager.control, FakeRcon())
    try:
        restarted.recover()
        assert restarted.load(undo["id"])["phase"] == "rolled_back"
        assert_world(restarted, "new")
        assert not restarted.marker.exists()
    finally:
        restarted.close()


@pytest.mark.parametrize("limit", ["max_archive_bytes", "max_archive_members"])
def test_undo_snapshot_limits(manager, limit):
    original = successful_restore(manager)
    setattr(manager.settings, limit, 1)
    with pytest.raises(OperationError):
        manager.request_undo(ADMIN, original["id"])


def test_undo_does_not_require_original_archive(manager):
    original = successful_restore(manager)
    (manager.settings.backup_dir / "sample.tar.gz").unlink()
    undo = manager.request_undo(ADMIN, original["id"])
    confirm(manager, undo)
    assert_world(manager, "old")
