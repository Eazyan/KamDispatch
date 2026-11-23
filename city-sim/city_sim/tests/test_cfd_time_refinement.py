"""Replay actual 60-second CFD evidence, including exported cell inventories."""
import json
from pathlib import Path
import sys
import numpy as np
import unittest

ROOT = Path(__file__).resolve().parents[3]/'docs/drift-engine/cfd'
# Import sibling scientific scripts without changing the application's path.
sys.path.insert(0, str(ROOT))
try:
    from verify_time_refinement import verify_study
    from run_time_refinement import compare_fields
finally:
    sys.path.pop(0)
EVIDENCE = ROOT/'time-evidence-v1'


class CFDTimeRefinementTests(unittest.TestCase):
    def test_all_histories_and_exported_fields_replay(self):
        replay = verify_study(EVIDENCE)
        self.assertEqual(replay['cases'], 3)
        self.assertEqual(replay['steps'], 2100)
        self.assertEqual(replay['duration_s'], 60)

    def test_same_field_has_zero_difference(self):
        comparison = compare_fields(EVIDENCE/'dt-005', EVIDENCE/'dt-005')
        self.assertEqual(comparison['air_L1_kg'], 0)
        self.assertEqual(comparison['bed_L1_kg'], 0)

    def test_temporal_differences_are_measured_from_cell_fields(self):
        report = json.loads((EVIDENCE/'time-refinement.json').read_text())
        for coarse, fine, key in [('dt-020', 'dt-010', 'dt_020_vs_010'),
                                  ('dt-010', 'dt-005', 'dt_010_vs_005')]:
            with np.load(EVIDENCE/coarse/'final-masses.npz') as a, np.load(EVIDENCE/fine/'final-masses.npz') as b:
                for field, metric in [('air_kg', 'air_L1_kg'), ('bed_kg', 'bed_L1_kg')]:
                    measured = float(np.linalg.norm(a[field]-b[field], ord=1))
                    declared = report['comparisons'][key][metric]
                    self.assertAlmostEqual(measured, declared, delta=max(1e-12, abs(declared)*1e-11))
