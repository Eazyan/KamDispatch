"""Independent replay of saved time-study histories and cell-mass comparisons."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import numpy as np
from run_time_refinement import CASES, summarize


def verify_study(folder):
    folder = Path(folder)
    manifest = json.loads((folder/'hashes.json').read_text())
    for name, expected in manifest.items():
        if hashlib.sha256((folder/name).read_bytes()).hexdigest() != expected:
            raise ValueError(f'Evidence hash mismatch: {name}')
    saved = json.loads((folder/'time-refinement.json').read_text())
    for name, _ in CASES:
        summary = json.loads((folder/name/'summary.json').read_text())
        with np.load(folder/name/'final-masses.npz') as fields:
            for key, total in [('air_kg', 'air_final_kg'), ('bed_kg', 'bed_final_kg')]:
                field = fields[key]
                if field.shape != (summary['cells'],) or not np.isfinite(field).all() or (field < 0).any():
                    raise ValueError('Invalid saved cell inventories')
                if abs(float(field.sum())-summary['final'][total]) > 1e-7:
                    raise ValueError('Saved field mass differs from ledger')
    replay = summarize(folder, saved['duration_s'])
    # runner_sha256 records the execution version, not the verifier's version.
    for pair, metrics in replay['comparisons'].items():
        for key, value in metrics.items():
            if not math.isclose(value, saved['comparisons'][pair][key], rel_tol=1e-11, abs_tol=1e-12):
                raise ValueError(f'Temporal comparison mismatch: {pair}/{key}')
    if not math.isclose(replay['observed_air_L1_order'], saved['observed_air_L1_order'], rel_tol=1e-11, abs_tol=1e-12):
        raise ValueError('Temporal order mismatch')
    for key in ('differences_decrease', 'cases'):
        if replay[key] != saved[key]:
            raise ValueError(f'Temporal comparison mismatch: {key}')
    return dict(cases=len(CASES), steps=sum(s['steps'] for s in saved['cases'].values()),
                duration_s=saved['duration_s'], observed_air_L1_order=saved['observed_air_L1_order'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('results', type=Path)
    args = parser.parse_args()
    print(verify_study(args.results))
