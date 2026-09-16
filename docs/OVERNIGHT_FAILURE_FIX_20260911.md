# Overnight recovery fix — September 11, 2026

Addresses the [investigated overnight failures](OVERNIGHT_FAILURE_20260911.md).
Implemented without starting the collector or restarting either service. The
running viewing dashboard will load its Python changes on a later restart.

## Ordered drain and shutdown

Entering suspended analysis now blocks entries and cancels active entry remainders
without discarding the established book, subscription identity, or sequence
numbers. Every queued delta still passes the normal sequence and depth checks.
True sequence/integrity failures still invalidate the book and block entries.
Disconnect/replacement connection processing invalidates the old generation after
its preceding frames have been applied. Fresh snapshots, reference, metadata,
clock/risk checks and the collector entry gate are unchanged.

Orderly shutdown now requests suspended draining before cancelling producers.
This avoids running strategy calculations on the remaining shutdown queue while
continuing ordered input recording, book updates and settlement handling. Clean
shutdown still requires recorder flush and checkpoint completion before the
acknowledgement and lease release. An abrupt host kill cannot guarantee persistence
of frames still waiting in memory.

## Writer ownership after an interrupted process

New writer leases record process evidence in a separate replaceable projection:
machine identity, hostname, boot identity, PID namespace, PID and process start
time. On Linux/WSL, acquisition can replace a previous lease only when its matching
evidence proves a previous local boot, an absent process, or PID reuse. Age of the
heartbeat is not takeover authority.

Lease comparison/deletion, replacement, process evidence and recovery audit share
one transaction. Competing acquirers cannot both obtain ownership; failed writes
roll back the takeover. Delayed cleanup from an old owner cannot release the new
owner. Unknown legacy ownership, another host/namespace, permission errors and
unsupported process-evidence platforms retain fail-closed manual recovery.

The normal named-run resume path still uses its original configuration and atomic
checkpoint. Acquiring ownership does not reset positions, risk, history, or the
collector's initial freshness gate. No systemd restart policy was changed.

## Operational reporting

Managed collector failures now publish a durable failure record plus a replaceable
failure projection. The separate dashboard and `paper-health` operational view can
show `WRITER_LEASE_BLOCKED` or `COLLECTOR_FAILED`, including a failure before the
first heartbeat. Original exception details remain in logs; the UI uses safe,
plain-language reasons.

A failed duplicate start cannot override a healthy writer's fresh heartbeat.
Later heartbeat status supersedes an older failure. Stale status no longer displays
historical queue, processing delay, reference age or recovery phase as current
metrics. The current dashboard process still uses its pre-change Python code until
restarted, and no new heartbeat is expected while the collector remains stopped.

## Validation

**199 affected tests passed** in 64.17 seconds, with two existing dependency
deprecation warnings. Coverage includes:

- Established sequenced book entering drain mode on an ordinary delta; no strategy
  evaluation during drain; genuine gap rejection and replacement snapshot checks.
- Overload integration with a real snapshot/delta stream, ordered recording,
  recovery to READY, zero final queue depth, and replay equivalence.
- Shutdown with queued deltas, preserving final depth and clean acknowledgement.
- A separate process killed with SIGKILL, followed by ownership recovery while
  preserving checkpoint and execution history.
- Competing recovery attempts, legacy/live owner exclusion, reboot/PID-reuse
  evidence, unverifiable ownership, stale-owner release, and rollback on write
  failure.
- Standalone startup failure through the dashboard API and later status recovery;
  existing storage, position/settlement, shadow, entry freshness and shutdown tests.

Ruff, formatting for changed Python files, JavaScript syntax and whitespace checks
passed. This is the affected suite, not a claim about the entire repository suite.
Local evidence: `data/runtime/overnight-fix/final-tests.xml`.
After final review, the 14 ownership tests passed again with additional malformed
identity assertions (`final-lease-tests.xml`). Invalid identity fields cannot
authorize takeover. The final-source actual-tape check also passed with the same
event counts and zero book rejections/depth mismatches (`recorded-drain-final.json`).

The new `scripts/check_overload_drain.py` checks recorded book state against separate
depth accumulation in the feed's source price coordinates. It checks every book
event, not only equality between two implementations. On the actual overnight
tape, across **233,395 events, three distinct connection drains and 4,731 suspended
deltas**:

| Result | Saved buggy engine | Fixed engine |
| --- | ---: | ---: |
| Rejected book events | 4,731 | 0 |
| Depth mismatches | 4,731 | 0 |
| Measured suspended ingestion time | 6.560 s | 0.057 s |

These timings isolate ingestion in a temporary empty portfolio with strategy
evaluation disabled. They do not measure full collector throughput or overnight
P&L. Older held-market settlement envelopes are not reconstructed in this focused
book check. Existing portfolio/settlement regressions cover that separate behavior.
The input tape is read only and the saved production source is retained locally as
`data/runtime/overnight-fix/engine-before.py`.

Reproduce the recorded drain check:

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 PYTHONPATH=src .venv/bin/python \
  scripts/check_overload_drain.py \
  --input data/raw/610f2428-415d-4711-b973-47877f489f9f.jsonl.gz \
  --config data/runtime/settlement-convergence-v8.json \
  --output /tmp/overnight-drain-check.json
```

The retained 13,251-event held-position replay also passed: primary and shadow
snapshots and records matched its saved baseline exactly. Its result is retained in
`data/runtime/overnight-fix/legacy-held-replay.json`.

## Existing legacy orphan repaired

The specific old lease lacked process evidence, so it was not automatically
reclaimed. After testing, service and file-holder checks reconfirmed the collector
was failed with PID 0, the only existing database holder was the viewing dashboard
(PID 290, `--no-collect`), and status/checkpoints were unchanged. A complete SQLite
backup was created before a transaction removed only the investigated owner
`6ef04b33-a83c-46a7-bd54-1c7644d84ea0` and added one `writer_recovered` audit record.

All checkpoint bodies, fill records and trade-result records were compared before
and after and matched exactly. SQLite `quick_check` passed. The backup and repair
proof are in `data/runtime/overnight-fix/`; `legacy-lease-repair.json` identifies the
backup file and verification results. No portfolio reset or service start occurred.

A later authorized start is still needed to run the fixes and restore a current
heartbeat. The failed overnight run is not treated as an endurance pass; sustained
capacity, memory stability and a monitored 24–48-hour session remain to be measured.
