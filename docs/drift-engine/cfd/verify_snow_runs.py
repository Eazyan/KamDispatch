"""Replay saved transient CFD budgets; no Docker, weather or solver required."""
import argparse
import csv
import json
import math
from pathlib import Path
from city_sim.cfd_snow_budget import audit


def verify_run(folder):
    folder = Path(folder)
    summary = json.loads((folder/'summary.json').read_text())
    with (folder/'snow-ledger.csv').open() as f:
        rows = [{k: float(v) for k, v in row.items()} for row in csv.DictReader(f)]
    if not rows or len(rows) != summary['steps']:
        raise ValueError('Missing or truncated time history')
    dt = summary['dt_s']
    if not math.isfinite(dt) or dt <= 0:
        raise ValueError('Invalid step duration')
    previous_bed = rows[0]['bed_initial_kg']
    previous_air = rows[0]['air_initial_kg']
    previous_e = previous_d = previous_in = previous_out = 0.
    keys = ('bed_initial_kg', 'air_initial_kg', 'bed_final_kg', 'air_final_kg',
            'boundary_in_kg', 'boundary_out_kg', 'explicit_loss_kg')
    for step, row in enumerate(rows, 1):
        if not all(math.isfinite(v) for v in row.values()):
            raise ValueError('Nonfinite saved ledger value')
        result = audit(**{key: row[key] for key in keys})
        if not result['passed'] or abs(row['residual_kg']-result['residual_kg']) > 1e-7:
            raise ValueError('Mass budget failure')
        if abs(row['time_s']-dt*step) > 1e-9:
            raise ValueError('Missing or inconsistent time step')
        if row['min_C_kg_m3'] < 0 or row['min_bed_kg'] < 0:
            raise ValueError('Negative inventory')
        if not 0 <= row['eroded_kg']-previous_e <= previous_bed+1e-7:
            raise ValueError('Erosion exceeds donor inventory')
        if not 0 <= row['deposited_kg']-previous_d <= previous_air+1e-7:
            raise ValueError('Deposition exceeds donor inventory')
        if row['boundary_in_kg'] < previous_in or row['boundary_out_kg'] < previous_out:
            raise ValueError('Nonmonotone cumulative external flux')
        previous_bed, previous_air = row['bed_final_kg'], row['air_final_kg']
        previous_e, previous_d = row['eroded_kg'], row['deposited_kg']
        previous_in, previous_out = row['boundary_in_kg'], row['boundary_out_kg']
    if rows[-1] != summary['final']:
        raise ValueError('Summary differs from time history')
    return {'steps': len(rows), 'max_abs_residual_kg': max(abs(r['residual_kg']) for r in rows)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('results', type=Path)
    args = parser.parse_args()
    folders = sorted(p.parent for p in args.results.glob('*/summary.json'))
    if not folders:
        raise SystemExit('No saved snow runs')
    for folder in folders:
        print(folder.name, verify_run(folder))
