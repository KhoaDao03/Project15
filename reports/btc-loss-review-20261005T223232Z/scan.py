import json,gzip,time
from pathlib import Path
P=Path(__file__).parent
now=1791239553.8285236
trades={r['ticker']:r for r in json.loads((P/'trades-all.json').read_text())}
checks={};coverage=[];files=0
for mf in sorted(Path('research-logs/BTC').glob('*/manifest.json')):
 m=json.loads(mf.read_text())
 if m.get('producer')!='live_executor':continue
 status=mf.with_name('status.json');coverage.append(dict(asset=m['asset'],session=m['session'],started_at=m['started_at'],status=json.loads(status.read_text()) if status.exists() else {}))
 targets={t:r for t,r in trades.items() if r['recent'] and r['asset']==m['asset'] and r['opened']>=m['started_at']-5}
 if not targets:continue
 for f in sorted(mf.parent.glob('events-*')):
  if not f.name.endswith('.gz'):continue
  if int(f.name.split('-')[1].split('.')[0])/1e9 < now-72*3600-1800:continue
  files+=1
  try:
   with gzip.open(f,'rt') as g:
    for line in g:
     if 'decision_check' not in line or ('"accepted":true' not in line and '"accepted": true' not in line):continue
     d=json.loads(line);b=d.get('body',{});t=b.get('market');r=targets.get(t)
     if not r or d.get('kind')!='decision_check' or not b.get('collector_record'):continue
     at=b.get('started_at') or d.get('processed_at') or d.get('captured_at');delta=at-r['requested']
     if not -3<=delta<=2:continue
     exact=b.get('decision_id')==r['check_id'];rank=(not exact,abs(delta))
     if t not in checks or rank<checks[t]['rank']:checks[t]={'rank':rank,'file':str(f),'record':d}
  except (EOFError,OSError,json.JSONDecodeError) as e:coverage[-1].setdefault('read_errors',[]).append({'file':str(f),'error':type(e).__name__})
 print('scan',m['asset'],m['session'],'matched',len(checks),'files',files,flush=True)
(P/'entry-checks.json').write_text(json.dumps(checks));(P/'coverage.json').write_text(json.dumps(coverage,indent=2));print('DONE matched',len(checks),flush=True)
