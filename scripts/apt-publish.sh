#!/bin/bash
# Publish the .deb in dist/ to the Debian update repository that
# Discover / Software / apt check: a signed apt repository in the apt/
# folder of the gh-pages branch, served by GitHub Pages at
# https://<owner>.github.io/ballkey/apt/. Older versions stay in it.
# Turns GitHub Pages on the first time. No sudo needed.
#   ./scripts/apt-publish.sh             build, sign and push
#   ./scripts/apt-publish.sh --no-push   build and sign into build/gh-pages only
# Needs the signing key from ./scripts/apt-make-key.sh.
set -euo pipefail
cd "$(dirname "$0")/.."

push=true
[[ ${1:-} == --no-push ]] && push=false
keydir=${APT_KEY_DIR:-~/.config/ballkey/apt-signing-key}
[[ -d $keydir ]] || {
    echo "No signing key in $keydir. Run ./scripts/apt-make-key.sh first."; exit 1; }
deb=$(ls dist/*.deb)
version=$(sed -n 's/^version: //p' nfpm.yaml)
[[ $(basename "$deb") == ballkey_${version}-* ]] || {
    echo "dist/ doesn't hold version $version. Run ./scripts/build-packages.sh first."; exit 1; }

# A separate copy of the gh-pages branch (made empty the first time).
site=build/gh-pages
rm -rf "$site"
origin=$(git remote get-url origin)
if git ls-remote --exit-code --heads "$origin" gh-pages >/dev/null; then
    git clone -q --branch gh-pages --single-branch "$origin" "$site"
else
    git init -q -b gh-pages "$site"
    git -C "$site" remote add origin "$origin"
    touch "$site/.nojekyll"   # serve the files as they are
fi
mkdir -p "$site/apt"
cp "$deb" "$site/apt/"

echo "Making the package index in a Debian container..."
podman run --rm -v "$PWD/$site/apt:/repo:Z" -w /repo docker.io/library/debian:13 bash -c "
set -e
rm -f Release InRelease Release.gpg
apt-get update -qq && apt-get install -y -qq apt-utils >/dev/null
apt-ftparchive packages . > Packages
gzip -9kf Packages
apt-ftparchive -o APT::FTPArchive::Release::Origin=BallKey \
    -o APT::FTPArchive::Release::Label=BallKey \
    -o APT::FTPArchive::Release::Description='BallKey updates' \
    release . > /tmp/Release
mv /tmp/Release Release"

echo "Signing..."
gpg --homedir "$keydir" --batch --yes --clearsign -o "$site/apt/InRelease" "$site/apt/Release"
gpg --homedir "$keydir" --batch --yes -abs -o "$site/apt/Release.gpg" "$site/apt/Release"
# The repository must be signed by the key the .deb installs.
gpg --homedir "$keydir" --armor --export | cmp -s - packaging/ballkey-archive-keyring.asc || {
    echo "$keydir isn't the key in packaging/ballkey-archive-keyring.asc."; exit 1; }

git -C "$site" add -A
git -C "$site" -c user.name="$(git config user.name)" -c user.email="$(git config user.email)" \
    commit -q -m "apt: add $(basename "$deb")"
if ! $push; then
    echo "Repository ready in $site/apt (not pushed)."
    exit 0
fi
git -C "$site" push -q origin gh-pages

repo=$(gh repo view --json nameWithOwner -q .nameWithOwner)
if ! gh api "repos/$repo/pages" >/dev/null 2>&1; then
    echo "Turning on GitHub Pages for the gh-pages branch..."
    gh api -X POST "repos/$repo/pages" -f 'source[branch]=gh-pages' -f 'source[path]=/' >/dev/null
fi
echo "Published. GitHub Pages takes a minute or two to update."
