"""Isolated stop benchmark: real GETs; ALL non-GET requests blocked before network transport.

Uses synthetic holdings in a temporary journal. Never sends a real order.
Run from the project root with its venv. No credentials are written to results.
"""
import asyncio
import importlib.util
import json
import sqlite3
import statistics
import tempfile
import time
from pathlib import Path
from uuid import uuid4

import httpx
from dotenv import dotenv_values

from btc15.api import KalshiClient
from btc15.config import Settings, Strategy
from btc15.live_automation import LiveAutomation
from btc15.manual_trading import ManualTrading

ROOT = Path('/root/Project15')


def old_class(module, path, name):
    spec = importlib.util.spec_from_file_location('btc15.' + module, path)
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return getattr(obj, name)


class GetOnlyTransport(httpx.AsyncBaseTransport):
    def __init__(self):
        self.network = httpx.AsyncHTTPTransport(retries=0)
        self.gets = []
        self.blocked = []

    async def handle_async_request(self, request):
        if request.method != 'GET':
            self.blocked.append(dict(method=request.method, path=request.url.path, at=time.time()))
            return httpx.Response(400, json={'error': {'message': 'Benchmark: outbound write blocked'}})
        self.gets.append(request.url.path)
        return await self.network.handle_async_request(request)

    async def aclose(self):
        await self.network.aclose()


async def main():
    old_live = old_class('_stop_benchmark_old_live', '/tmp/project15-stop-baseline/btc15/live_automation.py', 'LiveAutomation')
    old_manual = old_class('_stop_benchmark_old_manual', '/tmp/project15-stop-baseline/btc15/manual_trading.py', 'ManualTrading')
    credentials = dotenv_values(ROOT/'data/cloud/credentials.env')
    settings = Settings(api_key_id=credentials['KALSHI_API_KEY_ID'],
                        private_key_path=credentials['KALSHI_PRIVATE_KEY_PATH'])
    client = KalshiClient(settings)
    await client.http.aclose()
    transport = GetOnlyTransport()
    client.http = httpx.AsyncClient(base_url=settings.rest_url+'/', transport=transport, timeout=20)
    manifest = json.loads((ROOT/'data/cloud/manifest.json').read_text())
    member = next(m for m in manifest if m['asset']=='ETH')
    config = Strategy.load(ROOT/'data/cloud'/member['config'])
    with sqlite3.connect('file:'+str(ROOT/'data/cloud/manual-orders.sqlite')+'?mode=ro',uri=True) as db:
        controls = [json.loads(r[0]) for r in db.execute('SELECT body FROM live_controls')]
    control = min((c for c in controls if c['asset']=='ETH' and c['close_time']>time.time()+120),
                  key=lambda c:c['close_time'])
    ticker=control['ticker']
    class Store:
        def read_market_display(self, key=None):
            now=time.time()
            return dict(run_id=member['run_id'],connected=True,published_at=now,
                        markets=[dict(ticker=ticker,fresh=True,book_received=now,
                                      book=dict(yes_bid=.55,no_bid=.45))])
    samples=[]
    try:
        with tempfile.TemporaryDirectory(prefix='project15-stop-readonly-') as tmp:
            for iteration in range(5):
                for version, live_type, manual_type in [('before',old_live,old_manual),('after',LiveAutomation,ManualTrading)]:
                    path=Path(tmp)/f'{version}-{iteration}.sqlite'
                    manual=manual_type(path,settings,client_factory=lambda settings:client)
                    worker=live_type(manual,{'ETH':dict(config=config,run_id=member['run_id'])},{'ETH':Store()})
                    worker.running=True
                    local_control={k:v for k,v in control.items() if k not in ['exit_reason','stop_timing']}
                    local_control.update(enabled=False,paused=False)
                    worker.write(local_control)
                    seed=dict(id=str(uuid4()),origin='bot',state='complete',created_at=time.time(),updated_at=time.time(),
                              request=dict(ticker=ticker,action='buy',side='yes',count='10'),
                              exchange_order=dict(fill_count_fp='10.00'))
                    with manual.db() as db:db.execute('INSERT INTO manual_orders VALUES (?,?)',(seed['id'],json.dumps(seed)))
                    real_holdings=manual.holdings
                    async def synthetic_holdings(*args):
                        await real_holdings(*args)  # Measure the actual read, but use synthetic exposure locally.
                        return dict(yes='10',no='0')
                    manual.holdings=synthetic_holdings
                    reads=len(transport.gets); writes=len(transport.blocked)
                    started=time.time()
                    await worker.step_market(local_control)
                    assert len(transport.blocked)==writes+1, 'Did not reach the blocked submission boundary'
                    row=manual.rows()[0]
                    with manual.db() as db:
                        events=[json.loads(r[0]) for r in db.execute("SELECT body FROM execution_events WHERE kind='control_changed' ORDER BY seq")]
                    trigger=next(e['observed_at'] for e in events if e['body'].get('exit_reason')=='HARD_STOP')
                    sample=dict(version=version,iteration=iteration,get_count=len(transport.gets)-reads,
                                stop_commit_to_request_boundary_ms=(transport.blocked[-1]['at']-trigger)*1000,
                                invocation_to_request_boundary_ms=(transport.blocked[-1]['at']-started)*1000,
                                preflight_ms=row['timing'].get('preflight_ms'),reads=row['timing'].get('reads',[]))
                    samples.append(sample)
                    print(json.dumps({k:v for k,v in sample.items() if k!='reads'}),flush=True)
                    await asyncio.sleep(2)
    finally:
        await client.close()
    summary={}
    for version in ['before','after']:
        values=sorted(s['stop_commit_to_request_boundary_ms'] for s in samples if s['version']==version)
        summary[version]=dict(n=len(values),median_ms=statistics.median(values),min_ms=values[0],max_ms=values[-1])
    result=dict(method='Synthetic holdings and quote; real GETs; every non-GET blocked before network; no exchange acknowledgement measured',
                ticker=ticker,measured_at=time.time(),summary=summary,samples=samples,
                blocked_non_get_requests=len(transport.blocked),real_order_requests_sent=0)
    (ROOT/'reports/stop-latency-20260925/read-only-benchmark.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(summary,indent=2))


if __name__=='__main__':
    asyncio.run(main())
