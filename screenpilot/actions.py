"""Executes computer-toolset actions against the real Windows desktop."""
from __future__ import annotations

import time

import pyautogui

from .capture import Grab, ScreenGrabber

# Moving the mouse into a screen corner aborts everything. Keep this on.
pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.05

# Claude emits X11 / xdotool key names; pyautogui wants its own spellings.
KEYMAP = {
    "return": "enter", "kp_enter": "enter", "escape": "esc", "backspace": "backspace",
    "delete": "delete", "tab": "tab", "space": "space", "prior": "pageup",
    "next": "pagedown", "page_up": "pageup", "page_down": "pagedown",
    "home": "home", "end": "end", "insert": "insert", "up": "up", "down": "down",
    "left": "left", "right": "right", "super": "win", "super_l": "win",
    "super_r": "win", "meta": "win", "cmd": "win", "control": "ctrl",
    "ctrl_l": "ctrl", "ctrl_r": "ctrl", "alt_l": "alt", "alt_r": "alt",
    "shift_l": "shift", "shift_r": "shift", "menu": "apps", "print": "printscreen",
    "minus": "-", "plus": "+", "equal": "=", "comma": ",", "period": ".",
    "slash": "/", "backslash": "\\", "semicolon": ";", "apostrophe": "'",
    "grave": "`", "bracketleft": "[", "bracketright": "]",
}


def _keys(combo: str) -> list[str]:
    """'ctrl+shift+Return' -> ['ctrl', 'shift', 'enter']"""
    out = []
    for part in combo.replace(" ", "").split("+"):
        if not part:
            continue
        out.append(KEYMAP.get(part.lower(), part.lower()))
    return out


class ActionError(Exception):
    pass


class _NoCloak:
    """Used when there is no window to hide (console mode)."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Desktop:
    """Runs one toolset member at a time and reports the result.

    Returns either a plain string (acknowledgement) or a Grab (image result).
    """

    def __init__(self, grabber: ScreenGrabber, cloak=None):
        self.grabber = grabber
        # Optional context manager that hides our own window while we look --
        # otherwise the agent spends every screenshot reading its own chat log.
        self.cloak = cloak or _NoCloak()
        # Coordinates from the model are always in the pixel space of the last
        # *full* screenshot -- zoom results never redefine that space.
        self.last_full: Grab | None = None

    def _point(self, coord) -> tuple[int, int]:
        if self.last_full is None:
            self.last_full = self.grabber.grab()
        return self.last_full.to_screen(coord[0], coord[1])

    def screenshot(self) -> Grab:
        # Let any click-driven UI change settle before we look.
        time.sleep(0.35)
        with self.cloak:
            self.last_full = self.grabber.grab()
        return self.last_full

    def run(self, name: str, args: dict):
        name = name.lower().split(".")[-1].split("__")[-1]
        coord = args.get("coordinate")
        modifiers = _keys(args["text"]) if args.get("text") and name not in ("type", "key", "hold_key") else []

        if name == "screenshot":
            return self.screenshot()

        if name == "zoom":
            region = args.get("region")
            if not region or len(region) != 4:
                raise ActionError("zoom needs region=[x0,y0,x1,y1]")
            if self.last_full is None:
                self.screenshot()
            with self.cloak:
                return self.grabber.grab_zoom(region, self.last_full)

        if name == "cursor_position":
            x, y = pyautogui.position()
            base = self.last_full or self.grabber.grab()
            mx = (x - base.offset[0]) * base.scale
            my = (y - base.offset[1]) * base.scale
            return f"X={int(mx)}, Y={int(my)}"

        if name == "mouse_move":
            pyautogui.moveTo(*self._point(coord), duration=0.15)
            return "OK"

        if name in ("left_click", "right_click", "middle_click", "double_click", "triple_click"):
            if coord:
                pyautogui.moveTo(*self._point(coord), duration=0.15)
            button = {"left_click": "left", "right_click": "right", "middle_click": "middle"}.get(name, "left")
            clicks = {"double_click": 2, "triple_click": 3}.get(name, 1)
            for key in modifiers:
                pyautogui.keyDown(key)
            try:
                pyautogui.click(button=button, clicks=clicks, interval=0.08)
            finally:
                for key in reversed(modifiers):
                    pyautogui.keyUp(key)
            return "OK"

        if name == "left_click_drag":
            start = args.get("start_coordinate")
            if not start or not coord:
                raise ActionError("left_click_drag needs start_coordinate and coordinate")
            pyautogui.moveTo(*self._point(start), duration=0.15)
            for key in modifiers:
                pyautogui.keyDown(key)
            try:
                pyautogui.mouseDown()
                pyautogui.moveTo(*self._point(coord), duration=0.4)
                pyautogui.mouseUp()
            finally:
                for key in reversed(modifiers):
                    pyautogui.keyUp(key)
            return "OK"

        if name == "left_mouse_down":
            pyautogui.mouseDown()
            return "OK"

        if name == "left_mouse_up":
            pyautogui.mouseUp()
            return "OK"

        if name == "scroll":
            if coord:
                pyautogui.moveTo(*self._point(coord), duration=0.15)
            direction = (args.get("scroll_direction") or "down").lower()
            amount = int(args.get("scroll_amount") or 3)
            for key in modifiers:
                pyautogui.keyDown(key)
            try:
                if direction in ("up", "down"):
                    pyautogui.scroll(amount * 120 * (1 if direction == "up" else -1))
                else:
                    pyautogui.hscroll(amount * 120 * (1 if direction == "right" else -1))
            finally:
                for key in reversed(modifiers):
                    pyautogui.keyUp(key)
            return "OK"

        if name == "type":
            pyautogui.typewrite(args.get("text", ""), interval=0.012)
            return "OK"

        if name == "key":
            combo = _keys(args.get("text", ""))
            if not combo:
                raise ActionError("key needs text")
            for _ in range(max(1, min(100, int(args.get("repeat") or 1)))):
                pyautogui.hotkey(*combo)
            return "OK"

        if name == "hold_key":
            combo = _keys(args.get("text", ""))
            duration = min(float(args.get("duration") or 1), 300.0)
            for key in combo:
                pyautogui.keyDown(key)
            try:
                time.sleep(duration)
            finally:
                for key in reversed(combo):
                    pyautogui.keyUp(key)
            return "OK"

        if name == "wait":
            time.sleep(min(float(args.get("duration") or 1), 300.0))
            return "OK"

        raise ActionError(f"unsupported action: {name}")
