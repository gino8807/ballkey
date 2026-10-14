#!/bin/bash
# Test the packages in fresh containers of each distribution (podman, no
# sudo). Screenshots of the app go to build/screenshots/.
#   ./tests/container-test.sh            all distributions
#   ./tests/container-test.sh debian     just one
set -uo pipefail
cd "$(dirname "$0")/.."

declare -A IMAGE=(
    [fedora]="registry.fedoraproject.org/fedora:44 rpm"
    [debian]="docker.io/library/debian:13 deb"
    [ubuntu]="docker.io/library/ubuntu:26.04 deb"
    [arch]="docker.io/library/archlinux:latest arch"
)
targets=("${@:-fedora debian ubuntu arch}")
targets=(${targets[*]})

ls dist/*.rpm dist/*.deb dist/*.pkg.tar.zst >/dev/null || {
    echo "No packages in dist/. Run ./scripts/build-packages.sh first."; exit 1; }
mkdir -p build/screenshots

summary=()
for t in "${targets[@]}"; do
    read -r image kind <<<"${IMAGE[$t]}"
    echo "################ $t ($image)"
    podman run --rm \
        -v "$PWD/dist:/pkgs:ro,Z" \
        -v "$PWD/tests/in-container.sh:/test.sh:ro,Z" \
        -v "$PWD/build/screenshots:/out:Z" \
        "$image" bash /test.sh "$kind" "$t"
    if [[ $? -eq 0 ]]; then summary+=("$t: PASSED"); else summary+=("$t: FAILED"); fi
done

echo
printf '%s\n' "${summary[@]}"
