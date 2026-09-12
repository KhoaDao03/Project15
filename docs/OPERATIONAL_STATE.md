# Operational state and entry reasons

The Live overview now has a persistent **Bot operation** panel. It describes the
current PAPER collector, independently of the run selected for history or replay.
The existing entry-decision panel continues to describe the selected evaluation.

| State | Meaning |
| --- | --- |
| COLLECTING | Current collector data is available, but evaluation is missing, stale, or waiting for model history. |
| RECOVERING | The collector entry gate is closed while draining or rebuilding fresh, validated inputs. |
| EVALUATING | A recent evaluation exists for the current run and no operational blocker is reported. A rejected entry remains normal evaluation, not a collector failure. |
| BLOCKED | An operational or policy blocker is reported, or current collector operation cannot be confirmed. |

Entry readiness is separate: blocked, checking, waiting for entry conditions,
candidate, or unknown. A candidate only passed the latest evaluation filters;
execution still performs its own checks. Stale or unavailable status gives unknown
readiness. None of these display fields authorizes orders or changes risk state.

The [overnight failure fix](OVERNIGHT_FAILURE_FIX_20260911.md) adds persisted
standalone collector failure reasons, including database ownership conflicts, and
hides historical queue/freshness metrics when worker status is stale.

The panel shows plain-language operational reasons, per-market recovery reasons,
and the latest evaluation's entry-filter reasons separately. It includes the run,
feed connection, status age, processing delay, queue depth/capacity, reference age,
and recovery phase. HALT, disabled strategy entries, paused entries, observation
mode, and held-contract settlement recovery are explicit blockers. Backlog and
reference-age monitoring warnings do not invent an execution gate.

`/api/health` and `paper-health` include an `operational` object using the same
read-only projection. Health polling selects only PAPER status and the current
run's evaluation; another run's or backtest's candidate cannot appear as current
readiness. Evaluations older than ten seconds, future-dated evaluations, and
evaluations for already-closed markets cannot claim current readiness.

A recent receipt projection for the same run can expose recovery or disconnection
before the worker publishes its next status. It never overrides the collector gate
to READY. Worker status must be less than five seconds old to be confirmed current.
If browser status updates cease for over six seconds, the panel clears old metrics
and entry reasons and displays UNKNOWN. Status also refreshes on other dashboard
tabs. Historical trade and evaluation records are unchanged.

Validation covers state precedence, stale/future timestamps, run isolation,
observation and disabled-entry modes, normal rejected entries, fresh recovery
receipt overrides, expired markets, read-only API behavior, and the JavaScript
renderer/update watchdog. Existing collector, recovery, and dashboard tests are
included in the affected regression run. This is a reporting change, not an
endurance validation or a change to the trading strategy.

The affected regression run passed **74 tests** in 11.46 seconds. Ruff, formatting,
whitespace, and JavaScript syntax checks passed. Results are retained locally in
`data/runtime/operational-state-validation.xml`.

Implemented with both collector and dashboard stopped. No service start or restart
is part of this work.
