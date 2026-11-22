# hgs — HGS solver core for Снегоплан (contract)

Standalone CLI: `hgs input.json output.json` (or `hgs - -` for stdin/stdout).
Knows nothing about snow/maps: only tasks, vehicles, travel matrices, objective.

## Input JSON
{
  "n_nodes": N,                      // node indices 0..N-1 used by tasks/vehicles
  "classes": {                       // per vehicle class: deadhead travel-time matrix (seconds), N x N, row=from col=to.
    "plow":   {"matrix": [[...], ...]},   // value < 0 means unreachable
    "loader": {"matrix": [[...], ...]}
  },
  "tasks": [ {
     "id": 0,                        // unique int
     "start_node": 3, "end_node": 7, // vehicle enters task at start_node, leaves at end_node
     "service_time": {"plow": 120.0},// seconds per class; class absent => that class cannot do the task
     "weight": 10.0,                 // latency weight (priority * length already folded in)
     "ready_time": 0.0,              // cannot START before this (wait allowed), default 0
     "deadline": 10800.0,            // optional soft deadline for completion (null = none)
     "deadline_weight": 100.0        // penalty per second of lateness (default 0)
  } ],
  "vehicles": [ {
     "id": "П-01", "class": "plow",
     "start_node": 0, "end_node": 1,
     "shift_start": 0.0, "shift_end": 28800.0   // soft: overtime penalised
  } ],
  "objective": {
     "w_latency": 1.0,      // * sum(task.weight * task.completion_time)
     "w_deadhead": 1.0,     // * total deadhead travel time (incl. to first task and to end_node)
     "w_wait": 0.5,         // * total waiting time before ready_time
     "w_overtime": 1000.0,  // * sum over vehicles max(0, return_time - shift_end)
     "w_makespan": 0.0      // * max return_time
  },
  "params": {"time_limit_s": 10, "seed": 1, "pop_min": 25, "pop_gen": 40, "max_iter_no_improve": 20000}
}
Completion time of a task = start + service_time(class). Every task must be served exactly once by a compatible vehicle.

## Output JSON
{
  "status": "ok" | "infeasible",
  "routes": [ {"vehicle": "П-01",
               "visits": [ {"task": 0, "arrive": t, "start": t, "end": t} ],
               "return_time": t, "deadhead": s, "wait": s} ],
  "unassigned": [task ids that no vehicle can serve],
  "objective": {"total": x, "latency": x, "deadhead": x, "wait": x, "overtime": x, "makespan": x},
  "stats": {"iterations": n, "time_s": t, "best_found_at_s": t, "seed": s}
}
All components are the RAW (unweighted) sums; total = weighted sum.
