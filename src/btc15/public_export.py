"""Publish sanitized public files on a fixed schedule, independent of visitor traffic."""

import argparse
import asyncio
import json
import logging
import os
import tempfile
import time
from pathlib import Path

import httpx

from .public_dashboard import public_trade, read_snapshot
from .public_site import ASSETS

LOG = logging.getLogger(__name__)


def publish(directory, name, data):
    """Replace a complete file atomically; the website group can read, never write."""
    descriptor, temporary = tempfile.mkstemp(dir=directory, prefix=".publish-")
    try:
        with os.fdopen(descriptor, "w") as output:
            json.dump(data, output, allow_nan=False, separators=(",", ":"))
            output.flush()
            os.fchmod(output.fileno(), 0o640)
        os.replace(temporary, directory / name)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


async def read_history(client, asset, run_id):
    rows = []
    stale = False
    # Bound private work independently of arbitrary public offset/limit requests.
    # Never replace a complete previous history with a silently truncated history.
    async with asyncio.timeout(30):
        for offset in range(0, 50_000, 500):
            response = await client.get(f"/assets/{asset}/api/trades", params=dict(
                run_id=run_id, mode="PAPER", include_open="true", offset=offset, limit=500,
            ), timeout=15)
            response.raise_for_status()
            data = response.json()
            batch = data["rows"]
            if not isinstance(batch, list) or len(batch) > 500 or type(data["total"]) is not int:
                raise ValueError("Invalid history page")
            rows.extend(public_trade(row) for row in batch)
            stale = stale or bool(data.get("stale"))
            if offset + len(batch) >= data["total"]:
                return dict(rows=rows, total=len(rows), stale=stale, updated_at=time.time())
            if not batch:
                raise ValueError("Incomplete history")
    raise ValueError("History exceeds publishing budget")


async def export_history(client, directory):
    response = await client.get("/api/fleet")
    response.raise_for_status()
    for row in response.json()["assets"]:
        asset = row["asset"]
        if asset not in ASSETS:
            continue
        try:
            data = await read_history(client, asset, row["run_id"])
            publish(directory, f"history-{asset}.json", data)
        except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError, TimeoutError):
            LOG.warning("Public history refresh failed for %s; retaining previous data", asset)


async def run(directory, admin_port):
    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{admin_port}", timeout=3,
                                 follow_redirects=False, trust_env=False) as client:
        async def snapshots():
            while True:
                try:
                    publish(directory, "view.json", await read_snapshot(client))
                except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError):
                    LOG.warning("Public snapshot refresh failed; retaining previous data")
                await asyncio.sleep(5)

        async def histories():
            while True:
                try:
                    await export_history(client, directory)
                except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError):
                    LOG.warning("Public histories unavailable; retaining previous data")
                await asyncio.sleep(60)

        await asyncio.gather(snapshots(), histories())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--admin-port", type=int, default=8000)
    args = parser.parse_args()
    if not args.output.is_dir() or not 1 <= args.admin_port <= 65535:
        parser.error("Existing output directory and valid private port required")
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(run(args.output, args.admin_port))
