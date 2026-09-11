import argparse
import asyncio
import logging
import os
from dataclasses import asdict
from pathlib import Path

from .analytics import metrics
from .api import KalshiClient
from .config import Settings, Strategy
from .domain import dumps, parse_market
from .storage import Store, export_packet


def main():
    parser = argparse.ArgumentParser(description="Kalshi BTC15 settlement research; LIVE disabled")
    parser.add_argument("--config", help="JSON strategy overrides (all defaults are assumptions)")
    parser.add_argument("--database", help="SQLAlchemy database URL override")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-db")
    commands.add_parser("config")
    p = commands.add_parser("paper-service", help="Managed PAPER operation; no real orders")
    p.add_argument("--run-id", required=True)
    p.add_argument("--min-free-gb", type=float, default=10)
    p.add_argument("--seconds", type=float, help="Optional bounded acceptance session")
    p.add_argument(
        "--stop-confirmation-shadow", action="store_true", help="Compare stops in an isolated shadow ledger"
    )
    p = commands.add_parser("paper-health", help="Read-only probe; exit 1 when unhealthy")
    p.add_argument("--run-id", required=True)
    p = commands.add_parser(
        "settlement-recovery", help="Inspect or explicitly reconcile a quarantined settlement"
    )
    p.add_argument("--run-id", required=True)
    p.add_argument("--market", required=True)
    p.add_argument("--evidence-id", help="Retained final REST evidence to review")
    p.add_argument("--confirm", help="Exact evidence hash from the preview")
    p.add_argument("--reason", help="Required operator review explanation")
    commands.add_parser("discover")
    for name in ("collect", "paper"):
        p = commands.add_parser(name)
        p.add_argument("--seconds", type=float)
        if name == "paper":
            p.add_argument("--resume", help="Restore an atomic paper checkpoint")
    p = commands.add_parser("demo")
    p.add_argument("--output", default="data/synthetic.jsonl")
    p = commands.add_parser("backtest")
    p.add_argument("input", nargs="+")
    p.add_argument("--parent-run")
    p = commands.add_parser("audit")
    p.add_argument("input", nargs="+")
    p = commands.add_parser("analytics")
    p.add_argument("--mode", choices=["PAPER", "BACKTEST", "LIVE"], default="PAPER")
    p.add_argument("--run")
    p.add_argument("--archive", action="store_true", help="Read retired strategy results only")
    p = commands.add_parser("export")
    p.add_argument("opportunity_id")
    p.add_argument("--output", default="data/trade_packets")
    p = commands.add_parser("dashboard")
    dashboard_mode = p.add_mutually_exclusive_group()
    dashboard_mode.add_argument(
        "--no-collect", action="store_true", help="Serve the dashboard without starting collection"
    )
    dashboard_mode.add_argument(
        "--observe-only", action="store_true", help="Collect and evaluate without simulated orders"
    )
    p.add_argument(
        "--run-id", default="dashboard-paper", help="Named Settlement Edge PAPER run to start or resume"
    )
    p.add_argument("--port", type=int, default=8000)
    p = commands.add_parser("walk-forward")
    p.add_argument("manifest")
    commands.add_parser("halt")
    p = commands.add_parser("reference-history")
    p.add_argument("--timestamp", required=True)
    p.add_argument("--output", required=True)
    args = parser.parse_args()
    settings = Settings.env()
    settings.guard()
    if args.command == "settlement-recovery":
        from .recovery import recover_settlement

        store = Store(args.database or settings.database_url)
        try:
            print(
                dumps(
                    recover_settlement(
                        store,
                        args.run_id,
                        args.market,
                        evidence_id=args.evidence_id,
                        confirm=args.confirm,
                        reason=args.reason,
                    )
                )
            )
        finally:
            store.engine.dispose()
        return
    saved_config = Path(settings.data_dir) / "strategy.json"
    config = Strategy.load(args.config or (saved_config if saved_config.exists() else None))
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(), format="%(message)s")
    if args.command == "config":
        print(dumps(asdict(config)))
        return
    if args.command == "demo":
        from .demo import generate

        print(generate(args.output))
        return
    if args.command == "halt":
        Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
        (Path(settings.data_dir) / "HALT").touch()
        print("Kill switch set. Collector cancels paper orders and blocks new entries.")
        return
    if args.command in ("discover", "reference-history"):

        async def api():
            client = KalshiClient(settings)
            try:
                if args.command == "discover":
                    series, raws = await client.discover()
                    rows = []
                    for raw in raws:
                        try:
                            m = parse_market(raw, series)
                            rows.append(
                                dict(
                                    ticker=m.ticker,
                                    close_time=m.close_time,
                                    status=m.status,
                                    spec=asdict(m.spec),
                                    exchange_index=m.exchange_index,
                                    validation="VALID",
                                )
                            )
                        except (ValueError, KeyError) as exc:
                            rows.append(dict(ticker=raw["ticker"], validation="BLOCKED", reason=str(exc)))
                    print(dumps(rows))
                else:
                    data = await client.get(
                        "cfbenchmarks/history/values",
                        dict(id="BRTI", timespan="HOUR", timestamp=args.timestamp),
                        True,
                    )
                    target = Path(args.output)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with target.open("x") as f:
                        f.write(dumps(data) + "\n")
                    print(target)
            finally:
                await client.close()

        asyncio.run(api())
        return
    store = Store(args.database or settings.database_url)
    if args.command == "init-db":
        print("Database initialized")
    elif args.command == "paper-service":
        from .operation import serve

        print(
            asyncio.run(
                serve(
                    settings,
                    config,
                    store,
                    args.run_id,
                    int(args.min_free_gb * 1024**3),
                    args.seconds,
                    stop_confirmation_shadow=args.stop_confirmation_shadow,
                )
            )
        )
    elif args.command == "paper-health":
        from .operation import health

        result = health(store, args.run_id)
        print(dumps(result))
        raise SystemExit(0 if result["healthy"] else 1)
    elif args.command in ("collect", "paper"):
        from .runner import collect

        try:
            print(
                asyncio.run(
                    collect(
                        settings,
                        config,
                        store,
                        args.command == "paper",
                        args.seconds,
                        getattr(args, "resume", None),
                    )
                )
            )
        except KeyboardInterrupt:
            print("Stopped; raw journal retained")
    elif args.command == "backtest":
        from .research import replay_files

        run = replay_files(args.input, config, store, args.parent_run)
        print(dumps(metrics(store, "BACKTEST", run)))
    elif args.command == "audit":
        from .audit import audit_files

        print(dumps(audit_files(args.input)))
    elif args.command == "walk-forward":
        from .research import walk_forward

        print(dumps(walk_forward(args.manifest, store)))
    elif args.command == "analytics":
        print(dumps(metrics(store, args.mode, args.run, scope="archive" if args.archive else "settlement")))
    elif args.command == "export":
        print(export_packet(store, args.opportunity_id, args.output))
    elif args.command == "dashboard":
        import uvicorn

        from .dashboard import create_app

        app = create_app(
            store,
            collect_live=not args.no_collect,
            settings=settings,
            config=config,
            paper_execution=not args.observe_only,
            run_id=args.run_id,
        )
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=args.port,
                timeout_graceful_shutdown=5,
            )
        )
        app.state.shutdown_server = lambda: setattr(server, "should_exit", True)
        server.run()


if __name__ == "__main__":
    main()
