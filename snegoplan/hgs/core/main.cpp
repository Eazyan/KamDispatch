#include "hgs.hpp"
#include "json.hpp"
#include <cstdio>
#include <fstream>
#include <iostream>
#include <iterator>
#include <sstream>
#include <stdexcept>
#include <string>
#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#endif

static std::string readAll(const std::string& path) {
    if (path == "-") {
#ifdef _WIN32
        _setmode(_fileno(stdin), _O_BINARY);
#endif
        std::ostringstream ss;
        ss << std::cin.rdbuf();
        return ss.str();
    }
    std::ifstream f(path.c_str(), std::ios::binary);
    if (!f) throw std::runtime_error("cannot open input file: " + path);
    return std::string((std::istreambuf_iterator<char>(f)), std::istreambuf_iterator<char>());
}
static void writeAll(const std::string& path, const std::string& s) {
    if (path == "-") {
#ifdef _WIN32
        _setmode(_fileno(stdout), _O_BINARY);
#endif
        std::fwrite(s.data(), 1, s.size(), stdout);
        std::fflush(stdout);
        return;
    }
    std::ofstream f(path.c_str(), std::ios::binary);
    if (!f) throw std::runtime_error("cannot open output file: " + path);
    f.write(s.data(), (std::streamsize)s.size());
}

static std::string toJson(const Instance& in, const hgs::Result& r, unsigned long long seed) {
    std::string o;
    using json::numTo; using json::escapeTo;
    o += "{\"status\":";
    o += r.unassigned.empty() ? "\"ok\"" : "\"infeasible\"";
    o += ",\"routes\":[";
    for (size_t v = 0; v < r.routes.size(); ++v) {
        const hgs::RouteOut& ro = r.routes[v];
        if (v) o += ',';
        o += "{\"vehicle\":"; escapeTo(o, in.vehicles[v].id);
        o += ",\"visits\":[";
        for (size_t i = 0; i < ro.visits.size(); ++i) {
            const hgs::Visit& vi = ro.visits[i];
            if (i) o += ',';
            o += "{\"task\":"; numTo(o, (double)in.tasks[vi.task].id);
            o += ",\"arrive\":"; numTo(o, vi.arrive);
            o += ",\"start\":"; numTo(o, vi.start);
            o += ",\"end\":"; numTo(o, vi.end);
            o += '}';
        }
        o += "],\"return_time\":"; numTo(o, ro.returnTime);
        o += ",\"deadhead\":"; numTo(o, ro.deadhead);
        o += ",\"wait\":"; numTo(o, ro.wait);
        o += '}';
    }
    o += "],\"unassigned\":[";
    for (size_t i = 0; i < r.unassigned.size(); ++i) {
        if (i) o += ',';
        numTo(o, (double)in.tasks[r.unassigned[i]].id);
    }
    o += "],\"objective\":{\"total\":"; numTo(o, r.obj.total);
    o += ",\"latency\":"; numTo(o, r.obj.latency);
    o += ",\"deadhead\":"; numTo(o, r.obj.deadhead);
    o += ",\"wait\":"; numTo(o, r.obj.wait);
    o += ",\"overtime\":"; numTo(o, r.obj.overtime);
    o += ",\"makespan\":"; numTo(o, r.obj.makespan);
    o += ",\"lateness\":"; numTo(o, r.obj.lateness);
    o += "},\"stats\":{\"iterations\":"; numTo(o, (double)r.iterations);
    o += ",\"time_s\":"; numTo(o, r.timeS);
    o += ",\"best_found_at_s\":"; numTo(o, r.bestFoundS);
    o += ",\"seed\":"; numTo(o, (double)(long long)seed);
    o += "}}\n";
    return o;
}

int main(int argc, char** argv) {
    if (argc < 3) {
        std::fprintf(stderr, "usage: %s input.json output.json   (use '-' for stdin/stdout)\n", argv[0]);
        return 1;
    }
    std::string outPath = argv[2];
    try {
        Instance in = loadInstance(readAll(argv[1]));
        hgs::Result r = hgs::solve(in);
        writeAll(outPath, toJson(in, r, (unsigned long long)in.par.seed));
        return 0;
    } catch (const std::exception& e) {
        std::fprintf(stderr, "error: %s\n", e.what());
        try {
            std::string o = "{\"status\":\"error\",\"error\":";
            json::escapeTo(o, e.what());
            o += "}\n";
            writeAll(outPath, o);
        } catch (...) {}
        return 2;
    }
}
