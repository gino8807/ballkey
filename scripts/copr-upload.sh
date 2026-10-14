#!/bin/bash
# Upload the source rpm in dist/ to Fedora COPR, which builds it and
# publishes it in the update repository Discover / dnf check.
# Creates the COPR project the first time. Runs copr-cli in a Fedora
# container (no sudo); needs your API token in ~/.config/copr
# (from https://copr.fedorainfracloud.org/api/).
set -euo pipefail
cd "$(dirname "$0")/.."

owner=$(cat packaging/copr-owner)
srpm=$(ls dist/*.src.rpm)
[[ -f ~/.config/copr ]] || {
    echo "No ~/.config/copr. Save your token from https://copr.fedorainfracloud.org/api/ there first."
    exit 1; }

# label=disable: read the token file without relabelling it for SELinux.
podman run --rm --security-opt label=disable \
    -v "$HOME/.config/copr:/root/.config/copr:ro" \
    -v "$PWD/dist:/dist:ro" \
    registry.fedoraproject.org/fedora:44 bash -c "
set -e
dnf -y -q install copr-cli >/dev/null 2>&1
if ! copr-cli get $owner/ballkey >/dev/null 2>&1; then
    echo 'Creating COPR project $owner/ballkey...'
    copr-cli create ballkey \
        --chroot fedora-44-x86_64 --chroot fedora-44-aarch64 \
        --chroot fedora-rawhide-x86_64 --chroot fedora-rawhide-aarch64 \
        --description 'Simple graphical button remapper for any mouse or trackball (Wayland and X11).' \
        --instructions 'Easiest: download and double-click the .rpm from https://github.com/gino8807/ballkey/releases (it adds this repository). Or: sudo dnf copr enable $owner/ballkey && sudo dnf install ballkey' \
        --repo-priority 99 >/dev/null
fi
echo 'Uploading $(basename "$srpm") and waiting for COPR to build it (a few minutes)...'
copr-cli build $owner/ballkey /dist/$(basename "$srpm")
"
