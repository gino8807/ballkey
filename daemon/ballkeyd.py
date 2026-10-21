#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""BallKey daemon: remaps mouse/trackball buttons on Linux.

How it works:
  1. Find the configured device by vendor ID, product ID and name.
  2. "Grab" it, so no other program sees its events directly. Its
     keyboard part, if it has one (buttons that type e.g. Ctrl+C), is
     grabbed too.
  3. Create a virtual device through /dev/uinput.
  4. Copy every event from the real device to the virtual one, except
     mapped buttons, which are replaced by their assigned action.

When the device is unplugged (or goes to sleep), the daemon waits and
grabs it again as soon as it comes back.

The settings app talks to the daemon over the system D-Bus (see the
Daemon class below). Saving settings requires a polkit authorization.

Fail-safe: if this program stops for any reason (Ctrl+C, crash, killed),
the kernel releases the grab and removes the virtual device, so the real
device goes back to working normally. If the config file is broken, the
daemon never grabs the device at all.

Usage (needs root):
  ballkeyd.py --list                 show connected mice/trackballs
  ballkeyd.py --watch [-c CONFIG]    print buttons as you press them
  ballkeyd.py [-c CONFIG]            run the daemon
"""

import argparse
import json
import logging
import os
import signal
import sys
import tempfile

import dbus
import dbus.service
import evdev
import pyudev
from dbus.mainloop.glib import DBusGMainLoop
from evdev import ecodes
import gi
from gi.repository import GLib

# Newer GLib moved unix_signal_add to GLibUnix.signal_add. Older ones
# have no GLibUnix, or (e.g. Debian 13) a GLibUnix without it.
try:
    gi.require_version("GLibUnix", "2.0")
    from gi.repository import GLibUnix
    signal_add = GLibUnix.signal_add
except (ImportError, ValueError, AttributeError):
    signal_add = GLib.unix_signal_add

DEFAULT_CONFIG = "/etc/ballkey/config.json"
VIRTUAL_NAME_PREFIX = "BallKey"
# How often to look for the device even if no plug-in event arrives.
RETRY_SECONDS = 10
# Identify mode switches itself off after this long.
IDENTIFY_SECONDS = 60
# Buttons pressed within this many milliseconds of each other count as
# pressed together (for button combinations). Same as libinput uses.
CHORD_MS = 50

BUS_NAME = "org.ballkey.Daemon"
OBJECT_PATH = "/org/ballkey/Daemon"
INTERFACE = "org.ballkey.Daemon"
POLKIT_ACTION = "org.ballkey.configure"
SERVICE_UNIT = "ballkey.service"

EMPTY_CONFIG = {"version": 1, "device": None, "mappings": {},
                "chords": []}

log = logging.getLogger("ballkey")


# --- Actions ---------------------------------------------------------------
# Each action says what to send while the button is held down.
# "buttons"/"keys" are pressed on button-down and released on button-up.

SIMPLE_ACTIONS = {
    "left": ["BTN_LEFT"],
    "right": ["BTN_RIGHT"],
    "middle": ["BTN_MIDDLE"],
    "back": ["BTN_SIDE"],
    "forward": ["BTN_EXTRA"],
    "undo": ["KEY_LEFTCTRL", "KEY_Z"],
    "redo": ["KEY_LEFTCTRL", "KEY_LEFTSHIFT", "KEY_Z"],
    "cut": ["KEY_LEFTCTRL", "KEY_X"],
    "copy": ["KEY_LEFTCTRL", "KEY_C"],
    "paste": ["KEY_LEFTCTRL", "KEY_V"],
    "selectall": ["KEY_LEFTCTRL", "KEY_A"],
    "find": ["KEY_LEFTCTRL", "KEY_F"],
    "micmute": ["KEY_MICMUTE"],
    "volumeup": ["KEY_VOLUMEUP"],
    "volumedown": ["KEY_VOLUMEDOWN"],
    "playpause": ["KEY_PLAYPAUSE"],
    "stop": ["KEY_STOPCD"],
    "nexttrack": ["KEY_NEXTSONG"],
    "previoustrack": ["KEY_PREVIOUSSONG"],
}
# One press clicks this button twice.
DOUBLE_CLICKS = {
    "doubleclick": "BTN_LEFT",
    "doubleright": "BTN_RIGHT",
    "doublemiddle": "BTN_MIDDLE",
}
# Drag lock: one press holds this button down, the next press lets go.
# Pressing the real button also lets go.
DRAGS = {
    "dragleft": "BTN_LEFT",
    "dragright": "BTN_RIGHT",
    "dragmiddle": "BTN_MIDDLE",
}
OTHER_ACTIONS = {"keycombo", "none", "default"}

# Wheel tilt arrives as sideways scrolling, not as button presses, so
# each direction gets a made-up button code (above any real key code).
TILT_LEFT = ecodes.KEY_MAX + 1
TILT_RIGHT = ecodes.KEY_MAX + 2
TILT_BUTTONS = {"TILT_LEFT": TILT_LEFT, "TILT_RIGHT": TILT_RIGHT}
REL_HWHEEL_HI_RES = ecodes.ecodes.get("REL_HWHEEL_HI_RES", 12)


def tilt_button(value):
    """Which tilt button a sideways scroll step belongs to."""
    return TILT_LEFT if value < 0 else TILT_RIGHT


def code_of(name):
    """Turn a name like 'BTN_SIDE' into its numeric code."""
    try:
        return ecodes.ecodes[name]
    except (KeyError, TypeError):
        raise ValueError(f"unknown key or button name: {name}") from None


MODIFIERS = {ecodes.KEY_LEFTCTRL, ecodes.KEY_RIGHTCTRL, ecodes.KEY_LEFTSHIFT,
             ecodes.KEY_RIGHTSHIFT, ecodes.KEY_LEFTALT, ecodes.KEY_RIGHTALT,
             ecodes.KEY_LEFTMETA, ecodes.KEY_RIGHTMETA}


def key_button(codes):
    """The mapping key for a mouse button that types these keys: the key
    code itself for one key, or a set of codes for a combo like Ctrl+C."""
    return next(iter(codes)) if len(codes) == 1 else frozenset(codes)


def keys_name(codes):
    """Readable name like 'KEY_LEFTCTRL+KEY_C', modifiers first."""
    return "+".join(name_of(c) for c in sorted(
        codes, key=lambda c: (c not in MODIFIERS, c)))


def button_code(name):
    """Like code_of, but also knows the wheel tilt buttons and buttons
    that type a key combo (named like 'KEY_LEFTCTRL+KEY_C')."""
    if name in TILT_BUTTONS:
        return TILT_BUTTONS[name]
    if isinstance(name, str) and "+" in name:
        codes = {code_of(n) for n in name.split("+")}
        if len(codes) < 2:
            raise ValueError(f"{name}: key combo needs different keys")
        return frozenset(codes)
    return code_of(name)


def name_of(code):
    """Turn a numeric button/key code into a readable name."""
    for tilt, tilt_code in TILT_BUTTONS.items():
        if code == tilt_code:
            return tilt
    name = ecodes.BTN.get(code) or ecodes.KEY.get(code) or f"code {code}"
    # Some codes have several names, e.g. ('BTN_LEFT', 'BTN_MOUSE').
    return name[0] if isinstance(name, (list, tuple)) else name


class Mapping:
    """One button's replacement action, ready to run."""

    def __init__(self, button, spec):
        self.button = button
        if not isinstance(spec, dict):
            raise ValueError(f"{button}: mapping must be an object")
        self.action = spec.get("action")
        if self.action in SIMPLE_ACTIONS:
            self.codes = [code_of(n) for n in SIMPLE_ACTIONS[self.action]]
        elif self.action in DOUBLE_CLICKS:
            self.codes = [code_of(DOUBLE_CLICKS[self.action])]
        elif self.action in DRAGS:
            self.codes = [code_of(DRAGS[self.action])]
        elif self.action == "keycombo":
            keys = spec.get("keys")
            if not isinstance(keys, list) or not keys:
                raise ValueError(f"{button}: keycombo needs a 'keys' list")
            self.codes = [code_of(n) for n in keys]
        elif self.action in OTHER_ACTIONS:
            self.codes = []
        else:
            raise ValueError(f"{button}: unknown action {self.action!r}")

    def handle(self, ui, value, locked):
        """Run the action. value: 1 = pressed, 0 = released, 2 = auto-repeat.
        locked: buttons a drag action is holding down (shared by all
        mappings, so any release of that button ends the drag)."""
        if value == 2 or self.action == "none":
            return
        if self.action in DOUBLE_CLICKS:
            if value == 1:
                for _ in range(2):
                    ui.write(ecodes.EV_KEY, self.codes[0], 1)
                    ui.syn()
                    ui.write(ecodes.EV_KEY, self.codes[0], 0)
                    ui.syn()
                locked.discard(self.codes[0])
            return
        if self.action in DRAGS:
            if value == 1:
                code = self.codes[0]
                if code in locked:
                    locked.discard(code)
                    ui.write(ecodes.EV_KEY, code, 0)
                else:
                    locked.add(code)
                    ui.write(ecodes.EV_KEY, code, 1)
            return
        # Press in order, release in reverse order (like a person would).
        for code in (self.codes if value == 1 else reversed(self.codes)):
            ui.write(ecodes.EV_KEY, code, value)
            if value == 0:
                locked.discard(code)
        # The real device's own SYN_REPORT follows and finishes the frame.


class Chord:
    """Several buttons pressed together, which do their own action
    (e.g. Left + Right together = Middle click)."""

    def __init__(self, spec):
        if not isinstance(spec, dict):
            raise ValueError("combination must be an object")
        names = spec.get("buttons")
        if not isinstance(names, list) or len(set(names)) < 2:
            raise ValueError("combination needs at least two different "
                             "'buttons'")
        self.names = sorted(set(names))
        self.label = " + ".join(self.names)
        if any(n in TILT_BUTTONS for n in self.names):
            # A tilt has no "held down", so it can't be pressed together.
            raise ValueError(f"{self.label}: wheel tilt can't be part of "
                             "a combination")
        self.buttons = frozenset(code_of(n) for n in self.names)
        if spec.get("action") == "default":
            raise ValueError(f"{self.label}: combination can't be 'default'")
        self.mapping = Mapping(self.label, spec)


# --- Config ----------------------------------------------------------------

def parse_config(cfg, strict):
    """Check a config. Returns (device, mappings, chords); device may be
    None.

    strict=True: any bad mapping is an error (used when the app saves).
    strict=False: bad mappings are logged and skipped (used at startup),
    so one mistake doesn't disable the other buttons.
    """
    if not isinstance(cfg, dict) or cfg.get("version") != 1:
        raise ValueError("config 'version' must be 1")
    dev = cfg.get("device")
    if dev is not None:
        try:
            int(dev["vendor"], 16)
            int(dev["product"], 16)
        except (TypeError, KeyError, ValueError):
            raise ValueError("config 'device' needs hex 'vendor' and "
                             "'product' IDs") from None
    raw = cfg.get("mappings") or {}
    if not isinstance(raw, dict):
        raise ValueError("config 'mappings' must be an object")
    mappings = {}
    for button, spec in raw.items():
        try:
            m = Mapping(button, spec)
            if m.action != "default":
                mappings[button_code(button)] = m
        except ValueError as e:
            if strict:
                raise
            log.error("Ignoring mapping for %s: %s", button, e)
    raw = cfg.get("chords") or []
    if not isinstance(raw, list):
        raise ValueError("config 'chords' must be a list")
    chords = []
    for spec in raw:
        try:
            chord = Chord(spec)
            if any(c.buttons == chord.buttons for c in chords):
                raise ValueError(f"{chord.label}: combination listed twice")
            chords.append(chord)
        except ValueError as e:
            if strict:
                raise
            log.error("Ignoring button combination: %s", e)
    return dev, mappings, chords


def load_config(path):
    with open(path) as f:
        cfg = json.load(f)
    return (cfg, *parse_config(cfg, strict=False))


# --- Devices ---------------------------------------------------------------

def is_pointer(dev):
    """True for mice and trackballs: has a left button and relative motion."""
    caps = dev.capabilities()
    return (ecodes.BTN_LEFT in caps.get(ecodes.EV_KEY, [])
            and ecodes.EV_REL in caps)


def pointer_devices():
    devices = []
    for path in evdev.list_devices():
        try:
            dev = evdev.InputDevice(path)
        except OSError:
            continue
        if is_pointer(dev) and not dev.name.startswith(VIRTUAL_NAME_PREFIX):
            devices.append(dev)
        else:
            dev.close()
    return devices


def device_matches(dev, want):
    return (dev.info.vendor == int(want["vendor"], 16)
            and dev.info.product == int(want["product"], 16))


def find_device(want):
    devices = pointer_devices()
    matches = [d for d in devices if device_matches(d, want)]
    # Prefer an exact name match; some devices expose several nodes.
    exact = [d for d in matches if d.name == want.get("name")]
    chosen = (exact or matches or [None])[0]
    for d in devices:
        if d is not chosen:
            d.close()
    return chosen


def usb_connection(dev):
    """The part of a device's physical path that names its USB connection,
    e.g. 'usb-0000:00:14.0-2' for 'usb-0000:00:14.0-2/input1'."""
    phys = dev.phys or ""
    return phys.rsplit("/", 1)[0] if "/" in phys else None


def find_companions(dev):
    """The keyboard part of a mouse: other input devices with the same
    vendor and product ID on the same USB connection. Some mice (e.g.
    Logitech G502) send key presses like Ctrl+C from buttons through it.
    Never matches a separate keyboard: that has its own connection or
    product ID."""
    connection = usb_connection(dev)
    found = []
    if not connection:
        return found
    for path in evdev.list_devices():
        if path == dev.path:
            continue
        try:
            other = evdev.InputDevice(path)
        except OSError:
            continue
        if (usb_connection(other) == connection
                and other.info.vendor == dev.info.vendor
                and other.info.product == dev.info.product
                and not other.name.startswith(VIRTUAL_NAME_PREFIX)
                and ecodes.EV_KEY in other.capabilities()):
            found.append(other)
        else:
            other.close()
    return found


def buttons_of(dev):
    caps = dev.capabilities()
    keys = caps.get(ecodes.EV_KEY, [])
    names = [name_of(c) for c in keys if ecodes.BTN_MISC <= c < ecodes.KEY_OK]
    if ecodes.REL_HWHEEL in caps.get(ecodes.EV_REL, []):
        names += list(TILT_BUTTONS)
    return names


def device_info(dev):
    companions = find_companions(dev)
    for c in companions:
        c.close()
    return {"name": dev.name,
            "vendor": f"{dev.info.vendor:04x}",
            "product": f"{dev.info.product:04x}",
            "buttons": buttons_of(dev),
            # Buttons that type keys can only be found by pressing them.
            "keyboard_part": bool(companions)}


def make_virtual_device(dev):
    """A virtual device with everything the real one has, plus all mouse
    buttons and ordinary keyboard keys so any action can be sent."""
    caps = dev.capabilities()
    caps.pop(ecodes.EV_SYN, None)
    caps.pop(ecodes.EV_FF, None)
    keys = set(caps.get(ecodes.EV_KEY, []))
    keys.update(range(ecodes.BTN_LEFT, ecodes.BTN_TASK + 1))
    keys.update(range(ecodes.KEY_ESC, ecodes.KEY_MICMUTE + 1))
    caps[ecodes.EV_KEY] = sorted(keys)
    return evdev.UInput(caps, name=f"{VIRTUAL_NAME_PREFIX}: {dev.name}")


def read_events(dev, cond):
    """Read what's waiting on a device. Raises OSError if it's gone."""
    if cond & (GLib.IO_HUP | GLib.IO_ERR):
        raise OSError("device disconnected")
    try:
        return list(dev.read())
    except BlockingIOError:
        return []


# --- Daemon ----------------------------------------------------------------

def dbus_error(name, message):
    return dbus.DBusException(message, name=f"org.ballkey.Error.{name}")


class Companion:
    """The keyboard part of a mouse (see find_companions), read alongside
    it. Its events are collected per frame (up to SYN_REPORT), because
    keys pressed in the same frame come from one button, e.g. Ctrl+C."""

    def __init__(self, dev):
        self.dev = dev
        self.frame = []
        self.watch = None

    def add_event(self, event):
        """Collect an event. Returns the finished frame at SYN_REPORT."""
        if event.type == ecodes.EV_SYN:
            if event.code != ecodes.SYN_REPORT:
                return None
            frame, self.frame = self.frame, []
            return frame
        self.frame.append(event)
        return None

    def close(self):
        if self.watch:
            GLib.source_remove(self.watch)
            self.watch = None
        self.dev.close()


def pressed_keys(frame):
    return frozenset(e.code for e in frame
                     if e.type == ecodes.EV_KEY and e.value == 1)


class Daemon(dbus.service.Object):
    """Runs the remapping and answers the settings app over D-Bus.

    Everything happens in one GLib event loop: device events, plug-in
    events and D-Bus calls each get a callback, so none blocks the others.
    """

    def __init__(self, config_path, loop):
        self.config_path = os.path.abspath(config_path)
        self.loop = loop
        self.exit_code = 0
        self.config = dict(EMPTY_CONFIG)
        self.want = None          # device we should remap, from config
        self.mappings = {}        # button code -> Mapping
        self.held = {}            # mapped buttons currently held down
        self.locked = set()       # buttons held down by a drag action
        self.chords = []          # button combinations (Chord)
        # Combination buttons just pressed, held back for CHORD_MS to see
        # whether the rest of a combination follows.
        self.pending = []
        self.pending_timer = None
        self.active_chord = None  # combination currently held down
        self.chord_down = set()   # its buttons that are still down
        self.last_error = ""
        self.dev = self.ui = self.dev_watch = None
        self.companions = []      # the device's keyboard part (Companion)
        # Mapped key-combo buttons held down: (Mapping, keys still down).
        self.combos_held = []
        self.waiting_logged = False
        self.identify_main = False   # identifying on the remapped device
        self.identify_dev = self.identify_watch = self.identify_timeout = None
        self.identify_companions = []
        self.load_from_disk()

        # If D-Bus is unavailable, keep remapping anyway; only the
        # settings app stops working.
        self.system_bus = None
        try:
            self.system_bus = dbus.SystemBus()
            self.bus_name = dbus.service.BusName(
                BUS_NAME, self.system_bus, do_not_queue=True)
            super().__init__(self.bus_name, OBJECT_PATH)
        except dbus.DBusException as e:
            log.error("D-Bus unavailable, the settings app won't work: %s", e)
            super().__init__()

    def guard(self, func):
        """Wrap an event callback: an unexpected error stops the daemon
        (releasing the device) instead of leaving it grabbed but dead."""
        def wrapper(*args):
            try:
                return func(*args)
            except Exception:
                log.exception("Unexpected error; stopping so the device "
                              "works normally")
                self.exit_code = 1
                self.loop.quit()
                return False
        return wrapper

    # -- config --

    def load_from_disk(self):
        try:
            cfg, want, mappings, chords = load_config(self.config_path)
        except FileNotFoundError:
            log.info("No config yet (%s); waiting for settings.",
                     self.config_path)
            return
        except (OSError, ValueError) as e:
            # Fail-safe: never grab the device with a broken config.
            self.last_error = f"Config problem: {e}"
            log.error("Config problem in %s: %s", self.config_path, e)
            log.error("Leaving all devices alone (they work normally) "
                      "until new settings are saved.")
            return
        self.config, self.want = cfg, want
        self.mappings, self.chords = mappings, chords
        self.log_mappings()

    def log_mappings(self):
        for m in self.mappings.values():
            log.info("Mapping %s -> %s", m.button, m.action)
        for c in self.chords:
            log.info("Combination %s -> %s", c.label, c.mapping.action)

    def save_config(self, cfg):
        """Write the config atomically, so a crash never leaves half a file."""
        folder = os.path.dirname(self.config_path)
        os.makedirs(folder, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=folder, prefix=".config-")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(cfg, f, indent=2)
                f.write("\n")
            os.chmod(tmp, 0o644)
            os.replace(tmp, self.config_path)
        except BaseException:
            os.unlink(tmp)
            raise

    def apply_config(self, cfg, want, mappings, chords):
        same_device = self.dev and want and device_matches(self.dev, want)
        self.config, self.want = cfg, want
        self.mappings, self.chords = mappings, chords
        self.last_error = ""
        self.log_mappings()
        self.release_drags()
        if not same_device:
            self.disconnect()
            self.try_connect()

    def release_drags(self):
        """Let go of buttons a drag action is holding, so new settings
        never leave a button stuck down."""
        if self.ui and self.locked:
            for code in self.locked:
                self.ui.write(ecodes.EV_KEY, code, 0)
            self.ui.syn()
        self.locked.clear()

    # -- devices --

    def start(self):
        # Start listening for plug-in events before the first search,
        # so a device that appears in between isn't missed.
        self.monitor = pyudev.Monitor.from_netlink(pyudev.Context())
        self.monitor.filter_by("input")
        self.monitor.start()
        GLib.io_add_watch(self.monitor.fileno(), GLib.PRIORITY_DEFAULT,
                          GLib.IO_IN, self.guard(self.on_udev))
        GLib.timeout_add_seconds(RETRY_SECONDS, self.guard(self.on_retry))
        self.try_connect()

    def on_udev(self, fd, cond):
        added = False
        while (d := self.monitor.poll(timeout=0)) is not None:
            if d.action == "add" and (d.device_node or "").startswith(
                    "/dev/input/event"):
                added = True
        if added:
            # Give the new device a moment to finish setting up.
            GLib.timeout_add(500, self.guard(self.on_retry_once))
        return True

    def on_retry(self):
        self.try_connect()
        return True

    def on_retry_once(self):
        if self.dev:
            # The keyboard part may show up a moment after the mouse.
            self.connect_companions()
        else:
            self.try_connect()
        return False

    def try_connect(self):
        if self.dev or not self.want:
            return
        dev = find_device(self.want)
        if not dev:
            if not self.waiting_logged:
                log.info("Waiting for %s to be connected...",
                         self.want.get("name"))
                self.waiting_logged = True
            return
        self.waiting_logged = False
        log.info("Using %s (%s)", dev.name, dev.path)
        try:
            ui = make_virtual_device(dev)
        except OSError as e:
            self.last_error = f"Could not create virtual device: {e}"
            log.error("%s (is uinput loaded?)", self.last_error)
            dev.close()
            return
        try:
            dev.grab()
        except OSError as e:
            self.last_error = f"Could not grab {dev.name}: {e}"
            log.error("%s", self.last_error)
            dev.close()
            ui.close()
            return
        self.dev, self.ui = dev, ui
        self.last_error = ""
        self.dev_watch = GLib.io_add_watch(
            dev.fd, GLib.PRIORITY_HIGH,
            GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR,
            self.guard(self.on_dev_events))
        self.connect_companions()
        log.info("Remapping active.")

    def connect_companions(self):
        """Grab the device's keyboard part too, so buttons that type keys
        can be remapped. Keys that aren't mapped are passed on unchanged."""
        open_paths = {c.dev.path for c in self.companions}
        for dev in find_companions(self.dev):
            if dev.path in open_paths:
                dev.close()
                continue
            try:
                dev.grab()
            except OSError as e:
                log.warning("Could not grab %s, its keys can't be remapped: "
                            "%s", dev.name, e)
                dev.close()
                continue
            companion = Companion(dev)
            companion.watch = GLib.io_add_watch(
                dev.fd, GLib.PRIORITY_HIGH,
                GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR,
                self.guard(self.on_companion_events), companion)
            self.companions.append(companion)
            log.info("Also using %s (%s)", dev.name, dev.path)

    def on_companion_events(self, fd, cond, companion):
        try:
            events = read_events(companion.dev, cond)
        except OSError as e:
            # Usually the whole mouse was unplugged; the main device
            # notices that too.
            log.warning("%s lost: %s", companion.dev.name, e)
            companion.watch = None   # returning False removes the watch
            self.companions.remove(companion)
            companion.close()
            return False
        for event in events:
            frame = companion.add_event(event)
            if frame is not None:
                self.companion_frame(frame)
        return True

    def companion_frame(self, frame):
        """One frame from the keyboard part. The keys pressed in it are
        one mouse button (e.g. Ctrl+C); if that's mapped, its action runs
        instead of the keys. Everything else is passed on unchanged."""
        pressed = pressed_keys(frame)
        if pressed and self.identify_main:
            self.ButtonPressed(keys_name(pressed))
        mapping = self.mappings.get(key_button(pressed)) if pressed else None
        if mapping:
            self.combos_held.append((mapping, set(pressed)))
        passed = []
        for e in frame:
            held = next((h for h in self.combos_held
                         if e.type == ecodes.EV_KEY and e.code in h[1]), None)
            if held is None:
                passed.append(e)
            elif e.value == 0:
                # The action ends when all its keys have been let go.
                held[1].discard(e.code)
                if not held[1]:
                    self.combos_held.remove(held)
                    held[0].handle(self.ui, 0, self.locked)
        if mapping:
            mapping.handle(self.ui, 1, self.locked)
        for e in passed:
            self.ui.write_event(e)
        self.ui.syn()

    def on_dev_events(self, fd, cond):
        try:
            for event in read_events(self.dev, cond):
                self.handle_event(event)
        except OSError as e:
            log.warning("Device lost (unplugged or asleep?): %s", e)
            self.dev_watch = None   # returning False removes the watch
            self.disconnect()
            return False
        return True

    def handle_event(self, event):
        if event.type == ecodes.EV_REL and event.code in (
                ecodes.REL_HWHEEL, REL_HWHEEL_HI_RES):
            self.tilt(event)
            return
        if event.type != ecodes.EV_KEY:
            self.ui.write_event(event)
            return
        code, value = event.code, event.value
        if value == 1 and self.identify_main:
            self.report_button(code)
        if self.active_chord and code in self.active_chord.buttons:
            self.chord_button(code, value)
        elif code in self.pending:
            if value == 0:
                # Let go before a combination formed: an ordinary click.
                self.flush_pending()
                self.button(code, 0)
        elif value == 1 and not self.active_chord and any(
                code in c.buttons for c in self.chords):
            self.hold_back(code)
        else:
            self.button(code, value)

    def button(self, code, value):
        """A button press or release, with its mapping if it has one."""
        # Remember which action a held button started, so it is
        # released correctly even if settings change meanwhile.
        if value == 1:
            mapping = self.mappings.get(code)
            if mapping:
                self.held[code] = mapping
        elif value == 0:
            mapping = self.held.pop(code, None)
        else:
            mapping = self.held.get(code)
        if mapping:
            mapping.handle(self.ui, value, self.locked)
        else:
            self.ui.write(ecodes.EV_KEY, code, value)
            if value == 0:
                self.locked.discard(code)

    def tilt(self, event):
        """Sideways scrolling (wheel tilt). If that direction is mapped,
        each step is one press and release of its tilt button; if not,
        it scrolls sideways as normal."""
        code = tilt_button(event.value)
        # Every step comes twice: as REL_HWHEEL and as a high-resolution
        # REL_HWHEEL_HI_RES. Only the first one counts as a press.
        step = event.code == ecodes.REL_HWHEEL
        if step and self.identify_main:
            self.report_button(code)
        if code not in self.mappings:
            self.ui.write_event(event)
            return
        if not step:
            return
        for _ in range(abs(event.value)):
            self.button(code, 1)
            self.ui.syn()
            self.button(code, 0)
        # The real device's own SYN_REPORT follows and finishes the frame.

    # -- button combinations --
    # A button that is part of a combination isn't passed on straight
    # away: it waits up to CHORD_MS for the other buttons. If they come,
    # the combination's action runs instead; if not, the button acts
    # normally (just a few milliseconds late).

    def hold_back(self, code):
        self.pending.append(code)
        down = set(self.pending)
        possible = [c for c in self.chords if down <= c.buttons]
        if not possible:
            # These buttons can't become a combination any more.
            self.flush_pending()
        elif [c.buttons for c in possible] == [down]:
            # Exactly one combination, and it's complete.
            self.start_chord(possible[0])
        elif not self.pending_timer:
            self.pending_timer = GLib.timeout_add(
                CHORD_MS, self.guard(self.on_pending_timeout))

    def on_pending_timeout(self):
        self.pending_timer = None
        down = set(self.pending)
        chord = next((c for c in self.chords if c.buttons == down), None)
        if chord:
            self.start_chord(chord)
        else:
            self.flush_pending()
        return False

    def start_chord(self, chord):
        self.cancel_pending_timer()
        self.active_chord = chord
        self.chord_down = set(self.pending)
        self.pending = []
        log.debug("Combination %s", chord.label)
        chord.mapping.handle(self.ui, 1, self.locked)
        self.ui.syn()

    def chord_button(self, code, value):
        """A button of the held combination. Its action ends when all of
        its buttons have been let go."""
        if value == 1:
            self.chord_down.add(code)
        elif value == 0:
            self.chord_down.discard(code)
            if not self.chord_down:
                self.active_chord.mapping.handle(self.ui, 0, self.locked)
                self.ui.syn()
                self.active_chord = None

    def flush_pending(self):
        """Send held-back presses as ordinary presses."""
        self.cancel_pending_timer()
        pending, self.pending = self.pending, []
        for code in pending:
            self.button(code, 1)
        self.ui.syn()

    def cancel_pending_timer(self):
        if self.pending_timer:
            GLib.source_remove(self.pending_timer)
            self.pending_timer = None

    def disconnect(self):
        if self.dev_watch:
            GLib.source_remove(self.dev_watch)
            self.dev_watch = None
        for companion in self.companions:
            try:
                companion.dev.ungrab()
            except OSError:
                pass
            companion.close()
        self.companions = []
        self.combos_held = []
        if self.dev:
            try:
                self.dev.ungrab()
            except OSError:
                pass
            self.dev.close()
            self.dev = None
        if self.ui:
            self.ui.close()
            self.ui = None
        self.held.clear()
        self.locked.clear()
        self.cancel_pending_timer()
        self.pending = []
        self.active_chord = None
        self.chord_down = set()

    def shutdown(self):
        self.StopIdentify()
        self.disconnect()

    # -- identify mode --

    def report_button(self, code):
        # Only mouse buttons and wheel tilt, never keyboard keys.
        if (ecodes.BTN_MISC <= code < ecodes.KEY_OK
                or code in TILT_BUTTONS.values()):
            self.ButtonPressed(name_of(code))

    def on_identify_events(self, fd, cond):
        try:
            for event in read_events(self.identify_dev, cond):
                if event.type == ecodes.EV_KEY and event.value == 1:
                    self.report_button(event.code)
                elif (event.type == ecodes.EV_REL
                      and event.code == ecodes.REL_HWHEEL):
                    self.report_button(tilt_button(event.value))
        except OSError:
            self.identify_watch = None
            self.StopIdentify()
            return False
        return True

    def on_identify_companion_events(self, fd, cond, companion):
        try:
            for event in read_events(companion.dev, cond):
                frame = companion.add_event(event)
                if frame and pressed_keys(frame):
                    self.ButtonPressed(keys_name(pressed_keys(frame)))
        except OSError:
            companion.watch = None
            self.identify_companions.remove(companion)
            companion.close()
            return False
        return True

    def on_identify_timeout(self):
        self.identify_timeout = None
        self.StopIdentify()
        return False

    # -- D-Bus API --

    @dbus.service.method(INTERFACE, out_signature="s")
    def ListDevices(self):
        """JSON list of connected mice/trackballs with their buttons."""
        seen, out = set(), []
        for d in pointer_devices():
            info = device_info(d)
            key = (info["vendor"], info["product"], info["name"])
            if key not in seen:
                seen.add(key)
                out.append(info)
            d.close()
        return json.dumps(out)

    @dbus.service.method(INTERFACE, out_signature="s")
    def Status(self):
        name = self.dev.name if self.dev else (self.want or {}).get("name")
        return json.dumps({"running": True,
                           "configured": self.want is not None,
                           "device_connected": self.dev is not None,
                           "device": name,
                           "last_error": self.last_error})

    @dbus.service.method(INTERFACE, out_signature="s")
    def GetConfig(self):
        return json.dumps(self.config)

    @dbus.service.method(INTERFACE, in_signature="s",
                         sender_keyword="sender",
                         async_callbacks=("ok", "err"))
    def SetConfig(self, config_json, sender, ok, err):
        """Check, authorize (polkit password prompt), save and apply."""
        try:
            cfg = json.loads(config_json)
            want, mappings, chords = parse_config(cfg, strict=True)
        except (ValueError, TypeError, AttributeError) as e:
            err(dbus_error("InvalidConfig", str(e)))
            return

        def authorized():
            try:
                self.save_config(cfg)
            except OSError as e:
                err(dbus_error("SaveFailed", str(e)))
                return
            log.info("New settings saved.")
            self.apply_config(cfg, want, mappings, chords)
            ok()

        self.check_authorization(sender, authorized, err)

    def check_authorization(self, sender, on_yes, err):
        """Ask polkit whether the caller may change settings. Polkit shows
        the password prompt. Runs in the background so remapping
        continues while the user types."""
        authority = self.system_bus.get_object(
            "org.freedesktop.PolicyKit1",
            "/org/freedesktop/PolicyKit1/Authority", introspect=False)
        subject = dbus.Struct(
            ("system-bus-name",
             dbus.Dictionary({"name": dbus.String(sender, variant_level=1)},
                             signature="sv")),
            signature="sa{sv}")

        def reply(result):
            if result[0]:
                on_yes()
            else:
                err(dbus_error("NotAuthorized",
                               "Not authorized to change BallKey settings"))

        def failed(e):
            log.error("polkit check failed: %s", e)
            err(e)

        authority.CheckAuthorization(
            subject, POLKIT_ACTION, dbus.Dictionary({}, signature="ss"),
            dbus.UInt32(1),   # 1 = allow showing a password prompt
            "", dbus_interface="org.freedesktop.PolicyKit1.Authority",
            reply_handler=self.guard(reply), error_handler=failed,
            timeout=300)

    @dbus.service.method(INTERFACE, in_signature="sss")
    def StartIdentify(self, vendor, product, name):
        """Report button presses on this device via ButtonPressed."""
        self.StopIdentify()
        want = {"vendor": vendor, "product": product, "name": name}
        try:
            int(vendor, 16)
            int(product, 16)
        except ValueError:
            raise dbus_error("InvalidDevice", "vendor/product must be hex")
        if self.dev and device_matches(self.dev, want):
            self.identify_main = True
        else:
            dev = find_device(want)
            if not dev:
                raise dbus_error("DeviceNotFound", f"{name} is not connected")
            # Read without grabbing: the device keeps working normally.
            self.identify_dev = dev
            self.identify_watch = GLib.io_add_watch(
                dev.fd, GLib.PRIORITY_DEFAULT,
                GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR,
                self.guard(self.on_identify_events))
            for other in find_companions(dev):
                companion = Companion(other)
                companion.watch = GLib.io_add_watch(
                    other.fd, GLib.PRIORITY_DEFAULT,
                    GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR,
                    self.guard(self.on_identify_companion_events), companion)
                self.identify_companions.append(companion)
        self.identify_timeout = GLib.timeout_add_seconds(
            IDENTIFY_SECONDS, self.guard(self.on_identify_timeout))
        log.info("Identify mode on for %s", name)

    @dbus.service.method(INTERFACE)
    def StopIdentify(self):
        was_on = self.identify_main or self.identify_dev
        if self.identify_watch:
            GLib.source_remove(self.identify_watch)
        if self.identify_timeout:
            GLib.source_remove(self.identify_timeout)
        if self.identify_dev:
            self.identify_dev.close()
        for companion in self.identify_companions:
            companion.close()
        self.identify_companions = []
        self.identify_main = False
        self.identify_dev = self.identify_watch = self.identify_timeout = None
        if was_on:
            log.info("Identify mode off")

    @dbus.service.signal(INTERFACE, signature="s")
    def ButtonPressed(self, button):
        pass

    # -- start at boot (asks systemd, the service manager) --

    def systemd_manager(self):
        obj = self.system_bus.get_object(
            "org.freedesktop.systemd1", "/org/freedesktop/systemd1",
            introspect=False)
        return dbus.Interface(obj, "org.freedesktop.systemd1.Manager")

    @dbus.service.method(INTERFACE, out_signature="b",
                         async_callbacks=("ok", "err"))
    def GetBootEnabled(self, ok, err):
        """True if remapping starts at boot."""
        self.systemd_manager().GetUnitFileState(
            SERVICE_UNIT, reply_handler=lambda state: ok(state == "enabled"),
            error_handler=err)

    @dbus.service.method(INTERFACE, in_signature="b",
                         sender_keyword="sender",
                         async_callbacks=("ok", "err"))
    def SetBootEnabled(self, enabled, sender, ok, err):
        """Turn starting at boot on or off (polkit password prompt).
        Doesn't stop or start remapping right now."""
        def failed(e):
            log.error("Changing start-at-boot failed: %s", e)
            err(dbus_error("BootSettingFailed", str(e)))

        def changed(*_):
            log.info("Start at boot turned %s.", "on" if enabled else "off")
            # Like "systemctl daemon-reload" after "systemctl enable".
            self.systemd_manager().Reload(reply_handler=ok,
                                          error_handler=failed)

        def authorized():
            units = dbus.Array([SERVICE_UNIT], signature="s")
            manager = self.systemd_manager()
            if enabled:
                manager.EnableUnitFiles(units, False, False,
                                        reply_handler=changed,
                                        error_handler=failed)
            else:
                manager.DisableUnitFiles(units, False,
                                         reply_handler=changed,
                                         error_handler=failed)

        self.check_authorization(sender, authorized, err)


# --- Commands --------------------------------------------------------------

def cmd_list():
    devices = pointer_devices()
    if not devices:
        print("No mice or trackballs found. (Are you running with sudo?)")
    for d in devices:
        print(f"{d.name}")
        print(f"  vendor: {d.info.vendor:04x}  product: {d.info.product:04x}"
              f"  path: {d.path}")
        print(f"  buttons: {', '.join(buttons_of(d))}")
        print()


def cmd_watch(config_path):
    _, want, _, _ = load_config(config_path)
    dev = find_device(want) if want else None
    if not dev:
        log.error("Device not found: %s", want)
        return 1
    print(f"Watching {dev.name}. Press buttons; Ctrl+C to stop.")
    for event in dev.read_loop():
        if event.type == ecodes.EV_KEY and event.value == 1:
            print(f"  pressed: {name_of(event.code)}")
        elif event.type == ecodes.EV_REL and event.code == ecodes.REL_HWHEEL:
            print(f"  pressed: {name_of(tilt_button(event.value))}")


def cmd_run(config_path):
    DBusGMainLoop(set_as_default=True)
    loop = GLib.MainLoop()
    daemon = Daemon(config_path, loop)
    # "systemctl stop" (SIGTERM) and Ctrl+C both end the loop cleanly.
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal_add(GLib.PRIORITY_HIGH, sig, loop.quit)
    daemon.start()
    try:
        loop.run()
    finally:
        daemon.shutdown()
        log.info("Stopped. Device is back to normal.")
    return daemon.exit_code


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description="BallKey button remapper")
    parser.add_argument("-c", "--config", default=DEFAULT_CONFIG)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--list", action="store_true",
                       help="list connected mice and trackballs")
    group.add_argument("--watch", action="store_true",
                       help="print button names as you press them")
    args = parser.parse_args()

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    try:
        if args.list:
            return cmd_list()
        if args.watch:
            return cmd_watch(args.config)
        return cmd_run(args.config)
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError) as e:
        log.error("%s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
