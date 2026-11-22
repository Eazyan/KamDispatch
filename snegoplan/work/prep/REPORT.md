# REPORT: HGS input for plow stage, zone 3 (Снегоплан)

## What was done

1. Reviewed the existing `./prep/` package against TASK.md, fixed bugs (below).
2. Wrote `tests/test_prep.py` (plain asserts, `python3 tests/test_prep.py`) — **all 7 tests pass**.
3. Built zone-3 input, ran `./input/hgs` (time_limit 10 s), decoded to GeoJSON, printed summary.

Artifacts in `out/`:
- `zone3_plows.json` — HGS input (327 tasks, 5 vehicles, 2 classes, 118-node compressed matrices)
- `zone3_map.json` — decode map (task → edge/street/geometry/direction, node index → graph id, vehicle info)
- `zone3_solution.json` — solver output
- `zone3_routes.geojson` — 387 LineStrings (327 service + 60 deadhead legs)

## Fixes made to the previous run

- `excel.py`: crashed on footer note rows ("Все строки - пример…") in «Машины»/«Точки» and on the density-table sub-header — now skipped robustly.
- `build.py` (real bug): map file stored **graph node ids** in task `start_node/end_node`, but `decode.py` looks them up as **compressed indices** (`by_i[...]`) — decoding zone 3 would have crashed with KeyError. Map tasks now carry compressed indices plus `start_graph_node/end_graph_node` for reference.
- `tasks.py`: service time multiplied `v_work_kmh` by `коэф_скорости`; TASK.md's formula has no such factor — removed (the column is still loaded for future stages; it is 1.0 for all plow types anyway).
- `decode.py`: zero-length deadhead legs (next task starts where the previous ended) produced invalid 1-point LineStrings — now skipped; deadhead geometry is now oriented along the actual driving direction (was sometimes drawn backwards against oneway geometry); deadhead legs get a real `start_time = arrive − leg duration`.

## Formulas (prep/formulas.py, named constants with comments)

- Passes per direction: `ceil(width_dir_m / max_work_width)`, max over plow classes (КДМ 3.0 m, грейдер 3.55 m → 3.55 used). Each pass = one task, direction fixed `u→v` ("snow goes right"); both directions of a two-way street are separate graph edges, each with its own passes.
- Service time per class: `length_m / (v_work/3.6 · snow_k · slope_k)`, `snow_k = min(1, h_ref/h)`, `slope_k = 1/(1+0.03·max(0,slope_pct))` uphill / 1.0 downhill. Class omitted if `gabarit + 0.5 > width_m`.
- Task weight: `{1:10, 2:3, 3:1}[priority] · length_km`; priority-1: soft deadline 3 h, penalty 50/s.
- Transit time: `length / (v_transit/3.6 · traffic_coef) · (1 + 0.02·max(0,slope))`, Dijkstra over the **whole** graph (driving through other zones allowed), oneway respected, too-narrow edges forbidden, compressed to 118 used nodes, unreachable = −1 (none in this build).

## Zone 3 run — summary

- Zone: 250 edges → **327 tasks** (212 edges ×1 pass, 10 ×2, 81 ×3, 24 ×4; priorities 1/2/3 = 113/184/30), total service 47.73 km. Every task can be served by both classes (widths ≥ 3.0 m everywhere in zone 3).
- Conditions: «Северные районы» h = 25 cm, traffic 1.15. Vehicles (plows only; 7 non-plow machines skipped): M01–M03 КДМ, M04–M05 грейдер; start/finish snapped to graph (shift 0–8 h).
- Solver (`time_limit_s=10`, seed 1): status **ok**, unassigned **[]**, 336 iterations, best found at 7.76 s.
- Objective (raw): total 594 175.8 = latency 593 554.2 + 0.2·deadhead 3 107.9 + wait 0 + overtime 0 + 0·makespan 5 980.6; lateness 0 → **all priority-1 deadlines met** (all returns ≈ 1.5 h ≪ 3 h deadline; latency dominates simply because weight·completion_time is summed over all tasks).
- Per vehicle (service / deadhead km, return):

| vehicle | class | visits | service km | deadhead km | return |
|---|---|---|---|---|---|
| M01 | КДМ | 51 | 10.19 | 11.28 | 01:30 |
| M02 | КДМ | 94 | 11.10 | 7.62 | 01:33 |
| M03 | КДМ | 84 | 10.32 | 12.64 | 01:32 |
| M04 | грейдер | 49 | 8.28 | 3.81 | 01:34 |
| M05 | грейдер | 49 | 7.84 | 13.16 | 01:39 |

## Questionable / assumptions

- **Zone → conditions mapping is assumed**: the Excel «Условия» sheet has district names, not zone ids. Zones 1–3 take «Северные районы», 4–7 «Центр», 8–9 «Южные районы» — consistent with zone centroids (lat 53.046–53.070 for 1–3; center band for 4–7; southern 53.000–53.039 for 8–9). Change `ZONE_CONDITION_NAME` in `prep/formulas.py` if the real mapping differs.
- **All machines in the sheet are treated as the zone's contractor** (per TASK.md); no zone-contractor filtering exists in the data.
- Excel coordinates are marked "пример/оценка" in the source file; snapping distances are small but the bases/finish points are fictional.
- `коэф_скорости` from the type sheet is ignored in the plow formula (TASK.md formula has no such factor; it's 1.0 for all plow types anyway).
- Deadhead legs in GeoJSON are shortest paths by *time*, so their drawn length can slightly exceed the shortest-by-distance path.
