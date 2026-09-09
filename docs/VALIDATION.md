# Follow-up hardening validation — 2026-09-08/09

## Strategy organization and UI controls

The algorithm now lives under `src/btc15/strategies/settlement_edge/`. The dashboard
has next-session configuration controls and an entry-enable setting. Validation
performed during this change:

- The full suite passed 88 tests after the module move and initial UI/API work.
- Following the entry submission guard and version compatibility changes, the
  focused execution/dashboard/settings suite passed 30 tests.
- The final four settings tests passed, including CLI configuration precedence,
  invalid/foreign-origin save rejection, persistence, next-session behavior,
  disabled entries and preservation of legacy enabled configuration hashes.
- An isolated Chromium check verified editing, saving, reload persistence, the
  enable switch and desktop/mobile layout, with no JavaScript errors. It used a
  temporary database/settings directory, not the operational strategy file.

These checks validate software behavior. They add no authenticated capture,
trading-performance or long-running production acceptance evidence. Existing
sessions and historical configurations were not modified.

## One-second evaluation

The default evaluation interval is now one second in code and the example config.
The engine caches each market's model calculation for that interval and checks
current quotes between calculations. Decisions retain `model_evaluated_at` and
`model_age_seconds`; the 5 Hz display does not enter standard settlement sampling.

The initial load run exposed repeated state writes on unchanged quote checks and
accumulated a substantial backlog. Its evidence remains in `data/one-second-paper/`;
analysis was interrupted after confirming no fills, with raw data preserved. It is
not accepted as performance evidence. Unchanged checks now avoid redundant state
transitions and repeated submission attempts; model refreshes and changed decisions
still generate records. A regression checks 50 unchanged quote updates without
additional model calculations or state transitions.

The corrected full suite passed **87 tests**. A separate 90-second authenticated
PAPER run (`39e15b86-edd9-42d7-9732-411f11fd2d38`) recorded 216 decisions and 89 distinct
model calculations. Median model interval was 1.005 s, maximum 1.062 s; model age
at decision stayed below 0.971 s. Maximum sampled processing lag was 0.593 s and
maximum observed queue 751. It shut down cleanly with only expected disconnect
records and no fills. This short warmup run verifies cadence/load behavior, not
profitability or filled-position production acceptance. Metrics are retained in
`data/one-second-final/acceptance.json` alongside its database and raw capture.

Existing run configurations/checkpoints are not migrated. Resuming an older paper
run requires its original configuration; start a new run to adopt the new default.

## Live market display

The quote/BRTI display now streams independently of evaluation polling. The full
suite passed **84 tests**, including replaceable display projections and stream
staleness. Chromium observed 105 distinct snapshots in 12 seconds and 55 distinct
5 Hz BRTI values, retained the same card nodes, handled stale/recovered display,
rendered in PAPER and LIVE views, and fit a 390-pixel mobile viewport with no JS
errors. Median publication-to-server-delivery snapshot age was 54.9 ms, p95
103.0 ms. These are local display-path measurements, not exchange-to-browser
latency or trading latency guarantees. Evidence:
`data/dashboard-recovery/stream-browser-validation.json` and
`market-stream-mobile.png`. Standard settlement sampling was not changed.

Managed PAPER operation subsequently passed **78 regression tests**, systemd unit
validation and two authenticated service sessions (bounded run, checkpoint resume,
healthy probe and graceful SIGTERM). See [AUTONOMOUS_PAPER.md](AUTONOMOUS_PAPER.md).
Multi-day operational acceptance remains pending.

## Collection recovery — latest result

The [clock and rollover repair](COLLECTION_RECOVERY.md) passed a fresh authenticated
capture on 2026-09-09, 00:27:15–00:43:55 UTC: **453,202 events over 1,000.458 seconds**,
one socket connection, zero clock adjustments, zero sequence gaps, zero reference
gaps, six acknowledged rollover updates and a complete settlement minute matching
the published average. Both research audit and qualification passed; shutdown was
clean, with no unexpected health errors. Processing lag p99 was 0.078 s, maximum
0.243 s. Execution was disabled and no exchange orders were sent.

The full SQLite regression suite passed **72 tests** after the rollover fixes;
the subsequently added transport-loss regression passed in the **five-test collector
suite**. Ruff lint/format and whitespace checks passed. See the recovery report for
run/source hashes, raw evidence, host configuration and test scope.
The completed 453,202-event tape also replayed successfully in an isolated BACKTEST
database: no failed experiment, zero trades and zero open positions.

## Earlier hardening validation

The earlier results below are retained as history:

- SQLite: **70 tests passed** in 30.46 s; final full-suite result is recorded
  in `data/hardening-sqlite-tests.log`.
- PostgreSQL 17: **68 tests passed** in 62.78 s, including full replay/walk-forward,
  immutable history, atomic rollback, duplicate claims, partial-fill resume and
  collector disk-failure cleanup. A subsequent database-independent recorder-close
  test and empty-book regression were checked with the final SQLite suite. Each
  PostgreSQL test used a disposable schema; the temporary container was stopped.
- Ruff lint/format and Git whitespace checks passed.
- Authenticated WebSocket subscriptions to standard BRTI, 5 Hz BRTI, books, trades,
  ticker and lifecycle all succeeded. Historical BRTI access returned 18,000 values
  for a completed hour. Current-hour history is incomplete and can be delayed up
  to 15 minutes according to CF; it was not substituted into a causal replay.
- **1,000-second development soak:** 403,404 recorded events, zero wire sequence
  gaps, actual rollover/official settlement, 29 host wall-clock reversals and 21
  reference gaps. Earlier clock and empty-book recovery created excess reconnects.
  This is retained as failed clean-data evidence, not presented as final readiness.
- **90-second clock-fix check:** 41,123 events, one socket connection, zero sequence
  gaps, zero reference gaps despite three wall-clock steps. Maximum observed queue
  475; maximum sampled processing lag 0.140 s. Its JSONL and Parquet mirrors matched:
  replay/audit deduplicated exactly 41,123 duplicate IDs.
- **380-second settlement-window check:** 218,438 events, zero wire sequence gaps,
  rollover and official YES result for `KXBTC15M-26SEP082000-00`. All 60 settlement
  samples were observed; their mean **78446.918** matched the feed's official
  quarter-hour average exactly. It also had 12 host-clock reversals and seven
  reference gaps outside the complete settlement window, so its dataset audit
  correctly fails. Empty-book snapshots at rollover exposed a reconnect loop;
  parsing was fixed afterward and replayed against this authentic recording
  with zero `INVALID_DATA` errors. A sanitized public snapshot fixture covers it.
  Maximum observed queue 1,423; sampled processing lag p99 0.215 s, maximum 0.583 s.
- Authentic 90-second tape replay completed with zero trades and no failed
  experiment. No warmup, fills, settlement outcomes or economic evidence was invented.
- Fault tests demonstrate transactional retry after a settlement write failure,
  partial-fill fee/duplicate-state recovery, explicit resume cancellation, retained
  subscription errors, responsive receipt during slow processing, and cleanup on
  disk failure. Kernel/process-kill durability and power-loss recovery were not
  empirically simulated; abrupt-crash leases remain an operator gate.

The backward host-clock steps observed in these earlier captures were subsequently
resolved as documented above. Raw timestamps and negative findings are preserved.
There is no
multi-day independent calibration dataset or measured real-order queue/latency
comparison. **Those validation requirements remain open; autonomous production
paper and LIVE were not advanced.** See [RESEARCH_HARDENING.md](RESEARCH_HARDENING.md).

Local artifact directories: `data/hardening/{capture,soak,final,window}/raw/`,
`data/hardening/*-audit.json`, source-snapshotted run databases and replay reports.
Raw events/database files remain ignored by Git; no credentials were exported.
Sandboxed asyncio/TestClient event-loop wakeups stalled; local tests completed when
run outside that sandbox. Two third-party TestClient deprecation warnings remain.

---

# Executed validation — 2026-09-08

- `pytest -q -o faulthandler_timeout=30`: **56 passed in 15.49 seconds**.
  Coverage includes captured contract parsing, missing strikes, comparator/ties,
  all four order directions, fractional book levels, piecewise ticks, causal seeded
  probability, fixed observed samples, indicator warmup, stale data, entry boundaries,
  duplicate/partial orders, passive volume and queues, cancellation, aggressive IOC,
  depth/latency-limited hard stops, risk limits, fee carry, immutable history, state
  transitions, writer exclusion, raw journal/Parquet roundtrip, complete replay and
  export, dashboard APIs, mode isolation, event fee gating, invalid config, API
  signing/pagination/sharding, walk-forward frozen holdout and insufficient data.
- `ruff check src tests`: passed. `ruff format --check src tests`: 20 files formatted.
  Python compileall, JavaScript syntax check and Git whitespace checks passed.
- Live **read-only public** `btc15 discover`: succeeded against the production API.
  Verified active market parsing and pagination. Unopened strikes were not available;
  these are retained as pending/blocked, not inferred from a reference price.
- PostgreSQL 17 in a temporary localhost Docker container: schema creation, writes,
  database-enforced immutability, state transitions, unique entry claims and writer
  lease acquire/release passed. The main automated suite uses SQLite temporary
  databases; PostgreSQL did not receive the entire replay suite.
- Chromium/Playwright: desktop and mobile layout, PAPER/BACKTEST switching,
  opportunity table, completed-trades table, replay navigation/reference and
  probability charts, analytics charts, no horizontal overflow at 390px, and no
  JavaScript errors passed. Screenshots are local ignored artifacts in
  `data/dashboard-desktop.png` and `data/dashboard-mobile.png`.
- Offline synthetic CLI flow produced an entry, fractional partial fills and an
  official-result-shaped settlement event. This proves an accounting path, not
  actual exchange access, calibration, favorable execution, or economic edge.

## Environment limitations

Authenticated Kalshi WebSocket, historical CF entitlements, real paper sessions,
production orders and live reconciliation were **not tested**. No account secrets
were supplied or created. Temporary test services are stopped after validation.

Two third-party deprecation warnings remain in FastAPI/Starlette's test client
(httpx compatibility and an AnyIO alias). They did not fail tests. The sandboxed
FastAPI TestClient run stalled; the same suite passed when local networking was
available. Browser validation used existing Chromium with missing shared libraries
unpacked into /tmp; no system packages were installed. These are development
environment details, not exchange readiness evidence.
