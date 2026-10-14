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
rm -f /usr/share/dbus-1/system.d/org.ballkey.Daemon.conf
rm -f /usr/share/polkit-1/actions/org.ballkey.policy
rm -f /usr/share/applications/ballkey-settings.desktop
rm -f /usr/share/icons/hicolor/scalable/apps/ballkey.svg
rm -rf /usr/lib/ballkey /etc/ballkey
systemctl reload dbus
systemctl daemon-reload
systemctl reset-failed ballkey.service 2>/dev/null

echo "BallKey removed. Your mouse works normally."
