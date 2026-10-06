# Resource optimization deployment — 2026-10-05 UTC

The user authorized deployment after staged implementation and revalidation.
The validated patch was applied to `/root/Project15` on top of commit `918641c`.
All nine affected services started new processes at **00:48:09 UTC**:

| Service | New PID |
| --- | ---: |
| BTC collector | 123731 |
| ETH collector | 123732 |
| SOL collector | 123733 |
| XRP collector | 123734 |
| BNB collector | 123735 |
| HYPE collector | 123738 |
| DOGE collector | 123737 |
| Real-order executor | 123736 |
| Private dashboard | 123739 |

All five changed runtime modules match the validated checkout's SHA-256 hashes.
The service working directory and editable Python installation point to this
production checkout. The new processes started after the source was installed.
The existing dashboard request-sharing and visibility changes are also in this
checkout. Frozen strategy configurations and the fleet manifest were unchanged.

## Validation before deployment

The full 2,688-case collection was checked across two runs. The initial run was
interrupted near its time budget, and the remaining 113 cases were run separately:
**2,579 passed, 106 failed, 3 skipped**. Every one of those 106 failing cases was
rerun against unchanged production code and failed there too; the failure sets
match exactly. The suite is not fully green, but no new failing case was found.
The earlier focused tests and offline measurements are documented in
`RESOURCE_REVALIDATION.md` and `RESOURCE_OPTIMIZATIONS.md`.

## Restart and state preservation

The initial shutdown guard refused while five markets still had managed
positions. Collectors and execution remained running until those markets closed.
The subsequent shutdown preparation disabled entries but exceeded its 45-second
client timeout while updating thousands of historical controls. Before stopping
anything, a fresh successful execution API read and read-only database checks
confirmed completion: all asset and market entry controls disabled, no pending
orders, no active managed positions, and the executor responsive.

The services were then stopped gracefully, the scoped patch applied, source
hashes verified, and the same services started. Historical data and contract
identities were retained. There were temporary research-reader database-lock
warnings during the old preparation and new executor startup; later monitoring
checks found no continuing warnings after permission restoration.

Only after all seven collectors passed health checks and the executor/dashboard
responded successfully were the existing permissions restored through the normal
control API, with current revisions and safety checks:

- BTC: 25 contracts; SOL: 35; XRP: 20.
- ETH, BNB, HYPE, DOGE: 10 each.
- GOLD, SILVER and WTI remain disabled and their collector services remain stopped.

No trading thresholds, quantities, loss-guard rules, service units, or frozen
configurations were changed by this deployment. The process restart temporarily
requires fresh reference history; entry gates remain in force during warmup.

## Live checks and rollback material

Verified the new ticker-prefix and recent-control indexes, per-asset revision
table, and all four history triggers in the production order database. Collector
health checks use the actual run IDs and data directories from the fleet manifest.
All nine services have zero automatic restarts since this deployment. The executor
reports an advancing cycle and an available, untriggered global loss guard. The
private fleet endpoint returns HTTP 200 with execution available.

Deployment evidence and a consistent pre-deployment journal backup are at:
`/root/project15-deploy-backups/resource-20261005T003146Z/`.
This includes original source files, expected hashes, test logs, before/after
health checks, service identities, and saved/restored permissions. These are
private operational artifacts; do not publish their raw contents.

A code rollback must preserve the current live databases: never blindly restore
an older journal over orders recorded since the backup. Added indexes/triggers
can remain with the prior code. The deployed working-tree changes have not been
committed to Git by this deployment.

## Final verification

At approximately 00:55 UTC, all seven collectors were healthy and READY, with
fresh strategy evaluations, reference warmup cleared, and processing lag between
0.6 and 35.7 ms in the final sequential probes. The executor cycle age was 0.143
seconds; the dashboard returned HTTP 200 with execution available. Source and
configuration hashes and all saved permission/quantity settings match.

BTC briefly entered backlog recovery during observation and returned to READY
without a restart. Similar backlog warnings occurred before deployment (43 in
the sampled 15-minute baseline and 35 in the sampled 6-minute post-start period);
these unequal windows and changing traffic do not establish a performance
comparison. This remains an operational limitation to monitor. No uninterrupted
24-hour guarantee or whole-VPS resource reduction is claimed.
