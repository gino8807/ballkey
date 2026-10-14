#!/bin/bash
# Install BallKey from this folder for testing (before the real package
# exists). Undo everything with scripts/dev-uninstall.sh.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ $EUID -ne 0 ]]; then
    echo "Please run with sudo: sudo $0"
    exit 1
fi

install -Dm755 daemon/ballkeyd.py /usr/lib/ballkey/ballkeyd.py
# Keep existing settings (they may have been changed from the app).
if [[ ! -e /etc/ballkey/config.json ]]; then
    install -Dm644 config.json /etc/ballkey/config.json
fi
install -Dm644 packaging/ballkey.service /etc/systemd/system/ballkey.service
install -Dm644 packaging/ballkey-uinput.conf /etc/modules-load.d/ballkey-uinput.conf
install -Dm644 packaging/org.ballkey.Daemon.conf /usr/share/dbus-1/system.d/org.ballkey.Daemon.conf
install -Dm644 packaging/org.ballkey.policy /usr/share/polkit-1/actions/org.ballkey.policy

modprobe uinput
systemctl reload dbus
systemctl daemon-reload
systemctl enable ballkey.service
systemctl restart ballkey.service

echo
echo "BallKey installed and running."
echo "Check it with:  systemctl status ballkey"
