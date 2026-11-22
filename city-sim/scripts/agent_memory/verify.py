"""Read-only and idempotent memory verification; standard library, no network."""
import argparse
from datetime import date
import json
from pathlib import Path
import re
from urllib.parse import unquote
from memory import ROOT, MEMORY, identity, tasks, choose, stale

REQUIRED = ['AGENTS.md','GIT_INSTRUCTIONS.md','KNOWLEDGE_BASE.md',
 *[f'docs/agent-memory/{x}.md' for x in ['INDEX','STATE','TASKS','DECISIONS','HANDOFF','LOG','CLAIMS','HOOKS','PROTOCOL','VALIDATION']],
 '.agents/skills/resume-kamchatka/SKILL.md','.agents/skills/checkpoint-kamchatka/SKILL.md',
 '.codex/hooks.json','.codex/hooks/memory_hook.py','scripts/agent_memory/memory.py',
 'docs/agent-memory/inventory.csv','docs/agent-memory/checkpoint.json']

def verify(root, check_state=True):
    errors=[]
    for name in REQUIRED:
        if not (root/name).is_file(): errors.append('missing: '+name)
    if errors: return errors
    pages=[root/'AGENTS.md']+sorted((root/MEMORY).glob('*.md'))+list((root/'.agents/skills').glob('*/SKILL.md'))
    for path in pages:
        text=path.read_text()
        if path.parent == root/MEMORY:
            match=re.search(r'Updated: (\d{4}-\d{2}-\d{2})',text)
            try: date.fromisoformat(match[1] if match else '')
            except ValueError: errors.append('invalid/missing Updated date: '+str(path.relative_to(root)))
        for link in re.findall(r'\[[^\]]*\]\(([^)]+)\)',text):
            if re.match(r'https?://|#|mailto:',link): continue
            target=(path.parent/unquote(link.split('#')[0])).resolve()
            if not target.is_relative_to(root.resolve()) or not target.exists():
                errors.append(f'broken/escaping link: {path.relative_to(root)} -> {link}')
    # Index graph follows INDEX pages only: ordinary evidence backlinks are legitimate.
    graph={}
    pending=[(root/MEMORY/'INDEX.md').resolve()]
    while pending:
        path=pending.pop()
        if path in graph or not path.exists(): continue
        graph[path]=[]
        for link in re.findall(r'\[[^\]]*\]\(([^)]+)\)',path.read_text()):
            if not re.match(r'https?://|#',link):
                p=(path.parent/unquote(link.split('#')[0])).resolve()
                if p.is_relative_to(root.resolve()) and p.name == 'INDEX.md':
                    graph[path].append(p)
                    pending.append(p)
    def visit(node, stack):
        if node in stack: errors.append('cyclic index: '+str(node));return
        for child in graph.get(node,[]): visit(child,stack|{node})
    for node in graph: visit(node,set())
    rows=tasks(root); ids=[r['id'] for r in rows]
    if not rows: errors.append('empty task queue')
    if len(ids)!=len(set(ids)): errors.append('duplicate task ID')
    for row in rows:
        if not re.fullmatch(r'(WEATHER|DRIFT|GRAPH|OPS)-\d{3}',row['id']): errors.append('invalid ID: '+row['id'])
        if row['track'] not in {'WEATHER','DRIFT','GRAPH','OPS'} or not row['id'].startswith(row['track']+'-'): errors.append('invalid track: '+row['id'])
        if row['status'] not in {'todo','in_progress','blocked','done'}: errors.append('invalid status: '+row['id'])
        if row['priority'] not in {'P0','P1','P2','P3'}: errors.append('invalid priority: '+row['id'])
        if any(not row[x] for x in row): errors.append('empty task field: '+row['id'])
        if row['depends']!='-' and not set(row['depends'].split(','))<=set(ids): errors.append('missing dependency: '+row['id'])
        if row['status']=='blocked' and 'blocker:' not in row['ready']: errors.append('missing blocker: '+row['id'])
    dependencies={r['id']: [] if r['depends']=='-' else r['depends'].split(',') for r in rows}
    def depvisit(node, stack):
        if node in stack: errors.append('cyclic task dependency: '+node);return
        for child in dependencies.get(node,[]): depvisit(child,stack|{node})
    for node in dependencies: depvisit(node,set())
    try: choose(root)
    except ValueError as exc: errors.append(str(exc))
    for path in (root/'.agents/skills').glob('*/SKILL.md'):
        body=path.read_text()
        if not re.match(r'---\nname: [a-z0-9-]+\ndescription: [^\n]+\n---\n',body): errors.append('invalid skill frontmatter: '+str(path))
    handoff=(root/MEMORY/'HANDOFF.md').read_text()
    if not 300<=len(handoff.split())<=800: errors.append('handoff must contain 300–800 words')
    state=(root/MEMORY/'STATE.md').read_text()
    current=identity(root)
    if check_state:
        for key,value in current.items():
            if f'{key}: {value}' not in state: errors.append('stale state Git '+key)
        errors.extend(stale(root))
    return errors

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,default=ROOT)
    args=parser.parse_args();errors=verify(args.root.resolve())
    print(json.dumps({'ok': not errors,'errors': errors},ensure_ascii=False,indent=2))
    raise SystemExit(bool(errors))
