# hgs - Hybrid Genetic Search for heterogeneous open task routing

Contract: see ../SPEC.md. Usage: `hgs input.json output.json` (`-` = stdin/stdout).

## Build
    cd core && cmake -S . -B build -DCMAKE_BUILD_TYPE=Release && cmake --build build
Windows (MSVC): `cmake -S . -B build -A x64 && cmake --build build --config Release` -> build/Release/hgs.exe.
C++17 only, no dependencies (own JSON reader in json.hpp, UTF-8 preserved).

## Extra params (all optional)
`granular_k` (20), `nb_elite` (4), `nb_close` (5), `pop_init` (2*pop_min), `max_iter` (0=off), `verbose` (trajectory on stderr).
Output `objective.lateness` = sum(deadline_weight*max(0,end-deadline)), added to `total` as-is.
Empty routes contribute 0 to everything. Unreachable matrix entries (<0) are treated as 1e7 s.

## Algorithm
- Giant tour of tasks; Split: tasks are assigned to a vehicle class (forced if only one class can serve, otherwise inherited from the nearest tour neighbour), then per class a Bellman DP over (position, vehicles used; vehicles in fixed order, empty allowed) with exact simulated segment cost (incremental, segment length capped) - O(V_c * m * L).
- Population: biased fitness (cost rank + broken-pairs diversity rank), clone-first survivor selection, binary tournament, OX crossover, restart after `max_iter_no_improve` iterations without improvement. Single-threaded, own splitmix RNG: deterministic per seed. The run stops at time_limit_s; only if `max_iter_no_improve` is given explicitly does it also stop after 2 consecutive restart cycles fail to improve (keeps equal-seed runs identical when stagnation, not time, ends them). Initial population: 3/4 randomised nearest-neighbour giant tours, 1/4 random, capped at ~20% of the time budget.
- Education (local search): granular (k nearest tasks) relocate of 1-2 tasks, swap (1-2 vs 1-2), intra 2-opt (task order reversal; tasks keep direction), inter 2-opt* (two variants), move to empty vehicle, whole-route swap between vehicles; class compatibility checked. Moves are evaluated without building routes: candidate = prefix + <=3 chunks + suffix of a stored route. The suffix cost is an exact piecewise-linear function of arrival time; per position we store value, left/right slope and the range over which the slope stays exact (no wait/deadline kink crossed), giving O(1) evaluation, with an exact early-converging walk outside the range (suffix reuse across vehicles of the same class; overtime and return leg handled separately). Accepted moves are re-verified by full simulation. Build with -DHGS_CHECK to assert fast==exact on every evaluation; "last modified" pruning as in HGS.
- Complexity: LS pass ~ O(n*k*L) per move type (L route length); Split O(n*L*V).

## Limitations
Makespan not in Split (only in LS/fitness). No SWAP*. Class assignment in Split is heuristic. Windows file paths with non-ANSI chars unsupported (use `-`).
