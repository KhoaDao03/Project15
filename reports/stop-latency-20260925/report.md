# Stop-loss deployment and latency — 2026-09-25

Deployed at **00:19:38 UTC** after confirmed safe shutdown with no managed open
positions or unresolved orders. Backups, original switches, source archives and
hashes are retained in `data/deployments/stop-worker-20260925/` (private host storage).
No orders were created to benchmark latency.

## Measurement

Five alternating trials per version used the same reused HTTP client and live
Kalshi GET endpoints. Holdings and the 55¢ quote were synthetic, and the journals
were temporary. A custom transport blocked **every non-GET before network access**.
The measured interval is persisted stop trigger to the intercepted request boundary;
it excludes exchange POST/acknowledgement time and does not measure production
quote-to-trigger scheduling, collector backlog or account-lock contention.

| Version | Median | Range | REST reads before request |
| --- | ---: | ---: | ---: |
| Previous | 1052 ms | 1,045–1,056 ms | 6 |
| Deployed | 453 ms | 450–458 ms | 3 |

Reduction: **599 ms (57.0%)** in this paired benchmark.
Raw samples and per-read timings: [read-only benchmark](read-only-benchmark.json).
The remaining ~450 ms is largely the unchanged 200 ms REST read spacing plus network
and local processing. An actual order still needs network submission and acknowledgement.

Historical live evidence over the preceding 24 hours contains **24 first stop
orders**: persisted stop to POST median **1,995 ms**, P95 **3,504 ms**; POST response
median **28.8 ms**. These historical observations are not a matched comparison with
the isolated benchmark. See [historical timings](before.json).

**No natural live stop was observed after deployment during verification.**
[Post-deployment journal sample](after-live.json) records the observation window.
A live median/P95 or fill-latency improvement cannot yet be claimed. New real stop
orders will retain quote receipt/publication, detection, submission and response
timestamps for subsequent measurement. `measure_journal.py --since EPOCH --output FILE`
can re-read them without modifying the journal. Old triggers are recovered from
immutable `control_changed` events; retries are excluded from first-stop comparisons.

## Deployment verification

- All ten services active, zero automatic restarts during verification.
- All seven collector connections healthy and READY; sampled processing lag below 4 ms.
- BTC/ETH/SOL/XRP restored to enabled at 10 contracts; GOLD/SILVER/WTI remain disabled.
- Frozen configuration and manifest hashes unchanged; no strategy migration.
- Crypto entry gates still report reference-gap/model-quality warmup after restart;
  enabled policies do not bypass them. Commodity models also report warmup.
- Public dashboard HTTP 200. All executor research recorders report zero drops,
  no current diagnostics, and healthy journal/index reads. Transient journal-lock
  warnings occurred during shutdown/startup/policy restoration and had cleared at
  final verification.
- [Verification snapshot](verification.json) contains source/runtime hash verification,
  policies, collector state, recorder state and service PIDs.

## Validation and known limitations

The implementation passed 365 targeted regression checks and a final 30-check
focused run before this deployment, plus lint. The full pre-deployment suite was
attempted, then interrupted after **374 passes and 28 failures** to investigate
preset-dependent failures. All **28 failures reproduced on the pre-change source**
(33 additional checks passed in that baseline run): they expect older probability,
commodity entry-window and take-profit presets. These are not fixed by this deployment;
the full suite is **not green**. See [full-run output](tests.txt) and
[pre-change comparison](baseline-tests.txt).

The stop monitor continues to depend on collector publications and fresh-book
validation. Stops retain the configured 55¢ trigger, reduce-only IOC, 1¢ minimum,
final control checks and reconciliation. A trigger does not guarantee a fill price.

## Reproducing the paired benchmark

`benchmark_readonly.py` needs the pre-change `live_automation.py` and
`manual_trading.py` under `/tmp/project15-stop-baseline/btc15/`. Reconstruct those
from the private `source-before.tar.gz` backup if needed; do not replace production
source. The script loads credentials only to perform GETs and never writes them to
results. Run with the project virtual environment from the project root. Avoid
running it during a live exit because its GETs consume account read capacity.
