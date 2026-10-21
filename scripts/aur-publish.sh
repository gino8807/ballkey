#!/bin/bash
# Publish the current version (from nfpm.yaml) to the AUR, so Arch users
# get it with `yay -S ballkey` and updates with `yay -Syu`.
# Run it after the version's git tag (v<version>) is pushed to GitHub: the
# AUR package downloads that tag's source. Builds and checks the package
# in an Arch container first. Creates the AUR package the first time.
# No sudo needed; needs your SSH key added to your AUR account.
#   ./scripts/aur-publish.sh             build, check and push
#   ./scripts/aur-publish.sh --no-push   build and check only
set -euo pipefail
cd "$(dirname "$0")/.."

push=true
[[ ${1:-} == --no-push ]] && push=false
version=$(sed -n 's/^version: //p' nfpm.yaml)
url=https://github.com/gino8807/ballkey/archive/refs/tags/v$version.tar.gz

aur=build/aur
rm -rf "$aur"
if $push; then
    git clone -q ssh://aur@aur.archlinux.org/ballkey.git "$aur" 2>/dev/null || {
        echo "Couldn't reach the AUR. Is your SSH key added at https://aur.archlinux.org/account/?"
        exit 1; }
else
    mkdir -p "$aur"
fi

echo "Downloading the v$version source from GitHub..."
sha256=$(curl -fsSL "$url" | sha256sum | cut -d' ' -f1) || {
    echo "Couldn't download $url. Push the v$version tag first."; exit 1; }
sed -e "s/@VERSION@/$version/" -e "s/@SHA256@/$sha256/" packaging/aur/PKGBUILD > "$aur/PKGBUILD"

# pacman's install hooks, made from the scripts all the packages share.
{
    echo "# Made by scripts/aur-publish.sh from packaging/scripts/."
    for hook in post_install:postinstall post_upgrade:postinstall \
                pre_remove:preremove post_remove:postremove; do
        echo
        echo "${hook%%:*}() {"
        grep -v '^#!' "packaging/scripts/${hook#*:}.sh" | sed 's/^/    /;s/^ *$//'
        echo "}"
    done
} > "$aur/ballkey.install"

echo "Building and checking the package in an Arch container..."
podman run --rm -v "$PWD/$aur:/aur:Z" docker.io/library/archlinux:latest bash -c "
set -e
pacman -Syu --noconfirm --needed base-devel namcap >/dev/null 2>&1
pacman -S --noconfirm --needed \$(. /aur/PKGBUILD; echo \${depends[*]}) >/dev/null 2>&1
useradd -m builder
cp -r /aur /home/builder/aur && chown -R builder /home/builder/aur
cd /home/builder/aur
su builder -c 'makepkg --noconfirm >/dev/null && makepkg --printsrcinfo > .SRCINFO'
namcap PKGBUILD *.pkg.tar.zst || true
cp .SRCINFO /aur/
cp *.pkg.tar.zst /aur/"

if ! $push; then
    echo "AUR files and test package ready in $aur (not pushed)."
    exit 0
fi
git -C "$aur" add PKGBUILD .SRCINFO ballkey.install
git -C "$aur" commit -q -m "Update to $version"
git -C "$aur" push -q origin HEAD:master
echo "Published: https://aur.archlinux.org/packages/ballkey"
