"""Finite five-case CFD experiment, sharing one verified frozen RANS case."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import numpy as np
from verify_snow_runs import verify_run

CASES = [('nominal', []), ('half-step', ['--dt', '0.05']),
         ('no-erosion', ['--erosion-coefficient', '0']),
         ('depletion', ['--erosion-coefficient', '100000']),
         ('boundary-pulse', ['--erosion-coefficient', '0', '--initial-air-pulse'])]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--solver', type=Path, required=True)
    parser.add_argument('--reuse-wind', action='store_true')
    args = parser.parse_args()
    args.work.mkdir(parents=True, exist_ok=True)
    for index, (name, extra) in enumerate(CASES):
        command = [sys.executable, str(Path(__file__).with_name('run_snow_case.py')),
                   '--work', str(args.work), '--solver', str(args.solver), '--name', name] + extra
        if args.reuse_wind or index:
            command += ['--reuse-wind']
        with (args.work/(name+'.driver.log')).open('w') as log:
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
        print(name, verify_run(args.work/name), flush=True)
    summaries = {name: json.loads((args.work/name/'summary.json').read_text()) for name, _ in CASES}
    for key in ('scene_sha256', 'frozen_wind_sha256', 'mesh_sha256', 'solver_sha256', 'source_sha256'):
        if any(row[key] != summaries['nominal'][key] for row in summaries.values()):
            raise ValueError(f'Inconsistent experiment input: {key}')
    if summaries['no-erosion']['final']['air_final_kg'] != 0 or summaries['no-erosion']['final']['eroded_kg'] != 0:
        raise ValueError('Zero-source control failed')
    if summaries['boundary-pulse']['final']['boundary_out_kg'] <= 1:
        raise ValueError('Boundary outflow control was not exercised')
    base = np.load(args.work/'nominal/final-masses.npz')
    half = np.load(args.work/'half-step/final-masses.npz')
    relative_l1 = float(np.abs(base['air_kg']-half['air_kg']).sum()/base['air_kg'].sum())
    result = dict(status='five_short_scenarios_NOT_grid_convergence_or_field_validation',
                  dt_half_air_relative_L1=relative_l1,
                  cases={name: row['final'] for name, row in summaries.items()})
    (args.work/'matrix-summary.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
