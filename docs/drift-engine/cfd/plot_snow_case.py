"""Plot exported cell masses in the scene grid; optional matplotlib QA utility."""
import argparse
from pathlib import Path
import re
import numpy as np
from city_sim.drift_scene import DriftScene


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    scene = DriftScene.read(Path(__file__).resolve().parent/'scene-v1')
    text = (args.work/'wind/360/C').read_text()
    match = re.search(r'internalField\s+nonuniform\s+List<vector>\s+(\d+)\s*\((.*?)\n\)', text, re.S)
    if not match:
        raise ValueError('Missing CFD cell centres')
    centres = np.fromstring(match[2].replace('(', ' ').replace(')', ' '), sep=' ').reshape(-1, 3)
    masses = np.load(args.work/'nominal/final-masses.npz')
    xmin, ymin, xmax, ymax = scene.metadata['bounds_local_m']
    rows, cols = scene.metadata['shape']
    dx, dy = scene.metadata['dx_m'], scene.metadata['dy_m']
    col = np.floor((centres[:, 0]-xmin)/dx).astype(int)
    row = rows-1-np.floor((centres[:, 1]-ymin)/dy).astype(int)
    inside = (col >= 0) & (col < cols) & (row >= 0) & (row < rows)
    air, bed = np.zeros((rows, cols)), np.zeros((rows, cols))
    np.add.at(air, (row[inside], col[inside]), masses['air_kg'][inside])
    np.add.at(bed, (row[inside], col[inside]), masses['bed_kg'][inside])
    air = air/(dx*dy)*1000
    erosion = (scene.layers['loose_kg_m2']-bed/(dx*dy))*1000
    erosion[scene.layers['buildings_mask']] = np.nan
    plt.rcParams.update({'font.size': 10})
    fig, axes = plt.subplots(1, 2, figsize=(10, 5.7), constrained_layout=True)
    for ax, data, title in zip(axes, (erosion, air), ('Net bed loss (g/m²)', 'Suspended snow column (g/m²)')):
        ax.set_facecolor('#d6d6d6')
        im = ax.imshow(data, extent=(xmin, xmax, ymin, ymax), origin='upper', cmap='Blues', interpolation='nearest')
        ax.set_title(title)
        ax.set_xlabel('East (local m)')
        ax.set_ylabel('North (local m)')
        fig.colorbar(im, ax=ax, shrink=.78)
    fig.suptitle('Kamchatka quarter · 5 simulated seconds · frozen RANS\nScenario parameters; no field validation', fontsize=13)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output, dpi=160)


if __name__ == '__main__':
    main()
