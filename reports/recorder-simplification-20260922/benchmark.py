import importlib.util,json,statistics,tempfile,time
from pathlib import Path
from btc15.config import Strategy
from btc15.domain import dumps
from btc15.research_log import ResearchLog
spec=importlib.util.spec_from_file_location('btc15.research_log_before',Path(__file__).with_name('research_log.before.py'))
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
payload=dict(type='orderbook_delta',sid=7,seq=1,msg=dict(market_ticker='KXBTC15M-TEST',market_id='market-id',side='yes',price_dollars='0.7200',delta_fp='10.25',ts='2026-09-22T06:00:00.123Z'))
frames=[]
for i in range(12000):
 p={**payload,'seq':i+1};frames.append((dict(id=str(i),received=100+i/1000,monotonic_ns=i,connection_id='connection',payload=dumps(p)),p))
results=[]
for trial in range(3):
 for label,cls in [('before',module.ResearchLog),('after',ResearchLog)]:
  with tempfile.TemporaryDirectory() as directory:
   log=cls(directory,'BTC','benchmark',Strategy(),min_free_bytes=0)
   while not (log.directory/'manifest.json').exists():time.sleep(.001)
   cpu=time.process_time();start=time.perf_counter()
   for i,(row,p) in enumerate(frames):
    log.capture_input(row,p)
    log.emit('input_processed',dict(input_id=row['id'],valid=True,started_at=row['received'],finished_monotonic_ns=i))
    if i%250==0:
     while log.queue.qsize()>1000:time.sleep(.001)
   log.close();elapsed=time.perf_counter()-start;cpu=time.process_time()-cpu
   s=log.status();assert s['dropped']==0 and s['written']==24000 and s['error'] is None,s
   r=dict(version=label,trial=trial,cpu_seconds=cpu,wall_seconds=elapsed,events_per_cpu_second=12000/cpu,recorded=s['written'],drops=s['dropped']);results.append(r);print(json.dumps(r),flush=True)
summary={k:statistics.median(r['cpu_seconds'] for r in results if r['version']==k) for k in ['before','after']}
summary['cpu_reduction_percent']=100*(1-summary['after']/summary['before']);print(summary,flush=True)
Path(__file__).with_name('benchmark.json').write_text(json.dumps(dict(runs=results,summary=summary),indent=2))
