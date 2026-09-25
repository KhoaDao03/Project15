"""Local research logging switches, shared by collectors and executor recorders."""

import argparse
import fcntl
import json
import os
import tempfile
from pathlib import Path

from .assets import ASSETS

GROUPS = {
    "crypto": ("BTC", "ETH", "SOL", "XRP"),
    "commodities": ("GOLD", "SILVER", "WTI"),
    "all": tuple(ASSETS),
}


def read_control(root):
    try:
        value = json.loads((Path(root) / "logging-control.json").read_text())
    except FileNotFoundError:
        return {}
    if not isinstance(value, dict) or any(k not in ASSETS or type(v) is not bool for k, v in value.items()):
        raise ValueError("Logging controls must map supported assets to true/false")
    return value


def set_logging(root, assets, enabled):
    assets = tuple(assets)
    if not assets or any(a not in ASSETS for a in assets) or type(enabled) is not bool:
        raise ValueError("Choose supported assets and a boolean logging state")
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".logging-control.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = read_control(root)
        state.update(dict.fromkeys(assets, enabled))
        # Concurrent commands merge under the lock; readers see one complete file.
        fd, name = tempfile.mkstemp(prefix=".logging-control-", dir=root)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(state, stream, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, root / "logging-control.json")
        finally:
            Path(name).unlink(missing_ok=True)
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "stop", "status"))
    parser.add_argument(
        "markets", nargs="*", help="BTC ETH SOL XRP GOLD SILVER WTI, crypto, commodities, all"
    )
    parser.add_argument(
        "--root", type=Path, default=Path(os.environ.get("BTC15_RESEARCH_LOG_DIR", "research-logs"))
    )
    args = parser.parse_args()
    assets = []
    for name in args.markets:
        if name.lower() in GROUPS:
            assets.extend(GROUPS[name.lower()])
        elif name.upper() in ASSETS:
            assets.append(name.upper())
        else:
            parser.error("Unknown market or group: " + name)
    if args.action != "status" and not assets:
        parser.error("Specify markets or a group; no implicit all-market changes")
    try:
        state = (
            read_control(args.root)
            if args.action == "status"
            else set_logging(args.root, assets, args.action == "start")
        )
    except (OSError, ValueError) as exc:
        parser.exit(1, str(exc) + "\n")
    print(
        json.dumps({a: "started" if state.get(a, True) else "stopped" for a in (assets or ASSETS)}, indent=2)
    )
    print(
        "Requested state only. Enabled recorders apply changes within about one second; inspect recorder status for acknowledgement."
    )


if __name__ == "__main__":
    main()
