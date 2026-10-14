#!/bin/sh
# Runs after the package is installed or upgraded (rpm, deb and Arch).
#
# How each package manager says "first install" vs "upgrade":
#   rpm:   $1 = 1 on first install, 2 or more on upgrade
#   deb:   $1 = configure, $2 = previous version (empty on first install)
#   Arch:  post_install gets $1 = new version;
#          post_upgrade gets $1 = new version, $2 = old version
first_install=true
case "$1" in
    [2-9]) first_install=false ;;
esac
[ -n "$2" ] && first_install=false

# Pick up the new service, D-Bus and launcher files.
systemctl daemon-reload >/dev/null 2>&1 || true
modprobe uinput >/dev/null 2>&1 || true
gtk-update-icon-cache -q /usr/share/icons/hicolor >/dev/null 2>&1 || true
update-desktop-database -q /usr/share/applications >/dev/null 2>&1 || true

if $first_install; then
    # Start remapping at boot by default. On upgrades we leave this
    # alone, so a user who turned it off in the app keeps it off.
    systemctl enable ballkey.service >/dev/null 2>&1 || true
fi

# Only touch running services on a live system (not in a container
# or image build).
if [ -d /run/systemd/system ]; then
    systemctl reload dbus >/dev/null 2>&1 || true
    if $first_install; then
        systemctl start ballkey.service >/dev/null 2>&1 || true
    else
        # Upgrade: restart if it was running, so the new code is used.
        systemctl try-restart ballkey.service >/dev/null 2>&1 || true
    fi
fi
exit 0
