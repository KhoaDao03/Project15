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

(P/'features.json').write_text(json.dumps(rows,indent=2))
print('features',len(rows),'skipped',skipped)
