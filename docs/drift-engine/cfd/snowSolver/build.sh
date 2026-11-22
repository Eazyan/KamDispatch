#!/bin/bash
source /usr/lib/openfoam/openfoam2512/etc/bashrc
set -eo pipefail
# Build in a temporary writable container directory, preserving host source.
cp -R /audit/snowSolver /tmp/kamchatkaSnowFoam-src
cd /tmp/kamchatkaSnowFoam-src
wmake

cp "$FOAM_USER_APPBIN/kamchatkaSnowFoam" /runtime/kamchatkaSnowFoam
