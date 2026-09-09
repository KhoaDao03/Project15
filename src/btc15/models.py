"""Immutable model definitions, explicit paper activation, and shared event dispatch."""

import json
import time
from dataclasses import asdict

from .domain import dumps
from .engine import Engine
from .strategies.momentum import Momentum, volatility_model


def identity(config):
    return dict(
        model_id=getattr(config, "model_id", "settlement-edge"),
        model_name=getattr(config, "model_name", "BTC15 Settlement Edge"),
        model_version=getattr(config, "model_version", "v1"),
        config_hash=config.version,
    )


def definitions(store):
    defaults = [Momentum(), volatility_model()]
    result = {
        c.model_id + ":" + c.model_version: dict(config=asdict(c), active=False, created_at=None)
        for c in defaults
    }
    for row in store.list(kind="model_definition", limit=None):
        result[row["body"]["key"]] = {**row["body"], "active": False}
    for row in store.list(kind="model_activation", limit=None):
        key = row["body"]["key"]
        if key in result:
            result[key]["active"] = row["body"]["active"]
    return result


def register(store, config):
    key = config.model_id + ":" + config.model_version
    existing = definitions(store).get(key)
    if existing and Momentum(**existing["config"]).version != config.version:
        raise ValueError("Model/version is immutable; create a new model_version")
    with store.transaction():
        if store.claim("model-registry", key):
            store.add(
                "model_definition",
                dict(key=key, config=asdict(config), config_hash=config.version, created_at=time.time()),
                "model-registry",
                "PAPER",
                time.time(),
            )
        else:
            saved = definitions(store)[key]
            if Momentum(**saved["config"]).version != config.version:
                raise ValueError("Model/version is immutable; create a new model_version")
    return key


def activate(store, key, active):
    if type(active) is not bool:
        raise ValueError("Activation must be a boolean")
    known = definitions(store)
    if key not in known:
        raise ValueError("Unknown model/version")
    register(store, Momentum(**known[key]["config"]))
    store.add("model_activation", dict(key=key, active=active), "model-registry", "PAPER", time.time())


class ModelGroup:
    """Same immutable wire snapshot, separate instances of the shared engine/ledger.

    Attribute reads expose the control engine for the existing collector display.
    No strategy owns a network client. Resume membership/configuration is pinned.
    """

    def __init__(
        self,
        store,
        config,
        mode="PAPER",
        run_id=None,
        execute=True,
        clock=None,
        resume=False,
        model_keys=None,
        record_evaluations=True,
    ):
        known = definitions(store)
        if resume:
            previous = store.list(kind="model_group", run_id=run_id, limit=1)
            if not previous:
                raise ValueError("No model group manifest for resume")
            members = previous[0]["body"]["members"]
        else:
            keys = model_keys if model_keys is not None else [k for k, v in known.items() if v["active"]]
            members = [dict(key=k, config=known[k]["config"]) for k in keys]
        if len({m["key"] for m in members}) != len(members):
            raise ValueError("Duplicate model keys")
        with store.transaction():
            self.control = Engine(
                store, config, mode, run_id, execute, clock, resume, record_evaluations=record_evaluations
            )
            self.engines = [self.control]
            for member in members:
                c = Momentum(**member["config"])
                register(store, c)
                child_id = self.control.run_id + "/" + member["key"]
                child = Engine(
                    store, c, mode, child_id, execute, clock, resume, record_evaluations=record_evaluations
                )
                self.engines.append(child)
            if not resume:
                store.add("model_group", dict(members=members), self.run_id, mode, time.time())
            self.apply_activation(store)

    def __getattr__(self, name):
        return getattr(self.control, name)

    def apply_activation(self, store):
        if self.mode == "PAPER":
            known = definitions(store)
            for engine in self.engines[1:]:
                key = engine.config.model_id + ":" + engine.config.model_version
                engine.entries_active = known[key]["active"]

    def ingest(self, row):
        # Canonical JSON freezes the fetched event before evaluating any model.
        snapshot = dumps(row)
        calculations = {}
        valid = True
        for engine in self.engines:
            engine.shared_calculations = calculations
            valid = engine.ingest(json.loads(snapshot)) and valid
        return valid

    def halt(self, now):
        for engine in self.engines:
            engine.executor.halt(now)

    def checkpoint(self):
        for engine in self.engines:
            engine.store.checkpoint(engine.run_id, engine.executor.snapshot())

    def restore_daily_history(self):
        for engine in self.engines:
            engine.executor.restore_daily_history()


def comparison(store, mode="PAPER", days=None, now=None, control_config=None):
    """Version/hash separated realized results; no marking open positions to market."""
    from collections import defaultdict
    from statistics import mean

    now = time.time() if now is None else now
    cutoff = now - days * 86400 if days is not None else -float("inf")
    runs = store.list(kind="run", mode=mode, limit=None)
    groups = defaultdict(list)
    for r in runs:
        body = r["body"]
        model = body.get(
            "model",
            dict(
                model_id="settlement-edge",
                model_name="BTC15 Settlement Edge",
                model_version="v1",
                config_hash=body["versions"]["config"],
            ),
        )
        # Backtest bankrolls reset on each replay: do not pool their returns.
        key = dumps(model) + (r["run_id"] if mode == "BACKTEST" else "")
        groups[key].append((r, model))
    known = definitions(store)
    represented = {m["model_id"] + ":" + m["model_version"] for group in groups.values() for _, m in group}
    for key, entry in known.items():
        if key not in represented:
            c = Momentum(**entry["config"])
            groups[dumps(identity(c))] = [(dict(run_id=None, body=dict(config=asdict(c))), identity(c))]
    if not any(model["model_id"] == "settlement-edge" for group in groups.values() for _, model in group):
        from .config import Strategy

        c = control_config or Strategy()
        groups[dumps(identity(c))] = [(dict(run_id=None, body=dict(config=asdict(c))), identity(c))]
    output = []
    for group in groups.values():
        ids = {r["run_id"] for r, _ in group if r["run_id"] is not None}
        model = group[0][1]
        config = group[0][0]["body"]["config"]
        ops = [
            r
            for r in store.list(kind="opportunity", mode=mode, limit=None)
            if r["run_id"] in ids and r["timestamp"] <= now
        ]
        results = [
            r
            for r in store.list(kind="trade_result", mode=mode, limit=None)
            if r["run_id"] in ids and r["timestamp"] <= now
        ]
        fills = [
            r
            for r in store.list(kind="fill", mode=mode, limit=None)
            if r["run_id"] in ids and r["timestamp"] <= now and r["body"]["action"] == "buy"
        ]
        op_map = {r["id"]: r["body"] for r in ops}
        closed = {r["opportunity_id"] for r in results}
        entered = {r["opportunity_id"] for r in fills}
        selected = [r for r in results if cutoff <= r["timestamp"] <= now]
        signals = [r for r in ops if cutoff <= r["timestamp"] <= now]
        selected_fills = [r for r in fills if cutoff <= r["timestamp"] <= now]
        selected_entries = {r["opportunity_id"] for r in selected_fills}
        pnls = [r["body"]["net_pnl"] for r in selected]
        profit = sum(p for p in pnls if p > 0)
        loss = -sum(p for p in pnls if p < 0)
        equity = peak = drawdown = 0
        for r in selected:
            equity += r["body"]["net_pnl"]
            peak = max(peak, equity)
            drawdown = max(drawdown, peak - equity)

        def average(name, feature=False):
            values = [
                (op_map.get(op, {}).get("features", {}) if feature else op_map.get(op, {})).get(name)
                for op in selected_entries
            ]
            values = [v for v in values if v is not None]
            return mean(values) if values else None

        regimes = []
        for regime_name in ("LOW", "NORMAL", "HIGH", "EXTREME", "UNKNOWN"):

            def matches(op):
                return op_map.get(op, {}).get("features", {}).get(
                    "volatility_regime", "UNKNOWN"
                ) == regime_name or (
                    regime_name == "UNKNOWN"
                    and op_map.get(op, {}).get("features", {}).get("volatility_regime") is None
                )

            outcomes = [r for r in selected if matches(r["opportunity_id"])]
            wins = sum(r["body"]["net_pnl"] > 0 for r in outcomes)
            regimes.append(
                dict(
                    regime=regime_name,
                    signals_observed=sum(matches(r["id"]) for r in signals),
                    unique_markets_observed=len({r["market"] for r in signals if matches(r["id"])}),
                    trades_entered=sum(matches(op) for op in selected_entries),
                    wins=wins,
                    losses=sum(r["body"]["net_pnl"] < 0 for r in outcomes),
                    win_rate=wins / len(outcomes) if outcomes else None,
                    net_pnl=sum(r["body"]["net_pnl"] for r in outcomes),
                )
            )
        key = model["model_id"] + ":" + model["model_version"]
        bankroll = config["bankroll"]
        realized = sum(r["body"]["net_pnl"] for r in results)
        open_debit = sum(
            r["body"]["quantity"] * r["body"]["price"] + r["body"]["fee"]
            for r in fills
            if r["opportunity_id"] not in closed
        )
        output.append(
            dict(
                **model,
                status=("active" if known[key]["active"] else "inactive") if key in known else "control",
                mode=mode,
                run_ids=sorted(ids),
                period_days=days,
                starting_bankroll=bankroll,
                paper_equity_at_cost=bankroll + realized,
                open_entry_debit=open_debit,
                settled_trades=sum(r["body"]["reason"] == "SETTLEMENT" for r in selected),
                completed_trades=len(selected),
                open_trades=len(entered - closed),
                wins=sum(p > 0 for p in pnls),
                losses=sum(p < 0 for p in pnls),
                win_rate=sum(p > 0 for p in pnls) / len(pnls) if pnls else None,
                net_pnl=sum(pnls),
                average_pnl=mean(pnls) if pnls else None,
                average_entry_price=mean(r["body"]["price"] for r in selected_fills)
                if selected_fills
                else None,
                average_probability=average("conservative_probability"),
                average_edge=average("net_ev"),
                average_spread=average("spread"),
                average_seconds_remaining=average("seconds_remaining"),
                average_minutes_remaining=average("seconds_remaining") / 60
                if average("seconds_remaining") is not None
                else None,
                average_volatility=average("minute_volatility", True),
                profit_factor=profit / loss if loss else None,
                realized_max_drawdown=drawdown,
                roi=sum(pnls) / bankroll,
                regimes=regimes,
            )
        )
    return output
