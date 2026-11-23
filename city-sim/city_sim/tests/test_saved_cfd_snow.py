"""Offline replay of actual OpenFOAM runs; no solver in ordinary CI."""
import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]/'docs/drift-engine/cfd'
SPEC = importlib.util.spec_from_file_location('verify_snow_runs', ROOT/'verify_snow_runs.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
EVIDENCE = ROOT/'snow-evidence-v1'
NAMES = ['nominal', 'half-step', 'no-erosion', 'depletion', 'boundary-pulse']


class SavedSnowCFDTests(unittest.TestCase):
    def test_every_step_budget_positivity_and_donors(self):
        for name in NAMES:
            with self.subTest(name=name):
                self.assertEqual(MODULE.verify_run(EVIDENCE/name)['steps'], 100 if name == 'half-step' else 50)

    def test_evidence_hashes_and_current_numerical_sources(self):
        manifest = json.loads((EVIDENCE/'hashes.json').read_text())
        for name, expected in manifest.items():
            self.assertEqual(hashlib.sha256((EVIDENCE/name).read_bytes()).hexdigest(), expected, name)
        summaries = [json.loads((EVIDENCE/name/'summary.json').read_text()) for name in NAMES]
        for key in ['scene_sha256', 'frozen_wind_sha256', 'mesh_sha256', 'solver_sha256', 'source_sha256']:
            for row in summaries:
                self.assertEqual(row[key], summaries[0][key])
        for name, expected in summaries[0]['source_sha256'].items():
            self.assertEqual(hashlib.sha256((ROOT/name).read_bytes()).hexdigest(), expected)

    def test_zero_source_and_nontrivial_boundary_control(self):
        control = json.loads((EVIDENCE/'no-erosion/summary.json').read_text())['final']
        self.assertEqual(control['air_final_kg'], 0)
        self.assertEqual(control['eroded_kg'], 0)
        self.assertEqual(control['bed_final_kg'], control['bed_initial_kg'])
        pulse = json.loads((EVIDENCE/'boundary-pulse/summary.json').read_text())['final']
        self.assertGreater(pulse['boundary_out_kg'], 1000)
        with (EVIDENCE/'depletion/snow-ledger.csv').open() as f:
            first = next(csv.DictReader(f))
        self.assertLessEqual(float(first['eroded_kg']), float(first['bed_initial_kg']))

    def test_missing_boundary_flux_is_detected(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)/'pulse'
            shutil.copytree(EVIDENCE/'boundary-pulse', folder)
            with (folder/'snow-ledger.csv').open() as f:
                reader = csv.DictReader(f)
                fields = reader.fieldnames
                rows = list(reader)
            rows[-1]['boundary_out_kg'] = '0'
            with (folder/'snow-ledger.csv').open('w') as f:
                writer = csv.DictWriter(f, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
            with self.assertRaisesRegex(ValueError, 'budget'):
                MODULE.verify_run(folder)
