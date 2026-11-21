"""Offline continuity helpers. Never read transcripts, environment or credentials."""
import csv
import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
MEMORY = Path('docs/agent-memory')
IGNORED = {'.git', '.aws', '.venv', 'venv', 'node_modules', '__pycache__', '.pytest_cache'}
SNAPSHOT = MEMORY / 'checkpoint.json'

def safe(path):
    return not any(x in IGNORED or x.startswith('.env') or x.lower() in {'credentials', 'secrets', 'auth.json', '.ds_store'} or any(t in x.lower() for t in ('private_key', 'id_rsa', 'token', 'secret')) for x in path.parts)

def files(root):
    for directory, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if safe(Path(directory).relative_to(root) / d) and not (Path(directory)/d).is_symlink())
        for name in sorted(names):
            path = Path(directory) / name
            rel = path.relative_to(root)
            if safe(rel) and not path.is_symlink() and path.is_file():
                yield rel

def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()

def git(root, *args):
    p = subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True, timeout=10)
    return p.stdout.strip() if p.returncode == 0 else None

def identity(root):
    return {'branch': git(root, 'symbolic-ref', '--short', 'HEAD') or 'detached',
            'sha': git(root, 'rev-parse', '--verify', 'HEAD') or 'unborn'}

def tasks(root):
    result = []
    for line in (root/MEMORY/'TASKS.md').read_text().splitlines():
        if line.startswith('| ') and not line.startswith('| ID '):
            cells = [x.strip() for x in line.strip('|').split('|')]
            if len(cells) == 10:
                result.append(dict(zip(('id','title','track','priority','status','paths','ready','depends','evidence','previous'), cells)))
    return result

def choose(root, track=None):
    rows = tasks(root)
    completed = {t['id'] for t in rows if t['status'] == 'done'}
    available = [t for t in rows if t['status'] in {'todo', 'in_progress'} and (not track or t['track'] == track)
                 and (t['depends'] == '-' or set(t['depends'].split(',')) <= completed)]
    active = [t for t in available if t['status'] == 'in_progress']
    if len(active) > 1:
        raise ValueError('ambiguous active tasks: ' + ', '.join(t['id'] for t in active))
    if active:
        return active[0]
    ordered = sorted(available, key=lambda t: (t['priority'], t['id']))
    return ordered[0] if ordered else None

def manifest(root):
    """Metadata only: no file contents. A changed size/mtime is a warning, not a scientific result."""
    return {str(rel): [p.stat().st_size, p.stat().st_mtime_ns]
            for rel in files(root) if rel != SNAPSHOT and not str(rel).startswith('docs/agent-memory/verification-output')
            for p in [root/rel]}

def stale(root):
    path = root/SNAPSHOT
    if not path.exists():
        return ['checkpoint absent']
    saved = json.loads(path.read_text())
    changes = []
    if saved['git'] != identity(root):
        changes.append('Git branch/SHA changed')
    old, new = saved['files'], manifest(root)
    if old != new:
        changes.append('file metadata changed since checkpoint')
    return changes

def write_checkpoint(root):
    """Explicitly called after an agent wrote the semantic checkpoint. No automatic conclusions."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    content = {'updated_at': datetime.now(ZoneInfo('Asia/Kamchatka')).isoformat(),
               'git': identity(root), 'files': manifest(root)}
    (root/SNAPSHOT).write_text(json.dumps(content, ensure_ascii=False, indent=2)+'\n')

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['next','checkpoint','status'])
    parser.add_argument('--track', choices=['WEATHER','DRIFT','GRAPH','OPS'])
    args = parser.parse_args()
    if args.action == 'checkpoint':
        write_checkpoint(ROOT)
        print('Checkpoint metadata saved; semantic STATE/TASKS/HANDOFF/LOG must already be updated.')
    elif args.action == 'status':
        print(json.dumps({'git': identity(ROOT), 'warnings': stale(ROOT)}, ensure_ascii=False))
    else:
        print(json.dumps({'git': identity(ROOT), 'warnings': stale(ROOT), 'next': choose(ROOT,args.track)}, ensure_ascii=False, indent=2))
