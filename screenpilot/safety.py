"""Approval gate and kill switch.

The model can move the real mouse and type on the real keyboard, so every
state-changing action passes through here first.
"""
from __future__ import annotations

import threading

# Actions that only look at the screen or move the pointer. Safe to autorun.
OBSERVE = {"screenshot", "zoom", "cursor_position", "wait", "mouse_move", "scroll"}

# Everything else changes state: clicks, typing, key combos, drags.


class Aborted(Exception):
    """Raised when the user hits the kill switch or denies a whole run."""


class KillSwitch:
    """Panic button.

    Deliberately a chord, not bare ESC: Claude presses ESC itself all the time
    via pyautogui, and a global ESC hook cannot tell an injected keystroke from
    a real one -- so the agent would abort itself. The mouse-to-corner failsafe
    in actions.py is the second, hookless way out.
    """

    def __init__(self, combo: str = "ctrl+alt+x"):
        self.combo = combo
        self._flag = threading.Event()
        self._hook = None

    def arm(self) -> None:
        self._flag.clear()
        try:
            import keyboard
            self._hook = keyboard.add_hotkey(self.combo, self._flag.set)
        except Exception:
            self._hook = None  # no global hook; corner failsafe still works

    def disarm(self) -> None:
        if self._hook is not None:
            try:
                import keyboard
                keyboard.remove_hotkey(self._hook)
            except Exception:
                pass
            self._hook = None

    def check(self) -> None:
        if self._flag.is_set():
            raise Aborted("kill switch pressed")


def describe(name: str, args: dict) -> str:
    """One-line, human-readable version of what is about to happen."""
    coord = args.get("coordinate")
    where = f" at ({coord[0]}, {coord[1]})" if coord else ""
    if name == "type":
        return f'type: "{args.get("text", "")}"'
    if name == "key":
        repeat = args.get("repeat")
        return f'press {args.get("text", "")}' + (f" x{repeat}" if repeat and repeat > 1 else "")
    if name == "hold_key":
        return f'hold {args.get("text", "")} for {args.get("duration")}s'
    if name == "left_click_drag":
        start = args.get("start_coordinate") or ("?", "?")
        return f"drag ({start[0]}, {start[1]}) -> ({coord[0]}, {coord[1]})" if coord else "drag"
    if name == "scroll":
        return f'scroll {args.get("scroll_direction")} x{args.get("scroll_amount")}{where}'
    return f"{name}{where}"


class Gate:
    """Decides whether an action runs.

    mode:
      "act"  - autorun observation, ask before anything that changes state (default)
      "all"  - ask before every action, including screenshots
      "none" - autorun everything (only for tasks you have already watched once)
    """

    def __init__(self, mode: str = "act", ask=None):
        self.mode = mode
        self.trusted = False  # set when the user approves the rest of a task
        # ask(name, args) -> (choice, reason), choice in run/skip/trust/abort.
        # Defaults to the console prompt; the UI passes its own.
        self.ask = ask or console_ask

    def approve(self, name: str, args: dict) -> tuple[bool, str]:
        """Returns (allowed, denial_reason)."""
        if self.mode == "none" or self.trusted:
            return True, ""
        if self.mode == "act" and name in OBSERVE:
            return True, ""

        choice, reason = self.ask(name, args)
        if choice == "trust":
            self.trusted = True
            return True, ""
        if choice == "abort":
            raise Aborted("user aborted")
        if choice == "skip":
            return False, reason or "User skipped this action."
        return True, ""


def console_ask(name: str, args: dict) -> tuple[str, str]:
    print(f"\n  >> {describe(name, args)}")
    while True:
        choice = input("     [Enter]=run  s=skip  a=allow rest of task  q=abort > ").strip().lower()
        if choice in ("", "y", "yes"):
            return "run", ""
        if choice in ("s", "n", "no", "skip"):
            return "skip", input("     tell the model why (optional) > ").strip()
        if choice == "a":
            print("     autorunning the rest of this task (ctrl+alt+x still aborts)")
            return "trust", ""
        if choice == "q":
            return "abort", ""
