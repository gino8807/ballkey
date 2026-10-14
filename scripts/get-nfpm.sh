#!/bin/bash
# Download nfpm (the package builder) into build/tools/, checking it
# against the release's published SHA-256. No sudo, nothing installed
# system-wide.
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION=2.47.0
SHA256=0660ca602b2d2d2ae4781a06c692b3eeb9d437ffea05b831d76e41f4a3188783
FILE=nfpm_${VERSION}_Linux_x86_64.tar.gz
URL=https://github.com/goreleaser/nfpm/releases/download/v${VERSION}/${FILE}

if [[ -x build/tools/nfpm ]] && build/tools/nfpm --version 2>/dev/null | grep -q "$VERSION"; then
    echo "nfpm $VERSION already present."
    exit 0
fi

download=$(mktemp -d)
trap 'rm -rf "$download"' EXIT
curl -fsSL -o "$download/$FILE" "$URL"
echo "$SHA256  $download/$FILE" | sha256sum --check --quiet
tar -xzf "$download/$FILE" -C "$download" nfpm
mkdir -p build/tools
install -m755 "$download/nfpm" build/tools/nfpm
echo "nfpm $VERSION ready: build/tools/nfpm"
