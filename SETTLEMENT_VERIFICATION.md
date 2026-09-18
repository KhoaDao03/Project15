# KXBTC15M settlement verification

Audit: 2026-09-17 UTC. Scope: the independent, read-only `btc_probability` engine. **Primary settlement probability remains unavailable solely on the compatible profile's unresolved half-cent convention, plus any runtime input failures.** No verification override was installed. Recording, provider calculations, synthetic numerical tests, and explicitly assumption-based terminal research continue. Forecast calibration is not established by this audit.

## Finding and implementation

The old close-inclusive sample assumption is contradicted by two published settlement values. The supported normal profile now uses 60 one-second BRTI observations at `[T-60,T)`. Market wording and contract terms specify the preceding minute; captured official data independently support that interpretation. The close-inclusive final-minute feed helper has different semantics. This correction applies to the probability engine; the inactive legacy trading implementation remains unchanged and its contrary documentation is marked with an audit notice.

The profile `kxbtc15m-before-boundary-v1-20260917` recognizes an exact reviewed rule template, validates dynamic metadata, and pins both series-linked document hashes. Target, opening time and observation time are parsed afresh. A changed rule, document, comparison, reference, timing or precision stays blocked. PDF fetch failures do not stop market or reference collection. Free-form JSON review files cannot authorize a contract. No credentials change was required.

## Evidence and provenance

All captured files are in [the evidence directory](tests/probability/fixtures/settlement_audit_20260917/). [manifest.json](tests/probability/fixtures/settlement_audit_20260917/manifest.json) records URLs, HTTP status, retrieval time and SHA-256 for HTTP responses; derived files identify their origin. Tests verify every listed hash. Original PDFs and extracted text are both retained.

| ID | Applicable authoritative material and preserved artifact |
| --- | --- |
| S | [Current KXBTC15M series API](https://external-api.kalshi.com/trade-api/v2/series/KXBTC15M), `series.json`; fetched 06:48:36 UTC. |
| M | [Actual processed market](https://external-api.kalshi.com/trade-api/v2/markets/KXBTC15M-26SEP170300-00), `market.json`; fetched 06:48:37 UTC. `processed_market.json` preserves its prior recording event, raw response, normalization and receipt time. |
| T | Series-linked [contract terms](https://assets.kalshi.com/contract_terms/CRYPTO.pdf), `contract_terms_url.pdf`, SHA-256 `fde90b9c0825df277b0b2b2be6239af221eafd01a624c1b2d3a9eaff2d6fe75c`. |
| C | Series-linked [certification](https://assets.kalshi.com/regulatory/product-certifications/CRYPTO.pdf), `contract_url.pdf`, SHA-256 `4841dd60f533d857c58e0c338c39d5c1306282c6aa54751439b032d933b4f8a1`. Certification dated July 18, 2025; accessed September 17, 2026. |
| F | [CF feed documentation](https://docs.kalshi.com/websockets/cfbenchmarks-value), `feed.txt`; [history API](https://docs.kalshi.com/cfbenchmarks/rest-passthrough), `reference_history.txt`. |
| A | [Market schema](https://docs.kalshi.com/api-reference/market/get-market), `market_schema.txt`; [lifecycle documentation](https://docs.kalshi.com/getting_started/market_lifecycle), `lifecycle.txt`. |
| O | Official WS observations and published results: `window_0645.json`, `window_0700.json`, `final_market_0645.json`, `final_market_0700.json`. Raw WS frames include both aggregates. Exports retain original event IDs and availability timestamps from `data/probability/btc-live/recording.sqlite`. |

`reconciliation_0645.json` and `reconciliation_0700.json` are derived audit results, not authoritative sources. The PDF hashes are code pins; runtime retrieval preserves actual bytes in `rule_documents` events, with content hashes and receipt times. Documents are refreshed every 300 seconds and must be no older than 900 seconds at profile construction. Metadata older than 60 seconds invalidates runtime inputs. Response credentials/headers are not captured.

## Requirement-by-requirement audit

Paths below are relative to `src/`; tests are in `tests/probability/test_settlement_verification.py` unless qualified. “Verified” means supported for this matched normal-resolution profile, not every cryptocurrency contract or forecast accuracy. “Contradicted” identifies the prior assumption; the correction is stated explicitly. There are no unchecked critical parser items hidden under “unresolved.”

| Requirement; existing assumption and actual behavior | Implementation | Source | Status | Behavior test | Missing evidence / condition to clear |
| --- | --- | --- | --- | --- | --- |
| Family and reference: legacy parser already checked BRTI/CF; early probability discovery used weaker substring recognition. Exact supported family, 15-minute period, binary market, primary BRTI wording and CF source are now required. | `btc15/domain.py:parse_market`; `btc_probability/contract_profile.py:analyze` | S, M, T | Verified | `test_captured_contract_independent_expectations`, `test_changed_semantics_block`, `test_documents_stale_changed_and_price_ticks_not_rounding` | None for matched template; changed identity requires a separately reviewed profile. |
| Target: discovery previously treated `floor_strike` as unverified. It is now accepted for the matched greater-or-equal contract, as a Decimal string; no ticker/UI inference or local reconstruction replaces it. | `contracts.py:discover_spec` | M, A; O opening-window check | Verified | `test_captured_contract_independent_expectations`, `test_second_actual_processed_market_and_opening_target_reconcile` | TBD/missing targets remain blocked until official metadata supplies one. |
| Observation timing: legacy prose parser checked local-zone times against open/close; probability discovery previously assumed close. Profile now validates both rule timestamps, DST, metadata agreement and 900-second period. | `contract_profile.py:rule_time, analyze` | M, T | Verified | `test_captured_contract_independent_expectations`, `test_changed_semantics_block` | Changed or inconsistent times require authoritative corrected metadata/rules. |
| Distinct clocks: trading close was the horizon candidate; administrative expiration was not modeled. v2 separately retains close, observation end, expected expiration, administrative expiration and actual settlement time. | `contracts.py:discover_spec`; `schema.py:ContractSpec` | M, A, O | Verified for observation horizon; administrative conflict unresolved | `test_captured_contract_independent_expectations` | See administrative conflict below. No payout timer is inferred. |
| Arithmetic average: existing model already averages samples, then rounds and compares; it does not settle on last price. This working order is retained. | `models.py:simulate`; `schema.py:ContractSpec.yes` | M, T | Verified | `test_rounding_order_equality_and_unresolved_tie`, `test_published_value_reconciliation_not_just_yes_no`; existing numerical tests | None for non-tie normal averages. |
| Sample boundaries: legacy `SettlementSpecification.sample_times` uses `(T-60,T]`; initial probability parser lacked a reviewed schedule. New profile supplies `[T-60,T)` and the analogous opening window. | `btc15/domain.py:sample_times` unchanged; `contracts.py:discover_spec`; `reference.py:snapshot` | M, T, O | Prior inclusive-close assumption contradicted; correction supported | `test_boundaries_partial_duplicates_missing_late_and_conflicts`, both published-value tests | Broader observation coverage remains ongoing; these two windows do not establish outage behavior. |
| Feed aggregates: old `crosscheck` could treat the final-minute helper as settlement and omit end/receipt checks. Both fields are now preserved and reconciled against distinct schedules. | `reference.py:crosscheck`; `reconciliation.py:reconcile`; `live.py:references_loop` | F, O | Prior interchangeability contradicted | `test_crosscheck_causality_and_distinct_aggregates`, both published-value tests | None for observed normal windows. Helper field names alone do not define settlement. |
| Comparison/equality: early discovery defaulted unknown prose to `>`; legacy parser was stricter. Profile now requires exact “at least” template and structured `greater_or_equal`. | `contract_profile.py:analyze`; `schema.py:yes` | M, T, A | Verified | `test_changed_semantics_block`, `test_rounding_order_equality_and_unresolved_tie` | Unsupported comparisons remain blocked; no default. |
| Precision: legacy used asset precision and helper metadata. Profile gets two settlement decimals from the exact secondary rule; helper precision only checks compatibility. Target increments, trading ticks and UI display are not rounding authority. | `contract_profile.py:analyze`; `contracts.py:discover_spec` | M; T/C trading-tick distinction | Verified nearest-cent precision; tie unresolved | `test_documents_stale_changed_and_price_ticks_not_rounding`, `test_rounding_order_equality_and_unresolved_tie` | Tie convention below. |
| Half-cent ties: legacy considered half-even versus half-up ambiguity; manual probability reviews could choose a policy. Current profile leaves `rounding=None`, blocks primary output, and reports adjacent cents in numeric audit. | `contract_profile.py:analyze`; `reconciliation.py:reconstruct`; `engine.py:forecast` | M incomplete; checked T, C, F, A do not specify tie direction | Unresolved: authoritative material inspected but incomplete | `test_rounding_order_equality_and_unresolved_tie`; `test_pipeline.py:test_review_binds_current_rules_and_requires_target` | Binding Kalshi rule or attributable written clarification, preserved with a new profile version and tie-case tests. |
| Duplicates/conflicts: existing accumulator uses exact timestamps and rejects conflicting elapsed samples. Retained; identical duplicates never add weight. | `reference.py:ReferenceHistory.snapshot`; `reconciliation.py:reconstruct` | F supplies timestamp identity; conservative local integrity policy | Verified implementation | `test_boundaries_partial_duplicates_missing_late_and_conflicts` | Conflicts require authoritative corrected observations; no local selection heuristic. |
| Partial/missing samples: missing elapsed observations block primary calculation; future samples may be simulated only on verified mechanics. No carry-forward or zero-fill. Partial numeric means are explicitly not final values. | `reference.py:snapshot`; `models.py:simulate`; `reconciliation.py:reconstruct` | F; T does not equate collector gaps with source outage | Verified implementation | `test_boundaries_partial_duplicates_missing_late_and_conflicts`; existing partial-window model tests | Original timestamped official observations must arrive to fill gaps. |
| Late arrivals/replay: both source and receipt clocks gate eligibility. Previously aggregate checks used source time as availability; corrected. Later observations/results cannot improve earlier forecasts. | `reference.py:causal, crosscheck`; `reconciliation.py`; `recording.py:restore` | F; local causality policy | Prior aggregate availability shortcut contradicted | `test_crosscheck_causality_and_distinct_aggregates`, `test_pipeline.py:test_later_recovery_and_future_events_cannot_change_saved_forecasts` | None for implemented replay; no claim of prolonged outage-recovery qualification. |
| Contingencies: local missing feed does not prove official data unavailability. No automatic NO or payout is synthesized; unusual/provisional conditions block profile compatibility and published results remain separate. | `contract_profile.py:analyze`; `engine.py:forecast, apply` | T contingency/review provisions, M, A | Verified conservative behavior; discretionary payout not predictable locally | `test_changed_semantics_block`, missing-data test, `test_outcome_reducer_records_numeric_reconciliation` | An affected event requires Kalshi's published determination; no local default clears it. |
| Verification/provenance: arbitrary per-market JSON reviews previously could mark mechanics verified. Versioned profile now checks actual PDF bytes and rule text; JSON review adds a blocker. | `adapters.py:rule_documents`; `contract_profile.py`; `contracts.py:discover_spec` | S-linked T/C and M | Verified fail-closed implementation | `test_rule_document_capture_uses_actual_bytes`, `test_manifest_hashes`, changed-document and review tests | Changed documents require content review, new pins/version and independent fixture expectations. |
| Readiness/calibration: old early return suppressed all diagnostics. Verification, runtime validity and model state are now separate; assumption-based terminal research has its own field and cannot populate primary/terminal chart fields. Verified synthetic mechanics demonstrate raw uncalibrated output. | `engine.py:forecast`; `schema.py:Forecast`; `static/app.js` | Local model policy, not exchange settlement authority | Verified separation; forecast accuracy unestablished | `test_research_continues_without_primary_and_raw_calibration_separate` | Primary: all semantic checks plus valid inputs. Calibration: separately trained and held-out evaluated calibrator, not this parser audit. |

## Numeric reconciliation actually performed

Both completed observation windows have 60 exact official observations, no missing slots and no conflicting values. The trailing provider aggregate agrees within its eight-decimal serialization precision. Full result JSON retains event IDs and cutoffs.

| UTC observation end / market | Preceding-minute exact mean | Rounded / published | Close-inclusive helper mean | Helper rounded |
| --- | --- | --- | --- | --- |
| 06:45 / `KXBTC15M-26SEP170245-45` | 76492.62016666666666666666667 | **76492.62 / 76492.62** | 76492.46216666666666666666667 | 76492.46 |
| 07:00 / `KXBTC15M-26SEP170300-00` | 76406.24916666666666666666667 | **76406.25 / 76406.25** | 76406.23216666666666666666667 | 76406.23 |

**Publication state matters:** 06:45 was `finalized` with `settlement_ts=2026-09-17T06:45:08.460425Z`; 07:00 was `determined`, with a published value/result but no settlement timestamp at capture. The latter is not claimed as finalized or paid. Its remaining finality check clears only when a later official response reports finalization; any changed value must be reconciled again. Numeric agreement with a determined value is not proof that review cannot change it.

The first contract's target was 76349.87: both windows would produce YES, so outcome-only validation would miss the error. The second contract's opening target, 76492.62, independently matches the first preceding-minute reconstruction. A counterfactual target 76490 in the first window places the last included reference below target and the average above it; its test catches last-price substitution. This counterfactual is explicitly synthetic, not an additional live market.

Reconciliation excludes future-source observations even if clock skew makes their receipt earlier. Published results enter only at their recorded receipt time (06:45:30.717336 UTC and 07:00:16.711463 UTC respectively), not the earlier exchange settlement timestamp. Exact half-cent means return no selected rounded value. The collector now emits `settlement_reconciliation` after receiving outcomes; the reducer retains 20 minutes of audit observations. Results delayed beyond that retention may be incomplete online and can be reconstructed from the full append-only recording offline.

Reproduce the preserved audit:

```bash
.venv/bin/python -m btc_probability.reconciliation tests/probability/fixtures/settlement_audit_20260917/window_0645.json
.venv/bin/python -m btc_probability.reconciliation tests/probability/fixtures/settlement_audit_20260917/window_0700.json
# Causal partial view; official result is unavailable at this cutoff:
.venv/bin/python -m btc_probability.reconciliation tests/probability/fixtures/settlement_audit_20260917/window_0645.json --as-of 1789627470
```

## Genuine ambiguities and exact clearing conditions

**Critical: `ROUNDING_TIE_UNSPECIFIED`.** The market secondary rule says “rounded to the nearest 2 decimal places.” This fixes precision but leaves a mean exactly halfway between cents ambiguous. The inspected contract terms, certification, feed and market schema do not specify a tie-breaking direction. No captured example has an exact half-cent mean. This is inspected-but-incomplete authority, not a credentials issue or an unchecked item. Rounding each input first is also not substituted for rounding the arithmetic mean.

**One precise question for Kalshi (prepared, not sent):**

> For KXBTC15M, when the exact arithmetic mean of the 60 applicable BRTI observations is USD 76492.625, does the official settlement value become 76492.62 or 76492.63 before the greater-or-equal comparison, and which binding rule specifies that half-cent tie convention?

Clearing requires preserving Kalshi's authoritative answer/rule and its applicability/version, implementing that convention in a new profile, and testing ties on both even and odd cent neighbors. Repeated ordinary-window matches or choosing half-up in a JSON file do not clear it. Unknown changes remain blocked even after the tie rule is established.

**Administrative expiration conflict, outside the normal observation forecast:** T says “The Expiration time of the Contract shall be 10:00 AM ET.” M instead provides `expiration_time=2026-09-24T07:00:00Z` (03:00 EDT), plus `expected_expiration_time=2026-09-17T07:05:00Z`. Observation close is 07:00 UTC, and the finalized 06:45 market's actual settlement time was recorded separately. These are not interchangeable. The API and template are not assigned an invented precedence. Clear this discrepancy only with Kalshi's applicable administrative scheduling rule or corrected metadata; until then the engine makes no prediction of administrative expiration or payout time. Normal observation timing is independently specified in market primary text and numerically corroborated.

**Trading tick discrepancy, outside settlement rounding:** certification Appendix A uses $0.01; currently linked T uses $0.001; M advertises a tapered price grid. None determines how a half-cent settlement mean rounds. Clearing any trading-tick conflict needs an applicable exchange rule/version or corrected grid. This read-only settlement profile does not decide that hierarchy or place orders.

C also describes underlying-access instructions as “non-binding” and “for convenience only.” That prevents treating the feed helper as a contract amendment. No precedence between conflicting settlement rules has been invented.

**Operational blockers:** missing target clears on authoritative populated metadata; missing observations clear only with causal official observations; stale references/metadata or unavailable volatility clear on fresh valid inputs for that provider; changed documents/rules clear through reviewed versioned compatibility updates. Local loss of observations never establishes the official outage contingency. None of these requires halting unrelated model development.

## Exact changes and validation

- Added `src/btc_probability/contract_profile.py`: reviewed template, source pins, compatibility checks and granular unresolved evidence.
- Replaced permissive discovery in `contracts.py`; extended `schema.py` with provenance, distinct timestamps and readiness states.
- Added public-document capture to `adapters.py`; `live.py` retains both aggregate fields, continues repairs without requiring parser verification, and records numeric outcome reconciliations.
- Corrected `reference.py:crosscheck` window identity and availability checks; existing exact-slot accumulation and Monte Carlo averaging code remain intact.
- Added `reconciliation.py` with Decimal reconstruction, distinct provider comparisons, tie ambiguity, final-value comparison and causal CLI replay.
- Updated `engine.py` and dashboard diagnostic display to keep research separate from guarded primary probability; raw available model estimates are explicitly uncalibrated.
- Added captured evidence and `test_settlement_verification.py`; updated existing manual-review and aggregate tests to reject the unsafe old assumptions. Documentation now links this audit and removes obsolete override instructions.

Executed after the implementation and second-window tests:

```text
.venv/bin/ruff check src/btc_probability tests/probability
All checks passed!
.venv/bin/pytest tests/probability tests/test_domain_model.py -q
89 passed, 2 deprecation warnings in 4.57s
```

The warnings concern Starlette/httpx and AnyIO compatibility, not settlement assertions. Earlier in this audit the 60-test probability suite passed; subsequent added tests and legacy domain checks produced the 89-test result above. Legacy domain tests verify legacy behavior, including its old schedule, and are not evidence that schedule is authoritative. The entire unrelated trading test suite was not rerun for this focused change. No forecast accuracy, broad outage coverage, or unobserved live window is claimed.


## Runtime check

The BTC-only read-only recorder received two brief controlled restarts to load the tested profile and final publication-state labels; the dashboard stayed running. It resumed appending to the same recording, captured both matching PDFs (`rule_documents` event 7107), continued BRTI observations, and emitted a successful 07:00 numeric reconciliation (event 7935). Original forecasts were not rewritten. At the 07:07 UTC check the official open-market list was empty, so no new live forecast was claimed; forecast ticks and reference recording continued. Research/readiness output is validated by the tests above, not represented as a new live forecast during that interval.

## Subsequent user-authorized research assumption

The user subsequently requested rounding exact half-cent means up for a YES model lean and down for a NO lean. `user-model-lean-v1` implements this only in a separate assumption-based settlement research result. Lean is the unrounded averaging simulation's YES frequency, frozen before rounding; exactly 50/50 produces no directional result. Ordinary non-tie rounding is unchanged. The official guard, document checks, unresolved tie blocker and published-value reconciliation are unchanged. See [model documentation](docs/probability/MODEL.md#user-selected-model-lean-tie-assumption). This user-defined convention does not answer the Kalshi clarification question above.
