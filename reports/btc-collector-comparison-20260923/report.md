# BTC collector comparison — 2026-09-23 UTC

Read-only investigation; no service restart, strategy change, or feed modification.

## Equal-window input counts: 02:00–02:09 UTC

| Asset | Inputs | Mean per second | Peak one-second count | Order-book deltas |
|---|---:|---:|---:|---:|
| BTC | 404,793 | 749.6 | 2,325 | 367,436 |
| ETH | 122,454 | 226.8 | 2,115 | 106,209 |
| SOL | 58,873 | 109.0 | 1,963 | 44,424 |
| XRP | 76,606 | 141.9 | 1,282 | 60,332 |

Counts include websocket frames and locally emitted input events, from completed compressed collector archives. All four received the same 540 one-Hz and 2,700 five-Hz reference frames and 7,136 lifecycle messages in the interval. BTC's extra volume is overwhelmingly its own active contract's book updates and trades, not an accidental subscription to the other assets. 367,320 of BTC's 367,436 deltas were for the active contract; 116 were the outgoing contract.

BTC averaged 3.3x ETH, 6.9x SOL, and 5.3x XRP. Similar isolated peaks in other assets do not imply similar sustained load. Archive gaps can affect absolute counts; this is observed recorded traffic.

## Recovery evidence: 02:00–02:15 UTC

Recovery-event maximum lag / outstanding events: BTC 2.4664 seconds / 5,064; ETH 0.5629 / 1,560; SOL 0.6377 / 1,672; XRP 0.7269 / 1,684. BTC had repeated backlog and freshness recovery states. Other collectors are not entirely exempt: each also recorded a backlog state, with SOL/XRP additionally waiting for fresh metadata. Event maxima are not continuous peak measurements.

## Live sampling and profile

40 health samples across 80.47 seconds: BTC 62.6% of one CPU core, ETH 26.6%, SOL 27.7%, XRP 35.3%. BTC max lag 0.379 seconds and queue 437, versus ETH 0.032 / 4, SOL 0.081 / 12, XRP 0.064 / 3. No BTC health blocks occurred in this later, quieter sample. SOL/XRP blocks were metadata freshness, not processing backlog. No new archive drops in these four collectors during the sample.

20-second BTC py-spy GIL profile: 678 samples, no sampler errors. 370 samples were in process_batch; JSON encoder/decoder appeared in 93 samples (13.7%). Book maximum-price scans, engine processing, repeated recorder summary checks, and input serialization are visible costs. Categories overlap; sampled GIL time is not total CPU time, and the profile did not capture the earlier worst backlog.

BTC and ETH loaded identical runner/engine/recorder/domain source hashes. SOL/XRP loaded different older runner/engine versions; their recorder/domain match BTC. Therefore an older BTC-only code version is not the explanation relative to ETH.

The loaded runner feeds a single ordered processing pipeline. Each input incurs ingestion, book validation/quote lookups, and recording work. Full-book max() scans occur in validation, signal-input construction, and recorder bid checks. These costs amplify with BTC's sustained traffic; the six host cores do not automatically parallelize this ordered Python processing.

Host had ~4.2 GiB available RAM and 189 GiB free storage; no evidence of RAM/disk capacity exhaustion. Load averages near 5 on six CPUs do not rule out CPU contention. Precise shares of burst latency from scheduling, book work, and logging need incident-aligned profiling.

## Conclusion and next targeted work

Primary supported explanation: BTC's much heavier sustained book traffic leaves less processing headroom in the same collector pipeline, so bursts trigger backlog and data-freshness guards. Market price movement itself was not measured and is not the diagnosis.

Priorities: eliminate repeated full-book best-price scans with validated cached best quotes; reuse decoded payloads across ingestion/recovery/recording rather than repeated serialization/parsing; reduce redundant summary work while retaining ordered book deltas and threshold-crossing capture. Benchmark incident replay with sequence/book/decision equivalence before deploying. Do not solve this by merely relaxing freshness gates or dropping book deltas.
