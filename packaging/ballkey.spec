# RPM recipe for BallKey (Fedora). Built by scripts/build-packages.sh
# locally, and by Fedora COPR for the update repository.
# The version comes from nfpm.yaml (passed in as ballkey_version).

# Fedora account that owns the COPR repository (filled in by
# scripts/build-packages.sh from packaging/copr-owner).
%global copr_owner @COPR_OWNER@

Name:           ballkey
Version:        %{ballkey_version}
Release:        1%{?dist}
Summary:        Simple button remapper for mice and trackballs
License:        GPL-3.0-or-later
URL:            https://github.com/gino8807/ballkey
Source0:        ballkey-%{version}.tar.gz
# Shared with the .deb and Arch packages.
Source1:        postinstall.sh
Source2:        preremove.sh
Source3:        postremove.sh
BuildArch:      noarch
BuildRequires:  systemd-rpm-macros

Requires:       python3
Requires:       python3-evdev
Requires:       python3-pyudev
Requires:       python3-dbus
Requires:       python3-gobject-base
Requires:       python3-pyside6
Requires:       polkit

%description
Choose what each button on your mouse or trackball does (back, forward,
copy, paste, key combos and more) in a graphical app. Works with any
brand, on Wayland and X11.

%prep
%autosetup

%build
# Nothing to build: plain Python scripts.

%install
install -Dm755 daemon/ballkeyd.py %{buildroot}%{_prefix}/lib/ballkey/ballkeyd.py
install -Dm755 gui/ballkey-settings.py %{buildroot}%{_prefix}/lib/ballkey/ballkey-settings.py
install -Dm644 packaging/ballkey.service %{buildroot}%{_unitdir}/ballkey.service
install -Dm644 packaging/ballkey-uinput.conf %{buildroot}%{_modulesloaddir}/ballkey-uinput.conf
install -Dm644 packaging/org.ballkey.Daemon.conf %{buildroot}%{_datadir}/dbus-1/system.d/org.ballkey.Daemon.conf
install -Dm644 packaging/org.ballkey.Daemon.service %{buildroot}%{_datadir}/dbus-1/system-services/org.ballkey.Daemon.service
install -Dm644 packaging/org.ballkey.policy %{buildroot}%{_datadir}/polkit-1/actions/org.ballkey.policy
install -Dm644 packaging/ballkey-settings.desktop %{buildroot}%{_datadir}/applications/ballkey-settings.desktop
install -Dm644 packaging/ballkey.svg %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/ballkey.svg
install -d %{buildroot}%{_sysconfdir}/ballkey
# Update repository, so Discover / dnf find new versions.
install -d %{buildroot}%{_sysconfdir}/yum.repos.d
sed 's/@COPR_OWNER@/%{copr_owner}/g' packaging/ballkey-copr.repo \
    > %{buildroot}%{_sysconfdir}/yum.repos.d/ballkey.repo

# The same scripts as the other packages; they read rpm's $1 themselves.
%post
%(cat %{SOURCE1})

%preun
%(cat %{SOURCE2})

%postun
%(cat %{SOURCE3})

%files
%license LICENSE
%doc README.md
%dir %{_prefix}/lib/ballkey
%{_prefix}/lib/ballkey/ballkeyd.py
%{_prefix}/lib/ballkey/ballkey-settings.py
%{_unitdir}/ballkey.service
%{_modulesloaddir}/ballkey-uinput.conf
%{_datadir}/dbus-1/system.d/org.ballkey.Daemon.conf
%{_datadir}/dbus-1/system-services/org.ballkey.Daemon.service
%{_datadir}/polkit-1/actions/org.ballkey.policy
%{_datadir}/applications/ballkey-settings.desktop
%{_datadir}/icons/hicolor/scalable/apps/ballkey.svg
%dir %{_sysconfdir}/ballkey
%config(noreplace) %{_sysconfdir}/yum.repos.d/ballkey.repo

%changelog
* Wed Oct 14 2026 gino <gino8807@protonmail.com>
- See https://github.com/gino8807/ballkey/releases for what changed in each version.
