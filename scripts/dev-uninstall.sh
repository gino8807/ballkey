#!/bin/bash
# Remove everything scripts/dev-install.sh put on the system.
set -uo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Please run with sudo: sudo $0"
    exit 1
fi

# The real BallKey package, if installed, as e.g. "ballkey-0.1.4-1.fc44.noarch".
packaged_version() {
    { rpm -q ballkey || dpkg-query -W -f='ballkey ${Version}\n' ballkey ||
      pacman -Q ballkey; } 2>/dev/null | grep -v 'not installed' | head -1
}
# Command that puts the package's own files back.
reinstall_command() {
    if command -v dnf >/dev/null; then echo "sudo dnf reinstall ballkey"
    elif command -v apt >/dev/null; then echo "sudo apt install --reinstall ballkey"
    else echo "sudo pacman -S ballkey"; fi
}

package=$(packaged_version)
if [[ -n $package ]]; then
    # The package uses the same files as the dev install. Deleting them
    # would break the package and lose the settings saved from the app,
    # so only remove what the dev install added on top.
    rm -f /etc/systemd/system/ballkey.service
    rm -f /etc/modules-load.d/ballkey-uinput.conf
    systemctl daemon-reload
    echo "The BallKey package ($package) is installed, so its files and"
    echo "your settings were left alone. To replace the development copy"
    echo "with the package's own files, run:"
    echo "  $(reinstall_command)"
    exit 0
fi

systemctl disable --now ballkey.service 2>/dev/null
rm -f /etc/systemd/system/ballkey.service
rm -f /etc/modules-load.d/ballkey-uinput.conf
rm -f /usr/share/dbus-1/system.d/org.ballkey.Daemon.conf
rm -f /usr/share/dbus-1/system-services/org.ballkey.Daemon.service
rm -f /usr/share/polkit-1/actions/org.ballkey.policy
rm -f /usr/share/applications/ballkey-settings.desktop
rm -f /usr/share/icons/hicolor/scalable/apps/ballkey.svg
rm -rf /usr/lib/ballkey /etc/ballkey
systemctl reload dbus
systemctl daemon-reload
systemctl reset-failed ballkey.service 2>/dev/null

echo "BallKey removed. Your mouse works normally."
