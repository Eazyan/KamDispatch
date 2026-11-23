# Build inputs

23.11.2026. Playwright Python image v1.64.1-noble pinned by manifest digest
330d401ae23c905896a0c26c66c36fc971b621519e7b11b435a8328d7bf6e031
(observed in Docker build on the Windows amd64 worker).

seccomp_profile.json is derived from the official Moby profile:
https://raw.githubusercontent.com/moby/profiles/main/seccomp/default.json
downloaded 23.11.2026, plus unconditional clone/setns/unshare allowance recommended
for Chromium user namespaces in Playwright Docker documentation. The older
Playwright vendor profile failed before process launch with runc 1.3.3
(openat2 / unsafe procfs). Moby profile retains default-deny syscall filtering.
Upstream https://github.com/moby/profiles is Apache-2.0; local bytes are versioned.
Builds do not fetch upstream main.

Python dependencies: playwright==1.64.1, numpy==2.2.6, Pillow==11.3.0.
No paid data access is configured.
