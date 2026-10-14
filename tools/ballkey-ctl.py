#!/usr/bin/env python3
"""Small test tool for the BallKey daemon's D-Bus API. Runs as your normal
user (no sudo); changing settings shows the system password prompt.

  ballkey-ctl.py status                  is the daemon running, device connected?
  ballkey-ctl.py list                    connected mice/trackballs, numbered
  ballkey-ctl.py config                  current settings
  ballkey-ctl.py identify N              press buttons on device N to see names
  ballkey-ctl.py use N                   remap device N from now on
  ballkey-ctl.py set BUTTON ACTION [KEY ...]
        e.g.  set BTN_MIDDLE back
              set BTN_SIDE keycombo KEY_LEFTCTRL KEY_T
              set BTN_SIDE default
"""

import json
import signal
import sys

import dbus
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

try:  # newer GLib; older versions only have GLib.unix_signal_add
    from gi.repository import GLibUnix
    signal_add = GLibUnix.signal_add
except ImportError:
    signal_add = GLib.unix_signal_add

BUS_NAME = "org.ballkey.Daemon"
OBJECT_PATH = "/org/ballkey/Daemon"
INTERFACE = "org.ballkey.Daemon"


def daemon():
    obj = dbus.SystemBus().get_object(BUS_NAME, OBJECT_PATH)
    return dbus.Interface(obj, INTERFACE)


def devices():
    return json.loads(daemon().ListDevices())


def pick_device(arg):
    devs = devices()
    try:
        return devs[int(arg) - 1]
    except (ValueError, IndexError):
        sys.exit(f"No device number {arg}. Run 'list' to see the numbers.")


def save(cfg):
    print("Saving... (a password prompt may appear)")
    # Long timeout: the user may take a while to type the password.
    daemon().SetConfig(json.dumps(cfg), timeout=300)
    print("Saved and applied.")


def cmd_status():
    for key, value in json.loads(daemon().Status()).items():
        print(f"{key}: {value}")


def cmd_list():
    for i, d in enumerate(devices(), 1):
        print(f"{i}. {d['name']}  ({d['vendor']}:{d['product']})")
        print(f"   buttons: {', '.join(d['buttons'])}")


def cmd_config():
    print(json.dumps(json.loads(daemon().GetConfig()), indent=2))


def cmd_identify(arg):
    dev = pick_device(arg)
    bus = dbus.SystemBus()
    loop = GLib.MainLoop()
    bus.add_signal_receiver(lambda button: print(f"  pressed: {button}"),
                            signal_name="ButtonPressed",
                            dbus_interface=INTERFACE, bus_name=BUS_NAME)
    d = dbus.Interface(bus.get_object(BUS_NAME, OBJECT_PATH), INTERFACE)
    d.StartIdentify(dev["vendor"], dev["product"], dev["name"])
    print(f"Press buttons on {dev['name']}. Ctrl+C to stop "
          "(stops by itself after 60 seconds).")
    signal_add(GLib.PRIORITY_HIGH, signal.SIGINT, loop.quit)
    try:
        loop.run()
    finally:
        d.StopIdentify()


def cmd_use(arg):
    dev = pick_device(arg)
    cfg = json.loads(daemon().GetConfig())
    cfg["device"] = {k: dev[k] for k in ("vendor", "product", "name")}
    save(cfg)


def cmd_set(button, action, *keys):
    cfg = json.loads(daemon().GetConfig())
    mapping = {"action": action}
    if keys:
        mapping["keys"] = list(keys)
    cfg.setdefault("mappings", {})[button] = mapping
    save(cfg)


COMMANDS = {"status": cmd_status, "list": cmd_list, "config": cmd_config,
            "identify": cmd_identify, "use": cmd_use, "set": cmd_set}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        sys.exit(__doc__)
    # Must happen before the first connection, so 'identify' can
    # receive ButtonPressed signals on it.
    DBusGMainLoop(set_as_default=True)
    try:
        COMMANDS[sys.argv[1]](*sys.argv[2:])
    except TypeError:
        sys.exit(__doc__)
    except dbus.DBusException as e:
        if e.get_dbus_name() == "org.freedesktop.DBus.Error.ServiceUnknown":
            sys.exit("The BallKey service isn't running.")
        sys.exit(f"Error: {e.get_dbus_message()}")


if __name__ == "__main__":
    main()
