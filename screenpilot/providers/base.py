"""Provider-neutral message format and interface.

The agent loop keeps history in this format; each provider translates it to
whatever its API wants. Anthropic gets the native computer toolset. Everything
else -- local Ollama models included -- gets a JSON action protocol, because
most open vision models either lack tool calling or are bad at it.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Call:
    """One requested action."""
    id: str
    name: str
    args: dict


@dataclass
class Turn:
    """What the model produced this step."""
    text: str = ""                     # shown to the user
    calls: list[Call] = field(default_factory=list)
    thinking: str = ""                 # shown dimmed, if the model exposes it
    refusal: str = ""                  # non-empty means the model declined


# ---- internal message blocks -------------------------------------------------

def user_text(text: str) -> dict:
    return {"role": "user", "content": [{"type": "text", "text": text}]}


def tool_result(call_id: str, name: str, text: str | None = None,
                image: str | None = None, is_error: bool = False) -> dict:
    return {
        "type": "tool_result",
        "id": call_id,
        "name": name,
        "text": text,
        "image": image,
        "is_error": is_error,
    }


# The action vocabulary. Anthropic's toolset defines these server-side; for
# every other provider this table becomes the prompt.
ACTIONS = {
    "screenshot": "Look at the screen. No arguments. Do this before acting, and again after anything that changes the screen.",
    "zoom": "Read small text in a region. args: region=[x0,y0,x1,y1]",
    "left_click": "args: coordinate=[x,y]",
    "right_click": "args: coordinate=[x,y]",
    "double_click": "args: coordinate=[x,y]",
    "triple_click": "args: coordinate=[x,y] (selects a whole line)",
    "left_click_drag": "args: start_coordinate=[x,y], coordinate=[x,y]",
    "mouse_move": "args: coordinate=[x,y]",
    "scroll": "args: coordinate=[x,y], scroll_direction='up'|'down'|'left'|'right', scroll_amount=<int>",
    "type": "Type literal text. args: text='...'",
    "key": "Press a key or chord. args: text='Return' | 'ctrl+s' | 'alt+Tab'",
    "wait": "args: duration=<seconds>",
    "done": "The task is finished, or you are blocked and need the user. args: message='what you found or did'",
}


class Provider:
    """Interface. `step` gets the whole history and returns one Turn."""

    name = "base"
    label = "base"

    def step(self, history: list[dict]) -> Turn:
        raise NotImplementedError

    def note_assistant(self, history: list[dict], turn: Turn) -> None:
        """Append this turn to history in whatever shape the provider replays."""
        content: list[dict] = []
        if turn.text:
            content.append({"type": "text", "text": turn.text})
        for call in turn.calls:
            content.append({"type": "tool_call", "id": call.id, "name": call.name, "args": call.args})
        history.append({"role": "assistant", "content": content})
