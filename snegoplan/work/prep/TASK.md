# Task: build HGS plow-stage input from the map + Excel (Снегоплан)

Work ONLY inside the current directory. Inputs in ./input/:
- graph_v2.json — road graph of Petropavlovsk-Kamchatsky. Nodes: id, x (lon), y (lat), elevation, zone. Edges: id, u, v, key, length_m, slope_pct (sign = uphill in u->v direction), geometry [[lon,lat],...], highway, name, oneway, reverse_edge_id (two-way pair or null), lanes, width_m, width_dir_m (per-direction width), priority (1/2/3), zone (1..9), maxspeed.
- zones.json — 9 zones (ids, node ids, edge ids, stats).
- snegoplan_input.xlsx — sheets "Типы техники", "Машины", "Точки", "Условия" (Russian headers; inspect with openpyxl). Some example coordinates may be outside a zone — snap to nearest graph node.
- SPEC.md — exact JSON contract of the solver.
- hgs — the solver binary (Linux). Run: ./input/hgs in.json out.json

Write a Python package ./prep/ (python3 stdlib + networkx/numpy/scipy/openpyxl which are installed) with CLI:
  python3 -m prep.build --zone 3 --out out/zone3_plows.json --map out/zone3_map.json
It must:
1. Load Excel → vehicle types, vehicles (expand "кол-во" > 1 into separate vehicles with suffixes), points, conditions (snow height cm, density, traffic coef). Use only vehicles whose class is a plow-type (КДМ, грейдер) for this stage. ALL vehicles in the sheet belong to the selected zone's contractor.
2. Tasks = passes: for every edge of the zone (edge.zone == Z): passes_dir = ceil(width_dir_m / work_width) where work_width = the vehicle type's max working width (if classes differ, compute per class; use the max over plow classes for the task count, and store per-class service time). For a two-way street, the edge u->v and its reverse each get their own passes (direction fixed, snow goes right). One-way: all passes in u->v. Each pass = one HGS task with start_node=u, end_node=v.
3. Service time per class = length_m / (v_work_kmh/3.6 * snow_k * slope_k) where snow_k = min(1, h_ref/h) (h_ref from type sheet, default 20 cm), slope_k = 1/(1 + 0.03*max(0,slope_pct)) uphill and 1.0 downhill. If a class's ширина_габарит_м + 0.5 > width_m of the edge → class cannot do it (omit from service_time). Put these coefficients as named constants at the top of prep/formulas.py with comments.
4. weight = {1:10, 2:3, 3:1}[priority] * length_m / 1000 (per km). Priority-1 tasks get deadline = 3*3600 s from shift start with deadline_weight = 50 (constants). 
5. Travel matrices per class: Dijkstra over the WHOLE graph (driving through other zones allowed) with edge time = length / (v_transit*traffic_coef) * uphill factor (1 + 0.02*max(0,slope)), respecting oneway; edges too narrow for the class are forbidden. Compress to the node set {task start/end nodes, vehicle start/end nodes}; unreachable = -1.
6. Vehicles: start/end from Excel coords snapped to nearest graph node; shift from sheet (default 0..28800 s).
7. Objective defaults: w_latency=1, w_deadhead=0.2, w_wait=0, w_overtime=10, w_makespan=0; params time_limit_s=10, seed=1.
8. --map file: everything needed to decode the solution: task id → edge id, direction, pass index, street name, geometry, priority; compressed node index → graph node id; vehicle info.
9. Also write python3 -m prep.decode --map ... --solution ... --out out/zoneZ_routes.geojson that turns solver output into GeoJSON LineStrings per visit (properties: vehicle, task, street, start/end time hh:mm, kind=service) PLUS deadhead legs between tasks reconstructed as real shortest paths on the graph (kind=deadhead).
10. Tests in ./tests/ (plain python asserts runnable with python3 tests/test_prep.py): pass counts, no task without a capable class unless reported, matrix triangle-ish sanity, round-trip decode.
11. Run it for zone 3 with time_limit 10 and save out/zone3_plows.json, out/zone3_solution.json, out/zone3_routes.geojson; print a summary: tasks count, vehicles, objective breakdown, per-vehicle km service/deadhead.
Finish with a short REPORT.md: what you did, formulas, numbers, anything questionable.
