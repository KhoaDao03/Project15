# Invalid or changed metadata: settlement recovery

## Purpose and scope

Baseline: `82e07c5e988f926a23a25d5ae056bd0a14e545fb` on the Settlement Edge-only branch.
Invalid metadata must stop trading, not erase an owned contract or leave its
accounting without an explicit recovery path. This change pins validated entry
identity, retains quarantine in the accounting checkpoint, captures final REST
metadata and permits only an audited transition into settlement. It does not
change strategy thresholds, fee settings, sizing limits or the live-order block.

## What happens now

1. A submitted paper order pins the validated contract, including its event,
   exchange, market ID, close time, comparator, strike and settlement-rule hash.
2. Invalid metadata, changed identity/terms, or an explicit lifecycle metadata
   change cancels pending entry quantity and quarantines the contract. Filled
   inventory and reserved exposure remain. Neither buys nor pre-expiry sells are
   allowed in that contract while it is quarantined.
3. The collector continues polling the original tracked contract after its close.
   A fetched response is not considered complete until the ordered worker commits
   settlement. Invalid final responses are therefore retried rather than dropped.
4. A quarantined contract requires complete final REST evidence. Matching original
   identity and settlement terms can complete automatically. Valid but different
   terms require explicit operator review. Missing, malformed, unsupported or
   wrong-identity evidence remains blocked, even with operator confirmation.
5. Completion writes evidence, recovery decision, settlement, final trade result,
   risk release and checkpoint atomically. It enters `SETTLEMENT_PENDING` and then
   `CLOSED`; it never reopens trading or clears a risk kill switch.

A routine status, volume or title update is not a change in settlement terms.
A missing strike on a newly discovered listing remains a pending listing. A
missing strike on an already known contract instead triggers quarantine.

## Settlement evidence, not a guessed outcome

Kalshi's [lifecycle documentation](https://docs.kalshi.com/getting_started/market_lifecycle)
distinguishes REST `finalized` from `determined`, disputed and amended results.
Recovery requires a parser-valid binary contract, a matching market/event/exchange
identity, `status=finalized`, the same binary result as the evidence and a time
at or after both original and observed close times. The code uses the official
result, not the last BTC price or a recomputed prediction, to credit the remaining
held quantity.

The recorder retains the complete REST market and series snapshot inside the
settlement event. The `source=kalshi_rest` marker describes the collector's path;
it is not a cryptographic signature on uploaded/replayed data. Only replay trusted
recordings. Old synthetic/legacy settlement-only events continue to work for
unquarantined contracts, but cannot clear a quarantine without full evidence.

## Inspect first

Use the same database and exact run ID as the affected portfolio:

```bash
uv run --locked btc15 settlement-recovery --run-id RUN_ID --market MARKET_TICKER
```

A global `--database` can select a non-default database. The command displays the
pinned original contract, quarantine reason, retained evidence IDs and hashes.
Without an evidence ID it is a preview: no trade, payout or checkpoint is changed.
The command uses the run's original saved configuration, not next-session Settings.
Do not change a configuration or start a new run to bypass unresolved exposure.

The collector's status includes `settlement_recovery` for quarantined held
positions. `paper-health` reports `SETTLEMENT_RECOVERY_REQUIRED`. The existing
record/history API can retrieve `metadata_quarantine`, `settlement_evidence`,
`settlement_blocked` and `settlement_recovery` rows. A blocked settlement is not
recorded as a losing trade or zero payout.

## Review genuinely changed terms

After a clean stop and consistent backup, compare the pinned contract with the
latest evidence and independently verify the venue's final terms/result. Use the
exact evidence ID and hash returned by the preview, with a meaningful reason:

```bash
uv run --locked btc15 settlement-recovery --run-id RUN_ID --market MARKET_TICKER --evidence-id EVIDENCE_ID --confirm EVIDENCE_HASH --reason "Reviewed finalized venue terms and outcome"
```

This is an accounting write. It takes the same exclusive writer lease as the
collector and refuses while a writer owns the database. Never delete a lease or
modify checkpoint flags to make it succeed. Evidence must belong to this run,
mode and market and must not have been superseded by a differing observation.
Wrong hashes, empty reasons, unsupported settlement wording, scalar outcomes,
non-final statuses or identity mismatches cannot be overridden. There is no
`--result yes/no`, forced payout, or unrestricted unhalt option.

Operator review acknowledges changed but supported terms for this exact recorded
contract. It does not certify the old model was calibrated to those terms. Mark
such results as reviewed exceptions when assessing strategy performance. A truly
new unsupported methodology needs a separate parser/reconciliation review; this
command deliberately does not guess how to pay it.

## Restart, replay and duplicate behavior

New checkpoints preserve pinned contracts and quarantine reasons. Legacy held
positions can recover entry identity from validated metadata recorded at/before
entry; later changed metadata does not overwrite that identity. Missing or
ambiguous entry-time history raises a forensic-recovery error rather than guessing.
An old run must first resume under this version to persist that reconstruction.
No existing history or checkpoint is edited by a deployment alone.

Duplicate settlements do not pay twice. Conflicting later results produce an
explicit blocked record and do not rewrite an already completed P&L. A failed
accounting write rolls back settlement claims, transitions, risk and checkpoint,
so the same evidence can be retried. A generic `ERROR` or unclassified `HALTED`
state is not released through this metadata-specific path.

PAPER and BACKTEST use the same accounting path. BACKTEST recovery uses the
recorded evidence time, not today's clock. Operator review is a separately saved
accounting action: retain its audit row and command with your experiment. Replaying
the raw tape alone does not silently reproduce a human confirmation. Old tapes
without final metadata may legitimately end with an unresolved quarantine.

## Validation and remaining boundaries

Tests exercise real Engine/Executor/SQLite transactions, restart, duplicate and
conflicting results, conservative refusal cases, a mocked collector retry, and
reopened trade/P&L views. Inputs are synthetic; no authenticated live feed or
profitability claim is implied. The ordinary complete suite and synthetic engine
workflow must also pass on the published commit. See the PR's executed results.

Normal pre-expiry exit safety is unchanged. This is not the separate exit-depth
replenishment fix, an automatic correction of corrupted fractional inventory,
a database reset, or permission to submit real orders.
