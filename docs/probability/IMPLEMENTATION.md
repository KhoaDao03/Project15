# Implementation status — 2026-09-17

> **2026-09-17 audit update:** See [settlement verification](../../SETTLEMENT_VERIFICATION.md). The close-inclusive final-minute feed helper does not match the two captured published settlement values. The probability engine now uses the reviewed preceding-60-second profile, retains the half-cent tie blocker, and records live BRTI data. Historical statements below describe earlier behavior; legacy trading code was not changed by this focused audit.

## Completed

The isolated `btc_probability` package and `btc-probability` command provide typed contract/reference/forecast records, a pure Black digital diagnostic, correlated settlement-average Monte Carlo, exact deterministic settlement, three volatility providers, read-only Kalshi/Deribit adapters, strict settlement accumulation, snapshot books and tested delta invalidation, durable event capture, lineage, restart/replay, probability evaluation, and a GET-only browser chart.

The default recorder retains all three baseline providers for explicit dashboard comparison. Its configured primary provider defaults to options. No unavailable model is silently replaced by another. The existing `btc15` command, configurations, ledgers and services were not modified or enabled. The only packaging changes add a new script and package; existing locked dependencies remain unchanged.

Documentation: [run commands](README.md), [model](MODEL.md), [data contracts](DATA_CONTRACTS.md), [sources](../assumptions_and_sources.md). A small **synthetic** lineage-preserving recording is bundled at `examples/probability/sample.sqlite` (first minute of generated inputs, not a complete outcome study). It can be opened directly:

```bash
uv run --locked btc-probability dashboard examples/probability/sample.sqlite
uv run --locked btc-probability replay examples/probability/sample.sqlite --output /tmp/sample-replay.jsonl
```

Use a newly generated full synthetic run for evaluation; the short bundled sample intentionally contains no final outcomes.

## Validation actually performed

- New probability suite: **44 tests passed** on Python 3.12.13, including regression fixtures, numerical/decimal boundaries, missing/late/conflicting data, causality, reproducible replay, restart recovery, market rollover, book independence, IV conversion/quality, read-only routes and network allowlists. FastAPI tests required execution outside the sandbox because its event-loop thread wakeup was blocked inside it. Two dependency deprecation warnings remain.
- Scoped Ruff checks and JavaScript syntax checks passed. Wheel and source distribution built offline; the wheel includes the new static assets.
- Generated a two-contract synthetic recording with 20,000 simulation paths, replayed a bundled sample, and ran the CLI evaluation. All four comparison baselines had 12 common fixed-horizon forecasts in the two-contract run; both exact synthetic settlement results matched the generated outcomes. This is software evidence, not market performance.
- One 20,000-path, 60-future-sample model call took approximately **0.057 seconds** locally. The observed Wilson interval was approximately [0.4977, 0.5116], satisfying the configured 0.01 half-width target for that call. This is not an end-to-end latency or precision guarantee.
- Public live recorder smoke test ran for five seconds with blank probability credentials. Kalshi metadata/books and Deribit public reads returned HTTP 200. Its 15 forecast records were all correctly UNAVAILABLE for missing official reference access and unresolved rule semantics. Active-market and book responses were added to fixtures. A short smoke does not establish a complete usable option snapshot or authenticated streaming integration.
- A temporary localhost dashboard returned HTTP 200 and was rendered in Chromium headless shell. Verified the synthetic/read-only banner, actual computed explanation, four colored chart lines and two axes. The temporary browser and dashboard were stopped afterward.
- Broader repository test run exceeded a five-minute limit after reaching over 88% and showing failures. The first legacy failure (`test_historical_bleep_only_floor`) reproduces in a fresh archive of unchanged HEAD: expected 0.85 versus preset 0.0. The full suite is **not reported as passing**, nor are all observed failures individually attributed. Repository-wide lint also reports two existing import-order failures in `test_bleep85_only_active.py` and `test_hard_stop_only.py`; scoped new-package lint passes. Those unrelated files were left untouched.

## Unverified live requirements

Live probability needs a genuinely reviewed binding of target authority, sample timestamps, observation end and rounding-tie behavior, plus entitled and fresh BRTI data. These are not bundled as invented verified facts. The adapters exist but authenticated streaming, historical restoration against a real entitled account, prolonged rollover/reconnect behavior, and live numerical throughput remain unverified. REST book access worked publicly in this environment despite the documentation's authentication requirement; other deployments may receive authorization errors and retain null comparisons.

## Deferred optional research

Fitted probability calibration, hybrid weights, momentum/drift estimation, arbitrage-constrained smile derivatives, local/stochastic volatility and jump models are not implemented or claimed validated. Calibrated probabilities remain null. Physical forecast skill, pricing relevance, and robustness to option-maturity extrapolation require chronologically separated real outcomes. No trade execution or profitability experiment is included.

## Subsequent BTC-only run

On 2026-09-17, the user requested a BTC-only run. Two temporary user services now run the read-only recorder and localhost dashboard on port 8015. An initial WebSocket HTTP 404 exposed an incorrect hostname; corrected to the documented `external-api-ws.kalshi.com`. After restarting only this recorder, authenticated BRTI observations arrived successfully. Public books, metadata and options snapshots are also recorded. This establishes streaming access for this account at the time checked, but not historical recovery or endurance. Probabilities still remain UNAVAILABLE pending the verified per-contract settlement mapping. Original trading services remain disabled. Runtime details are in `data/probability/btc-live/RUNNING.md`.
