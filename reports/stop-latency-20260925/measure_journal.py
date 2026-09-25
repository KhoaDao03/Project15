import json, sqlite3, time, statistics, argparse
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--since',type=float,default=time.time()-86400);p.add_argument('--until',type=float,default=time.time());p.add_argument('--output',required=True);a=p.parse_args()
c=sqlite3.connect('file:/root/Project15/data/cloud/manual-orders.sqlite?mode=ro',uri=True)
rows=[json.loads(r[0]) for r in c.execute("SELECT body FROM manual_orders WHERE json_extract(body,'$.automation_reason')='HARD_STOP' AND json_extract(body,'$.timing.submitted_at') BETWEEN ? AND ? ORDER BY json_extract(body,'$.timing.submitted_at')",(a.since,a.until))]
first={}
for r in rows:first.setdefault(r['request']['ticker'],r)
metrics={};details=[]
def add(k,v):
 if v is not None and v>=0:metrics.setdefault(k,[]).append(v)
for ticker,r in first.items():
 t=r['timing']; events=[json.loads(e[0]) for e in c.execute("SELECT body FROM execution_events WHERE market=? AND kind='control_changed' ORDER BY seq",(ticker,))]
 triggers=[e for e in events if e.get('body',{}).get('exit_reason')=='HARD_STOP' and e['observed_at']<=t['submitted_at']]
 trigger=triggers[0]['observed_at'] if triggers else None
 # Exclude retries when the initial submitted stop preceded the observation window.
 previous=c.execute("SELECT 1 FROM manual_orders WHERE json_extract(body,'$.request.ticker')=? AND json_extract(body,'$.automation_reason')='HARD_STOP' AND json_extract(body,'$.timing.submitted_at') < ? LIMIT 1",(ticker,t['submitted_at'])).fetchone()
 if previous:continue
 add('stop_commit_to_post_ms',(t['submitted_at']-trigger)*1000 if trigger else None)
 for name,x,y in [('detection_to_post_ms','stop_detected_at','submitted_at'),('quote_receipt_to_detection_ms','quote_received_at','stop_detected_at'),('publication_to_detection_ms','quote_published_at','stop_detected_at'),('detection_to_ack_ms','stop_detected_at','acknowledged_at')]:
  add(name,(t[y]-t[x])*1000 if t.get(x) is not None and t.get(y) is not None else None)
 for k in ['preflight_ms','submission_ms','coordination_wait_ms']:add(k,t.get(k))
 details.append(dict(ticker=ticker,order_id=r['id'],state=r['state'],trigger_committed_at=trigger,timing=t))
def summary(v):
 v=sorted(v);return dict(n=len(v),median=statistics.median(v),p95=v[min(len(v)-1,int(.95*len(v)))],minimum=v[0],maximum=v[-1])
result=dict(since=a.since,until=a.until,submitted_stop_attempts=len(rows),first_stop_orders=len(details),metrics={k:summary(v) for k,v in metrics.items()},orders=details)
Path(a.output).write_text(json.dumps(result,indent=2));print(json.dumps({k:v for k,v in result.items() if k!='orders'},indent=2))
