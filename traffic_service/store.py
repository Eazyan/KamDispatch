"""Durable single-PC SQLite queue, atomic leases, and operator state."""
from contextlib import contextmanager
from datetime import datetime,timezone
import json,sqlite3,time,uuid
from pathlib import Path

def stamp():return datetime.now(timezone.utc).isoformat()

class Store:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.db() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,created TEXT NOT NULL,started REAL NOT NULL,finished TEXT,status TEXT NOT NULL,total INTEGER NOT NULL,cancel INTEGER NOT NULL DEFAULT 0,error TEXT,report TEXT);
            CREATE UNIQUE INDEX IF NOT EXISTS single_active_run ON runs((1)) WHERE status IN ('queued','running');
            CREATE TABLE IF NOT EXISTS jobs(run_id TEXT NOT NULL,tile_id TEXT NOT NULL,state TEXT NOT NULL,attempt INTEGER NOT NULL DEFAULT 0,lease TEXT,lease_until REAL,directory TEXT,error TEXT,PRIMARY KEY(run_id,tile_id));
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,at TEXT NOT NULL,kind TEXT NOT NULL,message TEXT NOT NULL);
            ''')
            for key,value in {'enabled':'0','interval_s':'300','next_run':'0','pause_reason':'','failure_count':'0'}.items():db.execute('INSERT OR IGNORE INTO settings VALUES (?,?)',(key,value))
    @contextmanager
    def db(self,write=False):
        db=sqlite3.connect(self.path,timeout=20);db.row_factory=sqlite3.Row
        try:
            if write:db.execute('BEGIN IMMEDIATE')
            yield db;db.commit()
        except BaseException:db.rollback();raise
        finally:db.close()
    def get(self,key,default=None):
        with self.db() as db:
            row=db.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone();return row[0] if row else default
    def set(self,**values):
        with self.db(True) as db:
            for key,value in values.items():db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',(key,str(value)))
    def event(self,kind,message):
        with self.db(True) as db:db.execute('INSERT INTO events(at,kind,message) VALUES (?,?,?)',(stamp(),kind,str(message)[:2000]))
    def active(self):
        with self.db() as db:
            row=db.execute("SELECT * FROM runs WHERE status IN ('queued','running') ORDER BY started DESC LIMIT 1").fetchone();return dict(row) if row else None
    def create_run(self,tiles):
        with self.db(True) as db:
            if db.execute("SELECT 1 FROM runs WHERE status IN ('queued','running')").fetchone():return None
            key=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:6]
            db.execute('INSERT INTO runs(id,created,started,status,total) VALUES (?,?,?,?,?)',(key,stamp(),time.time(),'queued',len(tiles)))
            db.executemany('INSERT INTO jobs(run_id,tile_id,state) VALUES (?,?,?)',[(key,t['id'],'queued') for t in tiles]);return key
    def recover(self):
        """Called only after obtaining the exclusive process lock."""
        with self.db(True) as db:
            db.execute("UPDATE runs SET status='interrupted',finished=?,error='Перезапуск после длительной остановки' WHERE status IN ('queued','running') AND started<?",(stamp(),time.time()-600))
            db.execute("UPDATE jobs SET state='cancelled' WHERE state IN ('queued','capturing','captured','analyzing') AND run_id IN (SELECT id FROM runs WHERE status='interrupted')")
            rows=db.execute("SELECT run_id,tile_id,state,directory FROM jobs WHERE state IN ('capturing','analyzing')").fetchall()
            for row in rows:
                state='captured' if row['directory'] and all((Path(row['directory'])/n).exists() for n in ('capture.json','traffic-off.png','traffic-on.png')) else 'queued'
                db.execute('UPDATE jobs SET state=?,lease=NULL,lease_until=NULL WHERE run_id=? AND tile_id=?',(state,row['run_id'],row['tile_id']))
            db.execute("UPDATE runs SET status='queued' WHERE status='running'")
    def claim(self,run_id,tile_id,directory):
        with self.db(True) as db:
            row=db.execute('SELECT * FROM jobs WHERE run_id=? AND tile_id=?',(run_id,tile_id)).fetchone()
            if not row or row['state']!='queued' or row['attempt']>=3:return None
            token=uuid.uuid4().hex;attempt=row['attempt']+1
            db.execute("UPDATE jobs SET state='capturing',attempt=?,lease=?,lease_until=?,directory=?,error=NULL WHERE run_id=? AND tile_id=?",(attempt,token,time.time()+300,str(directory),run_id,tile_id))
            db.execute("UPDATE runs SET status='running' WHERE id=?",(run_id,));return token
    def heartbeat(self,run_id):
        self.set(heartbeat=stamp())
        with self.db(True) as db:db.execute("UPDATE jobs SET lease_until=? WHERE run_id=? AND state IN ('capturing','analyzing')",(time.time()+300,run_id))
    def transition(self,run_id,tile_id,state,*,lease=None,error=None):
        with self.db(True) as db:
            row=db.execute('SELECT * FROM jobs WHERE run_id=? AND tile_id=?',(run_id,tile_id)).fetchone()
            if not row or (lease is not None and row['lease']!=lease):return False
            if lease is not None and row['lease_until'] and row['lease_until']<time.time():return False
            db.execute('UPDATE jobs SET state=?,error=? WHERE run_id=? AND tile_id=?',(state,str(error)[:2000] if error else None,run_id,tile_id));return True
    def jobs(self,run_id):
        with self.db() as db:return [dict(row) for row in db.execute('SELECT * FROM jobs WHERE run_id=? ORDER BY tile_id',(run_id,))]
    def cancelled(self,run_id):
        with self.db() as db:
            row=db.execute('SELECT cancel FROM runs WHERE id=?',(run_id,)).fetchone();return bool(row and row[0])
    def cancel(self):
        with self.db(True) as db:db.execute("UPDATE runs SET cancel=1 WHERE status IN ('queued','running')")
    def finish(self,run_id,status,error=None,report=None):
        with self.db(True) as db:
            db.execute('UPDATE runs SET status=?,finished=?,error=?,report=? WHERE id=?',(status,stamp(),str(error)[:2000] if error else None,json.dumps(report) if report else None,run_id))
            db.execute("UPDATE jobs SET state='cancelled' WHERE run_id=? AND state IN ('queued','capturing','captured','analyzing')",(run_id,))
    def pause(self,reason):self.set(enabled=0,pause_reason=reason)
    def status(self):
        with self.db() as db:
            settings={r['key']:r['value'] for r in db.execute('SELECT * FROM settings')}
            runs=[dict(r) for r in db.execute('SELECT * FROM runs ORDER BY started DESC LIMIT 20')]
            events=[dict(r) for r in db.execute('SELECT * FROM events ORDER BY id DESC LIMIT 30')]
        for run in runs:
            run['jobs']=self.jobs(run['id']);run['report']=json.loads(run['report']) if run['report'] else None
        return dict(settings=settings,runs=runs,events=events)
