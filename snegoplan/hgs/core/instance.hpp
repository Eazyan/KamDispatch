#pragma once
#include <cstdint>
#include <string>
#include <vector>

struct Task {
    long long id = 0;
    int sn = 0, en = 0;
    std::vector<double> svc; // per class index; <0 => class cannot serve
    double weight = 1.0, ready = 0.0, deadline = 0.0, dw = 0.0;
    bool hasDeadline = false;
};
struct Vehicle {
    std::string id;
    int cls = -1; // -1: unknown class (vehicle unusable)
    int sn = 0, en = 0;
    double ss = 0.0, se = 1e18;
};
struct Objective {
    double wLatency = 1.0, wDeadhead = 1.0, wWait = 0.5, wOvertime = 1000.0, wMakespan = 0.0;
};
struct Params {
    double timeLimit = 10.0;
    long long seed = 1;
    int popMin = 25, popGen = 40, maxIterNoImprove = 20000;
    int granularK = 20, nbElite = 4, nbClose = 5;
    int popInit = 0;          // 0 => auto
    long long maxIter = 0;    // 0 => unlimited (time limit only)
    bool stopOnStagnation = false; // true iff max_iter_no_improve was given explicitly: then 2 failed restart cycles end the run
    bool verbose = false;
};
struct Instance {
    int N = 0;
    std::vector<std::string> classNames;
    std::vector<std::vector<double>> mat; // per class, flat N*N, unreachable replaced by kUnreachable
    std::vector<Task> tasks;
    std::vector<Vehicle> vehicles;
    Objective obj;
    Params par;
};
const double kUnreachable = 1e7;

Instance loadInstance(const std::string& jsonText); // throws std::runtime_error
