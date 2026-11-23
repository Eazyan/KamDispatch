# Linux traffic pilot

23.11.2026 · Asia/Kamchatka. Stage 1 only: single slot, finite 3 tiles.
No API, scheduler or durable queue yet. All observations remain experimental.

## Run

```powershell
cd D:\traffic-worker\linux-pilot
$env:PATH = "C:\Program Files\Docker\Docker\resources\bin;" + $env:PATH
docker compose -p kam-traffic-pilot -f compose.traffic-worker.yml build
docker compose -p kam-traffic-pilot -f compose.traffic-worker.yml run --rm capture smoke /spool/smoke-unique
docker compose -p kam-traffic-pilot -f compose.traffic-worker.yml run --name kam-traffic-pilot-run capture capture /spool/pilot-unique --plan /app/containers/traffic-worker/pilot.json
```

Use a new output directory for each attempt; existing captures are not overwritten.
The image contains only capture code and its numerical calibration dependencies.
The Dockerfile includes the frozen three-tile pilot.json. Spool is the only host mount.
No docker.sock, personal browser profile, exposed CDP or network listeners.
Cancellation: `docker stop -t 15 kam-traffic-pilot-run`. No restart policy.
Failed runs stop immediately; no retries or automatic new provider requests.
403/429/CAPTCHA in capture stop the run; inspect failure JSON before any restart.
Keep raw off/on PNG, capture.json hashes and run.json. Status captured_pending_review
is not acceptance or a statement of accuracy. Inspect images for unloaded tiles,
misalignment and placeholders; current baseline uses UI checks and bounded waits,
not a proven readiness detector. No regular capture until provider conditions and
quality/freshness gates are resolved.

## Sources

- https://playwright.dev/python/docs/docker — non-root, Chromium sandbox, init/shared memory.
- seccomp_profile.json copied 23.11.2026 from
  https://raw.githubusercontent.com/moby/profiles/main/seccomp/default.json;
  committed local copy controls subsequent builds (upstream main is not fetched at runtime).
- Existing capture/calibration: scripts/capture_traffic.py, monitoring/traffic_image.py.

Windows PATH may omit system programs over SSH: invoke Windows PowerShell and
Docker by their full paths. Do not replace the global PATH as a workaround.

## Retention (user correction 23.11.2026)

Raw/diagnostic PNGs expire after **three hours** (10800 s), not one hour.
The retention service scans the dedicated spool once a minute, uses filesystem
last-write time (capture creates each file once, without later overwrites), and keeps JSON hashes
and audit logs. It never follows symlinks outside the spool. No screenshot archive
is kept elsewhere after QA. `docker compose ... up -d retention` starts this
cleanup service only, without periodic provider capture.

The initial Compose build progress was buffered by the SSH helper; BuildKit history
incorrectly appeared to have 0 steps. Full logs showed slow image download rather
than a deadlock. A single direct-build restart reused downloaded layers. Prefer
`docker build --progress plain -t kamdispatch-traffic:pilot -f containers/traffic-worker/Dockerfile .`
for transparent diagnostics on this Windows host.

## Observed deployment 23.11.2026

Headless Chromium 156.0.8078.4 smoke passed as uid 1001 with sandbox enabled.
Headed/Xvfb smoke could not capture a screenshot; production pilot uses headless
with a direct Python entrypoint (also allowing SIGTERM delivery).
First 2GIS navigation was rejected by the provider. The finite run stopped with
zero complete pairs, without retries or bypass. No city timing or accuracy result
exists; 5-minute operation is not enabled. Retention runs independently of source
availability and uses no network. Remaining checks include real Linux pairs,
readiness/quality, cancellation during capture, and durable orchestration.

## User steering: persistent Yandex browser

23.11.2026: user requested an always-open Yandex browser on Windows.
Installed Yandex 26.10.3.771 was found; a separate persistent profile is used
at D:\traffic-worker\yandex-profile, without accessing the personal profile.
Scheduled task `KamDispatch Traffic Browser` launches the browser in the logged-in
interactive user session and checks once per minute; it also triggers at logon.
Script: containers/traffic-worker/ensure-yandex.ps1, deployed to
D:\traffic-worker\ensure-yandex.ps1. A signed-out session cannot display the window.
CDP port 9223 binds to loopback only; do not forward it through the router.
The browser starts on about:blank and does not retry the rejected provider.
This is browser lifecycle setup only; the Linux capture adapter does not yet use
this browser. A Windows capture adapter / secure CDP tunnel is a separate next step.
To stop reopening: disable `KamDispatch Traffic Browser` in Task Scheduler.

Retention: `kam-traffic-pilot-retention-1` runs, checks every minute, TTL 10800 s.
Runtime self-test deleted an artificially old PNG and preserved fresh PNG/JSON.
The provider challenge screenshot was visually inspected; no attempt was made
to submit or bypass it. Local QA screenshot copy was deleted immediately.

## Windows attach and alternative-source probe

23.11.2026. Python 3.10.11 was already installed. Dedicated venv:
D:\traffic-worker\venv (playwright 1.64.1, numpy 2.2.6, Pillow 11.3.0).
`scripts/traffic_yandex_worker.py` attaches to the existing dedicated Yandex
context via localhost:9223, reuses the open 2GIS tab, restores viewport and
never closes the browser/context. Shared finite plan validation is in
scripts/traffic_capture_plan.py. Two tests verify browser survival and viewport
restoration on success/failure. Windows first attempt got four UI controls but
timed out because the provider's traffic button was disabled. No complete pair
was accepted. The user confirmed manual use of the same window during diagnostics
and that the traffic button also did not work manually.

By explicit user request, Yandex Maps is checked as an alternative in a separate
tab. `scripts/probe_yandex_maps.py` is one finite raw off/on probe, not a production
provider adapter: no georeferencing, graph matching or source-colour validation.
Do not feed these PNGs through the 2GIS matcher and claim equivalent observations.
The existing three-hour cleanup covers all files under the same spool.
