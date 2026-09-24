"""Offline, additive archive of closed research segments and coverage inventory.

Run with ``python -m btc15.research_archive SOURCE DESTINATION``. No trading imports,
network access, deletion, background activation or writes to the source archive.
"""

import argparse
import gzip
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

from .research_coverage import Coverage, atomic_json


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def archive(source, destination, *, rebuild_coverage=False):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if not source.is_dir():
        raise ValueError("Source directory does not exist")
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError("Source and destination must be separate directory trees")
    destination.mkdir(parents=True, exist_ok=True)
    copied = 0
    for manifest in sorted(source.glob("*/*/manifest.json")):
        session = manifest.parent
        files = [manifest, session / "source.json.gz", *sorted(session.glob("events-*.jsonl.gz"))]
        for path in files:
            if not path.is_file() or path.is_symlink():
                continue
            target = destination / path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                if digest(path) != digest(target):
                    raise ValueError(f"Immutable archive conflict: {path.relative_to(source)}")
                continue
            # Retention may race this read. An error aborts the pass; retry is additive.
            fd, temporary = tempfile.mkstemp(prefix=".archive-", dir=target.parent)
            try:
                with os.fdopen(fd, "wb") as out, path.open("rb") as stream:
                    shutil.copyfileobj(stream, out)
                if digest(Path(temporary)) != digest(path):
                    raise ValueError("Source changed during archive copy")
                os.replace(temporary, target)
                copied += 1
            finally:
                Path(temporary).unlink(missing_ok=True)
        # Mutable summaries are advisory. Never turn a partial JSON read into a snapshot.
        for name in ("status.json", "coverage.json"):
            path = session / name
            if path.exists():
                try:
                    body = json.loads(path.read_text())
                except (OSError, ValueError):
                    continue
                atomic_json(destination / path.relative_to(source), body)
    result = report(destination, rebuild_coverage=rebuild_coverage)
    atomic_json(destination / "archive-report.json", result)
    return dict(copied_files=copied, **result)


def rebuild_session_coverage(directory, manifest):
    """Recompute interval evidence from retained events, never editing originals.

    Historical generic gap markers remain losses: their missing event types
    cannot be inferred retrospectively. An incomplete tail is also retained.
    """
    status_path = directory / "status.json"
    status = json.loads(status_path.read_text()) if status_path.exists() else {}
    with tempfile.TemporaryDirectory(prefix="research-coverage-") as scratch:
        coverage = Coverage(
            scratch,
            manifest["asset"],
            manifest["session"],
            manifest["started_at"],
            manifest.get("capture_mode", "full"),
        )
        expected, last = 1, manifest["started_at"]
        errors = []

        def missing(count, following):
            coverage.observe(
                dict(
                    kind="recording_gap",
                    body=dict(dropped_records=count, previous_capture_at=last, next_capture_at=following),
                )
            )

        try:
            for segment in sorted(directory.glob("events-*.jsonl.gz")):
                try:
                    with gzip.open(segment, "rt") as stream:
                        for line in stream:
                            event = json.loads(line)
                            seq = event["capture_seq"]
                            if seq > expected:
                                missing(seq - expected, event.get("captured_at"))
                            elif seq < expected:
                                raise ValueError("Reversed or duplicate capture sequence")
                            coverage.observe(event)
                            expected = (
                                event["body"]["last_missing_seq"] + 1
                                if event["kind"] == "recording_gap"
                                else seq + 1
                            )
                            last = event.get("captured_at", last)
                except (OSError, ValueError, KeyError, EOFError) as exc:
                    errors.append(dict(file=segment.name, error=str(exc)))
            high_water = status.get("last_capture_seq", expected - 1)
            if high_water >= expected:
                missing(high_water - expected + 1, None)
            rebuilt = dict(
                capture_complete=not coverage.gaps and not errors,
                dropped=coverage.gap_records,
                diagnostics={"coverage_rebuild_required": errors} if errors else {},
            )
            result = coverage.summary(rebuilt, last)
            result.update(
                final=bool(status.get("clean_shutdown")),
                errors=errors,
                note="Interval evidence only; verify reference/model, execution and settlement dependencies before replay.",
            )
            return result
        finally:
            coverage.close()


def report(root, *, rebuild_coverage=False):
    sessions, hashes = [], {}
    for path in sorted(Path(root).glob("*/*/manifest.json")):
        manifest = json.loads(path.read_text())
        directory = path.parent
        row = dict(
            asset=directory.parent.name,
            session=directory.name,
            started_at=manifest.get("started_at"),
            records=0,
            kinds={},
            gaps=[],
            errors=[],
            receipt_days_utc=[],
            capture_mode=manifest.get(
                "capture_mode", "full" if manifest.get("schema_version") == 2 else "legacy_sampled"
            ),
        )
        days = set()
        expected = 1
        for segment in sorted(directory.glob("events-*.jsonl.gz")):
            hashes[str(segment.relative_to(root))] = digest(segment)
            try:
                with gzip.open(segment, "rt") as stream:
                    for line in stream:
                        event = json.loads(line)
                        seq = event["capture_seq"]
                        if seq != expected:
                            row["errors"].append(
                                dict(reason="SEQUENCE_DISCONTINUITY", expected=expected, actual=seq)
                            )
                        expected = seq + 1
                        if event["kind"] == "recording_gap":
                            row["gaps"].append(event["body"])
                            expected = event["body"]["last_missing_seq"] + 1
                        row["records"] += 1
                        kind = event["kind"]
                        row["kinds"][kind] = row["kinds"].get(kind, 0) + 1
                        if event.get("received_at") is not None:
                            from datetime import UTC, datetime

                            days.add(datetime.fromtimestamp(event["received_at"], UTC).date().isoformat())
            except (OSError, ValueError, KeyError, EOFError) as exc:
                row["errors"].append(
                    dict(reason="UNREADABLE_SEGMENT", file=segment.name, error=type(exc).__name__)
                )
        status_path = directory / "status.json"
        status = json.loads(status_path.read_text()) if status_path.exists() else {}
        row["status"] = status
        row["receipt_days_utc"] = sorted(days)
        row["provisional"] = not status.get("clean_shutdown", False)
        if expected - 1 != status.get("last_capture_seq"):
            row["errors"].append(dict(reason="UNVERIFIED_CAPTURE_TAIL", retained_high_water=expected - 1))
        coverage = directory / "coverage.json"
        row["coverage"] = json.loads(coverage.read_text()) if coverage.exists() else None
        if rebuild_coverage and manifest.get("schema_version") in (2, 3):
            row["rebuilt_coverage"] = rebuild_session_coverage(directory, manifest)
        for name in ("manifest.json", "source.json.gz"):
            item = directory / name
            if item.exists():
                hashes[str(item.relative_to(root))] = digest(item)
            else:
                row["errors"].append(dict(reason="MISSING_CONTEXT", file=name))
        sessions.append(row)
    return dict(
        sessions=sessions,
        sha256=hashes,
        note="Receipt days show observations, not continuous coverage. Inspect gaps, provisional sessions and per-market coverage; deduplicate execution events by event_id.",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument(
        "--rebuild-coverage",
        action="store_true",
        help="Rebuild per-market gap evidence from retained records (extra offline scan)",
    )
    args = parser.parse_args()
    result = archive(args.source, args.destination, rebuild_coverage=args.rebuild_coverage)
    print(json.dumps(dict(copied_files=result["copied_files"], sessions=len(result["sessions"]))))


if __name__ == "__main__":
    main()
