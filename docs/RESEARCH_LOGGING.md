# Research logging: full and sampled capture

Deployed with authorization on 2026-09-20. Portable recording is disabled by default
on fresh installations; it is enabled for the existing collectors and executor.
It does not change strategy parameters; reference preload can restore previously
accepted samples with their original timing. Old v1 files remain sampled archives.

## Start/stop controls — deployed 2026-09-25

Deployed with a guarded restart on 2026-09-25. All seven markets remain recording;
no pause was requested. Both collector and executor recorders loaded the controls.
From the project root:

```bash
.venv/bin/python -m btc15.research_control stop crypto
.venv/bin/python -m btc15.research_control start commodities
.venv/bin/python -m btc15.research_control stop ETH GOLD
.venv/bin/python -m btc15.research_control start all
.venv/bin/python -m btc15.research_control status
```

Supported markets: BTC, ETH, SOL, XRP, GOLD, SILVER, WTI. Commands accept multiple
markets, or the groups `crypto`, `commodities`, `all`. Use `--root /path/to/research-logs`
when the recording directory differs. Changes persist in `logging-control.json`.
Commands report **requested** state; enabled recorder writers normally acknowledge
within about one second, subject to writer load. Check their `paused` status
(in collector/executor research status or session `status.json`) for acknowledgement.
Collectors and executor recorders independently follow the same per-market switch.
A process launched with `BTC15_RESEARCH_LOG_ENABLED` disabled has no recorder and
still requires a restart to enable this facility.

Stop blocks new research capture before serialization, drains already queued events,
and closes the current segment. Small status/coverage files and retention maintenance
continue. It does not stop trading, change entry settings, delete recordings, or stop
mandatory trading journals and UI statistics. Start resumes into a new segment;
durable execution/settlement journals may subsequently backfill their original events.
No missing order-book or reference inputs are invented. Existing settlement recovery
may continue polling while paused, but its research output is suppressed.

Every pause reserves an explicit recording gap, even when no inputs arrive. Start/stop
markers carry receipt/capture/writer timing; a pause still open at shutdown has an
unknown end. `paused_intervals` and `suppressed_records` are separate from accidental
`dropped` records. Sessions containing pauses are not complete replay sessions; readers
must validate interval dependencies and require fresh book/reference evidence after
resuming. Full mode cannot resume exact replay from deltas alone after a pause.
Malformed controls retain each running recorder's last applied state and report a
`logging_control` diagnostic. Absent switches default to recording enabled.

## Deployed repair — 2026-09-24

Deployed on 2026-09-24: all seven collectors use sampled capture; the executor
uses full capture. Historical gaps and deleted files cannot be recovered by
changing the recorder.

Set `BTC15_RESEARCH_LOG_MODE=sampled` on **collectors** for lower-volume exploratory
backtests. Full mode remains the default; leave the executor in full mode.
Sampled mode uses schema 3 and `exact_replay=false`: it intentionally omits raw
book deltas and per-input processing records, before serialization/queue admission.
It retains raw reference/trade messages, accepted reference samples, every actual
model/decision, executions and settlement evidence. Decision IDs may refer to an
omitted depth input in sampled mode; that is intentional, not a sequence hole.
`book` records include all
observed `yes_levels`/`no_levels` once per second, plus existing threshold crossings.
These are real observations, not interpolated prices. `sampled_out` counts intentional
omissions separately from actual `dropped` records.

A backtester must explicitly opt into estimated execution for schema 3. Use the
last book **already observed** at the simulated decision/fill time, bound its age,
and label the result estimated. Never backfill earlier gaps with future snapshots,
interpolate official reference samples, or combine estimated and strict replay
results without distinguishing them. One-second books do not resolve 250 ms fills.
The external backtester is not part of this repository.

## Enablement and files

After deployment approval, `BTC15_RESEARCH_LOG_ENABLED=1` enables a process's
recorder. `BTC15_RESEARCH_LOG_DIR` selects the root (default `research-logs/`).
Collectors and the separate executor each need enablement for cross-process links.

```text
research-logs/
  coverage.sqlite                 # shared recovery/reference index
  BTC/SESSION/
    manifest.json                 # version, producer, run, frozen config, source hash
    source.json.gz                # captured source
    status.json                   # drops, errors, sequence high-water marks
    coverage.json                 # per-market coverage evidence
    events-....jsonl.gz            # completed segment
    events-....jsonl.gz.part       # active or interrupted segment
```

Each session has its own ID and sequence. Manifests also retain Python version and
model multipliers. Keep manifest/source files with events. Segments rotate every
ten minutes. Exclude `.part` files from ordinary transfers/analysis; a finalized
gzip file alone does not prove complete capture.

## Complete inputs and decision checks (full mode)

| Record | Evidence |
| --- | --- |
| `input` | Every received input before analysis queueing: full snapshots, deltas, public trades, references, metadata, lifecycle/results, connection/subscription events, errors, seeds and heartbeats |
| `input_processed` | Input ID, processing clocks/outcome and actual suspension/blocking flags |
| `model_calculation` | Every actual computation or failure; model ID, effective time, configuration version, features, probabilities and reference/history/seed IDs |
| `decision_check` | Every check, including unchanged decisions and cached-model reuse; decision/model/input IDs, book version, freshness clock, quality and rejection reasons |
| `execution_event` | Individual durable order/fill/control/checkpoint evidence, with delivery mode |
| `settlement_observation` | Newly observed official result, raw evidence and actual receipt time |
| `book`, `evaluation` | Optional sampled summaries, not authoritative replay inputs |

Raw payloads preserve exchange SID/sequence and source clocks; input bodies retain
connection ID, receipt clock and original event ID. An input without a processing
record must not be assumed applied. Reconnect invalidates books: require a fresh
snapshot before deltas. Book versions identify the snapshot and last applied update.
Capture cannot recover frames the application never received.

The recorder reuses parsed message identities for book/trade coverage counters,
avoiding a second parse of their full payloads. Archived messages still retain
every field and level in full mode. This optimization was deployed on 2026-09-24.

Signal collectors apply every depth update (recording each in full mode), but changes below the best
bid/ask do not repeat an entry check when the executable prices, available liquidity,
health gates and entry-window state are unchanged. They still evaluate on the
one-second schedule and official reference updates. Changes to executable quotes,
freshness or entry boundaries trigger immediate checks. Every check actually
performed is recorded; a processed input need not have a `decision_check` record.
Top quantities are compared up to the larger of the strategy's maximum order size
and minimum liquidity requirement; fluctuations above that amount cannot change
entry eligibility. Full depth and exact quantities are still applied (and recorded in full mode),
and the executor validates current liquidity before placing an order.
Paper order and position management retains its per-event checks.

Paper checks include execution freshness/resting/submission stages. Live checks
include entry, recheck, authorization and preflight, referencing the exact published
collector decision/book and observed health/policy. Preflight observations include
validated metadata, holdings and balance, but not credentials or authentication
headers. Their `*_observed_at` fields are caller observations, not transport clocks.

Full-mode book summaries retain top-five levels; schema 3 retains all observed levels.
Both use one-second intervals and 55/99¢ band changes;
evaluations retain one-second/83% band changes. Initial observations are changes,
not proven upward crossings. Models are never recalculated just for logging.

## Clocks and ordering

| Envelope field | Meaning |
| --- | --- |
| `session`, `capture_seq` | Session identity and increasing capture order |
| `source_time` | Known original source time in Unix seconds, otherwise null |
| `received_at`, `received_monotonic_ns` | Original local input receipt clocks, when known |
| `processed_at` | Processing/check observation time |
| `captured_at`, `capture_monotonic_ns` | Recorder capture clocks |
| `recorded_at` | Writer time, not an fsync guarantee |
| `caused_by` | Related input/decision ID |
| `kind`, `body` | Record type and content |

Preserve file/sequence order and follow dependencies; **never sort replay by source
time**. Receipt can run ahead of processing, and queued inputs can have older clocks
than a later-written gap marker. Monotonic clocks apply only within their process.
There is no global sequence across collector/executor sessions.

Calculation bodies retain the effective strategy time. Historical settlement
snapshots use `journal_timestamp`; order bodies retain `created_at`/`updated_at`.
Unknown receipt times stay null. A database or writer timestamp does not establish
when the bot first learned an outcome.

## Gaps, shutdown and coverage

Sequences are allocated before serialization/admission. A `recording_gap` occupies
the first missing sequence and records the missing range, count and surrounding
capture times when known. It follows queued predecessors. Per-market coverage
marks gaps overlapping that market or its preceding hour of reference dependencies;
late losses no longer invalidate completed earlier markets. Per-market
`recording_gap_indexes` refer to the summary's `recording_gap_intervals`. Unknown/unlocated
losses remain conservative. This is coverage evidence, not a promise of replay eligibility.

Terminal loss is marked when possible; status also records capture/written
high-water marks, drops, reasons and errors. Disk failure or a crash can leave an
unknown tail and stale status. `clean_shutdown` means drained/finalized;
`capture_complete` describes successful capture of the selected mode's records;
check `capture_mode`/`exact_replay` separately. `diagnostics` identifies failed journal
reads (with SQLite codes/messages), coverage-index failures and archive failures.
Journal reads release locks before serialization and retry without advancing past
undelivered events; failed reads no longer fabricate feed gaps. Modern immutable
execution journals replace redundant polling of order snapshots. Coverage failures
leave archived raw records intact and request a rebuild. Neither capture flag proves
upstream continuity, full settlement/execution coverage or retained older segments.

To resume interval-based replay after a gap, re-establish a fresh book, adequate
reference warm-up and valid execution state. A subsequent event alone repairs none
of these. Unbounded loss requires conservative exclusion.

## Reference continuity and warm-up

The writer journals accepted official-reference samples in `coverage.sqlite`.
Startup merges the rolling hour with existing cache/history, preserving receipt
and source times. Future receipts are excluded; conflicting prices force a recorded
cold start. Writer crashes can still lose queued samples. Existing history/seed
records preserve exactly what a run loaded; never backfill with later information.

Successful features include missing-second ranges, incomplete minutes, contiguous
candle count, 15-candle ATR readiness and 33-candle indicator readiness. Failed
models retain available features and errors. Probability records distinguish an
unavailable reference ATR from a measured ATR below the price floor. See
[reference preload](REFERENCE_PRELOAD.md).

## Individual execution evidence

`execution_events` in the manual-order database is append-only and rejects updates
and deletes. Requests, submission attempts, acknowledgments, rejections,
reconciliation, cancellation stages and individual received fills are recorded
alongside state changes in the same transaction, even when portable recording is off.

A submission attempt is intent **before final authorization**, not proof that the
exchange received it. Later events retain `submitted_at`; interrupted attempts may
need reconciliation. Cumulative reconciliation never fabricates individual fills.

Fills retain unique trade IDs, side, action, quantity, price, fees, source/receipt
clocks and known originating decision/check IDs. Unknown economics remain null;
`economics_complete` identifies that limit. Accepted duplicate fills count once;
ignored/unmatched notifications and stream boundaries remain evidence. Field meanings
follow [Kalshi's fill schema](https://docs.kalshi.com/websockets/user-fills).

Committed events go immediately to enabled executor recorders. Five-second writer
backfill retries failed admissions with original clocks. **Deduplicate by `event_id`**:
immediate and backfilled copies deliberately share it. Existing 30-second mutable
order/settlement snapshots are supplemental, not event histories.

Before execution tasks start, recording writes a local-state checkpoint: orders,
cumulative positions, pending orders, controls, retry/cooldown inputs, policy,
settlements and daily risk state. Header, per-order/per-market/per-settlement and
completion records share a checkpoint ID. Missing completion, historical fill
details or stream intervals limit actual-bot replay. Account reconciliation remains
authoritative; the checkpoint describes local knowledge and does not restore it.

## Closure and late settlement coverage

The shared index retains unresolved recorded markets across sessions. While an
enabled collector runs, a separate observer polls closed markets and captures
validated finalized responses with their new receipt time and source time when
available. It never injects retrospective outcomes into trading inputs. After a
shutdown, observation can resume in a later session, not during the downtime.

`coverage.json` reports snapshots, sequence gaps, book continuity breaks, decisions,
models, fallbacks, market-window coverage and settlement. Later results link original
session IDs through `origin_sessions`; join by asset/ticker. Old files are not
rewritten to imply earlier knowledge. Future/unobserved windows remain partial.

## Resource limits

| Limit | Behavior |
| --- | --- |
| Per-recorder queue | 8 MiB serialized payload and 8,192 records; producers never wait for disk |
| Writer | One daemon thread; gzip level 1, roughly one-second flush, ten-minute rotation |
| Maintenance | Roughly every five seconds, using monotonic elapsed time |
| Active-log retention | Move closed segments and context to the archive after seven days or over the shared 20 GiB allowance; do not delete unarchived data |
| Free disk below 30 GiB | Portable capture drops records; trading continues |
| Shutdown | Two-second drain budget; failed/timed-out sessions remain incomplete |

Seven collectors can queue 56 MiB of payload; seven executor recorders add another
56 MiB, plus Python/in-flight overhead. Serialization uses a short ordering lock;
compression, journal reads and retention run on writers. Measure CPU, lag and disk
growth before broad activation. Retention is a soft cap: `.part` files, shared
coverage index and the execution database are outside it. Reference rows prune to
one hour; settlement obligations persist. Inspect interrupted files manually.

## Multiweek archives

The recorder moves closed segments to a sibling `research-logs-archive/`
directory before retiring them from `research-logs/`. `BTC15_RESEARCH_ARCHIVE_DIR`
can select another **same-filesystem** path outside the active log tree. Moves are
atomic; existing immutable conflicts or archive failures leave originals intact and
report diagnostics. Open `.part` files are never moved. Context remains with both
trees. The archive is not automatically deleted and is not an off-host backup.
The 30 GiB free-space reserve still stops new capture when exhausted; export the
archive to other storage before then. More retention cannot create missing history.

Collect **both** trees for analysis. Combine archived segments first and active
closed segments second, into a separate destination, so newer advisory summaries
win. Run these offline on downloaded copies or at low priority:

```bash
.venv/bin/python -m btc15.research_archive research-logs-archive /path/to/combined
.venv/bin/python -m btc15.research_archive research-logs /path/to/combined --rebuild-coverage
```

Never use `--delete` for exports. The tool excludes `.part` files and live databases,
checks immutable conflicts and retries additively if rotation races copying.
`--rebuild-coverage` adds per-market `rebuilt_coverage` to the report from the retained
events, independent of stale session-wide flags. It preserves unknown prefixes,
missing sequence ranges and unverified tails; historical generic gap records are
not reclassified as harmless database retries. Full-session global flags alone
should not be used as the backtest interval filter. Validate book, reference/model,
settlement and execution dependencies for each candidate interval.

`archive-report.json` contains SHA-256 inventories, event counts, gaps, discontinuities,
unverified tails, provisional sessions, coverage snapshots and UTC receipt days.
A day with observations is not continuous coverage. Include losses, quiet periods
and outages; weeks of collection require real elapsed time after deployment.

## Analysis limits

Schemas 2 (full) and 3 (sampled) require a version-aware replay reader; this change does not implement a new
backtester. Actual-bot replay needs valid checkpoints and uninterrupted dependencies.
Counterfactual fills still estimate latency, queue position, fees and available depth.

## Deployment status

Latest deployment: 2026-09-24 at 04:22 UTC. All seven collectors, executor and both
dashboards restarted with the recorder repairs, pending BTC backlog policy and
dashboard changes. ETH uses multiplier 0.95 in a new run; previous runs remain intact.
Live switches, quantities and risk settings were preserved. Initial post-recovery
checks found all seven collectors healthy with zero recording drops. Later samples
showed intermittent metadata-freshness recovery gates; these still block entry. A transient
journal lock retried successfully without becoming a capture gap. Validation:
1,882 tests passed, 3 skipped, including a passing browser test. See the
[deployment report](../reports/deployment-20260924/report.md).

### Previous deployment

Deployed on 2026-09-20: all seven collectors, the separate executor and both
dashboards were restarted on the tested source. Collector and executor recording
is enabled for all seven assets. Initial checks found fresh feeds, READY recovery
states and no recorder errors or drops. Existing live policies were preserved:
BTC, ETH, SOL and XRP enabled at 10 contracts; GOLD, SILVER and WTI disabled.
Frozen configurations, journals and risk settings were preserved. Entry filters
continue to enforce reference-history quality during warm-up.

The pre-deployment state and source are backed up on the host under
`data/deployments/logging-v2-20260920T231834Z/`, alongside verification results.
Validation before deployment: 1,780 tests passed, 3 skipped; lint passed.
Continue monitoring disk growth, recorder drops and continuity. Longer research
coverage requires elapsed collection time and archiving before retention expires.
