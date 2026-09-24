"""Offline recorder-only benchmark. No exchange connections or live data writes."""
import json
import statistics
import tempfile
import time
from pathlib import Path

from btc15.config import Strategy
from btc15.domain import dumps
from btc15.research_log import ResearchLog

frames = []
for i in range(20000):
    kind = 'trade' if i % 20 == 0 else 'orderbook_delta'
    p = dict(type=kind, sid=1, seq=i + 1, msg=dict(market_ticker='KXBTC15M-TEST',
        side='yes', price_dollars='0.7200', delta_fp='10.25', count_fp='5.00'))
    frames.append((dict(id=str(i), received=100 + i / 1000, connection_id='c', payload=dumps(p)), p))
results = []
for trial in range(3):
    for mode in ('full', 'sampled'):
        with tempfile.TemporaryDirectory() as root:
            log = ResearchLog(root, 'BTC', 'benchmark', Strategy(), min_free_bytes=0, capture_mode=mode)
            while not (log.directory / 'manifest.json').exists():
                time.sleep(.001)
            start = time.process_time()
            for i, (row, payload) in enumerate(frames):
                assert log.capture_input(row, payload)
                assert log.emit('input_processed', dict(input_id=row['id'], valid=True))
                if i % 1000 == 0:
                    log.emit('book', dict(market='KXBTC15M-TEST',capture_mode=mode,
                        yes_levels=[[str(n / 1000), '10'] for n in range(1, 100)], no_levels=[['0.1','20']]))
                    log.emit('reference_sample', dict(source=i, received=i, price=50000, input_id=str(i)), i)
                    log.emit('model_calculation', dict(market='KXBTC15M-TEST'))
                    log.emit('decision_check', dict(market='KXBTC15M-TEST'))
                if i % 250 == 0:
                    while log.queue.qsize() > 1000:
                        time.sleep(.001)
            log.close()
            elapsed = time.process_time() - start
            status = log.status()
            assert status['dropped'] == 0 and status['error'] is None, status
            results.append(dict(mode=mode,trial=trial,cpu_seconds=elapsed,written=status['written'],
                sampled_out=status['sampled_out'],bytes=sum(p.stat().st_size for p in log.directory.glob('events*.gz'))))
            print(results[-1], flush=True)
summary = {mode:statistics.median(r['cpu_seconds'] for r in results if r['mode']==mode) for mode in ['full','sampled']}
summary['cpu_reduction_percent'] = 100 * (1-summary['sampled']/summary['full'])
Path(__file__).with_name('benchmark.json').write_text(json.dumps(dict(runs=results,summary=summary),indent=2))
print(summary)
