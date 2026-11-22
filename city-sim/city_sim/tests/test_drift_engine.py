import unittest
import unittest.mock
import numpy as np
from city_sim.drift_engine import run

class DriftTests(unittest.TestCase):
    def test_balance_reverse_determinism(self):
        z=np.zeros((12,12)); m=z.copy();m[5,5]=20
        a=run(z,m,wind_from_deg=270);b=run(z,m,wind_from_deg=90)
        self.assertAlmostEqual(a['balance']['error_kg'],0,places=7)
        x=np.arange(12)
        self.assertGreater((np.array(a['mass_kg_m2'])*x).sum(),(np.array(b['mass_kg_m2'])*x).sum())
        self.assertEqual(a['mass_kg_m2'],run(z,m)['mass_kg_m2'])
        self.assertGreaterEqual(np.min(a['mass_kg_m2']),0)

    def test_precipitation_buildings_feedback(self):
        z=np.zeros((12,12)); mask=z.astype(bool);mask[:,8]=True
        m=z.copy();m[:,6]=20
        a=run(z,m,buildings=mask,snowfall_kg_m2_h=2,feedback_m=.0001)
        self.assertAlmostEqual(a['balance']['error_kg'],0,places=7)
        self.assertTrue((np.array(a['mass_kg_m2'])[:,8]==0).all())
        self.assertGreater(a['wind_updates'],1)

    def test_terrain_and_invalid(self):
        z=np.tile(np.arange(12)*3.,(12,1))
        a=run(z,5);b=run(z,5,terrain=False)
        self.assertNotEqual(a['mass_kg_m2'],b['mass_kg_m2'])
        self.assertEqual(run(z,0)['balance']['actual_kg'],0)
        for kw in ({'seconds':-1},{'feedback_m':0},{'wind_speed_m_s':float('nan')}):
            with self.assertRaises(ValueError):run(z,5,**kw)
        z[0,0]=np.nan
        with self.assertRaises(ValueError):run(z,5)

class ServiceTests(unittest.TestCase):
    def test_saved_scene_api_and_bounds(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from fastapi.testclient import TestClient
        from city_sim.api import make_app
        from city_sim.tests.test_access import OSM
        with TemporaryDirectory() as folder:
            path=Path(folder); osm=path/'fixture.osm';osm.write_text(OSM)
            client=TestClient(make_app(path/'test.sqlite',osm,osm))
            with unittest.mock.patch('httpx.get',side_effect=AssertionError('offline only')):
                r=client.post('/api/v1/drift-engine/compare',json={'resolution':16})
            self.assertEqual(r.status_code,200,r.text)
            data=r.json()
            self.assertEqual(data['detailed_cfd']['status'],'not_connected')
            self.assertAlmostEqual(data['drift_engine']['balance']['error_kg'],0,places=6)
            self.assertEqual(len(data['drift_engine']['mass_kg_m2']),16)
            self.assertTrue(data['provenance']['data_sha256'])
            self.assertEqual(client.get('/drift-engine').status_code,200)
            for payload in ({'resolution':128},{'seconds':3601},{'wind_speed_m_s':-1},{'unknown':1}):
                self.assertEqual(client.post('/api/v1/drift-engine/compare',json=payload).status_code,422)

    def test_hash_identifies_shape_and_flat_equal_baseline(self):
        from city_sim.drift_service import DriftRequest,compare
        a=run(np.zeros((3,12)),1);b=run(np.zeros((6,6)),1)
        self.assertNotEqual(a['input_sha256'],b['input_sha256'])
        r=compare(DriftRequest(resolution=16,terrain=False,buildings=False))
        # Feedback is tiny and flat uniform input must retain the baseline.
        np.testing.assert_allclose(r['drift_engine']['mass_kg_m2'],r['legacy_dynamic']['mass_kg_m2'],atol=.01)
