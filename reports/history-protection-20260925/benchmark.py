"""Synthetic ASGI burst comparison; never accesses private/trading services."""
import argparse
import asyncio
import importlib.util
import json
import resource
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import httpx

parser = argparse.ArgumentParser()
parser.add_argument("source", type=Path)
args = parser.parse_args()
spec = importlib.util.spec_from_file_location("benchmark_site", args.source)
site = importlib.util.module_from_spec(spec)
spec.loader.exec_module(site)
decodes = []
network_attempts = []


def audit(event, arguments):
    if event == "socket.connect":
        network_attempts.append(str(arguments[1]))
        raise AssertionError("Benchmark must never connect to a service")


def decode(raw):
    decodes.append(len(raw))
    return json.loads(raw)


site.json = SimpleNamespace(loads=decode)
sys.addaudithook(audit)


async def run(directory):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=site.create_app(directory)),
                                base_url="http://synthetic") as client:
        started = time.perf_counter()
        cpu = time.process_time()
        responses = await asyncio.gather(*(
            client.get(f"/api/history/BTC?offset={i * 25}&limit=25",
                       headers={"X-Forwarded-For": f"192.0.2.{i}"}) for i in range(100)
        ))
        result = dict(statuses=dict(Counter(r.status_code for r in responses)),
                      seconds=round(time.perf_counter() - started, 3),
                      cpu_seconds=round(time.process_time() - cpu, 3),
                      history_decodes=len(decodes), decoded_bytes=sum(decodes))
        assert all(len(r.json()["rows"]) <= 25 for r in responses if r.status_code == 200)
        result["retry_after_on_every_429"] = all(r.headers.get("retry-after") == "1"
                                                 for r in responses if r.status_code == 429)
        # Allow the new limiter to refill, then check normal browsing recovers.
        await asyncio.sleep(1)
        result["recovery_status"] = (await client.get("/api/history/BTC")).status_code
        result["snapshot_status"] = (await client.get("/api/view")).status_code
        result["network_connection_attempts"] = network_attempts
        result["peak_rss_kib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return result


with tempfile.TemporaryDirectory(prefix="public-history-benchmark-") as temporary:
    directory = Path(temporary)
    rows = [dict(market="SYNTHETIC-" + str(i), timestamp=i, status="CLOSED", side="yes",
                 bought=1, entry=.8, exit=.9, fees=.01, net_pnl=.09) for i in range(10_000)]
    (directory / "history-BTC.json").write_text(json.dumps(dict(
        rows=rows, total=len(rows), updated_at=time.time(), stale=False)))
    (directory / "view.json").write_text(json.dumps(dict(assets=[], updated_at=time.time())))
    print(json.dumps(asyncio.run(run(directory)), indent=2))
