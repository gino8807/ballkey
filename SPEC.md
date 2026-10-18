# BallKey: Simple Button Remapper for Linux

Working name: **BallKey** (rename anytime).

## What it is

A simple graphical button remapper for any mouse or trackball on Linux, inspired by Kensington Works. The user installs one package, opens the app once, assigns actions to buttons, clicks Apply, and never has to open it again. Mappings apply automatically at every boot and whenever the device is reconnected.

**Not in scope for v1:** per-app profiles, scroll/pointer speed, multiple presets. Keep it simple.

## About me (read this first, Claude Code)

- I don't know how to code. Explain what you're doing in plain language, and give me exact test steps ("plug in the trackball, press the top-left button, you should see…").
- I'm comfortable with Linux and running commands you give me, but **ask before any command that uses sudo or changes my system**.
- Work in the phases below, in order. Don't start the next phase until I confirm the current one works.
- Use git. Commit at the end of each phase with a clear message.
- My test machine: Fedora KDE Plasma (Wayland). Test device: Kensington Expert trackball, but nothing in the code may be Kensington-specific.

## Hard requirements

1. **Works with any brand** of mouse or trackball. Identify buttons by what the device reports, never by hardcoded model.
2. **No terminal for end users.** Installing the package sets everything up; the app is fully graphical.
3. **Loads at boot** with no user action (on by default; the app has a "Start remapping at boot" toggle to turn this off), and **reapplies on reconnect** (unplug/replug, wireless wake, sleep/resume).
4. **Can't lock the user out.** If the user removes left click from every button, show a confirmation that auto-reverts in 15 seconds unless confirmed (like display settings). If the config is broken or the service crashes, the device must fall back to working normally.
5. **Works on Wayland and X11**, any desktop (KDE, GNOME, etc.).

## Architecture

**Two parts:**

1. **Background service (daemon)**, runs as root under systemd
   - Python 3 with `python-evdev`.
   - Finds the configured device by vendor ID, product ID and name.
   - Exclusively grabs it and creates a virtual device through `/dev/uinput` that has mouse buttons, movement, scroll wheel (including hi-res scroll) and the keyboard keys needed for shortcut actions.
   - Forwards every event unchanged, except mapped buttons, which are replaced with their assigned action.
   - Watches for hotplug with `pyudev` and re-grabs automatically when the device reappears.
   - Reads its settings from `/etc/ballkey/config.json`.
   - Releases the grab cleanly on stop or crash so the device works normally.
   - Ensures the `uinput` kernel module loads at boot (ship a `modules-load.d` file).

2. **Settings app (GUI)**, runs as the normal user
   - Python 3 with **PySide6 (Qt)** so it looks native on KDE and works fine on GNOME.
   - Talks to the daemon over **system D-Bus**. Changing settings requires a **polkit** authorization (standard graphical password prompt, no sudo).
   - The GUI never reads `/dev/input` directly; the daemon does it on the GUI's behalf.

## D-Bus API (daemon)

- `ListDevices()` → mice/trackballs currently connected (name, vendor, product, list of buttons they report)
- `StartIdentify(device)` / `StopIdentify()` → while active, the daemon emits a `ButtonPressed(code)` signal so the GUI can show "you pressed button X"
- `GetConfig()` / `SetConfig(json)` → read/save mappings and apply immediately (polkit-protected)
- `Status()` → running, device connected or not, last error

## GUI flow

1. Dropdown: pick a device (show its friendly name).
2. A list of the device's buttons, each with an action dropdown beside it.
3. "Identify button" mode: "Press a button on your device…" then highlights that row.
4. Apply button, plus "Restore defaults".
5. Small status line: "Service running · Device connected".

## v1 action list

- Left click, right click, middle click
- Double-click
- Back, forward
- Copy, paste, undo
- Custom key combo (user presses the combo to record it)
- Do nothing (disable the button)
- Default (button behaves normally)

**More actions** (added 2026-10-18): Redo (Ctrl+Shift+Z), Cut, Select
all, Find; double right/middle click; left/right/middle drag lock (one
press holds the button, the next press or a real click of that button
lets go; changing settings also lets go); media keys (play/pause, stop,
next/previous track, volume up/down, mic mute). The key combo dialog can
also pick any key from Esc to Mic mute from a list.

## Config file example

```json
{
  "version": 1,
  "device": { "vendor": "047d", "product": "1020", "name": "Kensington Expert Mouse" },
  "mappings": {
    "BTN_SIDE": { "action": "back" },
    "BTN_EXTRA": { "action": "keycombo", "keys": ["KEY_LEFTCTRL", "KEY_C"] }
  },
  "chords": [
    { "buttons": ["BTN_LEFT", "BTN_RIGHT"], "action": "middle" }
  ]
}
```

**Button combinations (chords)** were added after v1 (2026-10-16): two or
more buttons pressed within 50 ms of each other run the combination's
action (any action except Default) instead of their own. The action is
held until all of the combination's buttons are released. A button that
is part of a combination waits up to 50 ms before acting normally.

(The IDs above are illustrative; read the real ones from the device.)

## Packaging

- Use **nfpm** with one config to build `.rpm`, `.deb` and Arch `.pkg.tar.zst` (pacman) packages.
- The package installs: the daemon, the GUI, a `.desktop` launcher with icon, the systemd service, the D-Bus and polkit policy files, and the `modules-load.d` file.
- Post-install script: enable and start the systemd service. Post-remove script: stop and disable it.
- Python dependencies: prefer distro packages where available; otherwise bundle them so install never needs pip.

## Phases

**Order changed (2026-10-14):** after Phase 2, do Phase 4 (D-Bus + polkit) and Phase 5 (GUI) before Phase 3 (packaging), so the first package includes the app.

**Phase 1: Remap engine.** Daemon script that reads a hand-written config, grabs the device, and remaps one button. Run it manually to test. ✅ when: a side button on the trackball does "back" in Firefox, everything else works normally, and stopping the script returns the device to normal.

**Phase 2: Boot and hotplug.** systemd service, `uinput` autoload, pyudev reconnect handling, fail-safe passthrough. ✅ when: mapping works after a reboot with no action from me, and survives unplug/replug.

**Phase 3: Packages.** Build `.rpm`, `.deb` and Arch (pacman) packages with nfpm that install and enable everything. Upgrades must keep the user's settings and their boot on/off choice. ✅ when: I can install the .rpm by double-clicking it, reboot, and the mapping works; uninstalling removes it cleanly.

**Phase 4: D-Bus + polkit.** Add the API above. ✅ when: a small test tool can list devices, identify a button press, and change a mapping with a password prompt.

**Phase 5: GUI.** Build the settings app and the lockout-protection countdown. ✅ when: I can remap every button on my trackball using only the app, and the 15-second revert works.

**Phase 6: Other distros.** Test the `.deb` and Arch packages in a VM or container of Ubuntu/Debian and Arch. Add a README with install instructions and screenshots.

## Later ideas (not v1)

Scroll and pointer speed, slow-pointer precision toggle, text snippets, launch-an-app action, per-app profiles, Fedora COPR repo, AUR entry.
