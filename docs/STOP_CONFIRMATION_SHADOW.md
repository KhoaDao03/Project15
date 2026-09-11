# Stop-confirmation-v1: parallel paper comparison

The primary bot keeps its current entry rules, sizes, ordinary stop and probability
exit. This opt-in experiment copies only committed paper buy fills into a separate
SQLite ledger (`data/stop-confirmation-shadow.db`). It places no extra primary orders
and never updates primary inventory, risk, P&L or accuracy. The shadow keeps following
received books and official reference data after the primary position closes.

## Frozen candidate

- First ordinary price-stop breach starts a two-second check and counts one fresh
  reference observation. A second distinct, consecutive reference observation with
  a fresh qualifying book releases the stop. Repeated book events cannot add samples.
- If two seconds elapse while the bid remains below the stop, release without another
  confirmation. The normal execution latency starts after release; two seconds is a
  decision wait bound, not a guaranteed fill deadline or dollar-loss cap.
- Existing probability invalidation bypasses confirmation. A bid at or below 50% of
  average purchase price also bypasses it. This emergency threshold is experimental.
- Missing/unusable probability, a missing reference second, a connection/input gap or
  restart bypasses confirmation at the next healthy executable observation. Stale or
  invalid data never authorizes a fill. A released stop does not rearm its wait while
  its execution latency is pending.
- Recovery resets the check when displayed bids above the ordinary stop can cover
  the remaining position. A dust bid alone does not reset the wait. This experiment
  keeps the ordinary best-bid stop trigger; it does not introduce a VWAP stop trigger.
- The corrected displayed-depth matcher, post-eligibility observation requirement,
  fees, partial fills, consumed-depth protection and conditional exit timers remain.

## Isolation and recovery

Enable with `paper-service --stop-confirmation-shadow`. No strategy configuration
field changes. The observer reuses a current primary probability cache when possible;
it computes its own causal holding model when the primary has already exited.

Separate checkpoints preserve mirrored fill IDs, inventory, confirmations and gap
markers. Committed buy rows are re-read before copying, so rollback cannot create
shadow inventory. An already-open primary trade at initial activation is excluded
rather than pretending to have simulated its earlier path. Resumed shadow positions
are retained and marked interrupted. Validated official settlement evidence is still
required for payout. An observer error disables that experiment instance and records
`stop_shadow_status=FAILED`; primary execution remains active.

Results are fixed-purchase comparisons, not complete portfolio simulations: hypothetical
longer holdings do not block the real bot's next purchase. Future performance, activity
and exposure under an actually deployed alternative can therefore differ. Observed
input interruptions are flagged in comparison records; missing paths are not invented.

## Read results

`stop_shadow_comparison` records contain paired P&L, their difference, exit reasons,
holding durations, policy parameters and input-interruption flags. Primary history
is retained. Full shadow decisions/fills and checkpoints live in the separate ledger.

```bash
.venv/bin/python scripts/report_stop_shadow.py
```

Existing records API:
`/api/records?kind=stop_shadow_comparison&mode=PAPER&run_id=settlement-convergence-v7`
Use `kind=stop_shadow_status` to inspect activation/exclusion/failure records.

## Historical check and prospective evaluation

The nine original purchases replayed against displayed-depth-v2 produce:

| Rule | Net across nine | Largest original stop trade |
| --- | ---: | ---: |
| Current stop | -$0.0927 | -$2.3220 |
| Bounded two-confirmation candidate | +$0.1075 | -$2.1218 |

The other eight trades match. This is a $0.2002 improvement in a single trade, not the
+$2.75 recovery from the earlier unbounded experiment. Receipt-clock replays cannot
reproduce every collector processing delay. Keep the primary rule active while
collecting later pairs. Evaluate total net, worse delayed exits, worst losses, extra
holding time and interrupted-input cases; do not select thresholds using these same
nine outcomes. Local replay: `data/runtime/stop-confirmation-v1-replay.json`.
