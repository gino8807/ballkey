#!/usr/bin/env python3
"""BallKey daemon: remaps mouse/trackball buttons on Linux.

How it works:
  1. Find the configured device by vendor ID, product ID and name.
  2. "Grab" it, so no other program sees its events directly.
  3. Create a virtual device through /dev/uinput.
  4. Copy every event from the real device to the virtual one, except
     mapped buttons, which are replaced by their assigned action.

When the device is unplugged (or goes to sleep), the daemon waits and
grabs it again as soon as it comes back.

Fail-safe: if this program stops for any reason (Ctrl+C, crash, killed),
the kernel releases the grab and removes the virtual device, so the real
device goes back to working normally. If the config file is broken, the
daemon never grabs the device at all.

Usage (needs root):
  ballkeyd.py --list                 show connected mice/trackballs
  ballkeyd.py --watch [-c CONFIG]    print buttons as you press them
  ballkeyd.py [-c CONFIG]            run the remapper
"""

import argparse
import json
import logging
import signal
import sys
import time

import evdev
import pyudev
from evdev import ecodes

DEFAULT_CONFIG = "/etc/ballkey/config.json"
VIRTUAL_NAME_PREFIX = "BallKey"
# How often to look for the device even if no plug-in event arrives.
RETRY_SECONDS = 10

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
    "copy": ["KEY_LEFTCTRL", "KEY_C"],
    "paste": ["KEY_LEFTCTRL", "KEY_V"],
    "undo": ["KEY_LEFTCTRL", "KEY_Z"],
}
OTHER_ACTIONS = {"doubleclick", "keycombo", "none", "default"}


def code_of(name):
    """Turn a name like 'BTN_SIDE' into its numeric code."""
    try:
        return ecodes.ecodes[name]
    except KeyError:
        raise ValueError(f"unknown key or button name: {name}") from None


def name_of(code):
    """Turn a numeric button/key code into a readable name."""
    name = ecodes.BTN.get(code) or ecodes.KEY.get(code) or f"code {code}"
    # Some codes have several names, e.g. ('BTN_LEFT', 'BTN_MOUSE').
    return name[0] if isinstance(name, (list, tuple)) else name


class Mapping:
    """One button's replacement action, ready to run."""

    def __init__(self, button, spec):
        self.button = button
        self.action = spec.get("action")
        if self.action in SIMPLE_ACTIONS:
            self.codes = [code_of(n) for n in SIMPLE_ACTIONS[self.action]]
        elif self.action == "keycombo":
            keys = spec.get("keys") or []
            if not keys:
                raise ValueError(f"{button}: keycombo needs a 'keys' list")
            self.codes = [code_of(n) for n in keys]
        elif self.action in OTHER_ACTIONS:
            self.codes = []
        else:
            raise ValueError(f"{button}: unknown action {self.action!r}")

    def handle(self, ui, value):
        """Run the action. value: 1 = pressed, 0 = released, 2 = auto-repeat."""
        if value == 2 or self.action == "none":
            return
        if self.action == "doubleclick":
            if value == 1:
                for _ in range(2):
                    ui.write(ecodes.EV_KEY, ecodes.BTN_LEFT, 1)
                    ui.syn()
                    ui.write(ecodes.EV_KEY, ecodes.BTN_LEFT, 0)
                    ui.syn()
            return
        # Press in order, release in reverse order (like a person would).
        for code in (self.codes if value == 1 else reversed(self.codes)):
            ui.write(ecodes.EV_KEY, code, value)
        # The real device's own SYN_REPORT follows and finishes the frame.


# --- Config ----------------------------------------------------------------

def load_config(path):
    with open(path) as f:
        cfg = json.load(f)
    if cfg.get("version") != 1:
        raise ValueError("config 'version' must be 1")
    dev = cfg.get("device") or {}
    if not dev.get("vendor") or not dev.get("product"):
        raise ValueError("config 'device' needs 'vendor' and 'product'")
    mappings = {}
    for button, spec in (cfg.get("mappings") or {}).items():
        # One bad mapping shouldn't disable the others: skip just that one.
        try:
            m = Mapping(button, spec)
            if m.action != "default":
                mappings[code_of(button)] = m
        except (ValueError, AttributeError) as e:
            log.error("Ignoring mapping for %s: %s", button, e)
    return dev, mappings


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


def find_device(want):
    vendor = int(want["vendor"], 16)
    product = int(want["product"], 16)
    devices = pointer_devices()
    matches = [d for d in devices
               if d.info.vendor == vendor and d.info.product == product]
    # Prefer an exact name match; some devices expose several nodes.
    exact = [d for d in matches if d.name == want.get("name")]
    chosen = (exact or matches or [None])[0]
    for d in devices:
        if d is not chosen:
            d.close()
    return chosen


def buttons_of(dev):
    keys = dev.capabilities().get(ecodes.EV_KEY, [])
    return [name_of(c) for c in keys if ecodes.BTN_MISC <= c < ecodes.KEY_OK]


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
    want, _ = load_config(config_path)
    dev = find_device(want)
    if not dev:
        log.error("Device not found: %s", want)
        return 1
    print(f"Watching {dev.name}. Press buttons; Ctrl+C to stop.")
    for event in dev.read_loop():
        if event.type == ecodes.EV_KEY and event.value == 1:
            print(f"  pressed: {name_of(event.code)}")


def remap(dev, mappings):
    """Grab one device and remap it until it disconnects."""
    log.info("Using %s (%s)", dev.name, dev.path)
    try:
        ui = make_virtual_device(dev)
    except OSError as e:
        log.error("Could not create virtual device (is uinput loaded?): %s", e)
        dev.close()
        return
    try:
        dev.grab()
        log.info("Remapping active.")
        for event in dev.read_loop():
            if event.type == ecodes.EV_KEY and event.code in mappings:
                mappings[event.code].handle(ui, event.value)
            else:
                ui.write_event(event)
    except OSError as e:
        log.warning("Device lost (unplugged or asleep?): %s", e)
    finally:
        try:
            dev.ungrab()
        except OSError:
            pass
        dev.close()
        ui.close()


def wait_for_new_device(monitor):
    """Return when any input device is plugged in, or after RETRY_SECONDS."""
    deadline = time.monotonic() + RETRY_SECONDS
    while (left := deadline - time.monotonic()) > 0:
        d = monitor.poll(timeout=left)
        if d is None:
            return
        if d.action == "add" and (d.device_node or "").startswith(
                "/dev/input/event"):
            time.sleep(0.5)  # let the new device finish setting up
            return


def cmd_run(config_path):
    try:
        want, mappings = load_config(config_path)
    except (OSError, ValueError) as e:
        # Fail-safe: never grab the device with a broken config.
        log.error("Config problem in %s: %s", config_path, e)
        log.error("Leaving all devices alone (they work normally). "
                  "Fix the config, then restart the service.")
        while True:
            signal.pause()

    for m in mappings.values():
        log.info("Mapping %s -> %s", m.button, m.action)

    # Start listening for plug-in events before the first search,
    # so a device that appears in between isn't missed.
    monitor = pyudev.Monitor.from_netlink(pyudev.Context())
    monitor.filter_by("input")
    monitor.start()

    waiting_logged = False
    while True:
        dev = find_device(want)
        if dev:
            remap(dev, mappings)
            waiting_logged = False
        elif not waiting_logged:
            log.info("Waiting for %s to be connected...", want.get("name"))
            waiting_logged = True
        wait_for_new_device(monitor)


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

    # Treat "systemctl stop" (SIGTERM) like Ctrl+C so cleanup runs.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    try:
        if args.list:
            return cmd_list()
        if args.watch:
            return cmd_watch(args.config)
        return cmd_run(args.config)
    except KeyboardInterrupt:
        log.info("Stopped. Device is back to normal.")
        return 0
    except (OSError, ValueError) as e:
        log.error("%s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
