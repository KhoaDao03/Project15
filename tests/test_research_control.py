"""Operator research switches never alter trading settings or invent continuity."""

import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from research_helpers import records

from btc15.assets import ASSETS
from btc15.research_control import read_control, set_logging


def wait_for(check):
    deadline = time.monotonic() + 5
    while not check():
        assert time.monotonic() < deadline
        time.sleep(0.02)


def test_concurrent_controls_merge_and_validate(tmp_path):
    with ThreadPoolExecutor(max_workers=7) as pool:
        list(pool.map(lambda a: set_logging(tmp_path, [a], False), ASSETS))
    assert read_control(tmp_path) == dict.fromkeys(ASSETS, False)
    set_logging(tmp_path, ["ETH", "GOLD"], True)
    assert read_control(tmp_path)["ETH"] is True
    assert read_control(tmp_path)["BTC"] is False
    with pytest.raises(ValueError):
        set_logging(tmp_path, ["UNKNOWN"], False)
    (tmp_path / "logging-control.json").write_text('{"ETH": "false"}')
    with pytest.raises(ValueError):
        read_control(tmp_path)


@pytest.mark.parametrize("asset", ASSETS)
def test_paused_startup_and_resume_marks_gap(research_recorder, tmp_path, asset):
    set_logging(tmp_path, [asset], False)
    log = research_recorder(tmp_path, asset=asset)
    assert log.status()["paused"]
    assert not log.emit("model_calculation", {"not_serializable": object()})
    wait_for(lambda: bool(list(log.directory.glob("events-*.jsonl.gz"))))
    written = log.status()["written"]
    time.sleep(0.1)
    assert log.status()["written"] == written
    set_logging(tmp_path, [asset], True)
    wait_for(lambda: not log.status()["paused"])
    assert log.emit("model_calculation", {"after_resume": True})
    log.close()
    events = records(tmp_path)
    assert any(r["kind"] == "recording_gap" for r in events)
    assert [r["body"]["action"] for r in events if r["kind"] == "recording_control"] == ["stop", "start"]
    status = json.loads((log.directory / "status.json").read_text())
    assert not status["capture_complete"]
    assert status["dropped"] == 0
    assert status["suppressed_records"] == 1
    assert status["paused_intervals"] == 1


def test_stop_targets_both_producers_not_other_markets(research_recorder, tmp_path):
    collector = research_recorder(tmp_path, asset="ETH")
    executor = research_recorder(tmp_path, asset="ETH", producer="executor")
    commodity = research_recorder(tmp_path, asset="GOLD")
    set_logging(tmp_path, ["ETH"], False)
    wait_for(lambda: collector.status()["paused"] and executor.status()["paused"])
    assert not commodity.status()["paused"]
    assert commodity.emit("evidence", {})
    (tmp_path / "logging-control.json").write_text("invalid")
    wait_for(lambda: "logging_control" in collector.status()["diagnostics"])
    assert collector.status()["paused"]
    (tmp_path / "logging-control.json").write_text('{"ETH":true}')
    wait_for(lambda: not collector.status()["paused"] and not executor.status()["paused"])
    assert "logging_control" not in collector.status()["diagnostics"]


def test_close_during_pause_leaves_explicit_incomplete_tail(research_recorder, tmp_path):
    log = research_recorder(tmp_path)
    set_logging(tmp_path, ["BTC"], False)
    wait_for(lambda: log.status()["paused"])
    log.close()
    gap = next(r for r in records(tmp_path) if r["kind"] == "recording_gap")
    assert gap["body"]["next_capture_at"] is None
    assert not json.loads((log.directory / "status.json").read_text())["capture_complete"]


def test_cli_requires_explicit_targets_and_supports_groups(tmp_path, monkeypatch, capsys):
    from btc15.research_control import main

    monkeypatch.setattr("sys.argv", ["control", "stop", "--root", str(tmp_path)])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
    assert not (tmp_path / "logging-control.json").exists()
    monkeypatch.setattr("sys.argv", ["control", "stop", "crypto", "gold", "--root", str(tmp_path)])
    main()
    assert read_control(tmp_path) == dict.fromkeys(["BTC", "ETH", "SOL", "XRP", "BNB", "HYPE", "DOGE", "ETHD", "XRPD", "GOLD"], False)
    monkeypatch.setattr("sys.argv", ["control", "start", "all", "--root", str(tmp_path)])
    main()
    assert read_control(tmp_path) == dict.fromkeys(ASSETS, True)
    monkeypatch.setattr("sys.argv", ["control", "status", "--root", str(tmp_path)])
    main()
    assert "Requested state only" in capsys.readouterr().out
