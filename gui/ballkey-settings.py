#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""BallKey settings app: pick a device, choose an action for each button,
click Apply.

Runs as the normal user and never touches /dev/input itself; everything
goes through the BallKey daemon over the system D-Bus. Saving shows the
system password prompt (polkit).

Safety: if the new settings leave no button doing a left click, a
countdown asks "Keep these settings?" and reverts after 15 seconds
unless confirmed (like changing display resolution).
"""

import json
import os
import sys

from evdev import ecodes  # name tables only; no device access
from PySide6.QtCore import QObject, Qt, QTimer, Signal, Slot, SLOT
from PySide6.QtDBus import (QDBusConnection, QDBusInterface, QDBusMessage,
                            QDBusPendingCallWatcher)
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox,
                               QComboBox,
                               QDialog, QHBoxLayout, QHeaderView, QLabel,
                               QMessageBox, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

BUS_NAME = "org.ballkey.Daemon"
OBJECT_PATH = "/org/ballkey/Daemon"
INTERFACE = "org.ballkey.Daemon"
REVERT_SECONDS = 15

# Shown for every device that has them, even if not mapped yet.
COMMON_BUTTONS = ["BTN_LEFT", "BTN_RIGHT", "BTN_MIDDLE", "BTN_SIDE",
                  "BTN_EXTRA"]
BUTTON_LABELS = {
    "BTN_LEFT": "Left button",
    "BTN_RIGHT": "Right button",
    "BTN_MIDDLE": "Middle button / wheel click",
    "BTN_SIDE": "Side button (usually Back)",
    "BTN_EXTRA": "Side button (usually Forward)",
    "BTN_FORWARD": "Forward button",
    "BTN_BACK": "Back button",
    "BTN_TASK": "Task button",
}

# (action id, label) in dropdown order.
ACTIONS = [
    ("default", "Default (normal behaviour)"),
    ("left", "Left click"),
    ("right", "Right click"),
    ("middle", "Middle click"),
    ("doubleclick", "Double-click"),
    ("back", "Back"),
    ("forward", "Forward"),
    ("copy", "Copy"),
    ("paste", "Paste"),
    ("undo", "Undo"),
    ("keycombo", "Custom key combo…"),
    ("none", "Do nothing"),
]
KEYCOMBO_INDEX = [a for a, _ in ACTIONS].index("keycombo")

MODIFIER_ORDER = ["KEY_LEFTCTRL", "KEY_RIGHTCTRL", "KEY_LEFTSHIFT",
                  "KEY_RIGHTSHIFT", "KEY_LEFTALT", "KEY_RIGHTALT",
                  "KEY_LEFTMETA", "KEY_RIGHTMETA"]
KEY_LABELS = {
    "KEY_LEFTCTRL": "Ctrl", "KEY_RIGHTCTRL": "Ctrl",
    "KEY_LEFTSHIFT": "Shift", "KEY_RIGHTSHIFT": "Shift",
    "KEY_LEFTALT": "Alt", "KEY_RIGHTALT": "AltGr",
    "KEY_LEFTMETA": "Meta", "KEY_RIGHTMETA": "Meta",
}


def button_label(name):
    return BUTTON_LABELS.get(name, f"Extra button ({name})")


def key_label(name):
    if name in KEY_LABELS:
        return KEY_LABELS[name]
    short = name.removeprefix("KEY_")
    return short if len(short) <= 3 else short.capitalize()


def combo_label(keys):
    return "+".join(key_label(k) for k in keys)


def evdev_key_name(code):
    name = ecodes.KEY.get(code)
    return name[0] if isinstance(name, (list, tuple)) else name


def device_key(dev):
    return (dev["vendor"], dev["product"], dev["name"])


def has_left_click(config):
    """True if some button still does a left click."""
    mappings = config.get("mappings") or {}
    left = mappings.get("BTN_LEFT", {}).get("action", "default")
    if left in ("default", "left"):
        return True
    return any(m.get("action") == "left" for m in mappings.values())


# --- D-Bus -----------------------------------------------------------------

class DaemonClient(QObject):
    """Talks to the BallKey daemon over the system bus."""

    buttonPressed = Signal(str)

    def __init__(self):
        super().__init__()
        # BALLKEY_SESSION_BUS=1 is for development testing only.
        if os.environ.get("BALLKEY_SESSION_BUS"):
            self.bus = QDBusConnection.sessionBus()
        else:
            self.bus = QDBusConnection.systemBus()
        self.iface = QDBusInterface(BUS_NAME, OBJECT_PATH, INTERFACE,
                                    self.bus)
        # Saving may wait for the user to type a password.
        self.iface.setTimeout(300_000)
        self.bus.connect(BUS_NAME, OBJECT_PATH, INTERFACE, "ButtonPressed",
                         self, SLOT("onButtonPressed(QString)"))
        self.watchers = set()

    @Slot(str)
    def onButtonPressed(self, button):
        self.buttonPressed.emit(button)

    def call(self, method, *args):
        """Call and wait for the answer. Raises RuntimeError on failure."""
        msg = self.iface.call(method, *args)
        if msg.type() == QDBusMessage.MessageType.ErrorMessage:
            raise RuntimeError(msg.errorMessage() or msg.errorName())
        values = msg.arguments()
        return values[0] if values else None

    def call_json(self, method, *args):
        return json.loads(self.call(method, *args))

    def set_config(self, config, done):
        """Save in the background (a password prompt may appear).
        done(error_name, error_message) is called when finished;
        both are empty on success."""
        self.call_async("SetConfig", [json.dumps(config)], done)

    def set_boot_enabled(self, enabled, done):
        """Like set_config, for the start-at-boot setting."""
        self.call_async("SetBootEnabled", [enabled], done)

    def call_async(self, method, args, done):
        pending = self.iface.asyncCallWithArgumentList(method, args)
        watcher = QDBusPendingCallWatcher(pending, self)
        self.watchers.add(watcher)

        def finished(w):
            self.watchers.discard(w)
            if w.isError():
                done(w.error().name(), w.error().message())
            else:
                done("", "")
            w.deleteLater()

        watcher.finished.connect(finished)


# --- Dialogs ---------------------------------------------------------------

class KeyComboDialog(QDialog):
    """Records a key combination: hold the keys, then let go."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Record key combo")
        self.keys = []
        self.down = set()
        self.recording = []

        info = QLabel("Press the key combination you want, for example "
                      "Ctrl+T, then let go.")
        info.setWordWrap(True)
        self.shown = QLabel("…")
        self.shown.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = self.shown.font()
        font.setPointSizeF(font.pointSizeF() * 1.6)
        font.setBold(True)
        self.shown.setFont(font)

        self.ok = QPushButton("OK")
        self.ok.setEnabled(False)
        cancel = QPushButton("Cancel")
        # Buttons must not take keyboard focus, or keys like Enter and
        # Space would press them instead of being recorded.
        for b in (self.ok, cancel):
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setAutoDefault(False)
        self.ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)

        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(cancel)
        buttons.addWidget(self.ok)
        layout = QVBoxLayout(self)
        layout.addWidget(info)
        layout.addWidget(self.shown)
        layout.addLayout(buttons)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumWidth(360)

    def event(self, e):
        # Catch every key here (including Tab, Enter and Escape) so it is
        # recorded instead of moving focus or closing the dialog.
        if e.type() == e.Type.KeyPress:
            self.key_down(e)
            return True
        if e.type() == e.Type.KeyRelease:
            self.key_up(e)
            return True
        return super().event(e)

    def key_down(self, e):
        if e.isAutoRepeat():
            return
        # On Linux, Qt's native scan code is the kernel key code + 8,
        # whatever the keyboard layout.
        name = evdev_key_name(e.nativeScanCode() - 8)
        if not name:
            self.shown.setText("That key can't be used")
            return
        if not self.down:
            self.recording = []          # a fresh combo starts
        self.down.add(name)
        if name not in self.recording:
            self.recording.append(name)
        # Modifiers first (Ctrl, Shift, Alt, Meta), then the other keys.
        mods = [k for k in MODIFIER_ORDER if k in self.recording]
        rest = [k for k in self.recording if k not in MODIFIER_ORDER]
        self.keys = mods + rest
        self.shown.setText(combo_label(self.keys))
        self.ok.setEnabled(True)

    def key_up(self, e):
        if e.isAutoRepeat():
            return
        self.down.discard(evdev_key_name(e.nativeScanCode() - 8))


class RevertDialog(QDialog):
    """'Keep these settings?' with an automatic revert countdown."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Keep these settings?")
        self.left = REVERT_SECONDS
        self.text = QLabel()
        self.text.setWordWrap(True)
        keep = QPushButton("Keep changes")
        revert = QPushButton("Revert now")
        # Enter / Escape both revert; keeping needs a deliberate click.
        revert.setDefault(True)
        keep.clicked.connect(self.accept)
        revert.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(revert)
        buttons.addWidget(keep)
        layout = QVBoxLayout(self)
        layout.addWidget(self.text)
        layout.addLayout(buttons)
        self.setMinimumWidth(420)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(1000)
        self.update_text()

    def update_text(self):
        self.text.setText(
            "No button is set to <b>Left click</b> any more.<br><br>"
            "If this wasn't on purpose, do nothing: the previous settings "
            f"come back in <b>{self.left}</b> seconds.")

    def tick(self):
        self.left -= 1
        if self.left <= 0:
            self.timer.stop()
            self.reject()
        else:
            self.update_text()


# --- Main window -----------------------------------------------------------

class ActionBox(QComboBox):
    """Action dropdown for one button row."""

    def __init__(self, window, button):
        super().__init__()
        self.window_ = window
        self.button = button
        self.keys = []
        self.previous = 0
        for action, label in ACTIONS:
            self.addItem(label, action)
        self.activated.connect(self.on_activated)

    def set_mapping(self, mapping):
        action = (mapping or {}).get("action", "default")
        self.keys = list((mapping or {}).get("keys", []))
        index = max(self.findData(action), 0)
        self.setCurrentIndex(index)
        self.previous = index
        self.update_combo_text()

    def mapping(self):
        action = self.currentData()
        if action == "keycombo":
            return {"action": action, "keys": self.keys}
        return {"action": action}

    def update_combo_text(self):
        text = (f"Keys: {combo_label(self.keys)}" if self.keys
                else dict(ACTIONS)["keycombo"])
        self.setItemText(KEYCOMBO_INDEX, text)

    def on_activated(self, index):
        if index == KEYCOMBO_INDEX:
            dialog = KeyComboDialog(self.window())
            if dialog.exec() == QDialog.DialogCode.Accepted and dialog.keys:
                self.keys = dialog.keys
                self.update_combo_text()
            elif not self.keys:
                # Cancelled and nothing recorded before: undo the choice.
                self.setCurrentIndex(self.previous)
                return
        self.previous = self.currentIndex()
        self.window_.changed()


class MainWindow(QWidget):
    def __init__(self, client):
        super().__init__()
        self.client = client
        self.saved_config = None       # what the daemon has right now
        self.devices = []              # entries in the device dropdown
        self.extra_rows = set()        # buttons found with Identify
        self.identifying = False
        self.service_ok = False
        self.boot_known = False

        self.setWindowTitle("BallKey – Mouse & Trackball Buttons")
        self.setWindowIcon(QIcon.fromTheme("ballkey",
                                           QIcon.fromTheme("input-mouse")))

        self.device_box = QComboBox()
        self.device_box.setPlaceholderText("No mouse or trackball found")
        self.device_box.currentIndexChanged.connect(self.on_device_chosen)
        self.forget_button = QPushButton("Forget")
        self.forget_button.setToolTip(
            "Remove this device and its button settings from BallKey "
            "(for example a mouse you no longer have)")
        self.forget_button.clicked.connect(self.forget_device)
        top = QHBoxLayout()
        top.addWidget(QLabel("Device:"))
        top.addWidget(self.device_box, 1)
        top.addWidget(self.forget_button)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Button", "Action"])
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)

        self.identify_button = QPushButton("Identify button")
        self.identify_button.setToolTip(
            "Press a button on your device to find it in the list")
        self.identify_button.clicked.connect(self.toggle_identify)
        self.defaults_button = QPushButton("Restore defaults")
        self.defaults_button.clicked.connect(self.restore_defaults)
        self.apply_button = QPushButton("Apply")
        self.apply_button.setDefault(True)
        self.apply_button.clicked.connect(self.apply)
        buttons = QHBoxLayout()
        buttons.addWidget(self.identify_button)
        buttons.addStretch()
        buttons.addWidget(self.defaults_button)
        buttons.addWidget(self.apply_button)

        self.boot_box = QCheckBox("Start remapping at boot")
        self.boot_box.setToolTip(
            "When off, buttons behave normally after a restart until you "
            "open BallKey.")
        self.boot_box.clicked.connect(self.toggle_boot)

        self.hint = QLabel()
        self.status = QLabel()
        self.status.setStyleSheet("color: palette(placeholder-text);")

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.hint)
        layout.addLayout(buttons)
        layout.addWidget(self.boot_box)
        layout.addWidget(self.status)
        self.resize(560, 440)

        client.buttonPressed.connect(self.on_button_pressed)
        self.poll = QTimer(self)
        self.poll.timeout.connect(self.refresh_status)
        self.poll.start(2000)
        self.load()

    # -- loading --

    def load(self):
        """(Re)read everything from the daemon."""
        try:
            self.saved_config = self.client.call_json("GetConfig")
            connected = self.client.call_json("ListDevices")
        except RuntimeError:
            self.service_ok = False
            self.refresh_status()
            return
        self.service_ok = True
        self.load_boot_setting()
        self.fill_devices(connected)
        self.update_buttons()
        self.refresh_status()

    def load_boot_setting(self):
        # Optional: if it can't be read (e.g. no systemd), only the
        # checkbox is greyed out; the rest of the app keeps working.
        try:
            self.boot_box.setChecked(bool(self.client.call("GetBootEnabled")))
            self.boot_known = True
        except RuntimeError:
            self.boot_known = False

    def fill_devices(self, connected):
        configured = self.saved_config.get("device")
        current = self.current_device()
        devices = [dict(d, connected=True) for d in connected]
        if configured and device_key(configured) not in map(device_key,
                                                            devices):
            devices.insert(0, dict(configured, connected=False, buttons=[]))
        # The configured device goes first.
        if configured:
            devices.sort(key=lambda d: device_key(d) != device_key(configured))
        if [(device_key(d), d["connected"]) for d in devices] == \
                [(device_key(d), d["connected"]) for d in self.devices]:
            return
        self.devices = devices
        self.device_box.blockSignals(True)
        self.device_box.clear()
        for d in devices:
            text = d["name"] if d["connected"] else \
                f"{d['name']} (not connected)"
            self.device_box.addItem(text)
        keep = current or configured
        index = next((i for i, d in enumerate(devices)
                      if keep and device_key(d) == device_key(keep)), 0)
        self.device_box.setCurrentIndex(index)
        self.device_box.blockSignals(False)
        if current is None or device_key(current) != device_key(
                self.current_device() or current):
            self.on_device_chosen()
        else:
            self.update_buttons()

    def current_device(self):
        i = self.device_box.currentIndex()
        return self.devices[i] if 0 <= i < len(self.devices) else None

    def on_device_chosen(self, *_):
        self.stop_identify()
        self.extra_rows.clear()
        dev = self.current_device()
        configured = self.saved_config.get("device") if self.saved_config \
            else None
        if dev and configured and device_key(dev) == device_key(configured):
            mappings = self.saved_config.get("mappings") or {}
        else:
            mappings = {}   # another device: starts with normal behaviour
        self.fill_rows(mappings)
        self.update_buttons()

    def fill_rows(self, mappings):
        dev = self.current_device()
        reported = set(dev["buttons"]) if dev else set()
        names = [b for b in COMMON_BUTTONS
                 if b in reported or not dev or not dev["connected"]]
        for b in list(mappings) + sorted(self.extra_rows):
            if b not in names:
                names.append(b)
        self.table.setRowCount(0)
        for name in names:
            self.add_row(name, mappings.get(name))

    def add_row(self, name, mapping=None):
        row = self.table.rowCount()
        self.table.insertRow(row)
        item = QTableWidgetItem(button_label(name))
        item.setToolTip(name)
        item.setData(Qt.ItemDataRole.UserRole, name)
        self.table.setItem(row, 0, item)
        box = ActionBox(self, name)
        box.set_mapping(mapping)
        self.table.setCellWidget(row, 1, box)
        return row

    def row_of(self, name):
        for row in range(self.table.rowCount()):
            if self.table.item(row, 0).data(Qt.ItemDataRole.UserRole) == name:
                return row
        return None

    # -- editing --

    def build_config(self):
        dev = self.current_device()
        mappings = {}
        for row in range(self.table.rowCount()):
            box = self.table.cellWidget(row, 1)
            m = box.mapping()
            if m["action"] != "default":
                mappings[box.button] = m
        return {"version": 1,
                "device": {k: dev[k] for k in ("vendor", "product", "name")}
                if dev else None,
                "mappings": mappings}

    def has_changes(self):
        if not self.saved_config or not self.current_device():
            return False
        new = self.build_config()
        return (new["device"] != self.saved_config.get("device")
                or new["mappings"] != (self.saved_config.get("mappings")
                                       or {}))

    def changed(self):
        self.update_buttons()

    def update_buttons(self):
        dev = self.current_device()
        ok = self.service_ok and dev is not None
        self.device_box.setEnabled(self.service_ok)
        self.boot_box.setEnabled(self.service_ok and self.boot_known)
        self.table.setEnabled(ok)
        self.defaults_button.setEnabled(ok)
        self.identify_button.setEnabled(ok and dev["connected"])
        self.apply_button.setEnabled(ok and self.has_changes())
        self.forget_button.setEnabled(ok and self.is_saved_device(dev))

    def is_saved_device(self, dev):
        saved = (self.saved_config or {}).get("device")
        return bool(dev and saved and device_key(dev) == device_key(saved))

    def restore_defaults(self):
        for row in range(self.table.rowCount()):
            self.table.cellWidget(row, 1).set_mapping(None)
        self.hint.setText("All buttons set to Default. Click Apply to save.")
        self.update_buttons()

    # -- identify --

    def toggle_identify(self):
        if self.identifying:
            self.stop_identify()
            return
        dev = self.current_device()
        try:
            self.client.call("StartIdentify", dev["vendor"], dev["product"],
                             dev["name"])
        except RuntimeError as e:
            self.hint.setText(f"Can't identify buttons: {e}")
            return
        self.identifying = True
        self.identify_button.setText("Stop")
        self.hint.setText("Press a button on your device…")

    def stop_identify(self):
        if not self.identifying:
            return
        self.identifying = False
        self.identify_button.setText("Identify button")
        try:
            self.client.call("StopIdentify")
        except RuntimeError:
            pass

    def on_button_pressed(self, name):
        if not self.identifying:
            return
        self.stop_identify()
        row = self.row_of(name)
        if row is None:
            self.extra_rows.add(name)
            row = self.add_row(name)
        self.table.selectRow(row)
        self.table.scrollToItem(self.table.item(row, 0))
        self.hint.setText(f"You pressed: {button_label(name)}")

    # -- saving --

    def apply(self):
        new = self.build_config()
        old = self.saved_config
        self.set_busy(True, "Saving… (a password prompt may appear)")

        def done(error_name, error_message):
            self.set_busy(False)
            if error_name:
                self.show_save_error(error_name, error_message)
                return
            self.saved_config = new
            self.hint.setText("Settings saved.")
            self.update_buttons()
            if not has_left_click(new):
                self.confirm_or_revert(old)

        self.client.set_config(new, done)

    def confirm_or_revert(self, old):
        if RevertDialog(self).exec() == QDialog.DialogCode.Accepted:
            return
        self.set_busy(True, "Reverting to the previous settings…")

        def done(error_name, error_message):
            self.set_busy(False)
            if error_name:
                self.show_save_error(error_name, error_message)
                return
            self.saved_config = old
            self.on_device_chosen()
            self.hint.setText("Previous settings restored.")

        self.client.set_config(old, done)

    def forget_device(self):
        dev = self.current_device()
        answer = QMessageBox.question(
            self, "Forget device?",
            f"Forget <b>{dev['name']}</b>?<br><br>Its button settings will "
            "be removed and it will behave normally. You can set it up "
            "again any time while it's connected.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.stop_identify()
        new = {"version": 1, "device": None, "mappings": {}}
        self.set_busy(True, "Saving… (a password prompt may appear)")

        def done(error_name, error_message):
            self.set_busy(False)
            if error_name:
                self.show_save_error(error_name, error_message)
                return
            self.saved_config = new
            self.load()
            self.on_device_chosen()   # show the rows without its settings
            self.hint.setText(f"Forgot {dev['name']}.")

        self.client.set_config(new, done)

    def toggle_boot(self, enabled):
        self.set_busy(True, "Saving… (a password prompt may appear)")

        def done(error_name, error_message):
            self.set_busy(False)
            if error_name:
                self.boot_box.setChecked(not enabled)   # undo the click
                self.show_save_error(error_name, error_message)
            elif enabled:
                self.hint.setText("Remapping will start at boot.")
            else:
                self.hint.setText("Remapping won't start at boot. After a "
                                  "restart, open BallKey to turn it on.")

        self.client.set_boot_enabled(enabled, done)

    def show_save_error(self, name, message):
        if name.endswith("NotAuthorized"):
            self.hint.setText("Not saved: the password was not given.")
        else:
            self.hint.setText("Not saved.")
            QMessageBox.warning(self, "Couldn't save settings",
                                message or name)

    def set_busy(self, busy, text=""):
        for w in (self.device_box, self.table, self.identify_button,
                  self.defaults_button, self.apply_button, self.boot_box,
                  self.forget_button):
            w.setEnabled(not busy)
        if busy:
            self.hint.setText(text)
        else:
            self.update_buttons()

    # -- status --

    def refresh_status(self):
        try:
            status = self.client.call_json("Status")
        except RuntimeError:
            if self.service_ok:
                self.service_ok = False
                self.stop_identify()
                self.update_buttons()
            self.status.setText("● Service not running")
            return
        if not self.service_ok:
            self.load()   # the service just came back
            return
        try:
            self.fill_devices(self.client.call_json("ListDevices"))
        except RuntimeError:
            pass
        parts = ["Service running"]
        if not status["configured"]:
            parts.append("No device set up yet")
        elif status["device_connected"]:
            parts.append("Device connected")
        else:
            parts.append("Device not connected")
        if status.get("last_error"):
            parts.append(status["last_error"])
        self.status.setText(" · ".join(parts))

    def closeEvent(self, event):
        self.stop_identify()
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("BallKey")
    app.setDesktopFileName("ballkey-settings")
    window = MainWindow(DaemonClient())
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
