"""One-time, reviewed transformation for the approved Settlement Edge-only scope."""
import ast
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path.cwd()
BASE = 'b4f291105b2f1bbb8f025b82f922abe9b22084f4'

def write(path, text):
    p = ROOT / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)

def edit(path, replacements):
    p = ROOT / path
    text = p.read_text()
    for old, new in replacements:
        if old not in text:
            raise AssertionError(f'Missing edit anchor in {path}: {old[:160]!r}')
        text = text.replace(old, new)
    p.write_text(text)

def between(path, start, end, replacement):
    p = ROOT / path
    text = p.read_text()
    a = text.index(start)
    b = text.index(end, a)
    p.write_text(text[:a] + replacement + text[b:])

ORACLE = '''"""Compare Settlement Edge behavior across checkouts using identical synthetic inputs.

Run once before a refactor with --output baseline.json; run the changed checkout
with --output candidate.json --compare baseline.json using the same locked environment.
No network access, credentials, production database, or real-money orders are used.
"""
import argparse
import json
import tempfile
from pathlib import Path

from btc15.config import Strategy
from btc15.demo import generate
from btc15.engine import Engine
from btc15.storage import Store, read_events


def capture(config, tape, db):
    store = Store("sqlite:///" + str(db))
    try:
        engine = Engine(store, config, "BACKTEST", execute=True)
        for row in read_events(tape):
            assert engine.ingest(row), row["id"]
        report = {}
        for kind in ("opportunity", "order", "fill", "trade_result", "transition", "settlement", "health"):
            rows = []
            for row in store.list(kind=kind, run_id=engine.run_id, limit=None):
                body = dict(row["body"])
                for field in ("id", "order_id", "opportunity_id", "trade_id", "versions"):
                    body.pop(field, None)
                rows.append([row["timestamp"], body])
            # Record UUIDs must not decide equivalence when several records share a time.
            report[kind] = sorted(rows, key=lambda r: (r[0], json.dumps(r[1], sort_keys=True)))
        report["risk"] = engine.executor.snapshot()["risk"]
        report["positions"] = engine.executor.snapshot()["positions"]
        return report
    finally:
        store.engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--compare")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        tape = generate(root / "synthetic.jsonl")
        report = {
            name: capture(config, tape, root / (name + ".db"))
            for name, config in (
                ("original", Strategy()),
                ("moderate", Strategy.load("config/settlement-edge-paper-moderate.json")),
            )
        }
    Path(args.output).write_text(json.dumps(report, sort_keys=True))
    if args.compare:
        expected = json.loads(Path(args.compare).read_text())
        assert report == expected, "Settlement Edge behavior differs from the reference checkout"
    for name, result in report.items():
        print(json.dumps({"configuration": name, "input": "SYNTHETIC", "equivalent": bool(args.compare),
                          "evaluations": len(result["opportunity"]), "fills": len(result["fill"]),
                          "completed_trades": len(result["trade_result"]), "open_positions": len(result["positions"])}))


if __name__ == "__main__":
    main()
'''

if len(sys.argv) > 1 and sys.argv[1] == 'prepare':
    Path('/tmp/settlement-oracle.py').write_text(ORACLE)
    raise SystemExit(0)

# These files must remain byte-for-byte identical. No threshold or fee changes.
protected = [
    'src/btc15/strategies/settlement_edge/config.py',
    'src/btc15/strategies/settlement_edge/model.py',
    'src/btc15/strategies/settlement_edge/rules.py',
    'config/defaults.json', 'config/settlement-edge-paper-moderate.json',
    'src/btc15/config.py', 'src/btc15/api.py', 'src/btc15/domain.py',
    'pyproject.toml', 'uv.lock', '.env.example', 'compose.yaml',
]
before = {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in protected}

write('src/btc15/models.py', '''"""Settlement Edge identity and read-only interpretation of retained legacy history.

No strategy registry, activation, algorithm dispatch, or multi-engine execution lives
here. Historical identities are data: they never instantiate retired strategies.
"""

LEGACY_REVISION = "b4f291105b2f1bbb8f025b82f922abe9b22084f4"


def identity(config):
    return dict(model_id="settlement-edge", model_name="BTC15 Settlement Edge",
                model_version="v1", config_hash=config.version)


def run_model(row):
    body = row["body"]
    if body.get("model"):
        return body["model"]
    # Old control runs predate explicit identities. Child run IDs are not controls.
    archived = "/" in row["run_id"]
    return dict(model_id="legacy-archived" if archived else "settlement-edge",
                model_name="Archived strategy" if archived else "BTC15 Settlement Edge",
                model_version="v1", config_hash=body.get("versions", {}).get("config", "legacy"))


def history_models(store, mode):
    return {r["run_id"]: run_model(r) for r in store.run_summaries(mode)}


def archived(row, models):
    model = row["body"].get("model") or models.get(row["run_id"]) or run_model(row)
    return model.get("model_id") != "settlement-edge"


def select_history(rows, models, scope="settlement"):
    if scope not in ("settlement", "archive"):
        raise ValueError("Unknown history scope")
    return [r for r in rows if archived(r, models) == (scope == "archive")]


def require_single_run(store, run_id):
    """Reject incompatible resumes before creating an engine or touching checkpoints."""
    if not run_id:
        return
    groups = store.list(kind="model_group", run_id=run_id, limit=None)
    runs = store.list(kind="run", run_id=run_id, limit=None)
    if any(r["body"].get("members") for r in groups) or any(archived(r, {}) for r in runs):
        raise ValueError(
            f"Run {run_id!r} belongs to a retired multi-strategy portfolio. "
            f"Use revision {LEGACY_REVISION} to cancel/settle its exposure, then use a new "
            "Settlement Edge run ID. History is unchanged. See docs/SINGLE_STRATEGY.md."
        )


def guard_archived_exposure(store):
    """Legacy positions/pending orders must be recovered, not hidden by new startup."""
    models = history_models(store, "PAPER")
    key = lambda r: (r["run_id"], r["market"], r["opportunity_id"])
    closed = {key(r) for r in store.list(kind="trade_result", mode="PAPER", limit=None)}
    unresolved = {
        r["run_id"] for r in store.list(kind="fill", mode="PAPER", limit=None)
        if archived(r, models) and r["body"].get("action") == "buy" and key(r) not in closed
    }
    for run_id, model in models.items():
        if model.get("model_id") == "settlement-edge":
            continue
        checkpoint = store.load_checkpoint(run_id) or {}
        if checkpoint.get("positions") or any(o.get("active") for o in checkpoint.get("orders", {}).values()):
            unresolved.add(run_id)
    if unresolved:
        raise ValueError(
            "Unresolved archived paper exposure: " + ", ".join(sorted(unresolved))
            + f". Recover with revision {LEGACY_REVISION}; do not clear leases/checkpoints "
            "or reset budgets. See docs/SINGLE_STRATEGY.md."
        )
''')

# Remove the retired algorithms and their dispatch, preserving the control calculation.
Path('src/btc15/strategies/momentum.py').unlink()
edit('src/btc15/engine.py', [
    ('from .domain import Book, dumps, parse_market, timestamp', 'from .config import Strategy\nfrom .domain import Book, dumps, parse_market, timestamp\nfrom .models import require_single_run'),
    ('from .strategies.momentum import Momentum, ObservationCache, observations\n', ''),
    ('from .strategies.momentum import evaluate as evaluate_momentum\n', ''),
    ('        self.store, self.config, self.mode = store, config, mode',
     '        if type(config) is not Strategy:\n            raise ValueError("Only BTC15 Settlement Edge is executable")\n        if resume:\n            require_single_run(store, run_id)\n        self.store, self.config, self.mode = store, config, mode'),
    ('        self._causal_reference = (None, 0)\n', ''),
    ('        self._observations = ObservationCache()\n', ''),
    ('        self.shared_calculations = {}\n', ''),
    ('        for key in list(self.shared_calculations):\n            if key[0] != now:\n                del self.shared_calculations[key]\n', ''),
    ('                or isinstance(c, Momentum)\n                and material\n                and (quote_update or resting or position)\n                and now - cached[0] >= min(1.0, c.evaluation_interval)\n', ''),
])
between('src/btc15/engine.py', '    def causal_ticks(', '    def process(', '')
between('src/btc15/engine.py', '                    # Model parameters, not entry thresholds, determine cache compatibility.',
        '                    cached = (now, market.spec, f, p, None)',
        '                    f = features(self.ticks, now, c)\n                    p = probability(market.spec, self.ticks, now, f["sigma"], c)\n')
between('src/btc15/engine.py', '                if isinstance(c, Momentum):\n', '            except ValueError as exc:',
        '                q = quality(f, self.ticks, book, now, c)\n                decision = evaluate(market, book, self.ticks[-1], f, p, q, now, c, extras)\n')
between('src/btc15/engine.py', '                if isinstance(c, Momentum):\n', '                if self.record_evaluations:', '')

# Only momentum-specific executor branches are removed; the Decimal fix is preserved.
edit('src/btc15/execution.py', [
    ('                c.no_new_entry <= market.close_time - now <= c.entry_window_start\n                if hasattr(c, "model_id")\n                else c.no_new_entry < market.close_time - now <= c.entry_window_start',
     '                c.no_new_entry < market.close_time - now <= c.entry_window_start'),
    ('        if self.model_identity["model_id"] == "settlement-edge":\n            return model is None or model["model_id"] == "settlement-edge"\n        return model == self.model_identity',
     '        return model is None or model["model_id"] == "settlement-edge"'),
    ('        if getattr(c, "hold_to_settlement", False):\n            return\n', ''),
])
between('src/btc15/execution.py', '        if hasattr(c, "model_id") and not self.store.claim(',
        '        if not self.store.claim(self.run_id, "entry:" + market.ticker):', '')

# One collector, one engine, existing status-envelope shape for readers.
edit('src/btc15/runner.py', [
    ('from .engine import Engine', 'from .engine import Engine\nfrom .models import guard_archived_exposure, require_single_run'),
    ('    multi_model=False,\n', ''),
    ('        acquired = True\n', '        acquired = True\n        if paper:\n            require_single_run(store, resume or managed_run)\n            guard_archived_exposure(store)\n'),
    ('            (engine.restore_daily_history() if multi_model else engine.executor.restore_daily_history())',
     '            engine.executor.restore_daily_history()'),
    ('            (\n                engine.checkpoint()\n                if multi_model\n                else store.checkpoint(engine.run_id, engine.executor.snapshot())\n            )',
     '            store.checkpoint(engine.run_id, engine.executor.snapshot())'),
    ('engine.engines if multi_model else [engine]', '[engine]'),
    ('            if multi_model and not stop.is_set():\n                engine.apply_activation(store)\n', ''),
    ('                    (engine.halt(row["received"]) if multi_model else engine.executor.halt(row["received"]))',
     '                    engine.executor.halt(row["received"])'),
])
between('src/btc15/runner.py', '        if multi_model and paper and not resume:', '        engine = engine_type(', '')
edit('src/btc15/runner.py', [('        engine = engine_type(', '        engine = Engine(')])
between('src/btc15/runner.py', '    for member in getattr(engine, "engines", [engine]):', '\n\nasync def collect(',
        '    engine.execute = False\n    engine.entries_active = False\n    for ticker in list(engine.executor.orders):\n        if engine.executor.orders[ticker].active:\n            engine.executor.cancel(ticker, now, "safe_shutdown")\n')
# Collapse temporary one-element loops introduced by removal (AST, source line surgery).
def collapse_single_loops(path):
    p = Path(path)
    while True:
        text = p.read_text()
        tree = ast.parse(text)
        candidates = [n for n in ast.walk(tree) if isinstance(n, ast.For) and
                      isinstance(n.iter, ast.List) and len(n.iter.elts) == 1 and
                      isinstance(n.iter.elts[0], ast.Name) and n.iter.elts[0].id == 'engine' and
                      isinstance(n.target, ast.Name) and not n.orelse]
        if not candidates:
            break
        node = max(candidates, key=lambda n: n.lineno)
        lines = text.splitlines(keepends=True)
        body = ''.join(line[4:] if line.strip() else line for line in lines[node.lineno:node.end_lineno])
        body = re.sub(r'\b' + re.escape(node.target.id) + r'\b', 'engine', body)
        p.write_text(''.join(lines[:node.lineno-1]) + body + ''.join(lines[node.end_lineno:]))
collapse_single_loops('src/btc15/runner.py')
# No multi-engine shutdown bookkeeping.
edit('src/btc15/runner.py', [
    ('                                members = [engine]\n', ''),
    ('                                        open_positions=sum(len(e.executor.positions) for e in members),\n                                        runs=[e.run_id for e in members],',
     '                                        open_positions=len(engine.executor.positions),\n                                        runs=[engine.run_id],'),
])
edit('src/btc15/operation.py', [
    ('duration=None, *, multi_model=False', 'duration=None'),
    ('            **({"multi_model": True} if multi_model else {}),\n', ''),
])
edit('src/btc15/research.py', [('parent_run=None, model_keys=None', 'parent_run=None')])
between('src/btc15/research.py', '    if model_keys is None:', '    count = 0',
        '    engine = Engine(store, config, "BACKTEST", execute=True)\n')
edit('src/btc15/research.py', [
    ('    engines = engine.engines if model_keys is not None else [engine]\n    for member in engines:', '    for member in [engine]:'),
    ('                comparison_run=engine.run_id if model_keys is not None else None,\n', ''),
])
collapse_single_loops('src/btc15/research.py')

# Explicitly retire old multi-model CLI commands; preserve ordinary commands/defaults.
between('src/btc15/cli.py', '    p = commands.add_parser("models",', '    commands.add_parser("config")', '')
between('src/btc15/cli.py', '    if args.command == "models":', '    elif args.command == "init-db":', '')
edit('src/btc15/cli.py', [
    ('    elif args.command == "init-db":', '    if args.command == "init-db":'),
    ('help="Named PAPER run to start or resume"', 'help="Named Settlement Edge PAPER run to start or resume"'),
    ('    p.add_argument("--run")\n', '    p.add_argument("--run")\n    p.add_argument("--archive", action="store_true", help="Read retired strategy results only")\n'),
    ('print(dumps(metrics(store, args.mode, args.run)))', 'print(dumps(metrics(store, args.mode, args.run, scope="archive" if args.archive else "settlement")))'),
])

# Historical data remains available, explicitly scoped before all counts/aggregation.
edit('src/btc15/analytics.py', [
    ('import numpy as np\n', 'import numpy as np\n\nfrom .models import history_models, select_history\n'),
    ('def metrics(store, mode="PAPER", run_id=None):\n    ops = store.list("opportunity", run_id, mode, limit=None)',
     'def metrics(store, mode="PAPER", run_id=None, *, scope="settlement"):\n    models = history_models(store, mode)\n\n    def read(kind):\n        return select_history(store.list(kind, run_id, mode, limit=None), models, scope)\n\n    ops = read("opportunity")'),
    ('        from .models import comparison\n\n        result = metrics(store, mode, "__no_pooled_model_results__")',
     '        result = metrics(store, mode, "__no_pooled_model_results__", scope=scope)'),
    ('            model_comparison=comparison(store, mode),', '            model_comparison=[], '),
    ('Multiple model/config versions: select a run or use model-comparison; pooled results are suppressed',
     'Multiple configurations or archived strategies: select a run; pooled results are suppressed'),
    ('        mode=mode,\n        run_id=run_id,', '        mode=mode,\n        run_id=run_id,\n        history_scope=scope,'),
])
# Only the metrics function uses these reads, not ledger/global recovery queries.
p = Path('src/btc15/analytics.py'); text = p.read_text()
text = re.sub(r'store\.list\("([a-z_]+)", run_id, mode, limit=None\)', r'read("\1")', text)
p.write_text(text)

# The active dashboard always has one card, including when entries are disabled.
edit('src/btc15/dashboard.py', [
    ('from .models import definitions, identity', 'from .models import history_models, identity, run_model, select_history'),
    ('from .strategies.momentum import Momentum\n', ''),
    ('                            multi_model=True,\n', ''),
    ('        return store.run_summaries(mode, limit=100)',
     '        rows = store.run_summaries(mode)\n        return select_history(rows, {r["run_id"]: run_model(r) for r in rows}, scope)[:100]'),
    ('    def runs(mode: str = Query("PAPER", pattern="^(PAPER|BACKTEST|LIVE)$")):',
     '    def runs(mode: str = Query("PAPER", pattern="^(PAPER|BACKTEST|LIVE)$"),\n             scope: str = Query("settlement", pattern="^(settlement|archive)$")):'),
    ('    def strategies(mode: str = Query("PAPER", pattern="^(PAPER|BACKTEST|LIVE)$")):',
     '    def strategies(mode: str = Query("PAPER", pattern="^(PAPER|BACKTEST|LIVE)$"), run_id: str | None = None):'),
    ('        saved = Strategy.load(strategy_path) if strategy_path.exists() else session_config\n        entries = {}',
     '        selected_model = identity(session_config)\n        if run_id:\n            selected = store.run_summaries(mode)\n            selected = [r for r in selected if r["run_id"] == run_id and run_model(r)["model_id"] == "settlement-edge"]\n            if selected:\n                selected_model = run_model(selected[0])\n        entries = {}'),
    ('        summaries = store.run_summaries(mode)\n',
     '        summaries = select_history(store.run_summaries(mode), history_models(store, mode))\n        if run_id:\n            summaries = [r for r in summaries if r["run_id"] == run_id]\n'),
    ('        membership = tuple((key, tuple(r["run_id"] for r in entry["runs"])) for key, entry in entries.items())',
     '        selected_ids = {r["run_id"] for entry in entries.values() for r in entry["runs"]}\n        membership = tuple((key, tuple(r["run_id"] for r in entry["runs"])) for key, entry in entries.items())'),
    ('                results = store.list(kind="trade_result", mode=mode, limit=None)\n                fills = store.list(kind="fill", mode=mode, limit=None)',
     '                known = history_models(store, mode)\n                results = [r for r in select_history(store.list(kind="trade_result", mode=mode, limit=None), known) if r["run_id"] in selected_ids]\n                fills = [r for r in select_history(store.list(kind="fill", mode=mode, limit=None), known) if r["run_id"] in selected_ids]'),
])
between('src/btc15/dashboard.py', '        if saved.enabled:\n', '        # Show active configurations only;',
        '        include(selected_model, session_config.enabled)\n')
# Add explicit scope parameters to paginated historical APIs.
edit('src/btc15/dashboard.py', [
    ('        group_by_market: bool = False,', '        group_by_market: bool = False,\n        scope: str = Query("settlement", pattern="^(settlement|archive)$"),'),
    ('        rows = store.list(kind=kind, mode=mode, run_id=run_id, market=market, limit=None)[::-1]',
     '        rows = select_history(\n            store.list(kind=kind, mode=mode, run_id=run_id, market=market, limit=None),\n            history_models(store, mode), scope\n        )[::-1]'),
    ('    def trades(\n        mode: str = Query("PAPER", pattern="^(PAPER|BACKTEST|LIVE)$"),',
     '    def trades(\n        mode: str = Query("PAPER", pattern="^(PAPER|BACKTEST|LIVE)$"),\n        scope: str = Query("settlement", pattern="^(settlement|archive)$"),'),
    ('        opportunities = {\n            r["id"]: r["body"] for r in store.list(kind="opportunity", mode=mode, run_id=run_id, limit=None)\n        }',
     '        known = history_models(store, mode)\n        opportunities = {r["id"]: r["body"] for r in select_history(\n            store.list(kind="opportunity", mode=mode, run_id=run_id, limit=None), known, scope)}'),
    ('        for r in reversed(store.list(kind="trade_result", mode=mode, run_id=run_id, limit=None)):',
     '        for r in reversed(select_history(store.list(kind="trade_result", mode=mode, run_id=run_id, limit=None), known, scope)):'),
    ('    def analytics(mode: str = Query("PAPER", pattern="^(PAPER|BACKTEST|LIVE)$"), run_id: str | None = None):\n        return metrics(store, mode, run_id)',
     '    def analytics(mode: str = Query("PAPER", pattern="^(PAPER|BACKTEST|LIVE)$"), run_id: str | None = None,\n                  scope: str = Query("settlement", pattern="^(settlement|archive)$")):\n        return metrics(store, mode, run_id, scope=scope)'),
    ('        rows = store.latest_evaluation(selected, mode)',
     '        rows = select_history(store.latest_evaluation(selected, mode), history_models(store, mode))'),
])

# Load benchmarks retain single-engine processing and HTTP/SSE coverage.
for path in ('scripts/check_dashboard_load.py', 'scripts/check_processing_throughput.py'):
    edit(path, [
        ('from btc15.models import ModelGroup, activate, register', 'from btc15.engine import Engine'),
        ('from btc15.strategies.momentum import Momentum, volatility_model\n', ''),
        ('    for model in [Momentum(), volatility_model()]:\n        activate(store, register(store, model), True)\n', ''),
        ('group = ModelGroup(', 'group = Engine('),
        ('group.engines', '[group]'),
        ('group.apply_activation(store)', 'pass  # No runtime strategy activation in the single-engine bot.'),
    ])

# UI: one strategy; archive scope is historical and never changes the running engine.
edit('src/btc15/static/app.js', [
    ("function query(){return new URLSearchParams({mode:$('mode').value,", "function query(){return new URLSearchParams({mode:$('mode').value,scope:$('history-scope').value,"),
    ("trades:'Opportunity memory',analytics:'Results & accuracy',strategies:'Strategies'", "trades:'Trade history',analytics:'Results & accuracy',strategies:'Settings'"),
    ("get('/api/runs?mode='+mode)", "get('/api/runs?mode='+mode+'&scope='+$('history-scope').value)"),
    ("get('/api/strategies?mode='+$('mode').value)", "get('/api/strategies?'+query())"),
    ("strategies.rows.length+' active strategy configurations · '+$('mode').value+' · cards show latest saved evaluations across runs'", "'BTC15 Settlement Edge · '+$('mode').value+' · one strategy; configuration and run are shown below'"),
    ("const [d,catalog]=await Promise.all([get('/api/strategy'),get('/api/strategies?'+query())]);if(gen!==generation)return;renderStrategy(d);renderStrategies('strategy-library',catalog);", "const d=await get('/api/strategy');if(gen!==generation)return;renderStrategy(d);"),
    ("all recorded strategies, including inactive and historical configurations", "Settlement Edge only · selected configuration/run scope"),
    ("All history retained in this database; independent strategy portfolios are summed.", "Archived strategies are excluded. Configuration changes remain separately identified."),
    ("Combined totals interleave independent strategies and runs.", "Selected-scope totals interleave completed runs; archived strategies are excluded from the active view."),
    ("No saved evaluations in this mode. This strategy starts inactive; the model-paper workflow evaluates active momentum strategies.", "Entries are disabled. Evaluation and position management remain available while the collector runs."),
])
# Visible archive selector; live/settings buttons return to the live strategy scope.
p = Path('src/btc15/static/app.js'); t = p.read_text()
t = t.replace("function tab(name){", "function tab(name){if((name==='monitor'||name==='strategies')&&$('history-scope').value!=='settlement'){$('history-scope').value='settlement';$('run').value='';runs();}")
t += "\n$('history-scope').onchange=async()=>{generation++;$('run').value='';offset=0;await runs();tab('trades');};\n"
p.write_text(t)
# Replace the settings intro/library block, keeping the existing validated form.
p = Path('src/btc15/static/index.html'); t = p.read_text()
t = t.replace('>Opportunity memory</button>', '>Trade history</button>').replace('>Strategies</button>', '>Settings</button>')
t = t.replace('<label>Run<select id="run">', '<label>History<select id="history-scope"><option value="settlement">Settlement Edge</option><option value="archive">Archived strategies (read-only)</option></select></label><label>Run<select id="run">')
a = t.index('<section id="strategies"'); b = t.index('<article><div class="section-title"><h2>BTC15 Settlement Edge</h2>', a)
t = t[:a] + '<section id="strategies" class="tab" hidden><div class="section-intro"><h2>Settlement Edge settings</h2><p>One executable strategy. The original and moderate presets remain separate configurations; saving settings never changes an existing run.</p></div>' + t[b:]
t = t.replace('<h2>Strategy evaluations</h2>', '<h2>BTC15 Settlement Edge</h2>')
t = t.replace('Each strategy has its own decisions and portfolio. Saved evaluations may refer to different markets or times. Select a run to explore its history and results.', 'One active strategy and portfolio. Select a run to inspect its frozen configuration, decisions and results. Archived strategy history is available through the History selector.')
t = t.replace('Start collection with <code>btc15 collect</code> or paper execution with <code>btc15 paper</code>.', 'Start paper execution with <code>btc15 dashboard</code> or observe without orders using <code>btc15 dashboard --observe-only</code>.')
t = t.replace('History stays still while you read.', 'Compact paper mode retains filled-entry evidence, not every skipped evaluation. Archived strategy views are read-only. History stays still while you read.')
t = t.replace('One last eligible prediction per settled market. Small samples are not validation.', 'Based on retained predictions only. Compact paper history is filled-entry evidence, not every evaluated market. Small samples are not validation.')
t = t.replace('live-layout-3-feed-safety-16', 'settlement-only-1')
p.write_text(t)
# One card fills its container; no additional frontend framework.
with Path('src/btc15/static/style.css').open('a') as f:
    f.write('\n/* Single active strategy; old records are viewed separately. */\n#strategy-overview{grid-template-columns:minmax(0,1fr)}\n')

# Tests for retired algorithms are removed; shared safety coverage is specialized below.
Path('tests/test_momentum_models.py').unlink()
Path('tests/test_dashboard_strategies.py').unlink()

def remove_function(path, name):
    p = Path(path); t = p.read_text(); lines = t.splitlines(keepends=True)
    n = next(n for n in ast.walk(ast.parse(t)) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    start = min([n.lineno] + [d.lineno for d in n.decorator_list])
    p.write_text(''.join(lines[:start-1]) + ''.join(lines[n.end_lineno:]))
remove_function('tests/test_processing_performance.py', 'test_causal_reference_cache_handles_future_ticks_and_clock_reversal')
remove_function('tests/test_processing_safety.py', 'test_observation_cache_matches_uncached_at_fractional_boundaries_gaps_and_updates')
edit('tests/test_processing_safety.py', [
    ('@pytest.mark.parametrize("model", ["control", "momentum", "volatility"])', '@pytest.mark.parametrize("passive", [True, False])'),
    ('monkeypatch, delay, model\n', 'monkeypatch, delay, passive\n'),
    ('"trade" if model == "control" else "orderbook_delta"', '"trade" if passive else "orderbook_delta"'),
])
between('tests/test_processing_safety.py', '    from btc15.strategies.momentum import Momentum, volatility_model', '    clock = ', '    config = replace(config, passive=passive)\n')
edit('tests/test_trade_recording.py', [
    ('from btc15.strategies.momentum import Momentum, volatility_model\n', ''),
    ('@pytest.mark.parametrize("config", [Strategy(), Momentum(), volatility_model()])', '@pytest.mark.parametrize("config", [Strategy()])'),
])
between('tests/test_trade_recording.py', '    monkeypatch.setattr(\n        module,\n        "observations",', '    for i in range(10):', '')
edit('tests/test_dashboard_paper_defaults.py', [
    ('    from btc15.models import activate, register\n', ''),
    ('    from btc15.strategies.momentum import Momentum, volatility_model\n', ''),
    ('    for model in (Momentum(), volatility_model()):\n        activate(store, register(store, model), True)\n', ''),
    ('len(rows) == 3', 'len(rows) == 1'),
    ('len(resumes) == attempt * 3', 'len(resumes) == attempt'),
    ('    assert len(store.list(kind="model_group", run_id="dashboard-paper")) == 1',
     '    assert not store.list(kind="model_group", run_id="dashboard-paper")'),
])
edit('tests/test_dashboard_markets.py', [('assert kwargs["multi_model"] is True', 'assert "multi_model" not in kwargs')])
edit('tests/test_shutdown.py', [
    ('runner.stop_entries(SimpleNamespace(engines=[member]), now + 2)', 'runner.stop_entries(member, now + 2)'),
    ('@pytest.mark.parametrize("multi_model", [False, True])\n', ''),
    ('monkeypatch, multi_model\n', 'monkeypatch\n'),
    ('                multi_model=multi_model,\n', ''),
    ('assert len(runs) == (3 if multi_model else 1)', 'assert len(runs) == 1'),
])
between('tests/test_shutdown.py', '    if multi_model:\n', '    async def run():', '')
write('scripts/verify_settlement_behavior.py', ORACLE)

# Fill tests and active controls intentionally unchanged except explicit removed dispatch.
for path in ('src', 'tests', 'scripts'):
    for p in Path(path).rglob('*.py'):
        ast.parse(p.read_text(), filename=str(p))
for path, digest in before.items():
    assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest, path
print('Protected strategy, preset, dependency and API files are unchanged.')
