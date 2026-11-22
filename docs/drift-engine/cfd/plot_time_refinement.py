"""Scientific plot of saved temporal CFD refinement; optional matplotlib."""
import argparse
import csv
from pathlib import Path
import numpy as np


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    runs = {}
    for name, dt in [('dt-020', .2), ('dt-010', .1), ('dt-005', .05)]:
        with (args.results/name/'snow-ledger.csv').open() as f:
            rows = list(csv.DictReader(f))
        runs[dt] = {key: np.array([float(r[key]) for r in rows]) for key in rows[0]}
    fig, axes = plt.subplots(2, 2, figsize=(10, 7), constrained_layout=True)
    for dt, row in runs.items():
        label = f'dt = {dt:g} s'
        axes[0, 0].plot(row['time_s'], row['air_final_kg'], label=label)
        axes[0, 1].plot(row['time_s'], row['boundary_out_kg'], label=label)
        axes[1, 1].plot(row['time_s'], row['residual_kg'], label=label, linewidth=.8)
    fine = runs[.05]
    lookup = {round(t, 6): mass for t, mass in zip(fine['time_s'], fine['air_final_kg'])}
    for dt in [.2, .1]:
        row = runs[dt]
        reference = np.array([lookup[round(t, 6)] for t in row['time_s']])
        axes[1, 0].plot(row['time_s'], abs(row['air_final_kg']-reference)/reference*100,
                        label=f'dt = {dt:g} s vs 0.05 s')
    for ax, title, ylabel in zip(axes.flat,
            ['Suspended snow', 'Cumulative boundary outflow', 'Total air mass difference', 'Signed full budget residual'],
            ['kg', 'kg', '% relative to dt = 0.05 s', 'kg']):
        ax.set_title(title)
        ax.set_xlabel('Simulated time (s)')
        ax.set_ylabel(ylabel)
        ax.grid(alpha=.2)
        ax.legend(fontsize=8)
    fig.suptitle('Kamchatka CFD · 60 simulated seconds · fixed mesh and frozen RANS\nScenario closure; no spatial or field validation', fontsize=12)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output, dpi=160)


if __name__ == '__main__':
    main()
