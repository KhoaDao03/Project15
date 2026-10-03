import json,math,collections,csv,itertools
from pathlib import Path
P=Path(__file__).parent
trades={r['ticker']:r for r in json.loads((P/'trades-all.json').read_text())};checks=json.loads((P/'entry-checks.json').read_text());rows=[];skipped=[]
for t,x in checks.items():
 r=dict(trades[t]);b=x['record']['body'];d=(b.get('collector_record') or {}).get('body') or {};f=d.get('features') or {};p=d.get('probability') or {};book=((b.get('book_snapshot') or {}).get('market') or {}).get('book') or d.get('book') or {};side=r['side'];sign=1 if side=='yes' else -1
 if not d or d.get('side')!=side or (x['rank'][0] and d.get('decision_id')!=r['decision_id']):skipped.append(t);continue
 r['fill_remaining']=r['remaining'];r['remaining']=b['control']['close_time']-b['started_at'];r['planned_qty']=b['control']['contracts']
 r.update(exact_check_id=not x['rank'][0],check_delta=x['rank'][1],check_stage=b.get('stage'),config_hash=d.get('versions',{}).get('config'),entry_path=d.get('entry_path'),check_time=b.get('started_at'),source=x['file'])
 r.update(ask=book.get(side+'_ask'),bid=book.get(side+'_bid'),confidence=d.get('conservative_probability'),raw_probability=p.get('p_'+side),net_ev=d.get('net_ev'),quality=(d.get('quality') or {}).get('score'),lead_sigma=(d.get('lead') or {}).get('lead_sigma'),safety_ratio=p.get('safety_ratio'),sigma_multiplier=p.get('sigma_multiplier'),atr_floor=p.get('atr_floor_applied'),volatility_disagreement=f.get('volatility_disagreement'),model_age=b.get('started_at',0)-d.get('model_evaluated_at',b.get('started_at',0)),book_age=b.get('started_at',0)-((b.get('book_snapshot') or {}).get('market') or {}).get('book_received',b.get('started_at',0)),regime=f.get('regime'),queue_depth=(b.get('health') or {}).get('status',{}).get('queue_depth'))
 r['spread']=r['ask']-r['bid'] if r['ask'] is not None and r['bid'] is not None else None
 top=book.get(side+'_top5') or [];r['bid_top_qty']=top[0][1] if top else None;r['bid_top_cover']=r['bid_top_qty']/r['planned_qty'] if top else None;r['bid5_cover']=sum(x[1] for x in top)/r['planned_qty'] if top else None
 r['signed_imbalance']=sign*book['imbalance'] if book.get('imbalance') is not None else None
 for n in [5,15,30,60,180,300]:
  mom=f.get('momentum_'+str(n));rv=f.get('rv_'+str(n));r['momentum_'+str(n)]=sign*mom if mom is not None else None
  r['momentum_z_'+str(n)]=sign*mom/(rv*math.sqrt(n)) if mom is not None and rv and rv>0 else None
 for k in ['return_acceleration','consecutive_direction']:r[k]=sign*f[k] if f.get(k) is not None else None
 r['stoch']=p.get('stoch_k') if sign==1 else 100-p['stoch_k'] if p.get('stoch_k') is not None else None
 r['stoch_delta']=sign*(p['stoch_k']-p['stoch_k_previous']) if p.get('stoch_k') is not None and p.get('stoch_k_previous') is not None else None
 atr=p.get('atr');r['distance_atr']=sign*d['signed_distance']/atr if atr and d.get('signed_distance') is not None else None
 r['edge']=r['confidence']-r['ask'] if r['confidence'] is not None and r['ask'] is not None else None
 r['reference_ready']=(f.get('reference_readiness') or {}).get('atr_ready')
 rows.append(r)
rows.sort(key=lambda x:x['opened']);cut=rows[int(len(rows)*.7)]['opened'];n=len(rows)
for r in rows:r['split']='discovery' if r['opened']<cut else 'validation'
def summary(rs):return dict(n=len(rs),wins=sum(r['pnl']>0 for r in rs),losses=sum(r['pnl']<0 for r in rs),pnl=round(sum(r['pnl'] for r in rs),4))
def result(name,ids):
 rs=[rows[i] for i in ids];return dict(rule=name,**summary(rs),pnl_improvement=round(-sum(r['pnl'] for r in rs),4),loss_saved=round(-sum(r['pnl'] for r in rs if r['pnl']<0),4),win_forgone=round(sum(r['pnl'] for r in rs if r['pnl']>0),4),discovery=summary([r for r in rs if r['split']=='discovery']),validation=summary([r for r in rs if r['split']=='validation']),tickers=[r['ticker'] for r in rs])
# Fixed, coarse grids; no outcome-derived thresholds or ticker/time-specific rules.
grid={'remaining':[10,15,30,60,120,180,300,420,480,540], 'confidence':[.84,.85,.86,.88,.90,.92,.94,.96], 'raw_probability':[.85,.90,.95,.98], 'ask':[.82,.85,.88,.90,.92,.94,.95], 'spread':[.01,.02,.03,.04,.05,.08,.10], 'edge':[0,.01,.02,.03,.05], 'net_ev':[-.05,-.02,0,.01,.02,.03], 'quality':[85,90,95,99], 'lead_sigma':[.5,1,1.5,2,3,4], 'safety_ratio':[.5,1,1.5,2,3,4], 'distance_atr':[.5,1,1.5,2,3,4], 'bid_top_cover':[.1,.25,.5,1,2,5], 'bid5_cover':[.5,1,2,5,10], 'signed_imbalance':[-.8,-.5,-.25,0,.25,.5,.8], 'model_age':[.5,1,1.5,2], 'book_age':[.25,.5,1,2], 'volatility_disagreement':[.1,.25,.5,1,2], 'stoch':[10,20,30,50,70,80,90], 'stoch_delta':[-20,-10,0,10,20], 'consecutive_direction':[-5,-3,-1,0,1,3,5]}
for k in [5,15,30,60,180,300]:grid['momentum_z_'+str(k)]=[-2,-1,-.5,0,.5,1,2]
preds=[]
for k,vs in grid.items():
 for v in vs:
  for op in ['<','>']:
   ids={i for i,r in enumerate(rows) if isinstance(r.get(k),(int,float)) and math.isfinite(r[k]) and (r[k]<v-1e-10 if op=='<' else r[k]>v+1e-10)}
   if ids:preds.append((f'{k} {op} {v}',ids,k))
for k in ['atr_floor','reference_ready']:
 for val in [True,False]:
  ids={i for i,r in enumerate(rows) if r.get(k) is val}
  if ids:preds.append((f'{k} == {val}',ids,k))
base=[];zero=[];seen=set();tested=0;discovery_candidates=[];train_seen=set();train_ids={i for i,r in enumerate(rows) if r["split"]=="discovery"}
for scope in ['ALL']+sorted({r['asset'] for r in rows}):
 universe={i for i,r in enumerate(rows) if scope=='ALL' or r['asset']==scope};pp=[]
 for name,ids,k in preds:
  ids=ids&universe
  if len(ids)<2:continue
  rr=result(scope+': '+name,ids);base.append(rr);pp.append((name,ids,k));tested+=1
  if rr['losses']>=2 and rr['wins']==0:zero.append(rr)
  if rr['discovery']['losses']>=2 and rr['discovery']['wins']==0:discovery_candidates.append(rr)
 # Pairs limited to at most 30% exclusions each to keep readable and avoid broad duplicate gates.
 pp=[x for x in pp if len(x[1])<=max(15,len(universe)*.3)]
 for (a,aa,ka),(b,bb,kb) in itertools.combinations(pp,2):
  if ka==kb:continue
  ids=aa&bb
  if len(ids)<2:continue
  tested+=1
  train=ids&train_ids
  if len(train)>=2 and all(rows[i]['pnl']<0 for i in train):
   key=(scope,tuple(sorted(train)))
   if key not in train_seen:
    train_seen.add(key);discovery_candidates.append(result(scope+': '+a+' AND '+b,ids))
  if all(rows[i]['pnl']<0 for i in ids):
   key=(scope,tuple(sorted(ids)))
   if key in seen:continue
   seen.add(key);zero.append(result(scope+': '+a+' AND '+b,ids))
zero.sort(key=lambda x:(-x['losses'],len(x['rule'])))
discovery_candidates.sort(key=lambda x:(-x['discovery']['losses'],len(x['rule'])))
(P/'discovery-ranked.json').write_text(json.dumps(discovery_candidates,indent=2))
(P/'features.json').write_text(json.dumps(rows,indent=2));(P/'zero-win-candidates.json').write_text(json.dumps(zero,indent=2));(P/'single-filter-results.json').write_text(json.dumps(sorted(base,key=lambda x:-x['pnl_improvement']),indent=2))
with (P/'features.csv').open('w') as f:
 w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
report={'all_trades':summary(list(trades.values())),'matched':summary(rows),'exact_check_matches':sum(r['exact_check_id'] for r in rows),'first':rows[0]['entry_et'],'last':rows[-1]['entry_et'],'split_at':cut,'discovery':summary([r for r in rows if r['split']=='discovery']),'validation':summary([r for r in rows if r['split']=='validation']),'by_asset':{a:summary([r for r in rows if r['asset']==a]) for a in sorted({r['asset'] for r in rows})},'tested_combinations':tested,'zero_win_candidates':len(zero),'skipped':skipped}
(P/'summary.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2));print('TOP ZERO WIN CANDIDATES');print(json.dumps(zero[:12],indent=2))
