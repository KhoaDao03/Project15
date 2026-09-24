# BTC recurring collector-health block — September 24, 2026 UTC

Read-only investigation. No trading controls, thresholds, source, services or positions were changed. Evidence includes executor decision traces, collector journal, a 40-second BTC/ETH status observation, source snapshots, metadata receipts and a 15-second GIL profile.

## Confirmed causes

1. **Processing backlog.** In executor traces from 02:01:08–02:11:03 UTC, 1,733 repeated entry checks were rejected for collector health. They are checks, not 1,733 missed trades. All reported COLLECTOR_RECOVERING; 1,132 included BACKLOG_WARNING, 714 PROCESSING_LAG, and 430 STALE_REFERENCE. The maximum recorded processing lag was 3.904 seconds and queue depth 4,215. Recovery details included BACKLOG_NOT_DRAINED in 1,115 checks. Reasons overlap. The gate warns at 0.5 seconds and requires backlog drainage before allowing entries; lag below the executor's separate one-second ceiling can still block.

2. **Metadata refresh is slower than its freshness deadline.** Sixteen archived metadata inputs show 14 of 15 receipt intervals exceeding 30 seconds, typically about 38 seconds. Each discovery/metadata acquisition took approximately 1.25 seconds. The recovery gate rejects metadata older than 30 seconds. This therefore creates recurring periods of roughly eight seconds where metadata is stale, even with a drained queue. There were 575 executor rejections containing WAITING_FOR_FRESH_METADATA in the inspected ten-minute window.

   The loaded `runner.refresh()` publishes metadata, then polls tracked expired markets for settlements, then waits up to another 15 seconds before restarting. Fresh metadata and settlement polling share a serial loop. The long delay is after metadata acquisition, consistent with tail work and scheduling rather than a 38-second discovery HTTP request. Exact per-market settlement-poll costs were not captured in this audit. Schedule metadata independently of that tail work, or otherwise bound its cadence below the freshness deadline without weakening the deadline.

3. **Reference-stream recovery.** At 02:08:12 the collector requested REFERENCE_STREAM_STALLED recovery and disconnected around 02:08:14. This adds connection/reference/snapshot recovery checks. The logs establish that the accepted reference stream was stale, but do not independently establish whether upstream delivery or local processing caused the original reference gap.

## Status and recurrence

BTC collector PID 457664 has run since September 22 02:59:26; executor PID 502494 since September 23 01:59:12. Both services are active with zero automatic restarts.

During 02:11:08–02:11:47, BTC had 16 blocked samples out of 80; ETH had 3/80. All sampled blocks in this quieter period were WAITING_FOR_FRESH_METADATA. BTC median lag was 61ms, p95 266ms, max 466ms; maximum sampled queue 274. These are sampled health states, not trade-opportunity counts.

At 02:13:34 BTC was connected and READY, with no recovery reasons or warning, processing lag 474ms, queue 442 and reference age 1.21 seconds. Subsequent recorded transitions again alternated backlog/READY. Recovery is temporary; the cause is recurring.

The running engine, runner, domain and collector-recovery source snapshots match checked-in files. Thus the earlier quote-check and prompt status-clearance fixes are loaded. The recorder snapshot differs: the small change reusing decoded book/trade identity fields for coverage recording is present on disk but not loaded. That optimization alone is not established as sufficient to eliminate overload.

## Profile and resource evidence

A 15-second, 50Hz GIL-only py-spy sample collected 163 stacks with one sampling error. Overlapping categories: process_batch 87/163, research_log.py 43/163, engine.py 46/163, JSON encoding/decoding 25/163, domain.py 22/163. Best-bid scans, input serialization, research summaries and recovery checks remain visible. This sample occurred after the worst recorded burst and is not a complete attribution of incident latency.

Host had approximately 5GiB available RAM and 187GiB free disk. There is no evidence of capacity exhaustion. Earlier September 23 equal-window measurements found BTC sustained input traffic 3.3 times ETH; that is supporting historical context, not a new traffic-rate measurement for this incident.

Research OperationalError messages continue and the recorder has cumulative drops. The status samples saw one additional dropped record for BTC and ETH each. These recording problems require separate diagnosis; the evidence does not prove they caused every health block.

## Recommended next changes

1. Fix the deterministic metadata cadence mismatch: current receipts around 38 seconds versus a 30-second expiry. Keep metadata refresh from waiting behind settlement polling.
2. Benchmark and deploy the existing recorder optimization through the normal safe rollout; continue profiling best-quote scans and redundant per-input work if backlog remains.
3. Display the actual health reasons, lag and queue behind the generic dashboard message.

Do not widen freshness limits or bypass collector health. Do not assume a restart solves the metadata scheduling mismatch. A production restart was not performed during this investigation.

Evidence: `rejections.json`, `samples.json`, `metadata-timing.json`, `collector.log`, `profile.txt`, and `recorder-not-loaded.patch` in this directory.
