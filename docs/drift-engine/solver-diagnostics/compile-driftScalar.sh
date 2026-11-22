#!/bin/bash
set -eu
# Run in opencfd/openfoam-default:2512 with upstream source mounted at /source.
cp -R /source /tmp/driftScalarDyFoam
cd /tmp/driftScalarDyFoam
wmake
