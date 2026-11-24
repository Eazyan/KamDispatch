from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
import json,threading,urllib.request,urllib.error
from pathlib import Path
from traffic_service.store import Store
from traffic_service.server import App,make_handler

@contextmanager
def running(tmp_path):
    data=tmp_path/'data';data.mkdir();spool=tmp_path/'spool';spool.mkdir()
    store=Store(data/'test.sqlite')
    runtime=SimpleNamespace(data=data,spool=spool,store=store,plan={'tiles':[{'id':'one'}]},thread=None)
    runtime.request=lambda:store.create_run(runtime.plan['tiles'])
    app=App(runtime,0);server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(app));app.port=server.server_port
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield app,f'http://127.0.0.1:{server.server_port}'
    finally:server.shutdown();server.server_close();thread.join()

def request(url,**kwargs):
    try:
        with urllib.request.urlopen(urllib.request.Request(url,**kwargs)) as response:return response.status,json.load(response)
    except urllib.error.HTTPError as exc:return exc.code,json.load(exc)

def test_loopback_api_rejects_csrf_host_rebinding_and_artifact_traversal(tmp_path):
    with running(tmp_path) as (app,url):
        assert request(url+'/api/status')[0]==200
        assert request(url+'/api/status',headers={'Host':'evil.example'})[0]==403
        assert request(url+'/api/resume',method='POST',headers={'Content-Type':'application/json','Origin':'https://evil.example'},data=b'{}')[0]==403
        assert app.runtime.store.get('enabled')=='0'
        (tmp_path/'outside.png').write_bytes(b'private')
        assert request(url+'/artifacts/%2e%2e/outside.png')[0]==404
        code,_=request(url+'/api/resume',method='POST',headers={'Content-Type':'application/json'},data=b'{}')
        assert code==200 and app.runtime.store.get('enabled')=='1'

def test_duplicate_manual_run_is_rejected_and_pause_cancels(tmp_path):
    with running(tmp_path) as (app,url):
        args={'method':'POST','headers':{'Content-Type':'application/json'},'data':b'{}'}
        assert request(url+'/api/run',**args)[0]==202
        assert request(url+'/api/run',**args)[0]==409
        assert request(url+'/api/pause',**args)[0]==200
        assert app.runtime.store.cancelled(app.runtime.store.active()['id'])
