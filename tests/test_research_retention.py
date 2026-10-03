import fcntl
import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from btc15.research_retention import retain_completed


def segment(root, name, age):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"recorded")
    os.utime(path, (age, age))
    return path


def test_default_shared_cap_and_incremental_rotations(tmp_path, monkeypatch):
    # Include both collectors and executor sessions/assets in one shared limit.
    for i in range(10001):
        segment(tmp_path, f"{'BTC' if i % 2 else 'ETH'}/session-{i % 4}/events-{i:05}.jsonl.gz", i)
    assert retain_completed(tmp_path)
    assert len(list(tmp_path.glob("*/*/events-*.jsonl.gz"))) == 10000
    assert not (tmp_path / "ETH/session-0/events-00000.jsonl.gz").exists()

    def no_scan(*args):
        pytest.fail("Ordinary retention must use the index, not enumerate files")

    monkeypatch.setattr(Path, "glob", no_scan)
    newest = segment(tmp_path, "SOL/new/events-10001.jsonl.gz", 10001)
    assert retain_completed(tmp_path, [newest])
    assert retain_completed(tmp_path)  # Another recorder's maintenance pass.
    assert newest.exists()
    assert not (tmp_path / "BTC/session-1/events-00001.jsonl.gz").exists()
    with sqlite3.connect(tmp_path / "retention.sqlite") as db:
        assert db.execute("select count(*) from segments").fetchone()[0] == 10000


def test_failed_unlink_is_retryable_and_never_deletes_open_files(tmp_path, monkeypatch):
    old = segment(tmp_path, "BTC/s/events-1.jsonl.gz", 1)
    newest = segment(tmp_path, "ETH/s/events-2.jsonl.gz", 2)
    active = segment(tmp_path, "BTC/s/events-0.jsonl.gz.part", 0)
    metadata = segment(tmp_path, "BTC/s/source.json.gz", 0)
    original = Path.unlink

    def denied(path, **kwargs):
        if path == old:
            raise PermissionError("cannot delete")
        return original(path, **kwargs)

    monkeypatch.setattr(Path, "unlink", denied)
    with pytest.raises(PermissionError):
        retain_completed(tmp_path, limit=1)
    assert old.exists() and newest.exists() and active.exists() and metadata.exists()
    monkeypatch.setattr(Path, "unlink", original)
    assert retain_completed(tmp_path, limit=1)
    assert not old.exists() and newest.exists() and active.exists() and metadata.exists()


def test_reconciliation_catches_interrupted_registration_and_external_deletion(tmp_path):
    first = segment(tmp_path, "BTC/s/events-1.jsonl.gz", 1)
    assert retain_completed(tmp_path, limit=1)
    first.unlink()
    old = segment(tmp_path, "BTC/s/events-2.jsonl.gz", 2)
    newest = segment(tmp_path, "BTC/s/events-3.jsonl.gz", 3)
    with sqlite3.connect(tmp_path / "retention.sqlite") as db:
        db.execute("update reconciliation set completed=-1")
    assert retain_completed(tmp_path, limit=1)
    assert not old.exists() and newest.exists()
    # Lost index can also be rebuilt without touching unfinished files.
    (tmp_path / "retention.sqlite").unlink()
    assert retain_completed(tmp_path, limit=1) and newest.exists()


def test_busy_lock_defers_registration_but_preserves_disk_check(tmp_path, monkeypatch):
    from btc15.research_log import ResearchLog

    path = segment(tmp_path, "BTC/s/events-1.jsonl.gz", 1)
    log = ResearchLog.__new__(ResearchLog)
    log.root, log.completed_segments, log.min_free_bytes = tmp_path, [path], 10
    with (tmp_path / ".retention.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        monkeypatch.setattr("btc15.research_log.shutil.disk_usage", lambda _: SimpleNamespace(free=0))
        assert not log._retention()
        assert log.completed_segments == [path]
    monkeypatch.setattr("btc15.research_log.shutil.disk_usage", lambda _: SimpleNamespace(free=100))
    assert log._retention() and not log.completed_segments


def test_symlink_outside_root_is_never_deleted(tmp_path):
    root = tmp_path / "capture"
    victim = segment(tmp_path, "outside/session/events-1.jsonl.gz", 1)
    (root / "BTC").mkdir(parents=True)
    (root / "BTC/session").symlink_to(victim.parent, target_is_directory=True)
    with pytest.raises(ValueError, match="Not a completed"):
        retain_completed(root, limit=0)
    assert victim.exists()
