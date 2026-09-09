
# This file is concatenated after single_strategy_core.py by the one-time validation job.
# A single-strategy history view must also show failed attempts, not only filled entries.
p = Path('src/btc15/static/index.html'); t = p.read_text()
t = t.replace('<option value="opportunity">Evaluations</option>', '<option value="opportunity">Entry evidence / research evaluations</option><option value="order">Orders &amp; cancellations</option><option value="fill">Fills</option>')
p.write_text(t)
edit('src/btc15/static/app.js', [
    ("  const completed=$('record-kind').value==='trades';\n  if(!completed)q.set('group_by_market','true');",
     "  const kind=$('record-kind').value,completed=kind==='trades',ledger=kind==='order'||kind==='fill';\n  if(!completed&&!ledger)q.set('group_by_market','true');\n  if(ledger){q.set('kind',kind);q.delete('decision');}"),
    ("  $('decision-filter-label').hidden=completed;", "  $('decision-filter-label').hidden=completed||ledger;"),
    ("(completed?' completed trades':' markets · '+fmt(d.evaluations,0)+' evaluations')", "(completed?' completed trades':ledger?' '+kind+' records':' markets · '+fmt(d.evaluations,0)+' retained evaluations')"),
    ("completed?activityCard(r,true):marketGroup(r,q)", "completed?activityCard(r,true):ledger?ledgerCard(r):marketGroup(r,q)"),
])
with Path('src/btc15/static/app.js').open('a') as f:
    f.write('''
function ledgerCard(record){
  const b=record.body,card=text('article','','activity-card');
  const label=record.kind==='fill'?'Fill · '+(b.action||'unknown'): 'Order · '+(b.status||'recorded');
  card.append(text('h3',label),text('p',record.market+' · '+new Date(record.timestamp*1000).toLocaleString(),'muted'));
  card.append(text('p',(b.side?b.side.toUpperCase()+' · ':'')+'Quantity '+fmt(b.quantity??b.remaining)+' · '+money(b.price??b.limit)));
  if(b.reason)card.append(text('p',b.reason.replaceAll('_',' '),'reason'));
  card.append(technicalDetails(b));
  return card;
}
''')
# Benchmarks are single-engine tools, not wrappers around a one-member group.
for path in ('scripts/check_dashboard_load.py', 'scripts/check_processing_throughput.py'):
    p = Path(path)
    t = re.sub(r'\bgroup\b', 'engine', p.read_text())
    t = t.replace('models=len([engine])', 'models=1')
    t = re.sub(r'^\s*pass  # No runtime strategy activation in the single-engine bot\.\n', '', t, flags=re.M)
    p.write_text(t)
    collapse_single_loops(path)
# E731 would obscure rather than help the small recovery predicate.
edit('src/btc15/models.py', [('    key = lambda r: (r["run_id"], r["market"], r["opportunity_id"])',
                            '    def key(r):\n        return r["run_id"], r["market"], r["opportunity_id"]\n')])

write('tests/test_single_strategy.py', '''"""Single-engine scope, archive isolation, and non-destructive legacy recovery."""

import asyncio
import copy
from dataclasses import asdict, replace

import pytest
from fastapi.testclient import TestClient

from btc15 import runner
from btc15.analytics import metrics
from btc15.config import Settings, Strategy
from btc15.dashboard import create_app
from btc15.engine import Engine
from btc15.models import guard_archived_exposure, identity, require_single_run


def saved_run(store, run, *, archived=False, mode="PAPER", config=None):
    config = config or Strategy()
    model = identity(config)
    if archived:
        model = {**model, "model_id": "retired-momentum", "model_name": "Archived momentum"}
    store.add("run", dict(model=model, config=asdict(config), versions=dict(config=config.version)), run, mode, 1)
    return model


def trade_result(store, run, model, pnl, mode="PAPER"):
    store.add("trade_result", dict(model=model, net_pnl=pnl, gross_pnl=pnl, fees=0,
                                   reason="SETTLEMENT", side="yes", bought=1, cost=.5, proceeds=.5+pnl),
              run, mode, 3, "BTC", "op-"+run)


def test_only_one_disabled_strategy_card_and_no_registry_writes(store, tmp_path):
    settings = Settings(data_dir=str(tmp_path))
    with TestClient(create_app(store, settings=settings, config=replace(Strategy(), enabled=False))) as client:
        data = client.get("/api/strategies").json()
        assert len(data["rows"]) == 1
        assert data["rows"][0]["model"]["model_id"] == "settlement-edge"
        assert data["rows"][0]["entries_enabled"] is False
        assert data["rows"][0]["record"] is None
        assert client.get("/api/strategies?mode=INVALID").status_code == 422
    assert not store.list(limit=None)


def test_scope_filters_history_counts_runs_results_and_headline(store, tmp_path):
    control = saved_run(store, "control")
    retired = saved_run(store, "retired", archived=True)
    old_config = saved_run(store, "old-config", config=replace(Strategy(), min_edge=.04))
    for run, model, pnl in (("control", control, .1), ("retired", retired, 100), ("old-config", old_config, 7)):
        trade_result(store, run, model, pnl)
        store.add("order", dict(model=model, status="submitted"), run, "PAPER", 2, "BTC", "op-"+run)
    # Registry data remains, but it no longer enables an executable strategy.
    store.add("model_activation", dict(key="retired-momentum:v1", active=True), "model-registry", "PAPER", 1)
    before = copy.deepcopy(store.list(limit=None))
    with TestClient(create_app(store, settings=Settings(data_dir=str(tmp_path)))) as client:
        data = client.get("/api/strategies").json()
        assert len(data["rows"]) == 1
        assert data["lifetime"]["net_pnl"] == .1
        assert [r["run_id"] for r in data["rows"][0]["runs"]] == ["control"]
        assert {r["run_id"] for r in client.get("/api/runs").json()} == {"control", "old-config"}
        assert [r["run_id"] for r in client.get("/api/runs?scope=archive").json()] == ["retired"]
        assert client.get("/api/records?kind=order&limit=1").json()["total"] == 2
        archive = client.get("/api/records?kind=order&scope=archive").json()
        assert archive["total"] == 1 and archive["rows"][0]["run_id"] == "retired"
        assert client.get("/api/trades").json()["total"] == 2
        assert client.get("/api/trades?scope=archive").json()["total"] == 1
        assert client.get("/api/trades?run_id=retired").json()["total"] == 0
        assert client.get("/api/analytics?scope=archive&run_id=retired").json()["net_pnl"] == 100
        assert client.get("/api/analytics?run_id=control").json()["net_pnl"] == .1
        assert client.get("/api/records?scope=bogus").status_code == 422
        selected = client.get("/api/strategies?run_id=old-config").json()
        assert selected["rows"][0]["model"]["config_hash"] == old_config["config_hash"]
        assert selected["lifetime"]["net_pnl"] == 7
    assert store.list(limit=None) == before


def test_legacy_records_without_embedded_identity_use_the_run_identity(store):
    saved_run(store, "retired", archived=True)
    store.add("trade_result", dict(net_pnl=50, gross_pnl=50, fees=0, side="yes", reason="SETTLEMENT"),
              "retired", "PAPER", 2, "BTC", "op")
    assert metrics(store)["trades"] == 0
    assert metrics(store, run_id="retired", scope="archive")["net_pnl"] == 50


def test_modes_and_selected_run_do_not_mix(store, tmp_path):
    paper = saved_run(store, "paper")
    backtest = saved_run(store, "backtest", mode="BACKTEST")
    trade_result(store, "paper", paper, .2)
    trade_result(store, "backtest", backtest, 10, "BACKTEST")
    with TestClient(create_app(store, settings=Settings(data_dir=str(tmp_path)))) as client:
        assert client.get("/api/strategies").json()["lifetime"]["net_pnl"] == .2
        assert client.get("/api/strategies?mode=BACKTEST").json()["lifetime"]["net_pnl"] == 10
        assert client.get("/api/strategies?mode=LIVE").json()["lifetime"]["completed_trades"] == 0


def test_archived_evaluation_is_not_a_live_strategy(store, tmp_path):
    model = saved_run(store, "retired", archived=True)
    store.publish_record("evaluation", dict(model=model, decision="NO_TRADE"), "retired", "PAPER", 2)
    with TestClient(create_app(store, settings=Settings(data_dir=str(tmp_path)))) as client:
        assert client.get("/api/evaluation?run_id=retired").json()["record"] is None
        assert client.get("/api/strategies").json()["rows"][0]["record"] is None


def test_group_resume_fails_before_writing_or_dropping_children(store, config):
    store.add("model_group", dict(members=[dict(key="retired:v1", config={})]), "legacy", "PAPER", 1)
    before = copy.deepcopy(store.list(limit=None))
    with pytest.raises(ValueError, match="retired multi-strategy portfolio"):
        Engine(store, config, run_id="legacy", resume=True)
    assert store.list(limit=None) == before
    assert store.load_checkpoint("legacy") is None


def test_empty_legacy_group_and_flat_control_resume_preserve_identity(store, config):
    engine = Engine(store, config, run_id="control")
    store.checkpoint("control", engine.executor.snapshot())
    store.add("model_group", dict(members=[]), "control", "PAPER", 1)
    resumed = Engine(store, config, run_id="control", resume=True)
    assert resumed.run_id == "control"
    assert resumed.executor.snapshot()["config_version"] == config.version
    assert len(store.list(kind="run", run_id="control")) == 1
    assert len(store.list(kind="resume", run_id="control")) == 1
    assert store.list(kind="model_group", run_id="control")[0]["body"] == {"members": []}


def test_archived_run_id_cannot_be_resumed_as_control(store):
    saved_run(store, "retired", archived=True)
    with pytest.raises(ValueError, match="retired multi-strategy"):
        require_single_run(store, "retired")


@pytest.mark.parametrize("evidence", ["unresolved_fill", "checkpoint_position", "pending_order"])
def test_archived_exposure_blocks_new_paper_start_without_mutation(store, evidence):
    model = saved_run(store, "retired", archived=True)
    if evidence == "unresolved_fill":
        store.add("fill", dict(model=model, action="buy", quantity=1), "retired", "PAPER", 2, "BTC", "op")
    elif evidence == "checkpoint_position":
        store.checkpoint("retired", dict(positions={"BTC": {"quantity": 1}}, orders={}))
    else:
        store.checkpoint("retired", dict(positions={}, orders={"BTC": {"active": True}}))
    before = copy.deepcopy(store.list(limit=None))
    checkpoint = store.load_checkpoint("retired")
    with pytest.raises(ValueError, match="Unresolved archived paper exposure: retired"):
        guard_archived_exposure(store)
    assert store.list(limit=None) == before
    assert store.load_checkpoint("retired") == checkpoint


def test_resolved_archive_does_not_block_new_control(store):
    model = saved_run(store, "retired", archived=True)
    store.add("fill", dict(model=model, action="buy", quantity=1), "retired", "PAPER", 2, "BTC", "op")
    # Same opportunity ID in a different run must NOT resolve this inventory.
    store.add("trade_result", {}, "different", "PAPER", 3, "BTC", "op")
    with pytest.raises(ValueError, match="Unresolved archived"):
        guard_archived_exposure(store)
    store.add("trade_result", {}, "retired", "PAPER", 3, "BTC", "op")
    store.checkpoint("retired", dict(positions={}, orders={"BTC": {"active": False}}))
    guard_archived_exposure(store)


def test_runner_checks_legacy_before_creating_a_replacement_run(store, config, tmp_path, monkeypatch):
    from test_collection import fake_client

    fake_client(monkeypatch, {}, {})
    model = saved_run(store, "retired", archived=True)
    store.add("fill", dict(model=model, action="buy", quantity=1), "retired", "PAPER", 2, "BTC", "op")
    with pytest.raises(ValueError, match="Unresolved archived"):
        asyncio.run(runner.collect(Settings(data_dir=str(tmp_path)), config, store,
                                   paper=True, managed_run="new-control", duration=.1))
    assert not store.list(kind="run", run_id="new-control")
    assert store.writer_owner() is None


def test_retired_cli_commands_are_explicitly_unavailable(monkeypatch):
    import sys
    from btc15 import cli

    for command in ("models", "model-paper", "model-backtest", "model-comparison"):
        monkeypatch.setattr(sys, "argv", ["btc15", command])
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 2


def test_ui_has_single_strategy_settings_and_explicit_archive(store, tmp_path):
    with TestClient(create_app(store, settings=Settings(data_dir=str(tmp_path)))) as client:
        html = client.get("/").text
        javascript = client.get("/static/app.js").text
        assert 'id="history-scope"' in html and 'value="archive"' in html
        assert 'id="strategy-library"' not in html
        assert '>Settings</button>' in html
        assert 'value="order"' in html and 'value="fill"' in html
        assert "model-paper" not in javascript
        assert "all recorded strategies" not in javascript
''')

write('docs/SINGLE_STRATEGY.md', '''# Settlement Edge-only scope and recovery

## Implementation review (before changes)

Approved scope: remove Conservative Confirmed Momentum and Volatility-Regime
Momentum from active execution and the normal dashboard, not their historical
records. Baseline: `b4f291105b2f1bbb8f025b82f922abe9b22084f4`, including the
fractional passive-fill fix and locked validation workflow.

The dependency review traced the CLI, dashboard, runner, Engine, PaperExecutor,
registry/identity helpers, analytics, replay, benchmarks and shared safety tests.
ModelGroup restored children from an immutable manifest. Deleting its algorithms
without a resume guard could strand old positions. The dashboard also summed all
strategies: hiding cards alone would leave misleading headline results.

High-priority preservation: no threshold changes, no data reset, no schema migration,
no new dependency, no real-money orders, and no silent conversion of group checkpoints.
Medium-priority changes: retire registry/dispatch, scope read APIs before counting,
and keep the active configuration visible even when entries are disabled.

## Current architecture

One collector -> one Settlement Edge Engine -> shared PaperExecutor -> SQLite or
PostgreSQL ledger/checkpoints + compact raw input tape -> dashboard. The former
`models.py` is now only identity, historical classification and recovery guards;
it never imports or instantiates a retired algorithm. The original and moderate
configurations, probability model, fees and limits are unchanged.

`btc15 dashboard` starts/resumes one named PAPER run (default `dashboard-paper`).
`--observe-only` records/evaluates without simulated orders. `--no-collect` displays
an external writer. `btc15 paper`, `paper-service`, `collect`, `backtest`,
`walk-forward`, `audit`, `export`, `analytics` and safe shutdown remain available.
The `models`, `model-paper`, `model-backtest`, and `model-comparison` commands are
removed deliberately. They fail argument parsing rather than silently doing less.

## Existing installations: retire before switching

This commit does not inspect, stop, migrate, or modify the laptop's running bot.
Back up its database (including a consistent SQLite backup, not an arbitrary copy
of an active WAL file), raw tapes, frozen settings and source revision first.
Use the compatible baseline above to stop new momentum entries, cancel unfilled
orders, and let any filled inventory settle. Verify orders, fills, results and
checkpoints; a zero completed-trade counter is not proof of zero exposure.

The new runner rejects pending/open archived exposure and refuses to resume a
manifest containing retired children, even if those models were disabled later.
The error identifies the run and compatible recovery revision. Do not delete
leases, manifests, orders or checkpoints to bypass it. No automatic liquidation,
background cleanup, portfolio migration, or historical rewrite is implemented.

After legacy exposure is resolved and the writer has stopped cleanly, start a
**new** Settlement Edge run ID with the deliberately chosen frozen configuration.
The previous multi-strategy root is not reinterpreted as a single-strategy run.
Single-strategy checkpoints and old group manifests with no children can resume
under their original run ID/configuration. Existing control daily-risk history
still carries forward; a new run is not a risk-budget reset.

The preset is not changed by this refactor. For the moderate preset, an example is:

```bash
uv run btc15 --config config/settlement-edge-paper-moderate.json dashboard --run-id settlement-paper --port 8001
```

Repeat that exact configuration/run selection to resume. For an original-control
run, keep its original frozen config file instead. Saved UI settings apply only
to new sessions; they do not modify a running strategy/checkpoint.

## History and UI/API changes

The default UI shows one strategy card and Settings, not a strategy library.
Headline results cover its selected configuration (or explicit run), excluding
retired strategies. Trade history distinguishes orders/cancellations, fills,
completed trades, and entry evidence. Compact paper mode stores first-fill evidence,
not every rejected evaluation; empty evidence history does not imply no evaluation.

The History selector exposes **Archived strategies (read-only)** separately.
`/api/runs`, `/api/records`, `/api/trades`, and `/api/analytics` accept
`scope=settlement` (default) or `scope=archive`. Filtering occurs before counts,
groups and pagination. `/api/replay/{id}` still reads retained records by ID,
including archived evidence. No historical row is deleted. Legacy control records
without explicit identity remain controls; child run IDs without identity are
classified as archived. Scope never activates an algorithm or changes execution.

`/api/strategies` remains as a compatibility route returning exactly one Settlement
Edge card; it is not an activation/registration endpoint. `run_id` selects a
historical control configuration. `/api/strategy` retains validated new-session
settings writes. Security/origin checks and the live-trading block are unchanged.

```bash
uv run btc15 analytics --mode PAPER --run RETIRED_RUN_ID --archive
```

All analytics remain limited to retained evidence. Full counterfactual or rejection
analysis needs the complete raw tape and original configuration; actual historical
fills are not replaced by a new replay. Backtest totals are not a live portfolio.
Old momentum guides/benchmarks are marked historical, not executable instructions.

## Verification plan

Preserve the original model/config/rules and preset file hashes. Run the same
synthetic tape in the same locked environment before/after: compare probabilities,
features, reasons, decisions, orders, fills, fees, transitions, results and remaining
risk for original/moderate configurations. Run the full offline suite including
fractional-fill regression tests, new startup/resume/archive guards, lifecycle,
rollback, compact recording and API tests. Check lint, changed-file formatting,
Python/JS syntax and package builds. `scripts/verify_settlement_behavior.py` records
or compares that synthetic behavior without network or production data access.

Authentic overnight tapes, host performance and active laptop positions are not
available to this implementation session. A synthetic parity result is not proof
of market eligibility, live execution quality, or profitability. Unrelated audit
issues are intentionally not bundled into this scope change.
''')

write('README.md', '''# Kalshi15 — BTC15 Settlement Edge

Settlement-aware research and simulated trading for **Kalshi BTC15 only**.
One executable strategy: **BTC15 Settlement Edge**. This is a research build,
not a validated profitability claim. Real-money order submission remains blocked.

## Start

Requires Python 3.12+ and uv. No Node build or new frontend framework is required.

```bash
uv sync --extra dev --locked
cp .env.example .env
uv run btc15 init-db
uv run btc15 dashboard
```

Open `http://127.0.0.1:8000`. With configured Kalshi read/feed credentials, the
dashboard starts or resumes the named `dashboard-paper` PAPER run. Orders/fills
are simulated. `--run-id NAME` selects another run; `--observe-only` explicitly
collects without orders; `--no-collect` displays a separately running writer.
The last two flags are mutually exclusive. Missing credentials show a setup error,
not fabricated prices or trades. Paper startup checks a 10 GiB free-space reserve.

**Upgrading from the multi-strategy version:** read
[scope and non-destructive recovery](docs/SINGLE_STRATEGY.md) first. Old runs with
momentum children cannot silently resume as controls. Resolve their pending orders
and positions using the compatible revision, then select a new single-strategy
run ID. Do not reset data or clear a crash lease to bypass recovery.

## Four focused views

**Live overview** shows current BRTI/contract prices, one Settlement Edge card,
its configuration hash/run, execution/entry state, reasons, positions and risk.
The headline results exclude retired strategies and follow the selected control
configuration or run. Disabled entries remain visible; hiding a card is not a stop.

**Trade history** distinguishes entry evidence, orders/cancellations, fills and
completed trades. **Results & accuracy** shows retained-evidence calibration and
realized P&L. **Settings** saves validated parameters for new sessions. History
stays still while reading; use Refresh to load newer records.

The History selector has an explicit **Archived strategies (read-only)** view.
Existing records, IDs and replay explanations remain available without loading or
running retired strategy code. No startup/reset/cleanup deletes historical records.

## Configurations: one strategy, one active configuration

The original control defaults remain `120 < seconds_remaining <= 480`, minimum
ask $0.85, conservative probability >= 0.90, quality >= 85, and net edge/EV >= $0.03
per contract after estimated costs. Spread, liquidity, metadata, freshness, regime
and risk checks must also pass. These are research assumptions, not optimized values.

The existing moderate preset changes only entry-window start to 600 seconds,
minimum ask to $0.80, and both minimum net edge/EV to $0.02. No preset or setting
is silently selected by this scope change. Use a frozen config deliberately:

```bash
uv run btc15 --config config/settlement-edge-paper-moderate.json dashboard --run-id settlement-paper --port 8001
```

`--config` overrides `DATA_DIR/strategy.json`; absent both, built-in defaults apply.
Saving settings does not change an active run. Resume with its original config.
Configuration hashes, version identities and raw sources remain attached to evidence.
See [strategy semantics and presets](docs/STRATEGY.md).

## Data and execution

Paper mode keeps one durable compressed `data/raw/<session>.jsonl.gz` tape plus
orders, cancellations, first-fill entry evidence, fills, fees, settlement/results
and checkpoints. Skipped evaluations and periodic status are replaceable live
snapshots. An empty opportunity-history view does not mean no checks or orders.
`btc15 collect` and `dashboard --observe-only` retain full research evaluations
and JSONL/Parquet recordings. Read [recording limitations](docs/TRADE_RECORDING.md).

The shared engine uses the standard BRTI settlement samples; the independent 5 Hz
reference display is not a replacement for them. It validates the contract's
close time, comparison/rounding and price grid. UI prices do not authorize orders.
Passive fills require eligible opposing volume, latency and conservative queue
accounting; quote touches do not guarantee fills. The fractional-quantity fix is
retained. One attempt per market/run and daily attempt/exposure limits still apply.

Only one writer may own a database. SQLite WAL is the local default; PostgreSQL
support and the existing Compose/service configuration remain available.

```bash
uv run btc15 paper
# In a separate terminal, using the same database:
uv run btc15 dashboard --no-collect
```

Never commit `.env` or private keys. Public `btc15 discover` needs no credentials;
authenticated reference/order-book collection requires an appropriate RSA API key
configured locally. No real order-submission endpoint is enabled.

## Shutdown and recovery

The dashboard's **Shut down safely** stops entries, cancels unfilled remainders,
drains/flushes data, and saves positions before exiting. It does not liquidate
positions or delete evidence. Restart the same compatible run/config to resume.
A crash lease requires operator review; no automatic lease stealing is introduced.
`btc15 halt` writes the persistent HALT switch; it is not a guaranteed liquidation.
See [paper lifecycle](docs/PAPER_TRADING.md), [safety](docs/SAFETY.md), and the
[managed single-strategy service](docs/AUTONOMOUS_PAPER.md).

## Offline verification and research

```bash
uv run btc15 demo --output data/synthetic.jsonl
uv run btc15 --database sqlite:///data/demo.db backtest data/synthetic.jsonl
uv run btc15 --database sqlite:///data/demo.db dashboard --no-collect
uv run pytest -q
uv run ruff check src tests
uv build
```

The demo is **synthetic**, not historical returns. Choose BACKTEST for its results.
Do not mix repeated backtests or different configurations into a portfolio claim.

```bash
uv run btc15 audit data/raw/SESSION.jsonl.gz
uv run btc15 --config config/original.json backtest data/raw/SESSION.jsonl.gz
uv run btc15 analytics --mode BACKTEST --run RUN_ID
uv run btc15 export OPPORTUNITY_ID
uv run btc15 walk-forward config/walk-forward.example.json
```

Replace placeholders with original complete inputs/configs. Archived strategy
results can be read with `analytics --archive --run RUN_ID`, but archived algorithms
cannot execute in this version. Counterfactuals belong in new runs, never rewrites
of recorded predictions or fills. Authentic overnight validation remains separate
from unit tests, public REST discovery and synthetic end-to-end replay.

The retired commands `models`, `model-paper`, `model-backtest`, and `model-comparison`
are deliberately removed. See [SINGLE_STRATEGY.md](docs/SINGLE_STRATEGY.md) for the
API history scope, known compatible legacy revision, and migration safeguards.
''')

# Clearly distinguish historical documentation from current runtime instructions.
for path in ('docs/MOMENTUM_MODELS.md', 'docs/MOMENTUM_READINESS.md'):
    p = Path(path)
    p.write_text('> **Historical archive — not executable in the current single-strategy bot.**\n'
                 '> For legacy recovery and the compatible revision, see [SINGLE_STRATEGY.md](SINGLE_STRATEGY.md).\n\n' + p.read_text())
for path in ('docs/ARCHITECTURE.md', 'docs/TRADE_MEMORY.md', 'docs/PROCESSING_RELIABILITY.md', 'docs/VALIDATION.md'):
    p = Path(path)
    p.write_text('> **Scope update:** current runtime is Settlement Edge only. Multi-strategy descriptions\n'
                 '> and measurements below describe historical revisions, not current execution.\n'
                 '> See [SINGLE_STRATEGY.md](SINGLE_STRATEGY.md) for current architecture, history and recovery.\n\n' + p.read_text())
p = Path('docs/PAPER_TRADING.md'); t = p.read_text()
t = t.replace('Dashboard startup defaults\nto paper execution for Settlement Edge and all active momentum strategies.',
              'Dashboard startup defaults\nto paper execution for Settlement Edge only.')
a = t.index('Open paper positions are retained without forced liquidation.')
t = t[:a] + '''Open paper positions are retained without forced liquidation. Restart the same
single-strategy run with its original configuration to resume management/settlement.
The shutdown request affects the collector sharing this database, not the selected
historical run or archive filter. Live execution remains blocked.

Dashboard startup starts/resumes `dashboard-paper` using one Settlement Edge engine.
`--run-id NAME` selects a different run. `--observe-only` records without orders;
`--no-collect` is a viewing dashboard. Warmup, signal, risk, freshness, credentials,
disk-space and writer-lease checks still apply. Old multi-strategy manifests and
pending archived exposure are rejected with a recovery message, not silently dropped.
Read [SINGLE_STRATEGY.md](SINGLE_STRATEGY.md) before upgrading an existing installation.

Paper sessions use [trade evidence plus compact inputs](TRADE_RECORDING.md). No
historical records or raw tapes are removed by this scope change.
'''
p.write_text(t)
p = Path('docs/STRATEGY.md'); t = p.read_text()
t = t.replace('The implemented algorithm is **BTC15 Settlement Edge**', 'The only executable algorithm is **BTC15 Settlement Edge**')
t = t.replace('The folder layout gives future algorithms a clear location;\nadding one still requires engine integration, UI support and validation.',
              'Retired algorithms are preserved by Git history and cannot be activated in this version.')
t = t.replace('## Edit settings in the dashboard', '## Single-strategy scope\n\nSee [SINGLE_STRATEGY.md](SINGLE_STRATEGY.md) for legacy recovery and read-only archives.\nOriginal and moderate settings below are unchanged; the chosen run configuration remains frozen.\nThe local validation and multi-strategy group notes describe the earlier implementation.\n\n## Edit settings in the dashboard')
p.write_text(t)
p = Path('docs/TRADE_RECORDING.md'); t = p.read_text()
t = t.replace('Paper execution (dashboard, paper, paper-service and model-paper)', 'Paper execution (dashboard, paper and paper-service)')
t = t.replace("one compressed input tape shared by the session's models", 'one compressed input tape for its Settlement Edge engine')
t = t.replace("Every model's `raw_source` record points to the same tape for that collection session.", 'The run\'s `raw_source` record points to that collection session\'s tape.')
t = t.replace('## Validation', '## Historical multi-strategy validation\n\nThe measurements below predate the single-strategy scope change. They are not new\nperformance measurements of this version. See [SINGLE_STRATEGY.md](SINGLE_STRATEGY.md).\n\n## Validation')
p.write_text(t)

# Final static inventory: executable/runtime imports cannot resurrect retired algorithms.
for directory in ('src', 'scripts', 'tests'):
    for p in Path(directory).rglob('*.py'):
        text = p.read_text()
        ast.parse(text, filename=str(p))
        if 'from btc15.strategies.momentum import' in text or 'from .strategies.momentum import' in text or 'ModelGroup' in text:
            raise AssertionError(f'Retired runtime dependency remains: {p}')
for path, digest in before.items():
    assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest, path
print('SINGLE_STRATEGY_APPLY_COMPLETE: source/presets protected; runtime has no retired imports.')
