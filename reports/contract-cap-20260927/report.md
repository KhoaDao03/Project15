# Private dashboard contract cap — 2026-09-27

Removed the private dashboard's 20-contract ceiling from the input, LiveControl validation, status metadata, and final submission recheck. These now match the existing order-system ceiling of 100,000 contracts. Positive whole quantities remain required. Current trading quantities and enable policies were preserved.

264 mocked execution and fleet dashboard regression checks passed, including a 35-contract order and managed exit. Lint, JavaScript syntax and diff checks passed. Restarted the executor and private dashboard after checking the early market cycle, no active managed positions and no unresolved orders. Production status reports the new ceiling; browser checks accepted 35 and rejected 1.5 without submitting settings or orders.

The public dashboard was outside this request. Backups and scripts are in data/deployments/contract-cap-20260927/.
