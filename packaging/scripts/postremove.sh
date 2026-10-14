#!/bin/sh
# Runs after the package's files are removed (rpm, deb and Arch).
#
#   rpm:   $1 = 0 on removal, 1 or more on upgrade
#   deb:   $1 = remove or purge (others: upgrade, failed-upgrade, abort-*, disappear)
#   Arch:  post_remove only runs on removal ($1 = old version)
case "$1" in
    [1-9] | upgrade | failed-upgrade | abort-* | disappear)
        exit 0 ;;   # an upgrade or a failed step: nothing to clean up
esac

systemctl daemon-reload >/dev/null 2>&1 || true
systemctl reset-failed ballkey.service >/dev/null 2>&1 || true
if [ -d /run/systemd/system ]; then
    systemctl reload dbus >/dev/null 2>&1 || true
fi

# Settings saved from the app. Debian convention: "remove" keeps them,
# "purge" deletes them. rpm and Arch removal delete them.
if [ "$1" != "remove" ]; then
    rm -rf /etc/ballkey
fi
exit 0
