#!/bin/bash
# Runs as root inside a fresh container (see tests/container-test.sh):
# install the package, check the service and app work, then remove it.
# Containers have no systemd, real mouse or polkit agent, so this checks
# packaging, dependencies, D-Bus and the app's window; not remapping.
set -uo pipefail
kind=$1      # rpm, deb or arch
name=$2      # label for the screenshot

failures=0
step() { echo; echo "=== $*"; }
ok()   { echo "  PASS: $*"; }
bad()  { echo "  FAIL: $*"; failures=$((failures + 1)); }
check() { local what=$1; shift; if "$@" >/dev/null 2>&1; then ok "$what"; else bad "$what"; fi; }

step "Install package (dependencies from the distro's own repositories)"
case $kind in
    deb)
        export DEBIAN_FRONTEND=noninteractive
        apt-get update -qq
        apt-get install -y -qq /pkgs/ballkey_*_all.deb fonts-dejavu-core >/tmp/install.log 2>&1 ;;
    arch)
        pacman -Syu --noconfirm >/tmp/install.log 2>&1 &&
        pacman -U --noconfirm /pkgs/ballkey-*-any.pkg.tar.zst >>/tmp/install.log 2>&1 &&
        pacman -S --noconfirm ttf-dejavu >>/tmp/install.log 2>&1 ;;
    rpm)
        dnf install -y -q /pkgs/ballkey-*.noarch.rpm >/tmp/install.log 2>&1 &&
        dnf install -y -q dejavu-sans-fonts dbus-daemon >>/tmp/install.log 2>&1 ;;
esac
if [[ $? -eq 0 ]]; then ok "installed"; else bad "install failed"; tail -30 /tmp/install.log; exit 1; fi

step "Installed files"
for f in /usr/lib/ballkey/ballkeyd.py /usr/lib/ballkey/ballkey-settings.py \
         /usr/lib/systemd/system/ballkey.service \
         /usr/lib/modules-load.d/ballkey-uinput.conf \
         /usr/share/dbus-1/system.d/org.ballkey.Daemon.conf \
         /usr/share/dbus-1/system-services/org.ballkey.Daemon.service \
         /usr/share/polkit-1/actions/org.ballkey.policy \
         /usr/share/applications/ballkey-settings.desktop \
         /usr/share/icons/hicolor/scalable/apps/ballkey.svg \
         /usr/share/licenses/ballkey/LICENSE; do
    check "$f" test -e "$f"
done
check "service enabled at boot" \
    test -L /etc/systemd/system/multi-user.target.wants/ballkey.service

step "Service starts and answers on D-Bus"
mkdir -p /run/dbus
[[ -s /etc/machine-id ]] || dbus-uuidgen > /etc/machine-id
dbus-daemon --system --fork
python3 /usr/lib/ballkey/ballkeyd.py >/tmp/daemon.log 2>&1 &
daemon=$!
for _ in $(seq 50); do
    busctl status org.ballkey.Daemon >/dev/null 2>&1 && break
    sleep 0.1
done
status=$(busctl call org.ballkey.Daemon /org/ballkey/Daemon \
         org.ballkey.Daemon Status 2>&1)
echo "  Status: $status"
check "Status() answers" grep -q 'running.*true' <<<"$status"
check "ListDevices() answers" busctl call org.ballkey.Daemon \
    /org/ballkey/Daemon org.ballkey.Daemon ListDevices

step "Settings app opens (invisible window) and talks to the service"
PYTHONDONTWRITEBYTECODE=1 QT_QPA_PLATFORM=offscreen timeout 30 python3 - "$name" <<'EOF'
import importlib.util, sys
spec = importlib.util.spec_from_file_location(
    "gui", "/usr/lib/ballkey/ballkey-settings.py")
gui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gui)
from PySide6.QtWidgets import QApplication
app = QApplication([])
w = gui.MainWindow(gui.DaemonClient())
w.show()
app.processEvents()
print("  status line:", w.status.text())
w.grab().save(f"/out/{sys.argv[1]}.png")
sys.exit(0 if w.status.text().startswith("Service running") else 1)
EOF
if [[ $? -eq 0 ]]; then ok "app window works"; else bad "app window"; fi

kill -TERM $daemon; wait $daemon
check "service stops cleanly" test $? -eq 0
if grep -qE "ERROR|Traceback" /tmp/daemon.log; then
    bad "errors in service log:"; sed "s/^/    /" /tmp/daemon.log
fi

step "Uninstall"
mkdir -p /etc/ballkey && echo '{}' > /etc/ballkey/config.json
case $kind in
    deb)
        apt-get remove -y -qq ballkey >/tmp/remove.log 2>&1
        check "remove keeps settings (Debian convention)" test -e /etc/ballkey/config.json
        apt-get purge -y -qq ballkey >>/tmp/remove.log 2>&1 ;;
    arch) pacman -R --noconfirm ballkey >/tmp/remove.log 2>&1 ;;
    rpm)  dnf remove -y -q ballkey >/tmp/remove.log 2>&1 ;;
esac
check "program files gone" test ! -e /usr/lib/ballkey
check "license folder gone" test ! -e /usr/share/licenses/ballkey
check "settings gone" test ! -e /etc/ballkey
check "boot link gone" \
    test ! -e /etc/systemd/system/multi-user.target.wants/ballkey.service
check "launcher gone" test ! -e /usr/share/applications/ballkey-settings.desktop

echo
if [[ $failures -eq 0 ]]; then echo "ALL PASSED ($name)"; else echo "$failures FAILED ($name)"; fi
exit $failures
