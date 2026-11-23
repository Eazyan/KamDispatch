"""One browser slot, two offline analysis processes, durable local orchestration."""
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime,timezone
import json,os,shutil,threading,time
from pathlib import Path
from traffic_service.store import Store,stamp
from traffic_service.capture import capture,calibrate,ProviderBlocked,Cancelled
from traffic_service.analysis import analyze,merge
from traffic_service.geo import footprint_coverage
from monitoring.traffic_graph import export_graph,digest
from scripts.prune_traffic_spool import prune

_WORKER_GRAPH=None

def init_analysis(graph_file):
    global _WORKER_GRAPH
    _WORKER_GRAPH=export_graph(Path(graph_file).read_bytes())

def analyze_job(directory):return analyze(_WORKER_GRAPH,directory)

def atomic_json(path,data):
    path=Path(path);temp=path.with_suffix(path.suffix+'.tmp')
    with temp.open('w',encoding='utf-8') as out:
        json.dump(data,out,ensure_ascii=False,separators=(',',':'));out.flush();os.fsync(out.fileno())
    os.replace(temp,path)

class Runtime:
    def __init__(self,root):
        self.root=Path(root);self.data=self.root/'data';self.spool=self.root/'spool'
        self.data.mkdir(exist_ok=True);self.spool.mkdir(exist_ok=True)
        self.store=Store(self.data/'traffic.sqlite');self.stop_event=threading.Event()
        self.graph_file=self.data/'graph_v3.json';self.graph=export_graph(self.graph_file.read_bytes())
        self.plan=json.loads((self.data/'plan.json').read_text())
        if self.plan['graph_sha256']!=self.graph['graph_sha256']:raise ValueError('Graph/plan SHA mismatch')
        self.pool=None;self.thread=None
    def start(self):
        self.store.recover()
        self.pool=ProcessPoolExecutor(max_workers=2,initializer=init_analysis,initargs=(str(self.graph_file),))
        self.thread=threading.Thread(target=self.loop,name='traffic-orchestrator',daemon=True);self.thread.start()
    def request(self):return self.store.create_run(self.plan['tiles'])
    def close(self):
        self.stop_event.set();self.store.cancel()
        if self.thread:self.thread.join(timeout=55)
        if self.pool:self.pool.shutdown(wait=False,cancel_futures=True)
    def heartbeat(self):
        while not self.stop_event.wait(5):
            active=self.store.active()
            if active:self.store.heartbeat(active['id'])
            else:self.store.set(heartbeat=stamp())
    def loop(self):
        threading.Thread(target=self.heartbeat,daemon=True).start()
        last_prune=0
        while not self.stop_event.is_set():
            try:
                if time.monotonic()-last_prune>60:
                    prune(self.spool);last_prune=time.monotonic()
                active=self.store.active()
                if not active and self.store.get('enabled')=='1' and time.time()>=float(self.store.get('next_run','0')):
                    self.request();active=self.store.active()
                if active:self.run(active)
            except Exception as exc:
                self.store.event('error',str(exc));self.store.pause('Ошибка сервиса: '+str(exc)[:300])
            self.stop_event.wait(1)
    def run(self,run):
        run_id=run['id'];started=time.monotonic();pending={};results={};frames=[];error=None;status='failed'
        def cancelled():return self.stop_event.is_set() or self.store.cancelled(run_id)
        def process_done(wait=False):
            for future,(tile_id,lease) in list(pending.items()):
                if not wait and not future.done():continue
                try:
                    data=future.result(timeout=180 if wait else None)
                    if cancelled():raise Cancelled('Отменено')
                    if self.store.transition(run_id,tile_id,'accepted',lease=lease):
                        results[tile_id]=data;self.store.set(failure_count=0)
                except Cancelled:raise
                except Exception as exc:
                    self.store.transition(run_id,tile_id,'rejected',lease=lease,error=str(exc));self.store.event('quality',str(exc))
                    failures=int(self.store.get('failure_count','0'))+1;self.store.set(failure_count=failures)
                    if failures>=3:
                        self.store.pause('Три ошибки обработки. Сбор приостановлен.');raise RuntimeError('Три ошибки обработки')
                del pending[future]
        try:
            if cancelled():raise Cancelled('Съёмка отменена')
            if shutil.disk_usage(self.spool).free<5*1024**3:
                self.store.pause('На диске осталось меньше 5 ГиБ. Освободите место и возобновите сбор.');raise RuntimeError('Недостаточно места на диске')
            # Resume valid captured/accepted artifacts; never recapture an accepted tile.
            jobs={j['tile_id']:j for j in self.store.jobs(run_id)}
            from playwright.sync_api import sync_playwright
            with sync_playwright() as pw:
                browser=pw.chromium.connect_over_cdp('http://127.0.0.1:9223',timeout=15000)
                if len(browser.contexts)!=1:raise RuntimeError('Ожидался отдельный профиль браузера')
                pages=[p for p in browser.contexts[0].pages if 'yandex.ru/maps' in p.url]
                if not pages:raise RuntimeError('Откройте Яндекс Карты в рабочем окне браузера')
                page=pages[-1];old=page.viewport_size or page.evaluate('({width:innerWidth,height:innerHeight})')
                page.set_viewport_size(dict(zip(('width','height'),self.plan['viewport'])));page.set_default_timeout(15000)
                try:
                    calibration=calibrate(page,self.plan['zoom'],cancel=cancelled)
                    self.store.event('calibration',f"anchor {calibration['anchor'][0]:.2f},{calibration['anchor'][1]:.2f} residual {calibration['residual_px']} px")
                    for tile in self.plan['tiles']:
                        if cancelled():raise Cancelled('Съёмка отменена')
                        process_done();job=jobs[tile['id']];directory=Path(job['directory']) if job['directory'] else None
                        if job['state']=='accepted' and directory and (directory/'analysis.json').exists():
                            results[tile['id']]=json.loads((directory/'analysis.json').read_text());continue
                        if job['state'] in ('captured','analyzing') and directory and (directory/'capture.json').exists():
                            self.store.transition(run_id,tile['id'],'analyzing')
                            pending[self.pool.submit(analyze_job,str(directory))]=(tile['id'],None);continue
                        if job['state'] not in ('queued','capturing'):continue
                        directory=self.spool/run_id/(tile['id']+f"-a{job['attempt']+1}")
                        token=self.store.claim(run_id,tile['id'],directory)
                        if token is None:continue
                        try:
                            meta=capture(page,directory,tile['center'],self.plan['zoom'],tuple(self.plan['viewport']),calibration,cancelled)
                            self.store.transition(run_id,tile['id'],'captured',lease=token)
                            self.store.transition(run_id,tile['id'],'analyzing',lease=token)
                            pending[self.pool.submit(analyze_job,str(directory))]=(tile['id'],token)
                        except (ProviderBlocked,Cancelled):raise
                        except Exception as exc:
                            self.store.transition(run_id,tile['id'],'rejected',lease=token,error=str(exc));self.store.event('capture',str(exc))
                            failures=int(self.store.get('failure_count','0'))+1;self.store.set(failure_count=failures)
                            if failures>=3:
                                self.store.pause('Три последовательные ошибки съёмки. Проверьте рабочее окно браузера.');raise
                finally:
                    try:page.set_viewport_size(old)
                    except Exception:pass
                # Driver disconnects here; the user's browser remains alive.
            process_done(wait=True)
            if cancelled():raise Cancelled('Съёмка отменена')
            if not results:raise RuntimeError('Не получено ни одного пригодного кадра')
            jobs={j['tile_id']:j for j in self.store.jobs(run_id)}
            frame_rows=[]
            for tile_id,result in results.items():
                directory=Path(jobs[tile_id]['directory']);meta=json.loads((directory/'capture.json').read_text());frames.append(meta)
                frame_rows.append({'id':tile_id,'timestamp':meta['captured_at'],'bounds':result['bounds'],
                                   'raster':'/artifacts/'+directory.relative_to(self.spool).as_posix()+'/ribbons.png',
                                   'raw':'/artifacts/'+directory.relative_to(self.spool).as_posix()+'/traffic-on.png',
                                   'calibration_error_px':meta.get('anchor_residual_px')})
            frame_rows.sort(key=lambda f:f["timestamp"])
            coverage=footprint_coverage(self.graph,frames);merged=merge(self.graph,list(results.values()))
            elapsed=time.monotonic()-started;status='complete' if len(results)==len(self.plan['tiles']) and not coverage['missing_corridor_ids'] else 'partial'
            report=dict(run_id=run_id,status=status,source='yandex_maps_ui',published_at=stamp(),graph_sha256=self.graph['graph_sha256'],
                        started_at=run['created'],duration_s=elapsed,planned_frames=len(self.plan['tiles']),accepted_frames=len(results),
                        footprint_coverage=coverage,frames=frame_rows,**merged,validation='experimental_ui_assignment',max_age_s=600,direction='unresolved',not_for_automatic_dispatch=True)
            latest=self.data/'latest.json'
            if latest.exists():shutil.copyfile(latest,self.data/'previous.json')
            atomic_json(latest,report);self.store.finish(run_id,status,report={k:v for k,v in report.items() if k not in ('segments','frames')})
            self.store.event('complete',f'Проход {run_id}: {len(results)}/{len(self.plan["tiles"])} кадров, {elapsed:.1f} с')
        except ProviderBlocked as exc:
            error=str(exc);status='blocked_provider';self.store.pause(error)
        except Cancelled as exc:error=str(exc);status='cancelled'
        except Exception as exc:
            error=str(exc);status='failed'
            failures=int(self.store.get('failure_count','0'))+1;self.store.set(failure_count=failures)
            if failures>=3:self.store.pause('Повторяющаяся ошибка: '+error[:250])
        finally:
            if status not in ('complete','partial'):self.store.finish(run_id,status,error)
            for future in pending:future.cancel()
            now=time.time();base=run['started'];interval=int(self.store.get('interval_s','300'))
            self.store.set(next_run=base+(int(max(0,now-base)//interval)+1)*interval)
            if error:self.store.event(status,error)
