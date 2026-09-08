# Executed validation — 2026-09-08

- `pytest -q -o faulthandler_timeout=30`: **56 passed in 15.49 seconds**.
  Coverage includes captured contract parsing, missing strikes, comparator/ties,
  all four order directions, fractional book levels, piecewise ticks, causal seeded
  probability, fixed observed samples, indicator warmup, stale data, entry boundaries,
  duplicate/partial orders, passive volume and queues, cancellation, aggressive IOC,
  depth/latency-limited hard stops, risk limits, fee carry, immutable history, state
  transitions, writer exclusion, raw journal/Parquet roundtrip, complete replay and
  export, dashboard APIs, mode isolation, event fee gating, invalid config, API
  signing/pagination/sharding, walk-forward frozen holdout and insufficient data.
- `ruff check src tests`: passed. `ruff format --check src tests`: 20 files formatted.
  Python compileall, JavaScript syntax check and Git whitespace checks passed.
- Live **read-only public** `btc15 discover`: succeeded against the production API.
  Verified active market parsing and pagination. Unopened strikes were not available;
  these are retained as pending/blocked, not inferred from a reference price.
- PostgreSQL 17 in a temporary localhost Docker container: schema creation, writes,
  database-enforced immutability, state transitions, unique entry claims and writer
  lease acquire/release passed. The main automated suite uses SQLite temporary
  databases; PostgreSQL did not receive the entire replay suite.
- Chromium/Playwright: desktop and mobile layout, PAPER/BACKTEST switching,
  opportunity table, completed-trades table, replay navigation/reference and
  probability charts, analytics charts, no horizontal overflow at 390px, and no
  JavaScript errors passed. Screenshots are local ignored artifacts in
  `data/dashboard-desktop.png` and `data/dashboard-mobile.png`.
- Offline synthetic CLI flow produced an entry, fractional partial fills and an
  official-result-shaped settlement event. This proves an accounting path, not
  actual exchange access, calibration, favorable execution, or economic edge.

## Environment limitations

Authenticated Kalshi WebSocket, historical CF entitlements, real paper sessions,
production orders and live reconciliation were **not tested**. No account secrets
were supplied or created. Temporary test services are stopped after validation.

Two third-party deprecation warnings remain in FastAPI/Starlette's test client
(httpx compatibility and an AnyIO alias). They did not fail tests. The sandboxed
FastAPI TestClient run stalled; the same suite passed when local networking was
available. Browser validation used existing Chromium with missing shared libraries
unpacked into /tmp; no system packages were installed. These are development
environment details, not exchange readiness evidence.
