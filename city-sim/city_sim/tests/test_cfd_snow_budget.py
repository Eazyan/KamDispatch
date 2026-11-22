import unittest
import numpy as np
from city_sim.cfd_snow_budget import exchange, audit


class SnowBudgetTests(unittest.TestCase):
    def test_shared_donors_and_simultaneous_transfers(self):
        bed, air = np.array([3., 0.]), np.array([1., 0.])
        result = exchange(bed, air, [0, 0, 1], [0, 1, 0], [6, 3, 2], [2, 7, 2])
        np.testing.assert_allclose(result.erosion_kg, [2, 1, 0])
        np.testing.assert_allclose(result.deposition_kg, [.5, 0, .5])
        np.testing.assert_allclose(result.bed_kg, [.5, .5])
        np.testing.assert_allclose(result.air_kg, [2, 1])
        np.testing.assert_array_equal(bed, [3, 0])
        np.testing.assert_array_equal(air, [1, 0])

    def test_repeated_exhaustion_and_deposition_conserve_mass(self):
        rng = np.random.default_rng(73)
        bed, air = rng.random(5), rng.random(7)
        total = bed.sum() + air.sum()
        bi, ai = rng.integers(5, size=40), rng.integers(7, size=40)
        for _ in range(100):
            result = exchange(bed, air, bi, ai, rng.random(40)*10, rng.random(40)*10)
            bed, air = result.bed_kg, result.air_kg
            self.assertGreaterEqual(bed.min(), 0)
            self.assertGreaterEqual(air.min(), 0)
            self.assertAlmostEqual(bed.sum()+air.sum(), total, places=12)

    def test_quarter_scene_inventory_exchange(self):
        from pathlib import Path
        from city_sim.drift_scene import DriftScene
        scene = DriftScene.read(Path(__file__).resolve().parents[3] / 'docs/drift-engine/cfd/scene-v1')
        bed = (scene.layers['loose_kg_m2'] * scene.metadata['dx_m'] * scene.metadata['dy_m']).ravel()
        # Synthetic over-demand: tests mapping/inventory, not an erosion law.
        mapping = np.arange(bed.size)
        result = exchange(bed, np.zeros_like(bed), mapping, mapping, bed*2, np.zeros_like(bed))
        self.assertGreaterEqual(result.bed_kg.min(), 0)
        self.assertTrue(audit(bed_initial_kg=bed.sum(), air_initial_kg=0,
                              bed_final_kg=result.bed_kg.sum(),
                              air_final_kg=result.air_kg.sum())['passed'])

    def test_boundary_budget_and_missing_flux(self):
        args = dict(bed_initial_kg=10, air_initial_kg=2, bed_final_kg=7,
                    air_final_kg=1, boundary_in_kg=3, boundary_out_kg=6, explicit_loss_kg=1)
        self.assertTrue(audit(**args)['passed'])
        args['boundary_out_kg'] = 0
        self.assertFalse(audit(**args)['passed'])
        self.assertEqual(audit(**args)['residual_kg'], -6)

    def test_invalid_inputs_rejected(self):
        for bad in (-1, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                exchange([bad], [0], [0], [0], [0], [0])
            with self.assertRaises(ValueError):
                audit(bed_initial_kg=1, air_initial_kg=0, bed_final_kg=bad, air_final_kg=0)
        for mapping in ([1], [-1], [.5]):
            with self.assertRaises(ValueError):
                exchange([1], [0], mapping, [0], [1], [0])
        with self.assertRaises(ValueError):
            exchange([1], [0], [0, 0], [0], [1], [0])


if __name__ == '__main__':
    unittest.main()
