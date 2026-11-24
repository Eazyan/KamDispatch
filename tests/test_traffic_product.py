import math
import numpy as np
from PIL import Image
import pytest
from traffic_service.geo import world,geographic,fit,project
from traffic_service.analysis import color_mask

def controls():
    origin=world([158.65,53.045]);scale=2**23/(2*math.pi)
    t=np.array([[scale,0],[0,-scale],[2048,2048]])
    points=[]
    for px,py in [(650,360),(3756,360),(650,3736),(3756,3736),(2048,2048)]:
        ll=geographic(origin+np.array([(px-2048)/scale,-(py-2048)/scale]))
        points.append({'pixel':[px,py],'lon_lat':ll})
    return points

def test_ellipsoidal_projection_roundtrip_and_independent_controls():
    for ll in ([158.65,53.045],[-120,70],[0,-60]):assert geographic(world(ll))==pytest.approx(ll,abs=1e-10)
    points=controls();o,t,errors=fit(points)
    assert max(errors)<1e-6
    assert project(points[4]['lon_lat'],o,t)==pytest.approx(points[4]['pixel'],abs=1e-6)
    points[4]['pixel'][0]+=3
    with pytest.raises(ValueError,match='exceeds'):fit(points)

def test_yandex_palette_and_unchanged_park_are_distinct():
    off=Image.new('RGB',(5,1),(230,230,230));on=Image.new('RGB',(5,1))
    colors=[(85,190,50),(255,200,20),(220,20,15),(85,190,50),(230,230,230)]
    for x,c in enumerate(colors):on.putpixel((x,0),c)
    off.putpixel((3,0),colors[3])
    assert color_mask(off,on).tolist()==[[1,2,3,0,0]]
