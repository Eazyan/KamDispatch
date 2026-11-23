"""Delete only expired PNGs inside the dedicated spool, keeping audit metadata."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import signal
import time


def prune(root, now=None, ttl=10800):
    root=Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError('Spool must be an existing real directory')
    now=time.time() if now is None else now
    if ttl<10800:raise ValueError('Retention cannot be shorter than three hours')
    expired=[]
    # Never follow symlink directories or symlink files outside the spool.
    import os
    for directory,dirs,files in os.walk(root,followlinks=False):
        dirs[:]=[d for d in dirs if not (Path(directory)/d).is_symlink()]
        for name in files:
            p=Path(directory)/name
            if p.suffix.lower()!='.png' or p.is_symlink():continue
            try:
                stat=p.stat()
                if now-stat.st_mtime<ttl:continue
                p.unlink()
                expired.append({'path':str(p.relative_to(root)),'bytes':stat.st_size})
            except FileNotFoundError:pass
    if expired:
        with (root/'retention.jsonl').open('a',encoding='utf-8') as out:
            out.write(json.dumps({'at':datetime.fromtimestamp(now,timezone.utc).isoformat(),
                                  'ttl_s':ttl,'deleted':expired})+'\n')
    return expired


def main():
    p=argparse.ArgumentParser();p.add_argument('root',type=Path);p.add_argument('--watch',action='store_true');a=p.parse_args()
    import threading
    stop=threading.Event()
    for sig in (signal.SIGINT,signal.SIGTERM):signal.signal(sig,lambda *_:stop.set())
    while not stop.is_set():
        deleted=prune(a.root)
        if deleted:print(json.dumps({'deleted_png':len(deleted),'bytes':sum(f['bytes'] for f in deleted)}),flush=True)
        if not a.watch:break
        stop.wait(60)

if __name__=='__main__':main()
