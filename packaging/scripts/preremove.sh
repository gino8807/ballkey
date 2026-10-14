#!/bin/sh
# Runs before the package's files are removed (rpm, deb and Arch).
#
#   rpm:   $1 = 0 on removal, 1 or more on upgrade
#   deb:   $1 = remove, or upgrade / failed-upgrade / deconfigure
#   Arch:  pre_remove only runs on removal ($1 = old version)
case "$1" in
    [1-9] | upgrade | failed-upgrade | deconfigure)
        exit 0 ;;   # an upgrade: keep everything running
esac

# Stopping releases the device, so it works normally again.
systemctl disable --now ballkey.service >/dev/null 2>&1 || true
exit 0
