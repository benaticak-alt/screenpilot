"""Claude via the native computer toolset.

Unlike the JSON-protocol providers, the action vocabulary here is defined
server-side by `computer_toolset_20260801`, so we send no action list at all.
"""
from __future__ import annotations

from .base import Call, Provider, Turn

MODEL = "claude-opus-5"
TOOLSET_NAME = "computer"

SYSTEM = """You are driving the user's real Windows 11 desktop through the computer toolset.
This is their actual machine, not a sandbox: every click and keystroke is real and some are irreversible.

Working method:
- Take a screenshot first. Never act on a guess about what is on screen.
- After any action that changes the UI, screenshot again to confirm it did what you expected.
- Use zoom to read small text rather than squinting at the full screenshot.
- Work in small steps and say what you are doing as you go.

Hard limits - do not do these, stop and ask the user instead:
- Entering passwords, card numbers, or any other credential.
- Purchases, payments, or transfers.
- Sending an email or message, or posting anything publicly.
- Deleting files, emptying the recycle bin, or uninstalling software.
- Changing Windows settings, security settings, or system configuration.

If the task is finished, or you are blocked, or you need a decision from the user,
stop calling tools and reply in plain text."""


class AnthropicProvider(Provider):
    name = "anthropic"

    def __init__(self, model: str = MODEL):
        import anthropic
        self.client = anthropic.Anthropic()
        self.model = model
        self.label = model

    def _convert(self, history: list[dict]) -> list[dict]:
        out = []
        for message in history:
            # Assistant turns replay verbatim so thinking blocks survive intact.
            if message["role"] == "assistant" and message.get("native") is not None:
                out.append({"role": "assistant", "content": message["native"]})
                continue

            content = []
            for block in message["content"]:
                if block["type"] == "text":
                    content.append({"type": "text", "text": block["text"]})
                elif block["type"] == "tool_call":
                    content.append({
                        "type": "tool_use", "id": block["id"], "name": block["name"],
                        "input": block["args"], "toolset_name": TOOLSET_NAME,
                    })
                elif block["type"] == "tool_result":
                    result = {
                        "type": "tool_result",
                        "tool_use_id": block["id"],
                        "toolset_name": TOOLSET_NAME,
                    }
                    if block.get("image"):
                        result["content"] = [{
                            "type": "image",
                            "source": {"type": "base64", "media_type": "image/png",
                                       "data": block["image"]},
                        }]
                    else:
                        result["content"] = block.get("text") or "OK"
                    if block.get("is_error"):
                        result["is_error"] = True
                    content.append(result)
            out.append({"role": message["role"], "content": content})
        return out

    def step(self, history: list[dict]) -> Turn:
        response = self.client.beta.messages.create(
            model=self.model,
            max_tokens=16000,
            system=SYSTEM,
            tools=[{"type": "computer_toolset_20260801",
                    "cache_control": {"type": "ephemeral"}}],
            thinking={"type": "adaptive", "display": "summarized"},
            output_config={"effort": "high"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=self._convert(history),
        )

        if response.stop_reason == "refusal":
            detail = getattr(response, "stop_details", None)
            return Turn(refusal=getattr(detail, "explanation", "Claude declined this request."))

        turn = Turn()
        turn._native = response.content  # replayed as-is on the next call
        for block in response.content:
            if block.type == "text" and block.text.strip():
                turn.text = (turn.text + "\n" + block.text.strip()).strip()
            elif block.type == "thinking":
                turn.thinking = (getattr(block, "thinking", "") or "").strip()
            elif block.type == "tool_use":
                turn.calls.append(Call(id=block.id, name=block.name, args=block.input or {}))
        return turn

    def note_assistant(self, history: list[dict], turn: Turn) -> None:
        history.append({"role": "assistant", "content": [], "native": getattr(turn, "_native", None)})
