"""Bounded fixed-mesh OpenFOAM snow experiment. Run inside the audited image.

No web/weather requests. Keeps a persistent RANS case under --work. The snow
closure parameters are explicit scenarios; observations/field accuracy unknown.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import shutil

import numpy as np
from run_cfd import calculate, read_scalar, BASHRC
from snowmap.foam import vectors, write, run_foam
from city_sim.drift_scene import DriftScene
from city_sim.cfd_snow_budget import audit

HERE = Path(__file__).resolve().parent


def field(case, name, dimensions, values, boundaries):
    values = np.asarray(values)
    body = (f'dimensions {dimensions}; internalField nonuniform List<scalar>\n'
            f'{values.size}\n(\n' + '\n'.join(f'{v:.17g}' for v in values) + '\n);\n'
            f'boundaryField {{ {boundaries} }}')
    write(case, f'0/{name}', body, 'volScalarField')


def prepare_snow(wind_case, case, scene, coefficient, dt, duration, initial_air_pulse=False):
    case.mkdir(parents=True, exist_ok=False)
    shutil.copytree(wind_case/'constant/polyMesh', case/'constant/polyMesh')
    final = wind_case/'360'
    for name in ('U', 'nut', 'phi'):
        (case/'0').mkdir(exist_ok=True)
        shutil.copyfile(final/name, case/'0'/name)
    # Frozen wall values: no turbulence model is evaluated by the scalar solver.
    nut_path = case/'0/nut'
    nut_path.write_text(nut_path.read_text().replace('nutkWallFunction', 'fixedValue'))
    centres = np.asarray(vectors(final/'C'))
    meta = scene.metadata
    xmin, ymin, xmax, ymax = meta['bounds_local_m']
    rows, cols = meta['shape']
    col = np.floor((centres[:, 0]-xmin)/meta['dx_m']).astype(int)
    row = rows-1-np.floor((centres[:, 1]-ymin)/meta['dy_m']).astype(int)
    inside = (col >= 0) & (col < cols) & (row >= 0) & (row < rows)
    loading = np.zeros(len(centres))
    loading[inside] = scene.layers['loose_kg_m2'][row[inside], col[inside]]*meta['dx_m']*meta['dy_m']
    bc = ('inlet {type inletOutlet; phi transportPhi; inletValue uniform 0; value uniform 0;} '
          'outlet {type inletOutlet; phi transportPhi; inletValue uniform 0; value uniform 0;} '
          'ground {type zeroGradient;} building {type zeroGradient;} '
          'symmetry {type symmetry;}')
    initial_c = np.zeros(len(centres))
    if initial_air_pulse:
        # Explicit synthetic plume in two western columns to exercise outflow.
        initial_c[centres[:, 0] < centres[:, 0].min()+2*meta['dx_m']] = .001
    field(case, 'C', '[1 -3 0 0 0 0 0]', initial_c, bc)
    field(case, 'initialBedMass', '[1 0 0 0 0 0 0]', loading, bc)
    write(case, 'constant/snowProperties', f'settlingSpeed 0.5; erosionCoefficient {coefficient}; '
          'thresholdFrictionSpeed 0.25; schmidtNumber 0.7; kinematicViscosity 1.5e-5;')
    write(case, 'system/controlDict', f'application kamchatkaSnowFoam; startFrom startTime; startTime 0; '
          f'stopAt endTime; endTime {duration}; deltaT {dt}; writeControl timeStep; '
          f'writeInterval {round(duration/dt)}; writeFormat ascii; writePrecision 17; '
          'timeFormat general; timePrecision 12; runTimeModifiable false;')
    write(case, 'system/fvSchemes', 'ddtSchemes {default Euler;} gradSchemes {default Gauss linear;} '
          'divSchemes {default none; div(transportPhi,C) Gauss upwind;} '
          'laplacianSchemes {default Gauss linear uncorrected;} '
          'interpolationSchemes {default linear;} snGradSchemes {default uncorrected;} '
          'fluxRequired {default no; C;}')
    write(case, 'system/fvSolution', 'solvers {C {solver PBiCGStab; preconditioner DILU; tolerance 1e-14; relTol 0;}}')


def verify(case, scene, dt):
    with (case/'snow-ledger.csv').open() as f:
        steps = [{k: float(v) for k, v in row.items()} for row in csv.DictReader(f)]
    if not steps:
        raise ValueError('No audited steps')
    expected = float(scene.layers['loose_kg_m2'].sum()*scene.metadata['dx_m']*scene.metadata['dy_m'])
    for row in steps:
        if abs(row['bed_initial_kg']-expected) > 1e-6:
            raise ValueError('CFD projected ground inventory differs from scene')
        budget = audit(**{key: row[key] for key in ('bed_initial_kg', 'air_initial_kg', 'bed_final_kg',
                        'air_final_kg', 'boundary_in_kg', 'boundary_out_kg', 'explicit_loss_kg')})
        if not budget['passed'] or row['min_C_kg_m3'] < 0 or row['min_bed_kg'] < 0:
            raise ValueError(f'Independent budget/positivity audit failed: {row}')
    final = steps[-1]
    # Independently integrate written final fields using mesh volumes exported by OpenFOAM.
    output = run_foam(case, BASHRC, 'postProcess', '-func', 'writeCellVolumes', '-latestTime', timeout=120)
    (case/'postProcess.log').write_text(output)
    times = sorted((p for p in case.iterdir() if p.is_dir() and p.name.replace('.', '', 1).isdigit()), key=lambda p: float(p.name))
    folder = times[-1]
    count = len(np.asarray(vectors(case/'0/U')))
    concentration = read_scalar(folder/'C', count)
    volume = read_scalar(folder/'V', count)
    bed = read_scalar(folder/'bedMass', count)
    independent_air = float(np.dot(concentration, volume))
    independent_bed = float(bed.sum())
    if abs(independent_air-final['air_final_kg']) > 1e-7 or abs(independent_bed-final['bed_final_kg']) > 1e-7:
        raise ValueError('Written field integrals differ from solver ledger')
    np.savez_compressed(case/'final-masses.npz', air_kg=concentration*volume, bed_kg=bed)
    return dict(status='fixed_mesh_transient_CFD_scenario_NOT_field_validation', dt_s=dt, steps=len(steps),
                scene_sha256=scene.sha256, final=final,
                max_abs_residual_kg=max(abs(r['residual_kg']) for r in steps),
                written_field_air_kg=independent_air, written_field_bed_kg=independent_bed)


def wind_signature(work, scene):
    """Bind cached wind to the checked scene inputs and actual mesh/face fields."""
    files = [HERE/'sources/quarter.json', HERE/'sources/ground.npz']
    for path in files:
        expected = scene.metadata['source_sha256'][str(path.relative_to(HERE))]
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('Wind source geometry differs from scene')
    wind = work/'wind'
    return dict(scene_sha256=scene.sha256,
                source_sha256={str(p.relative_to(HERE)): hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in files+[HERE/'run_cfd.py']},
                fields={name: hashlib.sha256((wind/'360'/name).read_bytes()).hexdigest() for name in ('U', 'nut', 'phi')},
                mesh={p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in sorted((wind/'constant/polyMesh').iterdir()) if p.is_file()})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--solver', type=Path, required=True)
    parser.add_argument('--reuse-wind', action='store_true')
    parser.add_argument('--dt', type=float, default=.1)
    parser.add_argument('--duration', type=float, default=5.)
    parser.add_argument('--erosion-coefficient', type=float, default=.001)
    parser.add_argument('--name', default='nominal')
    parser.add_argument('--initial-air-pulse', action='store_true')
    args = parser.parse_args()
    if not np.isfinite([args.dt, args.duration, args.erosion_coefficient]).all() or args.dt <= 0 or args.duration <= 0 or args.erosion_coefficient < 0:
        parser.error('Finite positive dt/duration and nonnegative erosion coefficient required')
    if not np.isclose(args.duration/args.dt, round(args.duration/args.dt)):
        parser.error('Duration must be an integer number of steps')
    scene = DriftScene.read(HERE/'scene-v1')
    args.work.mkdir(parents=True, exist_ok=True)
    wind = args.work/'wind'
    if not args.reuse_wind:
        wind.mkdir(exist_ok=False)
        logs = args.work/'wind-logs'
        logs.mkdir()
        spec = json.loads((HERE/'sources/quarter.json').read_text())
        with np.load(HERE/'sources/ground.npz') as terrain:
            calculate(wind, spec, terrain, 'coarse', spec['weather']['dec-snow'], 360, logs, True)
    signature = wind_signature(args.work, scene)
    signature_path = args.work/'wind-provenance.json'
    if args.reuse_wind:
        if json.loads(signature_path.read_text()) != signature:
            raise ValueError('Cached wind provenance mismatch')
    else:
        signature_path.write_text(json.dumps(signature, indent=2)+'\n')
    case = args.work/args.name
    prepare_snow(wind, case, scene, args.erosion_coefficient, args.dt, args.duration, args.initial_air_pulse)
    log = run_foam(case, BASHRC, str(args.solver), timeout=600)
    (case/'solver.log').write_text(log)
    result = verify(case, scene, args.dt)
    result['erosion_coefficient_kg_s_m4'] = args.erosion_coefficient
    result['initial_air_pulse_kg_m3'] = .001 if args.initial_air_pulse else 0.
    result['wind_provenance'] = signature
    result['cells'] = json.loads((args.work/'wind-logs/summary.json').read_text())['cells']
    result['frozen_wind_sha256'] = {name: hashlib.sha256((wind/'360'/name).read_bytes()).hexdigest() for name in ('U', 'nut', 'phi')}
    result['mesh_sha256'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((wind/'constant/polyMesh').iterdir()) if p.is_file()}
    result['solver_sha256'] = hashlib.sha256(args.solver.read_bytes()).hexdigest()
    result['source_sha256'] = {str(p.relative_to(HERE)): hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in (HERE/'snowSolver/kamchatkaSnowFoam.C', HERE/'run_snow_case.py')}
    (case/'summary.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
