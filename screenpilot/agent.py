"""The agent loop: screenshot -> model -> action -> repeat.

Provider-agnostic. History lives in the neutral format from providers.base;
each provider translates it on the way out.
"""
from __future__ import annotations

from .actions import ActionError, Desktop
from .capture import Grab
from .providers import Provider, tool_result, user_text
from .safety import Aborted, Gate, describe

# How many screenshots stay in the transcript. Older ones become a placeholder --
# images dominate both the token bill and a local model's context window.
KEEP_IMAGES = 3


class Events:
    """Hooks the UI (or console) fills in. Defaults are silent."""

    def thinking(self, text: str) -> None: ...
    def say(self, text: str) -> None: ...
    def action(self, label: str) -> None: ...
    def screenshot(self, grab: Grab) -> None: ...
    def status(self, text: str) -> None: ...


def prune_images(history: list[dict], keep: int = KEEP_IMAGES) -> int:
    """Drop all but the newest `keep` screenshots. Returns how many remain."""
    seen = 0
    for message in reversed(history):
        for block in message.get("content", []):
            if block.get("type") != "tool_result" or not block.get("image"):
                continue
            seen += 1
            if seen > keep:
                block["image"] = None
                block["text"] = "[screenshot dropped from history to save context]"
    return min(seen, keep)


class ScreenPilot:
    def __init__(self, provider: Provider, desktop: Desktop, gate: Gate, kill,
                 max_steps: int = 40, events: Events | None = None):
        self.provider = provider
        self.desktop = desktop
        self.gate = gate
        self.kill = kill
        self.max_steps = max_steps
        self.events = events or Events()
        self.history: list[dict] = []

    def run(self, task: str) -> str:
        """Run one task to completion. History carries over between tasks."""
        self.gate.trusted = False
        self.history.append(user_text(task))

        final = ""
        for step in range(self.max_steps):
            self.kill.check()
            prune_images(self.history)
            self.events.status("thinking (step " + str(step + 1) + "/" + str(self.max_steps) + ")")

            turn = self.provider.step(self.history)

            if turn.refusal:
                return "[declined] " + turn.refusal
            if turn.thinking:
                self.events.thinking(turn.thinking)
            if turn.text:
                final = turn.text
                self.events.say(turn.text)

            self.provider.note_assistant(self.history, turn)

            if not turn.calls:
                return final or "[done]"

            results = [self._execute(call) for call in turn.calls]
            self.history.append({"role": "user", "content": results})

        return final or "[stopped after " + str(self.max_steps) + " steps]"

    def _execute(self, call) -> dict:
        name = call.name.lower().split(".")[-1].split("__")[-1]
        args = call.args or {}

        try:
            self.kill.check()
            allowed, reason = self.gate.approve(name, args)
            if not allowed:
                return tool_result(call.id, name, text=reason, is_error=True)

            self.events.action(describe(name, args))
            outcome = self.desktop.run(name, args)
        except Aborted:
            raise
        except ActionError as exc:
            return tool_result(call.id, name, text="Action failed: " + str(exc), is_error=True)
        except Exception as exc:  # a failed click must not kill the session
            return tool_result(call.id, name,
                               text="Action failed: " + type(exc).__name__ + ": " + str(exc),
                               is_error=True)

        if isinstance(outcome, Grab):
            self.events.screenshot(outcome)
            return tool_result(call.id, name, image=outcome.png_b64)
        return tool_result(call.id, name, text=str(outcome))
