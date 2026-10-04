"""Extract retained ETH collector observations; no trading state is modified."""
import collections, gzip, json, pathlib, sqlite3, time

ROOT = pathlib.Path('/root/Project15')
OUT = pathlib.Path(__file__).parent
groups = collections.defaultdict(lambda: {'eval': [], 'book': [], 'gaps': []})
counts = collections.Counter()
errors = []
inventory = []
for mf in sorted((ROOT/'research-logs/ETH').glob('*/manifest.json')):
    manifest = json.loads(mf.read_text())
    if manifest.get('producer') != 'collector':
        continue
    session = mf.parent.name
    gaps = []
    for f in sorted(mf.parent.glob('*.jsonl.gz')):
        inventory.append({'path':str(f), 'size':f.stat().st_size})
        try:
            with gzip.open(f, 'rb') as stream:
                for line in stream:
                    if not any(x in line for x in (b'"kind":"evaluation"', b'"kind": "evaluation"', b'"kind":"book"', b'"kind": "book"', b'"kind":"recording_gap"', b'"kind": "recording_gap"')):
                        continue
                    r=json.loads(line); b=r['body']; k=r['kind']; counts[k]+=1
                    if k=='recording_gap':
                        gaps.append(r);continue
                    ticker=b.get('ticker') or b.get('market')
                    if not ticker:continue
                    key=session+'|'+ticker
                    if k=='evaluation':
                        t=b.get('snapshot_timestamp') or r.get('received_at') or r['captured_at']
                        close=b.get('market_close_timestamp')
                        if not close or not 0<close-t<=610:continue
                        g=groups[key];g['close']=close
                        codes=[x['code'] for x in b.get('reasons',[])]
                        # Preserve all evaluation times to detect missing entry-window observations.
                        g['eval'].append({'t':t,'observed':r['captured_at'],'side':b.get('side'),'codes':codes,
                            'p':b.get('probability'),'book':b.get('book'), 'op':(b.get('settlement_spec') or {}).get('comparison_operator'),
                            'quality':(b.get('quality') or {}).get('score')})
                    elif k=='book':
                        t=r['captured_at']
                        levels=[]
                        for side in ['yes','no']:
                            depth=sorted(((float(p),float(q)) for p,q in (b.get(side+'_levels') or b.get(side+'_top5') or []) if float(q)>0),reverse=True)
                            keep=[];qty=0
                            for p,q in depth:
                                keep.append([p,q]);qty+=q
                                if qty>=10:break
                            levels.append(keep)
                        groups[key]['book'].append([t,bool(b.get('valid') and b.get('fresh')), *levels])
        except (OSError,EOFError,ValueError) as exc:
            errors.append({'file':str(f),'error':str(exc)})
    for key in list(groups):
        if key.startswith(session+'|'):groups[key]['gaps']=gaps
    print(session,'files',len(inventory),'markets',len(groups),flush=True)
with gzip.open(OUT/'observations.jsonl.gz','wt') as f:
    for key,g in groups.items():
        if not g['eval']:continue
        g['session'],g['ticker']=key.split('|');f.write(json.dumps(g)+'\n')
settles={}
manifest=json.loads((ROOT/'data/cloud/manifest.json').read_text())
runtime=next(m for m in manifest if m['asset']=='ETH')
db=ROOT/'data/cloud'/runtime['data_dir']/'paper.db'
with sqlite3.connect(f'file:{db}?mode=ro',uri=True) as c:
    for t,b in c.execute("select market,body from records where kind='settlement'"):
        result=json.loads(b).get('result')
        if result in ['yes','no']:
            assert t not in settles or settles[t]==result
            settles[t]=result
(OUT/'settlements.json').write_text(json.dumps(settles))
(OUT/'scan-summary.json').write_text(json.dumps({'counts':dict(counts),'errors':errors,'inventory':inventory,'settlements':len(settles),'runtime':runtime,'at':time.time()},indent=2))
print('DONE',dict(counts),'errors',errors,'settlements',len(settles),flush=True)
