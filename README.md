# BallKey

A simple button remapper for any mouse or trackball on Linux.

Install one package, open BallKey once, choose what each button does,
click **Apply**, and you're done. Your settings apply automatically at
every boot and whenever the device is reconnected.

![BallKey main window](docs/screenshots/main-window.png)

- Works with **any brand** of mouse or trackball.
- Works on **Wayland and X11**, on any desktop (KDE, GNOME, …).
- **No terminal needed**: everything happens in the app; changes ask for
  your password in the normal system prompt.
- **Can't lock you out**: if a change leaves no button doing a left
  click, BallKey asks you to confirm and puts the old settings back after
  15 seconds otherwise.

## About this project

BallKey was vibecoded, and it's my first project. I don't know how to
program: I described what I wanted, an AI coding assistant (Claude Code)
wrote the code, and I tested each step on my own computer (Fedora KDE,
with a Logitech G403 and Kensington Expert) before moving on. The Debian, Ubuntu and
Arch packages have only been tested in containers, not on real hardware.

It works well for me, but no experienced Linux developer has reviewed it
yet, so please keep that in mind. Bug reports and code review are very
welcome.

## Install

Download the package for your distribution, then:

| Distribution | Package | Install |
|---|---|---|
| Fedora 44+ | `ballkey-…fc44.noarch.rpm` | Double-click it (opens Discover / Software), or `sudo dnf install ./ballkey-*.noarch.rpm` |
| Debian 13+, Ubuntu 26.04+ | `ballkey_…_all.deb` | `sudo apt install ./ballkey_*_all.deb` |
| Arch Linux | `ballkey-…-any.pkg.tar.zst` | `sudo pacman -U ballkey-*-any.pkg.tar.zst` |

**Fedora updates:** the Fedora package also adds BallKey's update
repository ([COPR](https://copr.fedorainfracloud.org/coprs/gino8807/ballkey/)),
so Discover / Software and `dnf upgrade` find new versions by themselves.
You can also install straight from it:
`sudo dnf copr enable gino8807/ballkey && sudo dnf install ballkey`.
On Debian, Ubuntu and Arch, download and install new versions by hand for now.

**Older versions** (back to 0.1.0) are on the
[Releases page](https://github.com/gino8807/ballkey/releases), in case
you need one.

Everything else BallKey needs is installed automatically from your
distribution's own repositories. Ubuntu 24.04 isn't supported (it has no
PySide6 package).

## Use

1. Open **BallKey** from your app menu.
2. Pick your mouse or trackball under **Device**.
3. Choose an action for each button. Not sure which button is which?
   Click **Identify button** and press it: its row is highlighted.
4. Click **Apply** and enter your password.

Actions:

- **Clicks:** Left / Right / Middle click, Double left / right / middle
  click, and Left / Right / Middle **drag**: press once to hold the
  button down, move, then press again (or click normally) to let go.
- **Browsing:** Back, Forward.
- **Editing:** Undo, Redo, Cut, Copy, Paste, Select all, Find.
- **Media:** Play / pause, Stop, Next track, Previous track, Volume up,
  Volume down, Microphone on/off.
- **Keyboard key or combo…:** simply press the key or keys you want.
  Keys you can't press (not on your keyboard, or caught by the desktop)
  can be picked from a list in the same window.
- Do nothing, and Default (normal behaviour).

![Recording a key combo](docs/screenshots/key-combo.png)

If no button is left doing a left click, you'll see this. Do nothing and
your previous settings come back:

![Keep these settings?](docs/screenshots/keep-settings.png)

**Button combinations**: under the button list, click **Add
combination…**, tick two or more buttons, and choose what pressing them
*together* does. For example, on a mouse without a scroll wheel, set
**Left + Right** to **Middle click**. The buttons in a combination still
work on their own; they just react a few milliseconds later, while
BallKey waits to see whether you're pressing the other ones too.

**Start remapping at boot** (on by default): untick it if you don't want
your buttons changed after a restart. Opening BallKey turns remapping
back on for that session.

The list shows the common buttons; extra buttons appear when you use
**Identify button** on them. **Restore defaults** sets every button back
to normal and removes all combinations (click **Apply** to save).

**Forget** (next to the device list) removes the device BallKey has set
up, together with its button settings. Use it for a mouse you no longer
have; it shows as "(not connected)" in the list.

## Uninstall

Remove the `ballkey` package (Discover/Software, `sudo dnf remove ballkey`,
`sudo apt purge ballkey`, or `sudo pacman -R ballkey`). Your mouse goes
back to normal immediately. On Debian/Ubuntu, `apt remove` keeps your
settings and `apt purge` deletes them.

## Troubleshooting

- **Status says "Service not running":** `systemctl status ballkey` shows
  what's wrong; the full log is in `journalctl -u ballkey`.
- **A button acts strangely:** stopping the service always returns the
  device to normal: `sudo systemctl stop ballkey`.
- **Device not found:** check that the mouse works at all, then try
  another USB port, or reconnect it over Bluetooth.

Settings are stored in `/etc/ballkey/config.json`.

## How it works

A small background service (Python, `python-evdev`) takes exclusive
control of the chosen device and passes everything through a virtual
device, replacing only the buttons you mapped. It runs as root under
systemd and talks to the app over the system D-Bus; saving settings is
protected by polkit. If the service stops or crashes, the kernel hands
the device straight back, so it always falls back to working normally.

| Part | File |
|---|---|
| Background service | `daemon/ballkeyd.py` |
| Settings app (PySide6) | `gui/ballkey-settings.py` |
| systemd, D-Bus, polkit, launcher files | `packaging/` |
| Package recipe (nfpm) | `nfpm.yaml` |

## Build and test

```
./scripts/build-packages.sh     # .rpm (in a Fedora container), .deb and Arch packages into dist/
./tests/container-test.sh       # install/run/remove in Fedora, Debian, Ubuntu and Arch containers (podman)
```

`build-packages.sh` downloads a pinned, checksum-verified copy of
[nfpm](https://nfpm.goreleaser.com/) into `build/` the first time.

The `.rpm` is built from `packaging/ballkey.spec`; the `.deb` and Arch
packages from `nfpm.yaml`, which also holds the version number for all
three. `./scripts/copr-upload.sh` sends the source package to the COPR
update repository (needs a COPR API token in `~/.config/copr`; the owner
is set in `packaging/copr-owner`).

For development, `sudo ./scripts/dev-install.sh` installs straight from
the source folder (undo with `sudo ./scripts/dev-uninstall.sh`), and
`tools/ballkey-ctl.py` is a command-line client for the service's D-Bus
API.

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
