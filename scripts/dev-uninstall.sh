#!/bin/bash
# Remove everything scripts/dev-install.sh put on the system.
set -uo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Please run with sudo: sudo $0"
    exit 1
fi

systemctl disable --now ballkey.service 2>/dev/null
rm -f /etc/systemd/system/ballkey.service
rm -f /etc/modules-load.d/ballkey-uinput.conf
rm -rf /usr/lib/ballkey /etc/ballkey
systemctl daemon-reload
systemctl reset-failed ballkey.service 2>/dev/null

echo "BallKey removed. Your mouse works normally."
