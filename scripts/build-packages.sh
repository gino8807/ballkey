#!/bin/bash
# Build the .rpm, .deb and Arch packages into dist/. No sudo needed.
set -euo pipefail
cd "$(dirname "$0")/.."

./scripts/get-nfpm.sh
mkdir -p dist
for format in rpm deb archlinux; do
    build/tools/nfpm package --config nfpm.yaml --packager "$format" --target dist/
done
echo
ls -1 dist/
