"""Three-step temporal refinement on one frozen CFD mesh, default horizon 60 s.

Runs a finite matrix; all inputs and fluxes are shared. This measures temporal
sensitivity of the existing scenario closure, not spatial or field accuracy.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import numpy as np
from verify_snow_runs import verify_run

CASES = [('dt-020', .2), ('dt-010', .1), ('dt-005', .05)]


def compare_fields(coarse, fine):
    """Cellwise L1 in kg, relative to the finer suspended inventory."""
    with np.load(coarse/'final-masses.npz') as first, np.load(fine/'final-masses.npz') as second:
        air_l1 = float(np.abs(first['air_kg']-second['air_kg']).sum())
        bed_l1 = float(np.abs(first['bed_kg']-second['bed_kg']).sum())
        reference = float(second['air_kg'].sum())
    if reference <= 0 or not np.isfinite([air_l1, bed_l1, reference]).all():
        raise ValueError('Invalid temporal comparison')
    return dict(air_L1_kg=air_l1, bed_L1_kg=bed_l1, fine_air_kg=reference,
                air_relative_L1=air_l1/reference)


def summarize(work, duration):
    summaries = {}
    for name, dt in CASES:
        verify_run(work/name)
        summary = json.loads((work/name/'summary.json').read_text())
        if summary['steps'] != round(duration/dt) or abs(summary['final']['time_s']-duration) > 1e-8:
            raise ValueError('Incomplete duration')
        if summary['dt_s'] != dt:
            raise ValueError('Unexpected step')
        summaries[name] = summary
    reference = summaries[CASES[0][0]]
    for key in ('scene_sha256', 'frozen_wind_sha256', 'mesh_sha256', 'solver_sha256', 'source_sha256', 'wind_provenance'):
        if any(s[key] != reference[key] for s in summaries.values()):
            raise ValueError(f'Inconsistent temporal-study input: {key}')
    pairs = [compare_fields(work/CASES[i][0], work/CASES[i+1][0]) for i in range(2)]
    order = math.log2(pairs[0]['air_L1_kg']/pairs[1]['air_L1_kg']) if min(p['air_L1_kg'] for p in pairs) > 0 else None
    return dict(status='fixed_mesh_temporal_refinement_NOT_spatial_or_field_validation', duration_s=duration,
                comparisons={'dt_020_vs_010': pairs[0], 'dt_010_vs_005': pairs[1]},
                observed_air_L1_order=order,
                differences_decrease=bool(pairs[1]['air_L1_kg'] < pairs[0]['air_L1_kg']),
                cases={name: dict(dt_s=s['dt_s'], steps=s['steps'], final=s['final'],
                                 max_abs_residual_kg=s['max_abs_residual_kg']) for name, s in summaries.items()},
                runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--solver', type=Path, required=True)
    parser.add_argument('--duration', type=float, default=60.)
    parser.add_argument('--reuse-wind', action='store_true')
    args = parser.parse_args()
    if not math.isfinite(args.duration) or args.duration <= 0 or any(
            not math.isclose(args.duration/dt, round(args.duration/dt), abs_tol=1e-9) for _, dt in CASES):
        parser.error('Duration must contain a positive integer number of every step')
    args.work.mkdir(parents=True, exist_ok=True)
    if any((args.work/name).exists() for name, _ in CASES):
        parser.error('Study case directory already exists; use a fresh work directory')
    for index, (name, dt) in enumerate(CASES):
        command = [sys.executable, str(Path(__file__).with_name('run_snow_case.py')),
                   '--work', str(args.work), '--solver', str(args.solver),
                   '--name', name, '--dt', str(dt), '--duration', str(args.duration)]
        if args.reuse_wind or index:
            command += ['--reuse-wind']
        with (args.work/(name+'.driver.log')).open('w') as log:
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
        print(name, verify_run(args.work/name), flush=True)
    result = summarize(args.work, args.duration)
    (args.work/'time-refinement.json').write_text(json.dumps(result, indent=2, allow_nan=False)+'\n')
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
