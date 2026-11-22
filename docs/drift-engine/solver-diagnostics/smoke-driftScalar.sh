#!/bin/bash
set -eu
# Pinned upstream sources mounted read-only at /source. Run as non-root.
cp -R /source /tmp/driftScalarDyFoam
cd /tmp/driftScalarDyFoam
export FOAM_USER_APPBIN=/tmp/solver-bin
mkdir -p "$FOAM_USER_APPBIN"
export PATH="$FOAM_USER_APPBIN:$PATH"
sed -i 's/FOAM_APPBIN/FOAM_USER_APPBIN/' Make/files
wmake > /diagnostics/driftScalar-build-user-v2512.log 2>&1
cd tutorials/flatRoof3D
# Two stages and 20 subiterations are an execution smoke, not a converged prediction.
sed -i 's/driftEndTime="505000"/driftEndTime="10000"/' Allrun
sed -i 's/nSubCycles      1000/nSubCycles      20/' constant/erosionDepositionProperties
# Reduce the upstream ~1.5 million-cell mesh to a bounded execution test.
sed -i -E 's/^(nx[123])[[:space:]]+[0-9]+;/\1 8;/;s/^(ny[12])[[:space:]]+[0-9]+;/\1 6;/;s/^nz1[[:space:]]+[0-9]+;/nz1 4;/;s/^nz2[[:space:]]+[0-9]+;/nz2 8;/' system/blockMeshDict
trap 'cp log.* /diagnostics/ 2>/dev/null || true' EXIT
chmod +x Allrun Allrun.pre
./Allrun > /diagnostics/driftScalar-tutorial-driver.log 2>&1
cp log.* /diagnostics/
test -d 10000
# Preserve fields and evolving geometry, not only an exit status.
tar -czf /diagnostics/driftScalar-tutorial-results.tar.gz 0 5000 10000 constant system
