# Research recording repairs — staged 24 September 2026

No bot services, active environment, frozen trading configurations or existing
recordings were changed by this task. These changes require an authorized rollout.
Other pending working-tree changes were preserved.

## Recording pressure

Collectors can use `BTC15_RESEARCH_LOG_MODE=sampled`. The staged drop-in example is
`deploy/cloud/examples/research-sampled.conf`. The default remains full capture,
and the separate executor should stay in full mode.

Sampled capture omits raw order-book deltas and per-input processing records before
serialization and queue admission. It retains full observed local book states once
per second plus threshold crossings, reference/trade messages, accepted reference
samples, actual model calculations/decisions, and execution/settlement evidence.
Schema 3, `capture_mode=sampled`, `exact_replay=false` and separate `sampled_out`
counters distinguish this data from full replay and accidental data loss.

The benchmark used 20,000 pre-parsed messages (95% depth deltas, 5% trades), their
processing records, and periodic 99-level books/reference/model/decision records.
Three alternating runs per mode measured median recorder CPU of 1.640 s (full)
and 0.108 s (sampled), a 93.4% reduction. Compressed segments fell from approximately
1.46 MB to 54 KB. All required sampled records were retained with zero unexpected
drops. This excludes network decoding and strategy processing; it is not a live
collector capacity measurement or a guarantee of eliminating BTC health blocks.

## Database errors

Journal reads now fail promptly on contention, release their connections before
serialization, retain retry cursors, and report operation/message/SQLite error code
in `diagnostics`. They no longer invent a lost feed event when a poll fails.
Successful retry clears its current diagnostic. Modern durable execution events
replace redundant legacy order-snapshot polling. Legacy admission failure no longer
advances its cursor past an unrecorded snapshot/settlement.

Coverage-index errors after successful archive writes now require an index rebuild
without claiming the archived input was lost. `journal_reads_healthy` and
`coverage_index_healthy` are separate from raw `capture_complete`.

## Coverage and retention

Known recording losses are localized using capture-time intervals and a conservative
one-hour reference-dependency lookback. Earlier completed markets are not invalidated
by later unrelated drops. Unlocated losses remain conservative; this is evidence for
interval filtering, not an automatic declaration of complete execution replay.

`research_archive --rebuild-coverage` can rebuild per-market evidence offline from
retained events. Missing prefixes, tails and historical generic gaps remain marked.
It never changes original recordings or fills a historical hole with future prices.

Retired closed segments move atomically to the sibling `research-logs-archive` on
the same filesystem, with manifest/source context. Conflicts or failed moves leave
the original segment intact. The archive is not automatically deleted. Both active
and archived trees must be collected for research; combine archived data first and
newer active data second. The 30 GiB free-space reserve still stops capture, so
long-term collection needs export before disk fills. Old deleted files cannot be
recovered by this patch.

## External backtester

The separately operated backtester is not available in this repository. Its reader
must support schema 3, explicitly label estimated execution, use only book states
already observed at the simulated time with a bounded age, and keep estimated
results distinct from strict replay. It must apply per-interval dependency checks
instead of rejecting whole sessions. No official reference prices are imputed;
one-second books cannot establish exact 250 ms hypothetical fills.

The user confirmed the backtester runs on another machine and is unavailable here.
`BACKTEST_READER_HANDOFF.md` provides the schema, timestamp, depth and coverage
migration contract for that reader. Its changes cannot be validated in this repo.

## Validation

- Initial focused recorder suite: 42 passed.
- Full regression run: 1,879 passed, 3 skipped, one browser launch failure;
  461 seconds. `full-tests.txt` contains the complete output.
- The isolated browser retry also fails before loading the UI because the host
  lacks `libatk-1.0.so.0`; see `browser-check.txt`. No browser dependencies or
  unrelated UI code were changed for this task.
- Final focused verification after the last gap-boundary and archive regression
  additions: all 46 passed (`final-focused-tests.txt`).
- Ruff, formatting and whitespace checks passed for the changed Python files.
- The updated logging guide's local links resolve, and CLI help exposes the
  coverage rebuild option.

The full suite is not reported as wholly passing. The external backtester cannot
be validated here; its reader migration remains work on the other machine.
