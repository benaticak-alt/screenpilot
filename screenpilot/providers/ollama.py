"""Local vision models via Ollama, using a JSON action protocol.

Most open vision models either have no tool-calling or are unreliable at it, so
we ask for a single JSON object per turn and parse that instead. Ollama's
`format: json` constrains decoding, which makes small models far more reliable.
"""
from __future__ import annotations

import json
import uuid

import requests

from .base import ACTIONS, Call, Provider, Turn

DEFAULT_HOST = "http://localhost:11434"
DEFAULT_MODEL = "qwen3-vl:8b"  # measured 2x better at pointing than qwen2.5vl:7b

SYSTEM = """You control a Windows desktop by looking at screenshots and choosing ONE action at a time.

The screenshot you are shown is {width} x {height} pixels. All coordinates you give
must be absolute pixel positions inside that image: x from 0 to {width}, y from 0 to {height}.
The origin (0,0) is the TOP-LEFT corner. Aim at the CENTRE of the thing you want to click.

Reply with ONE JSON object and nothing else:

{{"thought": "<one short sentence about what you see and why>",
  "action": "<action name>",
  "coordinate": [x, y],
  "text": "...",
  "message": "..."}}

Include only the fields the action needs.

Available actions:
{actions}

Rules:
- Your first action for any new task is "screenshot". Never guess what is on screen.
- After any action that changes the screen, take another screenshot to check it worked.
- One action per reply. Do not batch.
- When the task is complete, or you cannot continue, use action "done" with a "message".
- Never type passwords or card numbers. Never send messages, buy anything, or delete
  files. If a task needs that, use "done" and explain what you need from the user."""

# Args we accept back from the model; anything else it invents is dropped.
ARG_KEYS = ("coordinate", "start_coordinate", "region", "text",
            "scroll_direction", "scroll_amount", "duration", "repeat")

DONE_WORDS = ("done", "finish", "finished", "stop", "complete", "none")


class OllamaProvider(Provider):
    name = "ollama"

    def __init__(self, model: str = DEFAULT_MODEL, host: str = DEFAULT_HOST,
                 screen_size: tuple[int, int] = (1366, 768), num_ctx: int = 16384):
        self.model = model
        self.host = host.rstrip("/")
        self.screen_size = screen_size
        self.num_ctx = num_ctx
        self.label = "ollama/" + model

    # -- history translation ---------------------------------------------------

    def _messages(self, history: list[dict]) -> list[dict]:
        width, height = self.screen_size
        system = SYSTEM.format(
            width=width,
            height=height,
            actions="\n".join("- " + k + ": " + v for k, v in ACTIONS.items()),
        )
        out = [{"role": "system", "content": system}]

        for message in history:
            if message["role"] == "assistant":
                # Replay the model's own JSON so it can see what it already tried.
                parts = []
                for block in message["content"]:
                    if block["type"] == "text" and block["text"]:
                        parts.append(block["text"])
                    elif block["type"] == "tool_call":
                        parts.append(json.dumps(dict({"action": block["name"]}, **block["args"])))
                out.append({"role": "assistant", "content": "\n".join(parts) or "{}"})
                continue

            text_parts, images = [], []
            for block in message["content"]:
                if block["type"] == "text":
                    text_parts.append(block["text"])
                elif block["type"] == "tool_result":
                    if block.get("image"):
                        images.append(block["image"])
                        text_parts.append("[" + block["name"] + " result: screenshot below]")
                    else:
                        prefix = "FAILED" if block.get("is_error") else "result"
                        text_parts.append("[" + block["name"] + " " + prefix + ": " + str(block.get("text")) + "]")
            msg = {"role": "user", "content": "\n".join(text_parts) or "(continue)"}
            if images:
                msg["images"] = images[-1:]  # one image per turn keeps small models coherent
            out.append(msg)
        return out

    # -- the call --------------------------------------------------------------

    def step(self, history: list[dict]) -> Turn:
        payload = {
            "model": self.model,
            "messages": self._messages(history),
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.2, "num_ctx": self.num_ctx},
        }
        try:
            response = requests.post(self.host + "/api/chat", json=payload, timeout=300)
            response.raise_for_status()
        except requests.RequestException as exc:
            return Turn(text="[ollama unreachable: " + str(exc) + "]")

        raw = (response.json().get("message") or {}).get("content", "").strip()
        return self._parse(raw)

    def _parse(self, raw: str) -> Turn:
        data = _loads(raw)
        if data is None:
            return Turn(text="[could not parse model reply: " + raw[:200] + "]")
        if isinstance(data, list):
            data = data[0] if data else {}
        if not isinstance(data, dict):
            return Turn(text="[unexpected model reply: " + raw[:200] + "]")

        thought = str(data.get("thought") or "")
        action = str(data.get("action") or "").strip().lower()

        if not action or action in DONE_WORDS:
            return Turn(text=str(data.get("message") or thought or "Done."), thinking=thought)

        args = {k: v for k, v in data.items() if k in ARG_KEYS and v is not None}
        for key in ("coordinate", "start_coordinate"):
            if key in args:
                args[key] = self._fix_coords(args[key])
        if "region" in args:
            args["region"] = self._fix_coords(args["region"], count=4)

        return Turn(thinking=thought, calls=[Call(id=uuid.uuid4().hex[:8], name=action, args=args)])

    def _fix_coords(self, value, count: int = 2):
        """Coerce whatever the model emitted into in-bounds pixel coordinates.

        Qwen2-era checkpoints emit coordinates normalised to 0-1000; 2.5 emits
        absolute pixels. If the numbers overflow the image we assume the former
        and rescale, rather than clicking off-screen.
        """
        if isinstance(value, dict):
            value = [value.get(k) for k in ("x", "y", "x2", "y2")][:count]
        try:
            nums = [float(v) for v in list(value)[:count]]
        except (TypeError, ValueError):
            return value
        if len(nums) < count:
            return value

        width, height = self.screen_size
        dims = ([width, height] * 2)[:count]
        if any(n > d for n, d in zip(nums, dims)) and all(n <= 1000 for n in nums):
            nums = [n / 1000.0 * d for n, d in zip(nums, dims)]
        return [int(max(0, min(d - 1, n))) for n, d in zip(nums, dims)]


def _loads(raw: str):
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        return None


def list_models(host: str = DEFAULT_HOST) -> list[dict]:
    try:
        response = requests.get(host.rstrip("/") + "/api/tags", timeout=5)
        response.raise_for_status()
        return response.json().get("models", [])
    except requests.RequestException:
        return []
