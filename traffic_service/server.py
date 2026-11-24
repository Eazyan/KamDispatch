"""Loopback-only product UI and typed operator controls. No shell or Docker API."""
import argparse,json,logging,os,signal,threading,time
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import urlsplit,unquote
from traffic_service.runtime import Runtime,atomic_json

class FileLock:
    def __init__(self,path):self.path=Path(path);self.handle=None
    def __enter__(self):
        self.handle=self.path.open('a+b');self.handle.seek(0);self.handle.write(b'1');self.handle.flush();self.handle.seek(0)
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(self.handle.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(self.handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        return self
    def __exit__(self,*_):self.handle.close()

class App:
    def __init__(self,runtime,port):self.runtime=runtime;self.port=port;self.cache={};self.lock=threading.Lock()
    def read(self,name):
        path=self.runtime.data/name
        if not path.exists():return None
        mtime=path.stat().st_mtime_ns
        with self.lock:
            if name not in self.cache or self.cache[name][0]!=mtime:self.cache[name]=(mtime,json.loads(path.read_text(encoding='utf-8')))
            return self.cache[name][1]
    def status(self):
        data=self.runtime.store.status();latest=self.read('latest.json')
        data.update(server_time=time.time(),disk_free_bytes=__import__('shutil').disk_usage(self.runtime.spool).free,planned_frames=len(self.runtime.plan['tiles']),retention_s=10800)
        if latest:
            data['latest']={k:v for k,v in latest.items() if k not in ('segments',)}
            data['latest']['frames']=[{**f,'available':(self.runtime.spool/f['raster'].removeprefix('/artifacts/')).exists()} for f in latest['frames']]
        else:data['latest']=None
        return data
    def action(self,action):
        s=self.runtime.store
        if action=='run':
            run=self.runtime.request()
            return (202,{'run_id':run}) if run else (409,{'error':'Проход уже выполняется'})
        if action=='pause':s.set(enabled=0,pause_reason='Пауза оператора');s.cancel()
        elif action=='resume':s.set(enabled=1,pause_reason='',failure_count=0,next_run=0)
        elif action=='cancel':s.cancel()
        elif action=='rollback':
            if s.active():return 409,{'error':'Сначала остановите текущий проход'}
            previous=self.read('previous.json')
            if not previous:return 404,{'error':'Предыдущей версии пока нет'}
            latest=self.read('latest.json');atomic_json(self.runtime.data/'latest.json',previous)
            if latest:atomic_json(self.runtime.data/'previous.json',latest)
        else:return 404,{'error':'Неизвестное действие'}
        s.event('operator',action);return 200,{'ok':True}

def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,fmt,*args):logging.info(fmt,*args)
        def respond(self,status,data,kind='application/json; charset=utf-8'):
            payload=json.dumps(data,ensure_ascii=False).encode() if not isinstance(data,bytes) else data
            self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(payload)))
            self.send_header('X-Content-Type-Options','nosniff');self.send_header('Cache-Control','no-cache');self.end_headers()
            try:self.wfile.write(payload)
            except (BrokenPipeError,ConnectionResetError):pass
        def allowed(self):
            return self.headers.get('Host','') in (f'127.0.0.1:{app.port}',f'localhost:{app.port}')
        def do_GET(self):
            if not self.allowed():return self.respond(403,{'error':'Local access only'})
            path=unquote(urlsplit(self.path).path)
            if path=='/api/status':return self.respond(200,app.status())
            if path=='/api/latest':return self.respond(200,app.read('latest.json'))
            if path=='/api/graph':return self.respond(200,app.read('graph.json'))
            if path=='/health':return self.respond(200,{'ok':True,'worker_alive':bool(app.runtime.thread and app.runtime.thread.is_alive())})
            if path.startswith('/artifacts/'):
                root=app.runtime.spool;relative=path.removeprefix('/artifacts/');target=(root/relative).resolve()
                if not target.is_relative_to(root.resolve()) or target.suffix.lower()!='.png' or not target.is_file():return self.respond(404,{'error':'Снимок удалён по сроку хранения или недоступен'})
                return self.respond(200,target.read_bytes(),'image/png')
            static=Path(__file__).with_name('static');target=(static/('index.html' if path=='/' else path.lstrip('/'))).resolve()
            if not target.is_relative_to(static.resolve()) or not target.is_file():return self.respond(404,{'error':'Not found'})
            kind={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.png':'image/png'}.get(target.suffix,'application/octet-stream')
            return self.respond(200,target.read_bytes(),kind)
        def do_POST(self):
            if not self.allowed():return self.respond(403,{'error':'Local access only'})
            origin=self.headers.get('Origin')
            if origin and origin not in (f'http://127.0.0.1:{app.port}',f'http://localhost:{app.port}'):return self.respond(403,{'error':'Origin rejected'})
            if self.headers.get('Content-Type','').split(';')[0]!='application/json':return self.respond(415,{'error':'JSON required'})
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<=size<=1024:return self.respond(413,{'error':'Too large'})
                if size:json.loads(self.rfile.read(size))
                action=urlsplit(self.path).path.removeprefix('/api/')
                if urlsplit(self.path).path!='/api/'+action:return self.respond(404,{'error':'Not found'})
                status,data=app.action(action);return self.respond(status,data)
            except (ValueError,json.JSONDecodeError):return self.respond(400,{'error':'Invalid JSON'})
    return Handler

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--port',type=int,default=8765);a=p.parse_args()
    a.root=a.root.resolve();(a.root/'data').mkdir(parents=True,exist_ok=True)
    logging.basicConfig(level=logging.INFO,handlers=[RotatingFileHandler(a.root/'data/service.log',maxBytes=5_000_000,backupCount=2,encoding='utf-8')])
    with FileLock(a.root/'data/service.lock'):
        runtime=Runtime(a.root);app=App(runtime,a.port);server=ThreadingHTTPServer(('127.0.0.1',a.port),make_handler(app));server.daemon_threads=True
        runtime.start()
        def stop(*_):threading.Thread(target=server.shutdown,daemon=True).start()
        signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
        try:server.serve_forever(poll_interval=.5)
        finally:runtime.close();server.server_close()
if __name__=='__main__':main()
