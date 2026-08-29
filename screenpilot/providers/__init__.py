"""Provider registry."""
from __future__ import annotations

from .base import ACTIONS, Call, Provider, Turn, tool_result, user_text

PROVIDERS = ("ollama", "anthropic")


def get_provider(name: str, model: str | None = None,
                 screen_size: tuple[int, int] = (1366, 768), host: str | None = None) -> Provider:
    name = (name or "ollama").lower()

    if name == "ollama":
        from .ollama import DEFAULT_HOST, DEFAULT_MODEL, OllamaProvider
        return OllamaProvider(
            model=model or DEFAULT_MODEL,
            host=host or DEFAULT_HOST,
            screen_size=screen_size,
        )

    if name in ("anthropic", "claude"):
        from .anthropic_api import MODEL, AnthropicProvider
        return AnthropicProvider(model=model or MODEL)

    raise ValueError("unknown provider: " + name + " (choose from " + ", ".join(PROVIDERS) + ")")


__all__ = ["ACTIONS", "Call", "Provider", "Turn", "PROVIDERS",
           "get_provider", "tool_result", "user_text"]
