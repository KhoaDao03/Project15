"""Freeze a fresh live-only deployment; never start services or copy credentials."""

import argparse
import json
from pathlib import Path

from btc15.config import Strategy


def prepare(output):
    root = Path(__file__).resolve().parents[1]
    configs = {}
    for asset in ("BTC", "ETH", "SOL", "XRP"):
        name = "active" if asset == "BTC" else asset.lower()
        path = root / "config" / f"settlement-edge-{name}-paper.json"
        Strategy.load(path)
        configs[asset] = path.read_text()
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    output.chmod(0o700)
    manifest = []
    for asset, contents in configs.items():
        (output / f"{asset}.json").write_text(contents)
        manifest.append(
            dict(
                asset=asset,
                config=f"{asset}.json",
                data_dir=asset,
                run_id=f"{asset}-live-signals",
                live_only=True,
            )
        )
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return output / "manifest.json"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="data/cloud")
    print(prepare(parser.parse_args().output))
