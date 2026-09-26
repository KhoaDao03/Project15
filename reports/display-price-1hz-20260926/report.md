# Reference-price display and one-second refresh

Deployed September 26, 2026 at 01:45 UTC.

BNB and HYPE were receiving official 1 Hz strategy ticks, but their dashboard reference snapshots were null because the display only projected the separate 5 Hz channel. The collector now projects validated official 1 Hz reference ticks as well, without replacing a newer sample with an older one. Index matching, finite/positive values, source-clock bounds and existing display freshness checks remain enforced. This projection does not change strategy inputs or authorize entries.

Both dashboards now refresh at one-second intervals: private market SSE, private fleet polling and public snapshot polling/publishing. The separate historical export schedule remains unchanged.

The BNB/HYPE collector reload waited until their active position/unresolved-order checks cleared in the next early-cycle window. Only these two collectors, the private dashboard and public publisher restarted. The executor continued running. Existing live policies, including the user-enabled BNB/HYPE policies, were unchanged.

Validation: 42 focused tests passed, including the reference-handler JavaScript test and public browser test. Scoped Ruff and whitespace checks passed. Live browser verification confirmed numeric BNB/HYPE prices on both dashboards, no JavaScript errors, private SSE intervals of approximately 1.003 seconds, and public polling intervals of approximately 1.02–1.12 seconds including request time. All affected services were active. The publisher briefly retained old exports during private-dashboard startup; final exports were fresh.

See [verification](verification.json). Private backups and deployment scripts are under `data/deployments/display-price-1hz-20260926/`.
