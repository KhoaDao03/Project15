"""Benchmark CLIs remain explicit and safe to import for offline testing."""

import json
import runpy
import sys
from pathlib import Path

import pytest

SCRIPTS = (
    "check_processing_throughput.py",
    "check_collector_throughput.py",
    "check_dashboard_load.py",
)


@pytest.mark.parametrize("script", SCRIPTS)
def test_benchmark_import_does_not_parse_arguments_or_start_work(script, monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", [script, "--not-a-real-argument"])
    before = set(tmp_path.iterdir())
    module = runpy.run_path(str(Path("scripts") / script))
    assert callable(module["main"])
    assert set(tmp_path.iterdir()) == before


@pytest.mark.parametrize("script", SCRIPTS)
def test_benchmark_help(script, capsys):
    main = runpy.run_path(str(Path("scripts") / script))["main"]
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    assert "usage:" in capsys.readouterr().out


@pytest.mark.parametrize("script", ["check_processing_throughput.py", "check_dashboard_load.py"])
def test_benchmark_rejects_insufficient_capture(script, tmp_path):
    journal = tmp_path / "empty.jsonl"
    journal.write_text("")
    main = runpy.run_path(str(Path("scripts") / script))["main"]
    with pytest.raises(SystemExit, match="Need at least two events"):
        main([str(journal)])


def test_processing_benchmark_preserves_report(tmp_path, capsys):
    # A long event span makes this a deterministic accounting check, not a timing benchmark.
    journal = tmp_path / "input.jsonl"
    rows = [
        dict(
            id=str(i), received=1000 + i * 3600, connection_id="test", payload=dict(type="heartbeat", msg={})
        )
        for i in range(2)
    ]
    journal.write_text("".join(json.dumps(row) + "\n" for row in rows))
    main = runpy.run_path("scripts/check_processing_throughput.py")["main"]
    main([str(journal), "--trades-only"])
    report = json.loads(capsys.readouterr().out)
    assert report["events"] == 2
    assert report["recorded_seconds"] == 3600
    assert report["recording"] == "trades_only"
    assert report["raw_events"] == report["saved_evaluations"] == report["fills"] == 0
    assert report["models"] == 1 and not report["warm_history"]
