# Running the tests

Run from the project root with the development dependencies installed:

```bash
.venv/bin/python -m pytest -q
```

For a focused logging check:

```bash
.venv/bin/python -m pytest -q tests/test_research*.py tests/test_live_research_capture.py
```

The full suite includes model, execution, replay, collector, and dashboard regressions.
Use the focused command while editing logging; run the full suite before deployment.
These commands do not start the bot or deploy anything. `conftest.py` isolates settings
and clears exchange credentials. The storage fixture uses temporary SQLite databases
unless `BTC15_TEST_DATABASE_URL` explicitly selects a test PostgreSQL database.

Shared setup lives in `conftest.py`: the asyncio backend, temporary stores, market
fixtures, Node.js lookup, and the research-recorder factory. Recorder tests still close
writers before inspecting finalized files; fixture teardown also closes them on failure.
`research_helpers.py` reads finalized segments in deterministic file order.

Install Node.js to run all three JavaScript checks. Without it, those cases explicitly
skip; the rest of the suite still runs. A skipped browser check is not a passing check.

Keep behavioral cases distinct (reconnects, gaps, deadlines, duplicate fills, causal
history, and settlement recovery). Share repeated setup rather than deleting cases or
adding expected-failure markers. Active-preset policy assertions live together in
`test_strategy_settings.py`; feature tests should isolate their relevant policy switches.
