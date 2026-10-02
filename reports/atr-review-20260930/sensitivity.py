import json,math,collections,statistics,csv,pathlib,gzip
from btc15.strategies.settlement_edge.bleep import capped_confidence,safety_clamp
P=pathlib.Path('reports/atr-review-20260930'); active=json.loads((P/'active-parameters.json').read_text()); candidates={'ETH':[.95,.9,.85,.8,.75],'XRP':[1.25,1.15,1.1,1,.9],'DOGE':[1.1,1.05,1,.95,.9],'BNB':[1.25,1.15,1.1,1,.9],'HYPE':[1.15,1.1,1.05,1,.9]}
def prob(r,m):
 p=r['probability']
 if p['remaining_samples']==0:return p['p_yes']
 z=math.copysign(p['safety_ratio'],p['base_p_up']-.5)*p['sigma_multiplier']/m
 t=1/(1+.2316419*abs(z));tail=math.exp(-.5*z*z)/math.sqrt(2*math.pi)*t*(.319381539+t*(-.356563782+t*(1.781477937+t*(-1.821255978+t*1.330274429))))
 base=1-tail if z>=0 else tail;indicator=min(.95,max(.05,.5+.12*p['lean']));up=min(.98,max(.02,(1-p['indicator_weight'])*base+p['indicator_weight']*indicator))
 if p['safety_clamp_enabled']:up=safety_clamp(up,abs(z),.78)
 # Crypto contracts in these logs settle YES for up. Validate reconstruction below.
 return up if r['settlement_spec']['comparison_operator'] in ['>=','>'] else 1-up
stats=collections.defaultdict(collections.Counter);first=collections.defaultdict(dict);eligible=collections.defaultdict(set);capblocked=collections.defaultdict(set);seen=set(); bad=[]
with ((P/'eligible.jsonl').open() if (P/'eligible.jsonl').exists() else gzip.open(P/'eligible.jsonl.gz','rt')) as f:
 for line in f:
  r=json.loads(line);a=r['asset'];p=r['probability'];side=r['side'];b=r['book'];key=(a,r['market'],int(r['t']))
  if key in seen:continue
  seen.add(key);stats[a]['snapshots']+=1
  try:
   baseline=prob(r,p['sigma_multiplier']);err=abs(baseline-p['p_yes'])
   if err>1e-8:raise ValueError(f'error={err}')
   bid=b[side+'_bid'];ask=b[side+'_ask'];cap=capped_confidence(1.,bid,ask,a)
   if cap is None or not .8<=ask<=.96:continue
  except (KeyError,TypeError,ValueError) as e:
   bad.append((a,r['market'],str(e)));continue
  stats[a]['validated']+=1;eligible[a].add(r['market']);threshold=active[a]['early'] if r['seconds_remaining']>420 else .83
  if cap<threshold-1e-12:stats[a]['cap_blocked']+=1;capblocked[a].add(r['market'])
  if p.get('atr_source')=='reference_price_floor' or p.get('atr_floor_applied'):stats[a]['floor_atr']+=1
  for m in candidates[a]:
   py=prob(r,m);conf=capped_confidence(py if side=='yes' else 1-py,bid,ask,a)
   if conf<threshold-1e-12:continue
   k=(a,m);stats[k]['passing_snapshots']+=1;prev=first[k].get(r['market'])
   if prev is None or r['t']<prev['t']:first[k][r['market']]=dict(t=r['t'],ask=ask,side=side,remaining=r['seconds_remaining'],source_atr=p['sigma_multiplier'],atr_source=p.get('atr_source'),session=r['session'])
rows=[];detail=[]
for a in candidates:
 base=first[(a,candidates[a][0])]
 for m in candidates[a]:
  cand=first[(a,m)];pairs=[(b,cand[k]) for k,b in base.items() if k in cand and b['side']==cand[k]['side']];diff=[100*(b['ask']-v['ask']) for b,v in pairs];earlier=[b['t']-v['t'] for b,v in pairs]; new=set(cand)-set(base)
  row=dict(asset=a,atr=m,eligible_markets=len(eligible[a]),passing_markets=len(cand),added_markets=len(new),paired_same_side=len(pairs),cheaper=sum(v>1e-6 for v in diff),same_price=sum(abs(v)<=1e-6 for v in diff),dearer=sum(v< -1e-6 for v in diff),mean_paired_saving_cents=statistics.mean(diff) if diff else None,mean_paired_seconds_earlier=statistics.mean(earlier) if earlier else None,mean_first_ask=statistics.mean(r['ask'] for r in cand.values()) if cand else None,mean_added_ask=statistics.mean(cand[k]['ask'] for k in new) if new else None,passing_snapshots=stats[(a,m)]['passing_snapshots']);rows.append(row)
  for market,v in cand.items():detail.append(dict(asset=a,atr=m,market=market,**v))
for name,data in [('sensitivity.csv',rows),('first-observed-opportunities.csv',detail)]:
 with (P/name).open('w') as f:
  w=csv.DictWriter(f,fieldnames=list(data[0]));w.writeheader();w.writerows(data)
meta={a:{**stats[a], 'eligible_markets':len(eligible[a]),'cap_blocked_markets':len(capblocked[a])} for a in candidates};meta['reconstruction_errors']=bad
(P/'sensitivity-quality.json').write_text(json.dumps(meta,indent=2));print(json.dumps(meta,indent=2));print(json.dumps(rows,indent=2))
