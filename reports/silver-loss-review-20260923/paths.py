"""Extract sampled books and evaluations; not a complete counterfactual replay."""
import datetime as dt
import gzip
import json
from pathlib import Path

OUT=Path(__file__).parent
trades=json.loads((OUT/'trades.json').read_text())
by={r['ticker']:r for r in trades}
keys=[k.encode() for k in by]
points={}; evaluations={}
for session in sorted(Path('/root/Project15/research-logs/SILVER').iterdir()):
    m=json.loads((session/'manifest.json').read_text())
    if m.get('producer')!='collector':continue
    for path in sorted(session.glob('*.jsonl.gz')):
        start=int(path.name.split('-')[1].split('.')[0])/1e9
        if not any(t['opened']-610 <= start <= t['opened']+900 for t in trades):continue
        with gzip.open(path,'rb') as stream:
            for line in stream:
                if b'"kind":"book"' not in line and b'"kind":"evaluation"' not in line:continue
                if not any(k in line for k in keys):continue
                r=json.loads(line);b=r['body'];ticker=b.get('market')
                if ticker not in by:continue
                at=r.get('captured_at') or r.get('received_at')
                trade=by[ticker]
                if at<trade['opened']-10:continue
                if r['kind']=='book':
                    points.setdefault(ticker,[]).append(dict(at=at,**b))
                else:
                    evaluations.setdefault(ticker,[]).append(dict(at=at,**b))
    print('books scanned',session.name,len(points),flush=True)
(OUT/'book-paths.json').write_text(json.dumps(points)+'\n')
(OUT/'evaluation-paths.json').write_text(json.dumps(evaluations)+'\n')
print('covered',len(points),len(evaluations),flush=True)
