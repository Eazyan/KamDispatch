#pragma once
#include "instance.hpp"
#include <vector>

namespace hgs {

struct Visit { int task; double arrive, start, end; };
struct RouteOut {
    std::vector<Visit> visits;
    double returnTime = 0, deadhead = 0, wait = 0;
};
struct Components { double total = 0, latency = 0, deadhead = 0, wait = 0, overtime = 0, makespan = 0, lateness = 0; };
struct TrajPoint { double t; long long iter; double cost; };
struct Result {
    std::vector<RouteOut> routes;      // one per vehicle (input order)
    std::vector<int> unassigned;       // task indices (into Instance::tasks)
    Components obj;
    long long iterations = 0;
    double timeS = 0, bestFoundS = 0;
    std::vector<TrajPoint> trajectory;
};

Result solve(const Instance& in);

} // namespace hgs
