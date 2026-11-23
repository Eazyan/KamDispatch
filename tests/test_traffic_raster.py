import unittest
from PIL import Image,ImageDraw
import numpy as np
from monitoring.traffic_raster import ribbon_overlay

class RasterTests(unittest.TestCase):
 def test_continuous_ribbon_keeps_every_pixel_without_bridging_real_gap(self):
  before=Image.new('RGB',(200,100),'white');after=before.copy();draw=ImageDraw.Draw(after)
  draw.line([(10,30),(90,30)],fill=(220,30,30),width=3);draw.line([(100,30),(190,30)],fill=(220,30,30),width=3)
  a=np.asarray(ribbon_overlay(before,after,[0,0,200,100]));self.assertTrue((a[30,10:91,3]==255).all());self.assertTrue((a[30,100:191,3]==255).all());self.assertTrue((a[30,91:100,3]==0).all())
 def test_unchanged_green_and_compact_logo_excluded(self):
  before=Image.new('RGB',(200,100),'white');ImageDraw.Draw(before).rectangle([10,10,60,60],fill=(30,190,30));after=before.copy();ImageDraw.Draw(after).rectangle([100,10,115,25],fill=(220,30,30))
  self.assertEqual(np.asarray(ribbon_overlay(before,after,[0,0,200,100]))[:,:,3].sum(),0)

 def test_dark_red_ribbon_retained(self):
  before=Image.new('RGB',(200,100),'white');after=before.copy();ImageDraw.Draw(after).line([(10,30),(190,30)],fill=(140,35,50),width=3)
  a=np.asarray(ribbon_overlay(before,after,[0,0,200,100]));self.assertTrue((a[30,10:191,3]==255).all());self.assertEqual(list(a[30,50,:3]),[210,43,41])

 def test_changed_white_arrow_repaired_but_unchanged_gap_kept(self):
  before=Image.new('RGB',(200,100),(220,220,220));after=before.copy();draw=ImageDraw.Draw(after)
  draw.line([(10,30),(190,30)],fill=(220,30,30),width=5)
  draw.rectangle([50,28,53,32],fill='white');draw.rectangle([100,28,103,32],fill=(220,220,220))
  a=np.asarray(ribbon_overlay(before,after,[0,0,200,100]));self.assertTrue((a[30,50:54,3]==255).all());self.assertTrue((a[30,100:104,3]==0).all())
