# Logging controls deployment — 2026-09-25

Deployed at 2026-09-25T03:47:03.303519+00:00 after the execution shutdown guard passed. All ten services are running; all seven collectors were healthy at verification. All fourteen recorder source snapshots match the new logging-control implementation.

Logging remains on for all markets. Per-market and grouped start/stop commands are now available without further bot restarts. Existing trading switches, quantities, risk fields, frozen configurations and manifest were preserved. The previous stop-worker deployment is unchanged; only `research_log.py` and new `research_control.py` differed from the running executor's recorded Python source.

Validation: 57 focused tests passed; lint and whitespace checks passed. The full suite has known preset-related failures documented in [the previous deployment](../stop-latency-20260925/report.md); no claim that the full suite is green. Live dashboard APIs returned HTTP 200. We did not deliberately pause live recording to test the controls; start/stop behavior was tested in isolated temporary directories.

All collectors and six executor recorders reported zero drops. XRP's executor recorder reported 13 queue-byte drops during startup. On subsequent verification, that count remained 13, its queue was empty and diagnostics were clear. The capture gaps remain recorded; this session must not be presented as uninterrupted replay.

Private backups and complete verification are in `data/deployments/logging-controls-20260925/`. See [commands and semantics](../../docs/RESEARCH_LOGGING.md).
