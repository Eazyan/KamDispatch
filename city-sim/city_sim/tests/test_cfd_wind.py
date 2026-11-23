import unittest
from pathlib import Path
import numpy as np
from city_sim.drift_engine import run
from city_sim.cfd_wind import load_scene, reproduce
from city_sim.snow_state import SnowState

ROOT=Path(__file__).resolve().parents[3]/'docs/drift-engine/cfd'

class VectorWindTests(unittest.TestCase):
    def field(self,vector,height=10.):
        return dict(vector_east_south_m_s=vector,height_agl_m=np.full(vector.shape[:2],height),
                    provenance={'solver':'synthetic_test'})

    def test_uniform_vector_matches_legacy_and_height_hash(self):
        z=np.zeros((9,9)); m=z.copy(); m[4,4]=20
        vector=np.zeros(z.shape+(2,));vector[...,0]=12
        def state():
            s=SnowState((0,0,270,270),m.copy(),z.copy(),np.full_like(z,900),np.ones_like(z),z.copy())
            s.ledger['initial_kg']=s.total_kg()
            return s
        a,c=state(),state()
        a.weather(600,12,270);c.weather(600,12,270,wind_vector=vector)
        np.testing.assert_allclose(a.mass,c.mass,atol=1e-12)
        b=run(z,m,wind_field=self.field(vector))
        self.assertNotEqual(b['input_sha256'],run(z,m,wind_field=self.field(vector,5))['input_sha256'])
        vector[0,0,0]=np.nan
        with self.assertRaises(ValueError):run(z,m,wind_field=self.field(vector))

    def test_local_turning_calm_obstacles_and_open_ledger(self):
        z=np.zeros((9,9)); m=z.copy();m[2,2]=20;m[6,6]=20
        vector=np.zeros(z.shape+(2,));vector[:4,:,0]=12;vector[5:,:,1]=-12
        mask=z.astype(bool);mask[2,4]=True
        a=run(z,m,buildings=mask,wind_field=self.field(vector),seconds=120)
        mass=np.array(a['mass_kg_m2'])
        self.assertGreater(mass[2,3],0);self.assertGreater(mass[5,6],0)
        self.assertEqual(mass[2,4],0);self.assertAlmostEqual(a['balance']['error_kg'],0,places=7)
        state=SnowState((0,0,9,9),np.ones_like(z),z.copy(),np.ones_like(z),np.ones_like(z),z.copy())
        state.ledger['initial_kg']=state.total_kg();state.parameters['boundary']='open'
        state.weather(600,0,0,wind_vector=vector,boundary_inflow={'west':.01},explicit_loss_kg_m2_h=.1)
        self.assertGreater(state.ledger['boundary_out_kg'],0)
        self.assertAlmostEqual(state.balance()['error_kg'],0,places=8)
        self.assertTrue((state.mass>=0).all())
        calm=run(z,m,wind_field=self.field(np.zeros_like(vector)))
        np.testing.assert_array_equal(calm['mass_kg_m2'],m)

    def test_saved_rans_axes_height_and_pair(self):
        z,mask,dx,dy,field=load_scene(ROOT)
        with np.load(ROOT/'results/dec-snow-coarse/wind.npz') as d:
            spec_x=(d['x']>=0)&(d['x']<380.96960340405093)
            spec_y=(d['y']>=0)&(d['y']<389.18278081685327)
            original=d['U'][0][np.ix_(spec_y,spec_x)][::-1]
            np.testing.assert_allclose(field['vector_east_south_m_s'][...,1][~mask],-original[...,1][~mask])
        self.assertTrue((field['height_agl_m']>0).all())
        self.assertEqual(len(field['provenance']['source_sha256']),4)
        r=reproduce(ROOT)
        self.assertGreater(r['difference_l1_kg'],1)
        self.assertGreater(r['turning_difference_l1_kg'],1)
        for key in ('baseline','coupled','aligned_control'):
            self.assertLess(r[key]['balance']['relative_error'],1e-12)
            self.assertTrue((np.array(r[key]['mass_kg_m2'])>=0).all())
        self.assertEqual(r['coupled']['wind_updates'],0)
        self.assertEqual(r['coupled']['input_sha256'],reproduce(ROOT)['coupled']['input_sha256'])
