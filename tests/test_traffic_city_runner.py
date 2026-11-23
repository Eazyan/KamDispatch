"""Exercise city collection orchestration with a fake capture transport, never 2GIS."""
from copy import deepcopy
import json
import math
import shutil
from types import SimpleNamespace

import pytest
from PIL import Image,ImageDraw
pytest.importorskip('numpy')
pytest.importorskip('shapely')
pytest.importorskip('cv2')
BrowserTimeoutError=pytest.importorskip('playwright.sync_api').TimeoutError
from shapely.geometry import Polygon,mapping
from monitoring import traffic_coverage as city
from monitoring.traffic_image import calibration,project,unproject
import numpy as np
from scripts import capture_traffic as transport


@pytest.fixture
def job(tmp_path):
    def coordinate(x,y):
        return [158+x/100000,math.degrees(math.atan(math.sinh(1.1-y/100000)))]
    seed=tmp_path/'seed';seed.mkdir()
    image=Image.new('RGB',(1440,1000),(245,242,224));draw=ImageDraw.Draw(image)
    for x in range(460,1340,50):draw.line((x,220,x,680),fill=(210,210,210),width=8)
    for name in ('traffic-off.png','traffic-on.png'):image.save(seed/name)
    metadata={'schema_version':1,'center':coordinate(900,500),'zoom':16,'viewport':[1440,1000],
        'analysis_region':[460,220,880,460],'captured_at':'2026-11-23T00:00:00Z',
        'source_url':'https://2gis.ru/p_kamchatskiy','control_points':[
            {'pixel':[x,y],'lon_lat':coordinate(x,y)} for x,y in ((600,300),(1200,300),(600,750),(1200,750))],
        'image_sha256':{name:transport.digest(seed/name) for name in ('traffic-off.png','traffic-on.png')}}
    transport.write_json(seed/'capture.json',metadata)
    boundary=tmp_path/'boundary.json';transport.write_json(boundary,mapping(Polygon([coordinate(x,y) for x,y in ((600,300),(700,300),(700,400),(600,400))])))
    roads=tmp_path/'roads.json';transport.write_json(roads,{'segments':[{'id':'road','coordinates':[coordinate(620,350),coordinate(680,350)]}]})
    output=tmp_path/'city';plan=city.prepare_city(output,boundary,roads,seed)
    def views(count):
        plan['tiles']=[{'id':'seed','center':metadata['center']}]+[
            {'id':f'tile-{i:02d}','center':[158.01+i*.0001,53.0]} for i in range(1,count)]
        transport.write_json(output/'plan.json',plan)
    def good_capture(folder,chrome,center,zoom,viewport):
        folder.mkdir(parents=True,exist_ok=False)
        for name in ('traffic-off.png','traffic-on.png'):shutil.copy2(seed/name,folder/name)
        current=deepcopy(metadata);current['center']=center;current['zoom']=zoom;current['viewport']=list(viewport)
        origin,transform,_=calibration(metadata)
        shift=project(center,origin,transform)-project(metadata['center'],origin,transform)
        for point in current['control_points']:
            point['lon_lat']=unproject(np.asarray(point['pixel'])+shift,origin,transform)
        transport.write_json(folder/'capture.json',current)
    return SimpleNamespace(output=output,views=views,good=good_capture,plan=plan)


def test_manual_resume_archives_an_unsealed_frame_without_recapturing_completed_inputs(job,monkeypatch):
    job.views(2)
    incomplete=job.output/'captures'/'tile-01';incomplete.mkdir();(incomplete/'partial.txt').write_text('preserve me')
    calls=[]
    def capture(*args):calls.append(args[2]);job.good(*args)
    monkeypatch.setattr(transport,'capture',capture)
    city.capture_city(job.output,'unused-fake-browser')
    status=json.loads((job.output/'collection-status.json').read_text())
    assert status['status']=='complete' and status['completed_count']==2
    assert len(calls)==1
    assert list((job.output/'attempts').rglob('partial.txt')), 'Interrupted evidence must be preserved'
    city.capture_city(job.output,'unused-fake-browser')
    assert len(calls)==1, 'Already verified captures must not be fetched twice'


def test_synthetic_30_view_run_finishes_after_one_transient_timeout(job,monkeypatch):
    job.views(30);calls=[]
    def capture(folder,*args):
        calls.append(tuple(args[1]))
        if len(calls)==1:
            folder.mkdir(parents=True);(folder/'partial.txt').write_text('timeout evidence')
            raise transport.CaptureTransientError('simulated navigation timeout')
        job.good(folder,*args)
    monkeypatch.setattr(transport,'capture',capture)
    monkeypatch.setattr(city.time,'sleep',lambda _:None)
    city.capture_city(job.output,'unused-fake-browser')
    status=json.loads((job.output/'collection-status.json').read_text())
    assert status['status']=='complete' and status['completed_count']==30
    assert len(calls)==30, '29 missing views plus exactly one bounded retry'
    assert len(list((job.output/'captures').glob('*/capture.json')))==30
    assert list((job.output/'attempts').rglob('partial.txt'))


def test_captcha_or_rate_limit_stops_without_retry_or_next_view(job,monkeypatch):
    job.views(3);calls=[]
    def blocked(*args):
        calls.append(1)
        transport.check_block(SimpleNamespace(title=lambda:'captcha'),SimpleNamespace(status=429,ok=False))
    monkeypatch.setattr(transport,'capture',blocked)
    with pytest.raises(RuntimeError,match='blocked'):
        city.capture_city(job.output,'unused-fake-browser')
    assert len(calls)==1
    status=json.loads((job.output/'collection-status.json').read_text())
    assert status['status']=='blocked' and status['completed_count']==1


def test_user_interrupt_is_recorded_and_a_later_manual_resume_can_finish(job,monkeypatch):
    job.views(3);calls=[]
    def interrupted(folder,*args):
        calls.append(tuple(args[1]));folder.mkdir(parents=True);(folder/'partial.txt').write_text('interrupted')
        raise KeyboardInterrupt()
    monkeypatch.setattr(transport,'capture',interrupted)
    with pytest.raises(KeyboardInterrupt):city.capture_city(job.output,'unused-fake-browser')
    status=json.loads((job.output/'collection-status.json').read_text())
    assert status['status']=='interrupted'
    monkeypatch.setattr(transport,'capture',job.good)
    city.capture_city(job.output,'unused-fake-browser')
    assert json.loads((job.output/'collection-status.json').read_text())['completed_count']==3


def test_second_collector_is_rejected_before_any_capture(job,monkeypatch):
    import fcntl
    job.views(2)
    def forbidden(*args):raise AssertionError('A second collector must not reach the transport')
    monkeypatch.setattr(transport,'capture',forbidden)
    with (job.output/'.capture.lock').open('a') as held:
        fcntl.flock(held,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(RuntimeError,match='already|collector|locked'):
            city.capture_city(job.output,'unused-fake-browser')


def test_transient_retry_budget_is_finite(job,monkeypatch):
    job.views(3);calls=[]
    def unavailable(*args):
        calls.append(1)
        raise transport.CaptureTransientError('simulated HTTP 503')
    monkeypatch.setattr(transport,'capture',unavailable)
    monkeypatch.setattr(city.time,'sleep',lambda _:None)
    with pytest.raises(transport.CaptureTransientError):city.capture_city(job.output,'unused-fake-browser')
    assert len(calls)==2
    assert not (job.output/'captures/tile-01').exists()
    status=json.loads((job.output/'collection-status.json').read_text())
    assert status['status']=='failed' and status['completed_count']==1


def test_quality_failure_is_not_retried_or_published(job,monkeypatch):
    job.views(2);calls=[]
    def bad_map(*args):calls.append(1);raise ValueError('Blank basemap')
    monkeypatch.setattr(transport,'capture',bad_map)
    with pytest.raises(ValueError,match='Blank'):city.capture_city(job.output,'unused-fake-browser')
    assert len(calls)==1
    assert not (job.output/'captures/tile-01').exists()


def test_sealed_attempt_is_recovered_after_a_cleanup_failure_without_new_capture(job,monkeypatch):
    job.views(2)
    def late_failure(*args):job.good(*args);raise RuntimeError('simulated browser cleanup failure')
    monkeypatch.setattr(transport,'capture',late_failure)
    with pytest.raises(RuntimeError,match='cleanup'):city.capture_city(job.output,'unused-fake-browser')
    def forbidden(*args):raise AssertionError('The sealed pair should be reused, not fetched again')
    monkeypatch.setattr(transport,'capture',forbidden)
    city.capture_city(job.output,'unused-fake-browser')
    assert (job.output/'captures/tile-01/capture.json').exists()
    assert json.loads((job.output/'collection-status.json').read_text())['status']=='complete'


def test_cache_binds_calibration_metadata_and_repairs_incomplete_analysis_outputs(job):
    job.views(1);city.analyze_city(job.output,require_full=True)
    folder=job.output/'captures/seed';review=folder/'review.html';original=review.read_text()
    review.write_text('interrupted output')
    city.analyze_city(job.output,require_full=True)
    assert review.read_text()==original
    metadata=json.loads((folder/'capture.json').read_text())
    with Image.open(folder/'traffic-off.png') as source:
        after=source.copy()
    ImageDraw.Draw(after).line((620,350,680,350),fill=(120,193,103),width=5)
    after.save(folder/'traffic-on.png')
    metadata['image_sha256']['traffic-on.png']=transport.digest(folder/'traffic-on.png')
    transport.write_json(folder/'capture.json',metadata)
    assert city.analyze_city(job.output,require_full=True)['observed_road_count']==1
    # Same PNGs but a changed analysis region: the old observation must not survive the cache.
    metadata['analysis_region']=[900,220,400,460]
    transport.write_json(folder/'capture.json',metadata)
    assert city.analyze_city(job.output)['observed_road_count']==0


def test_ui_timeout_is_not_mistaken_for_a_retryable_network_timeout(job,monkeypatch):
    job.views(2);calls=[]
    def changed_ui(*args):calls.append(1);raise BrowserTimeoutError('button did not appear')
    monkeypatch.setattr(transport,'capture',changed_ui)
    with pytest.raises(BrowserTimeoutError):city.capture_city(job.output,'unused-fake-browser')
    assert len(calls)==1


@pytest.mark.parametrize('status',[403,429])
def test_http_access_block_is_classified_before_reading_the_page(status):
    def unavailable_title():raise AssertionError('A blocked response does not need DOM access')
    with pytest.raises(transport.CaptureBlockedError):
        transport.check_block(SimpleNamespace(title=unavailable_title),SimpleNamespace(status=status,ok=False))


def test_atomic_publication_keeps_the_previous_file_when_rename_fails(tmp_path,monkeypatch):
    path=tmp_path/'report.json';path.write_text('old complete report')
    def disk_failure(*args):raise OSError('simulated publication failure')
    monkeypatch.setattr(transport.os,'replace',disk_failure)
    with pytest.raises(OSError):transport.write_json(path,{'new':'report'})
    assert path.read_text()=='old complete report'
    assert list(tmp_path.iterdir())==[path]


def test_view_that_did_not_move_to_requested_city_tile_is_not_published(job,monkeypatch):
    job.views(2)
    original=json.loads((job.output/'captures/seed/capture.json').read_text())
    def stuck_camera(folder,*args):
        job.good(folder,*args)
        current=json.loads((folder/'capture.json').read_text())
        current['control_points']=original['control_points']
        transport.write_json(folder/'capture.json',current)
    monkeypatch.setattr(transport,'capture',stuck_camera)
    with pytest.raises(ValueError,match='camera|position'):city.capture_city(job.output,'unused-fake-browser')
    assert not (job.output/'captures/tile-01').exists()


def test_actual_zoom_change_cannot_hide_behind_unchanged_zoom_metadata(job,monkeypatch):
    job.views(2)
    def zoom_changed(folder,*args):
        job.good(folder,*args)
        current=json.loads((folder/'capture.json').read_text());origin,transform,_=calibration(current)
        camera=project(current['center'],origin,transform)
        for point in current['control_points']:
            point['lon_lat']=unproject(camera+2*(np.asarray(point['pixel'])-camera),origin,transform)
        transport.write_json(folder/'capture.json',current)
    monkeypatch.setattr(transport,'capture',zoom_changed)
    with pytest.raises(ValueError,match='scale|zoom'):city.capture_city(job.output,'unused-fake-browser')
    assert not (job.output/'captures/tile-01').exists()


@pytest.mark.parametrize('broken',['{"type":','[]','{"metadata": [], "features": []}'])
def test_broken_analysis_cache_is_rebuilt_from_verified_pngs(job,broken):
    job.views(1);city.analyze_city(job.output,require_full=True)
    cached=job.output/'captures/seed/roads.geojson';cached.write_text(broken)
    report=city.analyze_city(job.output,require_full=True)
    assert report['full_capture_passed']
    assert len(json.loads(cached.read_text())['features'])==1


def test_captcha_with_http503_is_not_a_retryable_server_outage():
    with pytest.raises(transport.CaptureBlockedError):
        transport.check_block(SimpleNamespace(title=lambda:'captcha'),SimpleNamespace(status=503,ok=False))


def test_plain_http503_remains_a_bounded_retryable_outage():
    with pytest.raises(transport.CaptureTransientError):
        transport.check_block(SimpleNamespace(title=lambda:'2GIS service unavailable'),SimpleNamespace(status=503,ok=False))
