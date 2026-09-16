# Stale market metadata recovery

On September 13, the XRP 01:00 market accepted a published strike of 1.3639 at 00:45:16 EDT. At 00:45:32 a subsequent REST response contained an older venue revision with unpublished strike fields. The collector treated it as invalid metadata, quarantined the market, and reconnected. Later valid metadata could not release the persisted quarantine.

The engine now ignores a market record only when both venue `updated_time` timestamps are valid and the incoming revision is strictly older than the accepted revision. Missing, malformed, equal, or newer timestamps do not bypass contract validation.

For a quarantine created before this fix, automatic recovery is narrowly limited to `METADATA_INVALID` caused by an older unpublished-strike record. The immutable quarantine observation must identify that older revision. Filling only its absent strike fields from the pinned contract must reproduce the original contract hash, and a new active REST observation must independently match that same hash. Changed terms and unrelated halts remain blocked.

The engine also requires healthy reference data, a valid fresh book, and no outstanding collector integrity failure. The REST request must begin after quarantine and be no more than 30 seconds old. Venue pauses and portfolio risk halts prevent recovery. No expired contract is reopened.

Recovery records `metadata_recovery` evidence and atomically transitions the quarantined market to POSITION_OPEN if inventory is held, or EVALUATING otherwise. Checkpoint failures roll back the transition, recovery record, and in-memory state. Positions, risk budgets, and canceled orders are preserved. Entry eligibility remains subject to all normal strategy/freshness checks.

Validation: 17 focused stale-metadata tests and 121 existing settlement, restart, collector, crypto-asset, and execution tests passed. The actual XRP quarantine was also recovered in an isolated database copy with positions and risk state unchanged. Evidence, source snapshot, and pre-deployment XRP backup are under `data/runtime/xrp-stale-metadata-fix/`.

The XRP collector was restarted at 00:58:17 EDT to load the fix. Other collectors and the shared dashboard were not restarted by this deployment.

Live verification at approximately 00:58:39 EDT confirmed the loaded source hash, READY state, no blocking reasons, and an empty quarantine map. XRP positions and risk state matched the pre-deployment checkpoint. The immutable live recovery record is saved as `live-recovery-record.json` beside `live-verification.json`.

## Expiry regression corrected at 01:12 EDT

The first deployment recovered the market to EVALUATING with no open position. At 01:00:16, the venue's final settlement arrived, but the state table did not permit EVALUATING → SETTLEMENT_PENDING. The resulting exception rolled back settlement and was classified as INVALID_DATA, causing a reconnect on every retry. This was a lifecycle gap in the recovery fix; validation had stopped at recovery instead of exercising expiry.

The state table now permits that settlement transition. Existing settlement-time, result, evidence, and accounting checks remain in place. A new regression test covers quarantine → verified recovery → expiry without a fill, verifies unchanged risk/no fabricated trade result, and verifies repeated settlement is idempotent.

All 18 stale-metadata tests and 74 settlement/restart/collector regressions passed. The actual recorded settlement was also replayed into an isolated ledger copy: EVALUATING became CLOSED, one settlement committed, and positions/risk were unchanged. XRP alone was restarted at 01:12:47 EDT. Live verification confirmed the fixed source, CLOSED historical market, exactly one settlement, and collector READY. Evidence: `data/runtime/xrp-expiry-fix/`.
