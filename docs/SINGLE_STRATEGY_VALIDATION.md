# Single-strategy verification — September 9, 2026

Implementation: `311ff00c1b40a440483a62fa78a5630a358ff95a`.
Reference behavior: `b4f291105b2f1bbb8f025b82f922abe9b22084f4`, including the fractional-fill fix.

Actual GitHub Actions run: https://github.com/KhoaDao03/Project15/actions/runs/34394455414
Job: `102610575768` (`implement`). The completed job log was inspected.

## Executed checks

The existing locked dependencies were installed with `uv sync --locked --extra dev`
on Python 3.12.3 / Ubuntu 24.04. No import stubs were used. Both old and new code
ran in the same environment against the same deterministic synthetic input tape.

| Check | Observed result |
| --- | --- |
| Original control before/after | Identical normalized evidence: 1,380 evaluations, 3 fills, 1 completed trade, 0 open positions |
| Moderate preset before/after | Identical normalized evidence: 921 evaluations, 0 fills, 0 completed trades, 0 open positions |
| Full remaining offline regression suite | 204 passed, 2 warnings |
| `ruff check src tests scripts` | Passed |
| Formatting check on all 19 changed Python files | Passed |
| Python compile/import-related test checks | Passed |
| `node --check src/btc15/static/app.js` | Passed |
| Source distribution and wheel build | Passed |
| Protected model/rules/config/preset/dependency/API files | Byte-for-byte unchanged |

Parity compares features, probabilities, rejection reasons, decisions, orders,
fills, fees, settlement, transitions, results and remaining risk. Only random IDs
and software-version metadata are excluded. The moderate fixture's zero fills
were also zero before the refactor; no thresholds or prices were changed to force
it to trade. These are **synthetic regression results, not market performance**.

The test count is lower than the old 274-case multi-strategy suite because retired
momentum and group-specific tests were removed. Shared passive/aggressive freshness,
shutdown, recovery, compact evidence, ledger and fractional-fill tests remain. New
checks cover archive isolation, old-group refusal, pending archived exposure,
configuration/run/mode selection, removed commands, and non-destructive history APIs.
Warnings concern existing Starlette/httpx and AnyIO deprecated interfaces.

## Publishing and limitations

The one-time transformation was validated before source changes were committed and
pushed to `fix/fractional-passive-fills` without force. Temporary transformation
scripts and the write-enabled application workflow are removed by the follow-up
cleanup commit. The existing read-only paper-validation workflow remains.

No production database, local strategy settings, credentials, leases, running bot,
main-branch merge, or live-trading activation was changed. No migration/reset was
performed. Existing unsupported group resumes fail with an explicit recovery path;
see [SINGLE_STRATEGY.md](SINGLE_STRATEGY.md) before switching a deployed installation.

This run did not test an authentic overnight input tape, authenticated WebSocket
paper session, PostgreSQL backend, manual browser rendering, or production load.
JavaScript syntax and API/HTML contracts passed; that is not a full browser UI test.
