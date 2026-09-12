"""Stream-wiring checks for the persistent pause; all network inputs are mocked."""

import asyncio
import json
import time

import pytest
import test_position_management as management_tests
from test_collection import fake_client, fake_socket
from test_market_pause import metadata, quote, sells, setup, venue

from btc15 import runner
from btc15.config import Settings
from btc15.domain import D
from btc15.storage import read_events

scenario = management_tests.scenario


@pytest.mark.parametrize("paper", [False, True])
def test_collector_records_request_fence_before_discovery(
    store, config, tmp_path, raw, series, monkeypatch, paper
):
    fake_client(monkeypatch, raw, series)
    client_type = runner.KalshiClient
    discovered_at = []

    class Client(client_type):
        async def discover(self):
            discovered_at.append(time.time())
            await asyncio.sleep(0)
            return await super().discover()

    monkeypatch.setattr(runner, "KalshiClient", Client)
    fake_socket(monkeypatch, [])
    original = runner.Engine.ingest

    async def run():
        loop = asyncio.get_running_loop()
        stop = asyncio.Event()

        def observed(self, row):
            result = original(self, row)
            payload = json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"]
            if payload.get("type") == "metadata":
                loop.call_soon_threadsafe(stop.set)
            return result

        monkeypatch.setattr(runner.Engine, "ingest", observed)
        return await asyncio.wait_for(
            runner.collect(Settings(data_dir=str(tmp_path)), config, store, paper=paper, stop_event=stop),
            timeout=15,
        )

    asyncio.run(run())
    suffix = "*.jsonl.gz" if paper else "*.jsonl"
    events = list(read_events(next((tmp_path / "raw").glob(suffix))))
    rows = [r for r in events if r["payload"]["type"] == "metadata"]
    assert len(rows) == 1 and len(discovered_at) == 1
    msg = rows[0]["payload"]["msg"]
    assert msg["source"] == "kalshi_rest"
    assert msg["request_started_at"] <= discovered_at[0] <= rows[0]["received"]
    assert store.writer_owner() is None


def test_sequence_gap_does_not_forget_a_market_pause(scenario, store, market, now):
    s = setup(scenario)
    venue(s, market, now)

    def sequenced(seq, when):
        s.clock[0] = when
        return s.e.ingest(
            dict(
                id=f"seq-{seq}",
                received=when,
                monotonic_ns=int(when * 1e9),
                connection_id="VENUE",
                payload=dict(type="heartbeat", sid=42, seq=seq, msg={}),
            )
        )

    assert sequenced(1, now + 0.1)
    assert not sequenced(3, now + 0.2)
    quote(s, market, now + 0.3)
    assert market.ticker in s.e.executor.venue_pauses
    assert market.ticker in store.load_checkpoint(s.e.run_id)["venue_pauses"]
    assert not s.e.executor.orders and not store.list(kind="fill")


def test_paused_quotes_do_not_manufacture_exit_replenishment(scenario, store, market, series, now):
    s = setup(scenario, buy=True)
    quote(s, market, now + 1, ".50", depth=".10")
    quote(s, market, now + 2, ".50", depth=".10")
    key = (market.ticker, D(".50"))
    assert s.e.executor.exit_consumed[key] == D(".10")
    venue(s, market, now + 3)
    quote(s, market, now + 4, ".50", depth="0")
    quote(s, market, now + 5, ".50", depth=".10")
    assert s.e.executor.exit_consumed[key] == D(".10")
    assert sum(D(r["body"]["quantity"]) for r in sells(store)) == D(".10")
    metadata(s, market, series, now + 6, request_started_at=now + 5.5)
    quote(s, market, now + 7, ".50", depth=".10")
    assert sum(D(r["body"]["quantity"]) for r in sells(store)) == D(".10")
    # Only continuously observed post-resumption depletion/reappearance credits volume.
    quote(s, market, now + 8, ".50", depth="0")
    quote(s, market, now + 9, ".50", depth=".30")
    assert sum(D(r["body"]["quantity"]) for r in sells(store)) == D(".40")
    assert len(store.list(kind="trade_result")) == 1
    assert not s.e.executor.positions and not s.e.executor.risk.reserved
