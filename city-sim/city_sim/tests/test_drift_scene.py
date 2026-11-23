import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import numpy as np
from city_sim.drift_scene import load_quarter, bilinear, DriftScene
from city_sim.tests.test_cfd_wind import ROOT

class SceneTests(unittest.TestCase):
    def test_layers_geometry_mass_and_hash(self):
        a=load_quarter(ROOT);b=load_quarter(ROOT)
        self.assertEqual(a.sha256,b.sha256)
        l=a.layers;mask=l['buildings_mask']
        self.assertIsNone(a.metadata['measured_dtm'])
        self.assertEqual(a.metadata['observations'],[])
        np.testing.assert_allclose(l['obstacle_surface_m'][mask],
            (l['ground_estimate_m']+l['building_height_m'])[mask])
        np.testing.assert_allclose(l['snow_surface_m'][~mask]-l['ground_estimate_m'][~mask],.025)
        self.assertTrue((l['loose_kg_m2'][mask]==0).all())
        self.assertNotEqual(a.sha256,load_quarter(ROOT,loose_kg_m2=0).sha256)
        b.layers['wind_east_south_m_s'][0,0,0]+=.01
        self.assertNotEqual(a.sha256,b.sha256)
        with TemporaryDirectory() as folder:
            manifest=a.export(folder)
            self.assertEqual(DriftScene.read(folder).sha256,a.sha256)
            manifest_path=Path(folder)/'scene.json'
            changed=json.loads(manifest_path.read_text());changed['dx_m']+=1
            manifest_path.write_text(json.dumps(changed))
            with self.assertRaises(ValueError):DriftScene.read(folder)
            a.export(folder)
            archive=Path(folder)/'layers.npz';archive.write_bytes(archive.read_bytes()+b'tampered')
            with self.assertRaises(ValueError):DriftScene.read(folder)
            a.export(folder)
            with np.load(Path(folder)/'layers.npz',allow_pickle=False) as saved:
                for name,value in l.items():np.testing.assert_array_equal(saved[name],value)
        for kwargs in ({'loose_kg_m2':-1},{'density_kg_m3':0},{'loose_kg_m2':float('nan')}):
            with self.assertRaises(ValueError):load_quarter(ROOT,**kwargs)

    def test_reference_interpolation_rejects_extrapolation(self):
        x=np.arange(3.);y=np.arange(3.);z=y[:,None]+2*x[None,:]
        np.testing.assert_allclose(bilinear(x,y,z,np.array([.5,1.5]),np.array([.5,1.5])),[[1.5,3.5],[2.5,4.5]])
        with self.assertRaises(ValueError):bilinear(x,y,z,np.array([-1.]),np.array([1.]))
