"""Standalone read-only commands; no imports of the trading application."""

import argparse
import asyncio
import json
import logging
import os
from dataclasses import asdict, replace
from pathlib import Path

from .schema import Config, encode


def main():
    parser = argparse.ArgumentParser(description="Independent read-only BTC settlement probability research")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--provider", choices=["options", "realized", "fixed"])
    sub = parser.add_subparsers(dest="command", required=True)
    synth = sub.add_parser("synthetic")
    synth.add_argument("output", type=Path)
    synth.add_argument("--markets", type=int, default=2)
    synth.add_argument("--step", type=int, default=15)
    live = sub.add_parser("record")
    live.add_argument("output", type=Path)
    live.add_argument("--duration", type=float, default=0, help="seconds; zero records until interrupted")
    live.add_argument("--rules-review", type=Path)
    rep = sub.add_parser("replay")
    rep.add_argument("input", type=Path)
    rep.add_argument("--output", type=Path, required=True)
    ev = sub.add_parser("evaluate")
    ev.add_argument("input", type=Path)
    ev.add_argument("--output", type=Path, required=True)
    dash = sub.add_parser("dashboard")
    dash.add_argument("input", type=Path)
    dash.add_argument("--port", type=int, default=8015)
    sub.add_parser("config")
    args = parser.parse_args()
    values = json.loads(args.config.read_text()) if args.config else {}
    if args.provider:
        values["provider"] = args.provider
    config = Config(**values)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.command == "synthetic":
        from .synthetic import create_recording

        if args.markets < 1 or args.step < 1:
            parser.error("markets and step must be positive")
        create_recording(args.output, config, markets=args.markets, step=args.step)
        print("Synthetic recording:", args.output)
    elif args.command == "record":
        from .live import record_live

        if args.duration < 0:
            parser.error("duration must be nonnegative")
        try:
            asyncio.run(
                record_live(
                    args.output,
                    config,
                    key_id=os.getenv("PROB_KALSHI_API_KEY_ID", ""),
                    key_path=os.getenv("PROB_KALSHI_PRIVATE_KEY_PATH", ""),
                    duration=args.duration,
                    reviews_path=args.rules_review,
                )
            )
        except KeyboardInterrupt:
            print("Read-only recording stopped; restart with the same configuration to resume.")
    elif args.command == "replay":
        from .recording import metadata, replay

        recorded_config = Config(**json.loads(metadata(args.input)["config"]))
        selected = (
            config
            if args.config
            else (replace(recorded_config, provider=args.provider) if args.provider else None)
        )
        with args.output.open("x") as output:
            for f in replay(args.input, selected):
                row = asdict(f)
                row["playback_mode"] = "replay"
                output.write(encode(row) + "\n")
        print("Replay forecasts:", args.output)
    elif args.command == "evaluate":
        from .evaluation import evaluate
        from .recording import metadata

        if not args.config:
            config = Config(**json.loads(metadata(args.input)["config"]))
        with args.output.open("x") as output:
            json.dump(evaluate(args.input, config), output, indent=2, allow_nan=False)
            output.write("\n")
        print("Evaluation:", args.output)
    elif args.command == "dashboard":
        import uvicorn

        from .dashboard import create_app

        uvicorn.run(create_app(args.input), host="127.0.0.1", port=args.port)
    else:
        print(json.dumps(asdict(config), indent=2))


if __name__ == "__main__":
    main()
