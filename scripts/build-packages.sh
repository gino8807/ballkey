#!/bin/bash
# Build the packages into dist/. No sudo needed.
#   .deb and Arch: nfpm (nfpm.yaml)
#   .rpm (+ source rpm for COPR): rpmbuild in a Fedora container
#     (packaging/ballkey.spec)
# The version for all of them is the one in nfpm.yaml.
set -euo pipefail
cd "$(dirname "$0")/.."

version=$(sed -n 's/^version: //p' nfpm.yaml)
# Fedora account that owns the COPR update repository (packaging/copr-owner,
# or COPR_OWNER=... for a test build). Checked here because a bad name makes
# the installed repository file break every dnf command.
copr_owner=${COPR_OWNER:-$(cat packaging/copr-owner 2>/dev/null || true)}
if [[ ! $copr_owner =~ ^[A-Za-z0-9_-]+$ ]]; then
    echo "Set the COPR owner (Fedora account name) in packaging/copr-owner first."
    exit 1
fi
rm -rf dist
mkdir -p dist

./scripts/get-nfpm.sh
for format in deb archlinux; do
    build/tools/nfpm package --config nfpm.yaml --packager "$format" --target dist/
done

echo "building rpm in a Fedora container..."
work=build/rpm
rm -rf "$work"
mkdir -p "$work/SOURCES"
# Source tarball of the project files (as they are now, committed or not).
git ls-files -z --cached --others --exclude-standard |
    tar --null -T - --transform "s,^,ballkey-$version/," \
        -czf "$work/SOURCES/ballkey-$version.tar.gz"
cp packaging/scripts/*.sh "$work/SOURCES/"
# Bake the version and COPR owner in, so the source rpm rebuilds anywhere (e.g. COPR).
sed -e "s/%{ballkey_version}/$version/" -e "/^%global copr_owner/s/@COPR_OWNER@/$copr_owner/" \
    packaging/ballkey.spec > "$work/ballkey.spec"
podman run --rm -v "$PWD/$work:/rpm:Z" registry.fedoraproject.org/fedora:44 \
    bash -c "dnf -y -q install rpm-build systemd-rpm-macros >/dev/null 2>&1 &&
             rpmbuild -ba --quiet --define '_topdir /rpm' /rpm/ballkey.spec"
cp "$work"/RPMS/noarch/*.rpm "$work"/SRPMS/*.src.rpm dist/

echo
ls -1 dist/
