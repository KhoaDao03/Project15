import json,gzip,pathlib,collections,time
P=pathlib.Path('reports/atr-review-20260930');assets=['ETH','XRP','DOGE','BNB','HYPE']; summary=[]
with (P/'eligible.jsonl').open('w') as out:
 for a in assets:
  for d in sorted((pathlib.Path('research-logs')/a).iterdir()):
   m=json.loads((d/'manifest.json').read_text())
   if m['producer']!='collector':continue
   status=json.loads((d/'status.json').read_text());n=0;kept=0;lo=None;hi=None;seen=set();markets=set()
   for f in sorted(d.glob('*.jsonl.gz')):
    with gzip.open(f,'rb') as z:
     for line in z:
      if b'"evaluation"' not in line:continue
      r=json.loads(line)
      if r['kind']!='evaluation':continue
      b=r['body'];t=b.get('snapshot_timestamp',r.get('received_at')); n+=1;lo=t if lo is None else min(lo,t);hi=t if hi is None else max(hi,t)
      market=b.get('ticker') or b.get('market');markets.add(market)
      if not 1<(b.get('seconds_remaining') or 0)<=600:continue
      if {v['code'] for v in b.get('reasons',[])}-{'MIN_PROBABILITY','SPREAD','ENTRY_WINDOW'}:continue
      key=(market,int(t))
      if key in seen:continue
      seen.add(key)
      row={k:b.get(k) for k in ['side','reasons','seconds_remaining','pre_market_cap_probability','conservative_probability','market_cap_applied','expected_fill_price','signed_distance','probability','quality','book','market_close_timestamp','lead','settlement_spec']};row.update(asset=a,t=t,market=market,session=d.name,capture_seq=r.get('capture_seq'));out.write(json.dumps(row)+'\n');kept+=1
   summary.append(dict(asset=a,session=d.name,evaluations=n,eligible=kept,markets=len(markets),first=lo,last=hi,status=status,manifest=m));print(a,d.name[:16],n,kept,flush=True)
(P/'all-capture-summary.json').write_text(json.dumps(summary,indent=2))
