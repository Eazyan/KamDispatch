"""City coverage must use the entire reference geometry, not visible road centroids."""
import math
from copy import deepcopy
import pytest
np=pytest.importorskip('numpy')
pytest.importorskip('shapely')
from shapely.geometry import Polygon,MultiPolygon,mapping
from monitoring.traffic_image import calibration,project,unproject
from monitoring import traffic_coverage


def coordinate(x,y):
    return [158+x/100000,math.degrees(math.atan(math.sinh(1.1-y/100000)))]


def seed():
    return {'control_points':[{'pixel':[x,y],'lon_lat':coordinate(x,y)} for x,y in
                              ((30,30),(370,30),(30,270),(370,270))],
            'center':coordinate(180,130),'viewport':[400,300],'analysis_region':[20,20,360,260],
            'zoom':16,'captured_at':'2026-11-23T00:00:00Z'}


def moved_capture(original,tile):
    result=deepcopy(original);origin,transform,_=calibration(original)
    shift=project(tile['center'],origin,transform)-project(original['center'],origin,transform)
    for point in result['control_points']:
        point['lon_lat']=unproject(np.array(point['pixel'])+shift,origin,transform)
    result['center']=tile['center'];result['tile_id']=tile['id']
    return result


def polygon(x0,y0,x1,y1):
    return Polygon([coordinate(x,y) for x,y in ((x0,y0),(x1,y0),(x1,y1),(x0,y1))])


def test_planner_and_measured_footprints_cover_all_parts_of_city():
    boundary=mapping(MultiPolygon([polygon(-100,50,300,250),polygon(800,700,950,850)]))
    original=seed();plan=traffic_coverage.plan_city(boundary,original,overlap_px=20)
    captures=[moved_capture(original,tile) for tile in plan['tiles']]
    roads=[{'id':key,'coordinates':[coordinate(x,y) for x,y in points]} for key,points in
           [('west',[(-90,150),(290,150)]),('east',[(810,750),(940,750)])]]
    report=traffic_coverage.audit_geometry(boundary,roads,captures)
    assert len(plan['tiles'])>1
    assert report['boundary_fully_captured']
    assert report['boundary_coverage_fraction']==1
    assert report['fully_captured_road_count']==2
    assert report['uncovered_road_length_m']==0
    partial=traffic_coverage.audit_geometry(boundary,roads,captures[:1])
    assert partial['boundary_coverage_fraction']<1
    assert partial['fully_captured_road_count']<2


def test_road_with_visible_centroid_but_unseen_end_is_not_fully_captured():
    boundary=mapping(polygon(0,0,400,300))
    roads=[{'id':'crosses-edge','coordinates':[coordinate(0,150),coordinate(250,150)]}]
    report=traffic_coverage.audit_geometry(boundary,roads,[seed()])
    assert report['fully_captured_road_count']==0
    assert 0 < report['road_length_coverage_fraction'] < 1
    assert report['uncovered_road_length_m']>0


def test_no_captures_cannot_count_as_full_city_coverage():
    report=traffic_coverage.audit_geometry(mapping(polygon(0,0,400,300)),
         [{'id':'road','coordinates':[coordinate(30,150),coordinate(200,150)]}],[])
    assert not report['boundary_fully_captured']
    assert report['fully_captured_road_count']==0
    assert report['boundary_coverage_fraction']==0


def test_city_acceptance_rechecks_pngs_instead_of_trusting_a_complete_status(tmp_path):
    import json
    from PIL import Image,ImageDraw
    from scripts.capture_traffic import write_json,digest
    folder=tmp_path/'seed';folder.mkdir()
    before=Image.new('RGB',(1440,1000),(245,242,224))
    draw=ImageDraw.Draw(before)
    for x in range(460,1340,50):
        draw.line((x,220,x,680),fill=(210,210,210),width=8)
    for file in ('traffic-off.png','traffic-on.png'):
        before.save(folder/file)
    metadata={'schema_version':1,'center':coordinate(900,500),'zoom':16,'viewport':[1440,1000],
              'analysis_region':[460,220,880,460],'captured_at':'2026-11-23T00:00:00Z',
              'source_url':'https://2gis.ru/p_kamchatskiy','control_points':[
                 {'pixel':[x,y],'lon_lat':coordinate(x,y)} for x,y in ((600,300),(1200,300),(600,750),(1200,750))],
              'image_sha256':{file:digest(folder/file) for file in ('traffic-off.png','traffic-on.png')}}
    write_json(folder/'capture.json',metadata)
    boundary_file=tmp_path/'boundary.geojson';write_json(boundary_file,mapping(polygon(600,300,700,400)))
    roads_file=tmp_path/'roads.json';write_json(roads_file,{'segments':[{'id':'road','coordinates':[coordinate(620,350),coordinate(680,350)]}]})
    output=tmp_path/'city'
    plan=traffic_coverage.prepare_city(output,boundary_file,roads_file,folder)
    # This tiny test city is entirely inside the verified seed; no network capture is needed.
    plan['tiles']=plan['tiles'][:1];write_json(output/'plan.json',plan)
    report=traffic_coverage.analyze_city(output,require_full=True)
    assert report['full_capture_passed']
    assert report['observed_road_count']==0 and report['unknown_road_count']==1
    write_json(output/'collection-status.json',{'status':'complete','completed_count':1})
    before.putpixel((0,0),(0,0,0));before.save(output/'captures/seed/traffic-on.png')
    with pytest.raises(ValueError,match='Full-city'):
        traffic_coverage.analyze_city(output,require_full=True)
    rejected=json.loads((output/'coverage-report.json').read_text())
    assert not rejected['full_capture_passed']
    assert rejected['valid_capture_count']==0
    assert rejected['invalid_captures'][0]['tile_id']=='seed'
    assert rejected['boundary_coverage_fraction']==0


def test_reference_road_tail_is_not_silently_clipped_to_city_before_measuring_coverage():
    boundary=mapping(polygon(30,30,300,250))
    roads=[{'id':'tail-outside','coordinates':[coordinate(-100,150),coordinate(100,150)]}]
    report=traffic_coverage.audit_geometry(boundary,roads,[seed()])
    assert report['boundary_fully_captured']
    assert report['fully_captured_road_count']==0, 'The full original road geometry is the denominator'
    assert report['uncovered_road_length_m']>0
