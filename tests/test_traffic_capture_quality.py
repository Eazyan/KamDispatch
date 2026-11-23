"""A screenshot rectangle is not map coverage when its basemap is still blank."""
import pytest
from PIL import Image,ImageDraw
pytest.importorskip('numpy')
pytest.importorskip('cv2')
from monitoring import traffic_image


def map_image():
    image=Image.new('RGB',(400,300),(245,242,224))
    draw=ImageDraw.Draw(image)
    for x in range(10,390,30):
        draw.rectangle((x,10,x+15,290),fill=(205+x%30,210,200))
        draw.line((x+20,0,x+20,300),fill='white',width=4)
    return image


def test_blank_reference_cannot_count_as_a_loaded_map():
    before=Image.new('RGB',(400,300),(245,242,224))
    with pytest.raises(ValueError,match='basemap|blank'):
        traffic_image.validate_basemap_pair(before,map_image(),[0,0,400,300])


def test_loaded_basemap_with_new_traffic_remains_valid():
    before=map_image();after=before.copy()
    ImageDraw.Draw(after).line((30,0,30,300),fill=(120,193,103),width=4)
    quality=traffic_image.validate_basemap_pair(before,after,[0,0,400,300])
    assert quality['status']=='passed'
    assert quality['before_unique_colors']>1


def test_partially_unloaded_reference_is_rejected_even_when_rest_has_texture():
    after=map_image();before=after.copy()
    ImageDraw.Draw(before).rectangle((0,0,199,299),fill=(245,242,224))
    with pytest.raises(ValueError,match='basemap|loading'):
        traffic_image.validate_basemap_pair(before,after,[0,0,400,300])
