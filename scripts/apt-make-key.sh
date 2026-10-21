#!/bin/bash
# Make the key that signs the Debian update repository (run once, ever).
# The secret key stays in ~/.config/ballkey/apt-signing-key; the public
# key goes into packaging/ballkey-archive-keyring.asc, which the .deb
# installs so apt trusts the repository. No sudo needed.
# Back up the key folder: without it, new versions can't be published
# to people who already installed BallKey.
set -euo pipefail
cd "$(dirname "$0")/.."

keydir=~/.config/ballkey/apt-signing-key
if [[ -e $keydir ]]; then
    echo "$keydir already exists; not making a new key."
    exit 1
fi
maintainer=$(sed -n 's/^maintainer: .*<\(.*\)>/\1/p' nfpm.yaml)
mkdir -p "$(dirname "$keydir")"
mkdir -m 700 "$keydir"
gpg --homedir "$keydir" --batch --passphrase '' \
    --quick-gen-key "BallKey updates <$maintainer>" ed25519 sign never
gpg --homedir "$keydir" --armor --export > packaging/ballkey-archive-keyring.asc
echo "Public key saved to packaging/ballkey-archive-keyring.asc (commit it)."
echo "Back up $keydir somewhere safe."
