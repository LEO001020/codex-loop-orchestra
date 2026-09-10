from pathlib import Path
import json, re, time, datetime
root=Path(r'E:\codex-LOOP\codex-loop-s-f2')
out=Path(r'E:\codex-LOOP\codex-loop-s-f2\data\reports\headless-wave\adhoc-8765-desktop-hooktrust\_sol_scratch.md')
parts=[]
cfg=Path(r'C:\Users\hzq00\.codex\config.toml').read_text(encoding='utf-8-sig')
lines=cfg.splitlines()
hits=[f'{i}:{line}' for i,line in enumerate(lines,1) if re.search(r'hook|managed_dir|trust_level|allow_managed', line, re.I)]
parts.append('## config.toml hook/trust hits')
parts.append('\n'.join(hits))
parts.append('\n## config.toml section headers containing hook')
parts.append('\n'.join(f'{i}:{line}' for i,line in enumerate(lines,1) if line.startswith('[') and 'hook' in line.lower()))
rep=root/'data'/'reports'/'headless-wave'/'adhoc-8765-desktop-hooktrust'
parts.append('\n## report files')
if rep.exists():
    for p in sorted(rep.iterdir()):
        parts.append(f'{p.name} {p.stat().st_size} {datetime.datetime.fromtimestamp(p.stat().st_mtime)}')
er=json.loads((root/'data'/'lifecycle'/'exec_roster.json').read_text(encoding='utf-8'))
jobs=er.get('jobs') or {}
man=json.loads((root/'data'/'manifests'/'adhoc-8765-desktop-hooktrust.json').read_text(encoding='utf-8'))
wanted=set(t['task_id'] for t in man['tasks'])
parts.append('\n## matching exec jobs')
states={}
matched=0
for k,v in jobs.items():
    hay=k + ' ' + str(v.get('task_name','')) + ' ' + str(v.get('packet_id',''))
    if any(w in hay for w in wanted) or 'desktop-hooktrust' in hay or '8765-desktop' in hay:
        matched += 1
        st=str(v.get('state'))
        states[st]=states.get(st,0)+1
        parts.append('%s state=%s model=%s hb=%s name=%s' % (k, st, v.get('model'), v.get('heartbeat_at'), v.get('task_name')))
parts.append('matched=%s states=%s total_jobs=%s schema_updated=%s' % (matched, states, len(jobs), er.get('updated_at')))
# native roster summary
nr=json.loads((root/'data'/'lifecycle'/'native_roster.json').read_text(encoding='utf-8'))
agents=nr.get('agents') or {}
from collections import Counter
c=Counter(str((row or {}).get('status')) for row in agents.values() if isinstance(row, dict))
parts.append('\n## native roster')
parts.append('updated_at=%s agents=%s pending=%s status=%s' % (nr.get('updated_at'), len(agents), len(nr.get('pending') or []), dict(c)))
out.write_text('\n'.join(parts), encoding='utf-8')
print(str(out))
print('matched', matched, states)
print('native', dict(c), 'agents', len(agents), 'updated', nr.get('updated_at'))
print('reports', [p.name for p in sorted(rep.iterdir())] if rep.exists() else None)
