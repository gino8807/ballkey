#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Replays recorded device events through the daemon's remapping and
checks what comes out. Needs no real device, root or uinput.

Run:  python3 tests/test-remap.py
"""

import importlib.util
import json
import logging
import os
import tempfile
import unittest

from evdev import InputEvent, ecodes

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location(
    "ballkeyd", os.path.join(HERE, "..", "daemon", "ballkeyd.py"))
ballkeyd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ballkeyd)

EV_KEY, EV_REL, EV_SYN = ecodes.EV_KEY, ecodes.EV_REL, ecodes.EV_SYN
SYN = (EV_SYN, ecodes.SYN_REPORT, 0)

# Recorded with evtest from a Logitech G502 HERO (046d:c08b):
# tilt left once, then tilt right once.
G502_TILT_LEFT = [(EV_REL, ecodes.REL_HWHEEL, -1),
                  (EV_REL, ballkeyd.REL_HWHEEL_HI_RES, -120), SYN]
G502_TILT_RIGHT = [(EV_REL, ecodes.REL_HWHEEL, 1),
                   (EV_REL, ballkeyd.REL_HWHEEL_HI_RES, 120), SYN]
# Same mouse: front thumb button click.
G502_THUMB = [(EV_KEY, ecodes.BTN_EXTRA, 1), SYN,
              (EV_KEY, ecodes.BTN_EXTRA, 0), SYN]


class FakeUInput:
    """Stands in for the virtual device and records what is sent."""

    def __init__(self):
        self.sent = []

    def write(self, type_, code, value):
        self.sent.append((type_, code, value))

    def write_event(self, event):
        self.sent.append((event.type, event.code, event.value))

    def syn(self):
        self.sent.append(SYN)


def make_daemon(mappings, chords=()):
    """A daemon with these settings, sending to a FakeUInput."""
    cfg = {"version": 1,
           "device": {"vendor": "046d", "product": "c08b", "name": "Test"},
           "mappings": mappings, "chords": list(chords)}
    with tempfile.NamedTemporaryFile("w", suffix=".json",
                                     delete=False) as f:
        json.dump(cfg, f)
    try:
        daemon = ballkeyd.Daemon(f.name, loop=None)
    finally:
        os.unlink(f.name)
    daemon.ui = FakeUInput()
    return daemon


def replay(daemon, events):
    for type_, code, value in events:
        daemon.handle_event(InputEvent(0, 0, type_, code, value))
    return daemon.ui.sent


class TiltTest(unittest.TestCase):

    def test_unmapped_tilt_scrolls_normally(self):
        d = make_daemon({})
        self.assertEqual(replay(d, G502_TILT_LEFT + G502_TILT_RIGHT),
                         G502_TILT_LEFT + G502_TILT_RIGHT)

    def test_tilt_left_does_back(self):
        d = make_daemon({"TILT_LEFT": {"action": "back"}})
        self.assertEqual(replay(d, G502_TILT_LEFT), [
            (EV_KEY, ecodes.BTN_SIDE, 1), SYN,
            (EV_KEY, ecodes.BTN_SIDE, 0), SYN])

    def test_only_mapped_direction_changes(self):
        d = make_daemon({"TILT_LEFT": {"action": "back"}})
        self.assertEqual(replay(d, G502_TILT_RIGHT), G502_TILT_RIGHT)

    def test_tilt_keycombo(self):
        d = make_daemon({"TILT_RIGHT": {"action": "keycombo",
                                        "keys": ["KEY_LEFTCTRL", "KEY_T"]}})
        self.assertEqual(replay(d, G502_TILT_RIGHT), [
            (EV_KEY, ecodes.KEY_LEFTCTRL, 1), (EV_KEY, ecodes.KEY_T, 1), SYN,
            (EV_KEY, ecodes.KEY_T, 0), (EV_KEY, ecodes.KEY_LEFTCTRL, 0), SYN])

    def test_tilt_do_nothing(self):
        d = make_daemon({"TILT_LEFT": {"action": "none"}})
        self.assertEqual(replay(d, G502_TILT_LEFT), [SYN, SYN])

    def test_tilt_identify(self):
        d = make_daemon({})
        pressed = []
        d.ButtonPressed = pressed.append
        d.identify_main = True
        replay(d, G502_TILT_LEFT + G502_TILT_RIGHT)
        self.assertEqual(pressed, ["TILT_LEFT", "TILT_RIGHT"])

    def test_tilt_not_allowed_in_combination(self):
        with self.assertRaises(ValueError):
            ballkeyd.parse_config(
                {"version": 1, "chords": [{"buttons": ["BTN_LEFT",
                                                       "TILT_LEFT"],
                                           "action": "middle"}]},
                strict=True)


class ButtonTest(unittest.TestCase):
    """Ordinary buttons still behave as before."""

    def test_unmapped_button_passes_through(self):
        d = make_daemon({"TILT_LEFT": {"action": "back"}})
        self.assertEqual(replay(d, G502_THUMB), G502_THUMB)

    def test_mapped_button(self):
        d = make_daemon({"BTN_EXTRA": {"action": "middle"}})
        self.assertEqual(replay(d, G502_THUMB), [
            (EV_KEY, ecodes.BTN_MIDDLE, 1), SYN,
            (EV_KEY, ecodes.BTN_MIDDLE, 0), SYN])


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    unittest.main()
