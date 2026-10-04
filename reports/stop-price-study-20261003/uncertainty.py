import json,collections,random,statistics,math
from pathlib import Path
P=Path(__file__).parent;rows=json.loads((P/'paired-trades.json').read_text());rng=random.Random(1503);out={}
for stop in [5,40,45,50,55,60,65,70,75,80]:
 clusters=collections.defaultdict(float)
 for r in rows:clusters[math.ceil(r['opened']/900)]+=r['results'][f'1.0:{stop}']['pnl']-r['results']['1.0:55']['pnl']
 vals=list(clusters.values());boot=sorted(sum(rng.choices(vals,k=len(vals))) for _ in range(5000))
 out[str(stop)]={'cluster_count':len(vals),'delta':sum(vals),'bootstrap95_delta_interval':[boot[125],boot[4874]]}
for stop in [5,50,55,70,80]:
 rr=[r['results'][f'1.0:{stop}']['pnl']/r['qty'] for r in rows];los=[x for x in rr if x<0];out[str(stop)].update({'mean_loss_per_contract':statistics.mean(los) if los else None,'worst_loss_per_contract':min(rr),'losing_trades':len(los)})
(P/'uncertainty.json').write_text(json.dumps(out,indent=2));print(json.dumps(out,indent=2))
