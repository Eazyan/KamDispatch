"""One raw off/on Yandex Maps probe; not georeferenced or graph-matched."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit,parse_qs
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.capture_traffic import digest,write_json


def camera(url):
    q=parse_qs(urlsplit(url).query)
    return {k:q.get(k) for k in ('ll','z','tilt','azimuth')}

def traffic_enabled(url):
    return '/probki/' in urlsplit(url).path or 'trf' in parse_qs(urlsplit(url).query).get('l',[''])[0]

def main():
    from playwright.sync_api import sync_playwright
    output=Path(sys.argv[1]);output.mkdir(parents=True,exist_ok=False)
    start=time.monotonic();meta={'source':'yandex_maps_ui','validation':'raw_probe_ungeoreferenced','provider_updated_at':None}
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.connect_over_cdp('http://127.0.0.1:9223',timeout=15000)
            page=next(p for c in browser.contexts for p in c.pages if 'yandex.ru/maps' in p.url)
            viewport=page.viewport_size or page.evaluate('({width:innerWidth,height:innerHeight})')
            page.set_viewport_size({'width':1920,'height':1080})
            try:
                url='https://yandex.ru/maps/78/petropavlovsk/?ll=158.650000%2C53.045000&z=14'
                response=page.goto(url,wait_until='domcontentloaded',timeout=45000)
                if response.status in (403,429) or 'captcha' in page.url or 'капча' in page.title().lower():
                    raise RuntimeError('Yandex provider blocked; stop without retry')
                button=page.locator('[aria-label^="Пробки в "]')
                button.wait_for(state='visible',timeout=20000)
                page.wait_for_timeout(4000)
                if traffic_enabled(page.url):
                    raise RuntimeError('Expected traffic off initially')
                meta.update(source_url=page.url,browser_version=browser.version,viewport=[1920,1080])
                old_camera=camera(page.url)
                meta['reference_captured_at']=datetime.now(timezone.utc).isoformat()
                page.screenshot(path=str(output/'traffic-off.png'),animations='disabled',scale='css')
                button.click(timeout=15000)
                page.wait_for_url(traffic_enabled,wait_until='domcontentloaded',timeout=15000)
                page.wait_for_timeout(5000)
                if camera(page.url)!=old_camera:raise RuntimeError('Camera changed during traffic toggle')
                meta.update(traffic_url=page.url,captured_at=datetime.now(timezone.utc).isoformat(),camera=old_camera)
                page.screenshot(path=str(output/'traffic-on.png'),animations='disabled',scale='css')
                meta['image_sha256']={name:digest(output/name) for name in ('traffic-off.png','traffic-on.png')}
                meta['status']='raw_pair_captured'
                print(page.locator('body').inner_text()[-2200:],flush=True)
            finally:
                page.set_viewport_size(viewport)
            # The Playwright driver disconnects; the visible browser is never closed.
    except BaseException as exc:
        meta.update(status='failed',error=str(exc));raise
    finally:
        meta['duration_s']=time.monotonic()-start
        write_json(output/'probe.json',meta);print(json.dumps(meta,ensure_ascii=False),flush=True)
if __name__=='__main__':main()
