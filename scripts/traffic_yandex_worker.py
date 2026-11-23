"""Finite Windows pilot attaching to the dedicated, already open Yandex browser."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import importlib.metadata
import json
from pathlib import Path
import shutil
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.capture_traffic import capture, digest, write_json, ProviderBlocked
from scripts.traffic_capture_plan import validate_plan

@contextmanager
def attached_yandex(_executable=None,viewport=(4096,4096)):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser=pw.chromium.connect_over_cdp('http://127.0.0.1:9223',timeout=15000)
        if len(browser.contexts)!=1:
            raise RuntimeError('Expected exactly one dedicated browser context')
        context=browser.contexts[0]
        pages=[p for p in context.pages if p.url.startswith('https://2gis.ru/')]
        if not pages:
            raise RuntimeError('Open 2GIS manually in the dedicated Yandex window first')
        page=pages[-1]
        old_viewport=page.viewport_size or page.evaluate("({width: innerWidth, height: innerHeight})")
        page.set_viewport_size({'width':viewport[0],'height':viewport[1]})
        page.set_default_timeout(20000)
        try:
            yield page
        except BaseException:
            try:
                stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')
                page.screenshot(path=str(Path(__file__).resolve().parents[1]/'spool'/('yandex-failure-'+stamp+'.png')),timeout=5000)
            except Exception:
                pass
            raise
        finally:
            # Disconnect only. Never call browser.close/context.close on the visible browser.
            if old_viewport:
                page.set_viewport_size(old_viewport)

def main():
    p=argparse.ArgumentParser();p.add_argument('output',type=Path);p.add_argument('--plan',type=Path,required=True)
    p.add_argument('--tile',choices=['center','north','south']);a=p.parse_args()
    plan=json.loads(a.plan.read_text());tiles=validate_plan(plan)
    if a.tile:tiles=[tile for tile in tiles if tile['id']==a.tile]
    if not tiles:raise ValueError('No selected pilot tiles')
    if shutil.disk_usage(a.output.parent).free<5*1024**3:raise RuntimeError('Spool reserve below 5 GiB')
    a.output.mkdir(parents=True,exist_ok=False)
    run={'status':'running','started_at':datetime.now(timezone.utc).isoformat(), 'adapter':'persistent_yandex_cdp',
         'plan_sha256':digest(a.plan),'code_sha256':digest(Path(__file__)),
         'capture_code_sha256':digest(Path(__file__).with_name('capture_traffic.py')),
         'playwright_version':importlib.metadata.version('playwright'),'tiles':[], 'validation':'experimental_unvalidated'}
    start=time.monotonic();write_json(a.output/'run.json',run)
    try:
        for tile in tiles:
            tick=time.monotonic();print('START',tile['id'],flush=True)
            capture(a.output/tile['id'],None,tile['center'],plan['zoom'],tuple(plan['viewport']),page_factory=attached_yandex)
            run['tiles'].append({'id':tile['id'],'duration_s':time.monotonic()-tick})
            write_json(a.output/'run.json',run)
        run['status']='captured_pending_review'
    except BaseException as exc:
        run.update(status='blocked_provider' if isinstance(exc,ProviderBlocked) else 'cancelled' if isinstance(exc,KeyboardInterrupt) else 'failed',error=str(exc))
        raise
    finally:
        run.update(duration_s=time.monotonic()-start,finished_at=datetime.now(timezone.utc).isoformat(),
                   bytes=sum(p.stat().st_size for p in a.output.rglob('*') if p.is_file()))
        write_json(a.output/'run.json',run);print(json.dumps(run),flush=True)
if __name__=='__main__':main()
