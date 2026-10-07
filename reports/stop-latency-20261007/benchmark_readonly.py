"""Measure cold versus warm stop metadata with real GETs and blocked writes.

Uses a temporary journal and synthetic exposure. The transport rejects EVERY
non-GET before network I/O. Does not start a collector or change live controls.
"""
import asyncio
import json
import sqlite3
import statistics
import tempfile
import time
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import httpx
from dotenv import dotenv_values

from btc15.api import KalshiClient
from btc15.config import Settings
from btc15.manual_trading import ManualOrder, ManualTrading

ROOT = Path('/root/Project15')

class ReadOnlyTransport(httpx.AsyncBaseTransport):
    def __init__(self):
        self.network = httpx.AsyncHTTPTransport(retries=0)
        self.blocked = 0
    async def handle_async_request(self, request):
        if request.method != 'GET':
            self.blocked += 1
            return httpx.Response(400, json={'error': {'message': 'BENCHMARK_WRITE_BLOCKED'}})
        return await self.network.handle_async_request(request)
    async def aclose(self):
        await self.network.aclose()

async def main():
    env = dotenv_values(ROOT / 'data/cloud/credentials.env')
    settings = Settings(api_key_id=env['KALSHI_API_KEY_ID'], private_key_path=env['KALSHI_PRIVATE_KEY_PATH'])
    client = KalshiClient(settings)
    await client.http.aclose()
    transport = ReadOnlyTransport()
    client.http = httpx.AsyncClient(base_url=settings.rest_url + '/', transport=transport, timeout=20)
    with sqlite3.connect('file:' + str(ROOT / 'data/cloud/manual-orders.sqlite') + '?mode=ro', uri=True) as db:
        controls = [json.loads(r[0]) for r in db.execute("SELECT body FROM live_controls WHERE json_extract(body,'$.close_time') > ? AND json_extract(body,'$.asset')='BTC'", (time.time()+120,))]
    ticker = min(controls, key=lambda c: c['close_time'])['ticker']
    samples = []
    try:
        with tempfile.TemporaryDirectory(prefix='stop-cache-benchmark-') as tmp:
            manual = ManualTrading(Path(tmp) / 'orders.sqlite', settings, client_factory=lambda _: client)
            await manual.market(client, ticker)
            cache = deepcopy(manual._stop_metadata)
            real_holdings = manual.holdings
            async def holdings(*args):
                await real_holdings(*args)
                return dict(yes='1', no='0')
            manual.holdings = holdings
            for iteration in range(5):
                for warm in ([False, True] if iteration % 2 == 0 else [True, False]):
                    manual._stop_metadata = deepcopy(cache) if warm else {}
                    order = ManualOrder(client_order_id=uuid4(), ticker=ticker, action='sell', side='yes', count=1, limit_cents=1, confirm='REAL_MONEY')
                    row = await manual.submit(order, authorize=lambda: None, reason='HARD_STOP')
                    assert row['message'].endswith('BENCHMARK_WRITE_BLOCKED')
                    assert row['timing']['metadata_cache_hit'] is warm
                    samples.append(dict(warm=warm, timing=row['timing']))
                    await asyncio.sleep(.3)
            assert transport.blocked == 10
    finally:
        await client.close()
    result = dict(ticker=ticker, blocked_writes=transport.blocked, samples=samples)
    result['summary'] = {name: dict(n=5, median_preflight_ms=statistics.median(s['timing']['preflight_ms'] for s in samples if s['warm'] == warm), read_counts=[len(s['timing']['reads']) for s in samples if s['warm'] == warm]) for name, warm in [('cold', False), ('warm', True)]}
    print(json.dumps(result, indent=2))

if __name__ == '__main__':
    asyncio.run(main())
