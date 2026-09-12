# Collector connection setup fix — September 11, 2026

The collector previously changed its shared connection ID before awaiting the
WebSocket handshake. A heartbeat or metadata refresh could use that ID before
the `connected` event. The engine invalidated the prematurely introduced
connection; when `connected` arrived with the same ID, its integrity-failure flag
remained set. Recovery immediately requested another reconnect.

The receiver now retains the old identity throughout setup. It assigns the new
ID only after the socket opens, then emits `connected` without yielding to other
producers. This establishes the connection boundary before any heartbeat,
metadata, subscription, reference, or book event can use the new identity.
Failed handshakes retain the old identity and follow normal drain/retry handling.

The engine's sequence checks and integrity latch are unchanged. Real sequence
gaps still invalidate the book. Fresh metadata, a sequenced snapshot, an accepted
reference tick, and all other readiness checks remain necessary to release the
collector entry gate. Existing source tapes retain their original event ordering;
this fix prevents the faulty ordering in new capture rather than rewriting history.

The integration regressions deliberately hold each handshake open until both
heartbeat and metadata events have been received and processed. They cover initial
connection, a disconnect after reaching READY, replacement snapshots/deltas, and
a failed initial handshake. Both regressions reproduce DATA_INTEGRITY_FAILURE on
the saved pre-fix runner. The fixed runner reaches READY on both successful
connections, preserves sequence order and book depth, releases the writer on
shutdown, and does not initialize the disabled shadow experiment.

Validation passed all 92 affected tests in 33.83 seconds, covering collection,
recovery, lifecycle, processing safety, operational status, and shadow behavior.
Ruff lint/format checks and whitespace checks passed. The saved pre-fix runner
failed both new regressions with the reproduced false integrity-recovery request.

The installed collector continues to omit `--stop-confirmation-shadow`. This
removes the completed-comparison query bottleneck from the active run. The disabled
experiment's query defect still requires a separate fix before reactivation.
Its historical ledger and comparisons are retained.

The collector was restarted at 12:58:15 EDT on September 11 with this fix and
shadow mode still disabled. The dashboard stayed running. The loaded source hash
matched the tested checkout, and both ledgers' checkpoints and trade records were
unchanged across deployment. Five consecutive startup-health samples reported
READY, an empty queue, fresh reference data, and approximately 5–9 milliseconds
of processing delay. The new connection's first recorded event was `connected`,
following 75 bootstrap/control events with the preceding identity. No false
integrity recovery occurred during this check. This is startup validation, not
a completed endurance session.

Local test and deployment evidence is under
`data/runtime/connection-handshake-fix-20260911/`.
