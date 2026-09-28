# Eight-minute entry deployment — 2026-09-28

Deployed at the 00:00 UTC market boundary. BTC 87%, ETH 86%, SOL 83%, XRP 85%, DOGE 83%, BNB 86%, HYPE 84% apply for 420 < remaining <= 480 seconds. All return to 83% at 420 seconds. Production evaluations confirm matching run/config identities, fresh reference feeds and unchanged ATR multipliers. Contract quantities are preserved. Both dashboards show ten assets; all services are active.

565 deployment-focused tests passed, following the original 707 staged checks. Source lint, JavaScript syntax and diff checks passed. Old collector databases and original run records remain intact. Compact deployment backups are in data/deployments/eight-minute-20260927/.

## Rollout incident and remaining block

The new databases initially lacked historical settlement records, causing the existing global loss guard to calculate -428.644 and disable new buys. Verified historical settlement facts and their evidence were copied from the retained databases, with original record/run provenance. All earlier trade P&Ls match; newly recovered final settlements can add results. Corrected dashboard realized P&L is +164.0263 at verification.

The guard remains latched and buying remains disabled. Automatic approval review rejected clearing the false latch because resetting a trading safety control was not explicitly authorized. No reset or re-enable action was performed. User approval is required to clear the false latch while keeping buys disabled; re-enabling trading is a separate user action.

For future run migrations, restore settlement history and verify complete P&L before starting the execution service/global guard. Reference history alone is insufficient.

## Cleanup

Removed /root/Project15-staged-eight-minute after verifying all changed files match deployed source. The patch, deployment notes and compact source/config backups are retained. See cleanup.json for bytes removed.

## Loss guard reset — resolved after explicit user approval

On 2026-09-28 the user approved resetting the false loss-guard latch. Recomputed combined realized P&L was +164.0263. The latch and associated loss_guard annotations were cleared with a backup, then the execution service was restarted. Live status confirms triggered=false, no error, and the -50 threshold remains active. Buying remains disabled for all markets and contract quantities are unchanged. See loss-guard-reset.json.
