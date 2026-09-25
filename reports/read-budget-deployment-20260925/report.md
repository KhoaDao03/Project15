# Read-budget and buy-preflight deployment

Deployed **2026-09-25 05:17:47 UTC**, following positive safe-shutdown confirmation and stopped-state
backups. No open managed bot positions or unresolved orders were present at preflight.

## Changes loaded

- Shared token-bucket read limiter: 200 tokens/second, 600-token capacity, verified
  against the account API immediately before deployment.
- Burst reads when credit is available, shared 429 backoff and a 30-token reserve
  for hard-stop preflight. All seven collectors, executor and private dashboard
  see the same limiter file (matching device/inode), under the same service user.
- Automatic buys no longer fetch balance or perform a local cash-sufficiency check.
  Fresh holdings, market validation, final controls and existing retry limits remain.
  Balance display remains available; Kalshi enforces funds at submission.

## Verification

All nine restarted services are active with zero automatic restarts. All seven
collectors are connected, healthy and READY; sampled processing lag is below 4 ms.
The seven executor source snapshots match `api.py`, `manual_trading.py` and the new
`read_budget.py`. Source and frozen configuration/manifest hashes match preparation.

BTC/ETH/SOL/XRP buy policies are restored to enabled, 10 contracts. GOLD/SILVER/WTI
remain disabled. Stop prices, strategy versions and quantities are unchanged.
Crypto reference-gap/model-quality warmup and commodity model warmup still gate
entries after restart; saved enabled switches do not override them.

The isolated public website and its trusted publisher remained running. The old
public-dashboard service remains inactive; it was not restarted.

Transient research journal-lock warnings occurred during control restoration.
Final executor recorder checks show zero dropped events, no diagnostics and healthy
journal reads. The limiter recorded a startup shared backoff, which had expired
with available tokens at final verification. No claim of zero rate-limit responses
or long-term uninterrupted recording is made.

Validation before deployment: **421 targeted tests passed**; scoped lint and
whitespace checks passed. The full suite has previously documented preset-related
failures and is not claimed green. No trades were forced and no new buy-latency
measurement is claimed in this deployment.

## Evidence

- [Cutover](cutover.json)
- [Final verification](verification.json)
- [Test output](tests.txt)

Private backups, before/after policies, exact source and account-limit responses
are under `data/deployments/read-budget-20260925/`. The unrelated public server/export
modules were already deployed separately; this handoff did not change that installation.
