# Recorder simplification — staged, 22 September 2026

Book snapshots, deltas and public trades already arrive with a parsed payload.
Coverage previously parsed the archived payload string again, although it only
needs message type, subscription/sequence numbers and market identity. The
recorder now copies these identity fields for the background coverage worker.
The full original payload is still serialized and archived exactly as before.
The independent copy prevents later caller mutations from changing coverage.

This keeps the existing archive schema, event ordering, clocks, queue bounds,
raw depth, trade fields, per-input processing records and actual decision checks.
No service was restarted or production setting changed. This change does not
address the separate SQLite capture errors or retention limits.

An alternating before/after synthetic benchmark recorded 12,000 delta inputs and
12,000 processing records per run, with three runs of each version. Median process
CPU time fell from 0.858 s to 0.811 s (5.5%). Every run wrote all 24,000 records
with zero drops. Individual timings overlap; this is a small recorder-only
benchmark, not a live collector improvement or proof of queue elimination.
Results are in benchmark.json; the task-specific code change is recorder.patch.

The regression compares optimized coverage against coverage reconstructed from
full archived frames. It covers snapshots, deltas, trades, a sequence gap,
disconnection/reconnection, and mutation of caller-owned payloads after capture.

Validation: all 36 focused logging, replay, completeness and live-capture tests
passed (two existing deprecation warnings). Ruff and whitespace checks passed.
Async teardown stalled inside the sandbox; the isolated collector test and then
the complete focused selection passed outside it. The full project suite has not
been rerun for this staged change.
