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
    commands.add_parser("discover")
    for name in ("collect", "paper"):
        p = commands.add_parser(name)
        p.add_argument("--seconds", type=float)
    p = commands.add_parser("demo")
    p.add_argument("--output", default="data/synthetic.jsonl")
    p = commands.add_parser("backtest")
    p.add_argument("input")
    p.add_argument("--parent-run")
    p = commands.add_parser("analytics")
    p.add_argument("--mode", choices=["PAPER", "BACKTEST", "LIVE"], default="PAPER")
    p.add_argument("--run")
    p = commands.add_parser("export")
    p.add_argument("opportunity_id")
    p.add_argument("--output", default="data/trade_packets")
    p = commands.add_parser("dashboard")
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
    config = Strategy.load(args.config)
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
    elif args.command in ("collect", "paper"):
        from .runner import collect

        try:
            print(asyncio.run(collect(settings, config, store, args.command == "paper", args.seconds)))
        except KeyboardInterrupt:
            print("Stopped; raw journal retained")
    elif args.command == "backtest":
        from .runner import backtest

        run = backtest(args.input, config, store, args.parent_run)
        print(dumps(metrics(store, "BACKTEST", run)))
    elif args.command == "walk-forward":
        from .research import walk_forward

        print(dumps(walk_forward(args.manifest, store)))
    elif args.command == "analytics":
        print(dumps(metrics(store, args.mode, args.run)))
    elif args.command == "export":
        print(export_packet(store, args.opportunity_id, args.output))
    elif args.command == "dashboard":
        import uvicorn

        from .dashboard import create_app

        uvicorn.run(create_app(store), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
