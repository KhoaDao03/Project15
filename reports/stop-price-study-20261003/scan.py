import json,gzip,collections,concurrent.futures,time,bisect
from pathlib import Path
P=Path(__file__).parent

def scan(asset):
 trades={t:r for t,r in json.loads((P/'trades.json').read_text()).items() if r['asset']==asset};points=collections.defaultdict(dict);errors=[];n=0
 intervals=sorted((r['start']-5,r['close']+2) for r in trades.values());starts=[x[0] for x in intervals]
 for mf in sorted(Path('research-logs',asset).glob('*/manifest.json')):
  m=json.loads(mf.read_text())
  if m.get('producer')!='collector':continue
  fs=sorted(mf.parent.glob('*.jsonl.gz'));file_starts=[int(f.name.split('-')[1].split('.')[0])/1e9 for f in fs]
  for i,f in enumerate(fs):
   lo=file_starts[i]-5;hi=file_starts[i+1]+5 if i+1<len(fs) else f.stat().st_mtime+5
   k=bisect.bisect_right(starts,hi)
   if not any(end>=lo for st,end in intervals[max(0,k-5):k]):continue
   try:
    with gzip.open(f,'rb') as g:
     for line in g:
      if b'"kind":"book"' not in line and b'"kind": "book"' not in line:continue
      d=json.loads(line);b=d['body'];t=b.get('market');r=trades.get(t)
      if not r:continue
      at=d.get('captured_at') or d.get('received_at')
      if not r['start']-5<=at<=r['close'] or not b.get('valid') or not b.get('fresh'):continue
      levels=b.get(r['side']+'_levels') or b.get(r['side']+'_top5') or []
      levels=sorted(((float(x),float(q)) for x,q in levels if float(q)>0),reverse=True)
      # Retain only the depth needed to sell the original position.
      kept=[];qty=0
      for x,q in levels:
       kept.append((x,q));qty+=q
       if qty>=r['qty']:break
      points[t][at]=[at,levels[0][0] if levels else 0.0,kept]
   except (EOFError,OSError,ValueError) as e:errors.append({'file':str(f),'error':str(e)})
   n+=1
   if n%100==0:print(asset,n,'segments',len(points),'trades',flush=True)
 (P/f'{asset}-paths.json').write_text(json.dumps({t:sorted(p.values()) for t,p in points.items()}));print(asset,'DONE',n,len(points),flush=True)
 return {'asset':asset,'files':n,'trades_with_points':len(points),'errors':errors}
if __name__=='__main__':
 with concurrent.futures.ProcessPoolExecutor(max_workers=2) as pool:results=list(pool.map(scan,['BTC','ETH','SOL','XRP','DOGE','BNB','HYPE']))
 (P/'scan-coverage.json').write_text(json.dumps(results,indent=2))
