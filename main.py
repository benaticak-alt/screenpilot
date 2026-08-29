"""ScreenPilot - an AI that watches your screen and drives it.

  python main.py                            chat window, local Ollama model
  python main.py --provider anthropic       chat window, Claude Opus 5
  python main.py --console                  no window, terminal only
"""
from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv

from screenpilot.actions import Desktop
from screenpilot.agent import ScreenPilot
from screenpilot.capture import ScreenGrabber, enable_dpi_awareness
from screenpilot.providers import PROVIDERS, get_provider
from screenpilot.safety import Aborted, Gate, KillSwitch


def check_ollama(model: str, host: str) -> str | None:
    """Returns an error message, or None if we are good to go."""
    from screenpilot.providers.ollama import list_models

    models = list_models(host)
    if not models:
        return ("Ollama is not answering on " + host + ".\n"
                "Start it with:  ollama serve")

    names = [m.get("name", "") for m in models]
    stems = [n.split(":")[0] for n in names]
    if model not in names and model.split(":")[0] not in stems:
        return ("Model '" + model + "' is not installed.\n"
                "Get it with:  ollama pull " + model + "\n"
                "Installed: " + (", ".join(names) or "none"))

    for entry in models:
        if entry.get("name") == model and "vision" not in (entry.get("capabilities") or []):
            return ("Model '" + model + "' has no vision capability, so it cannot see "
                    "your screen.\nTry:  ollama pull qwen3-vl:8b")
    return None


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Let an AI see and control your screen.")
    parser.add_argument("--provider", choices=list(PROVIDERS), default="ollama",
                        help="ollama = free and local (default); anthropic = Claude Opus 5")
    parser.add_argument("--model", default=None, help="override the model name")
    parser.add_argument("--host", default=None, help="Ollama host (default http://localhost:11434)")
    parser.add_argument("--console", action="store_true", help="terminal instead of the chat window")
    parser.add_argument("--monitor", type=int, default=1, help="monitor index (1 = primary)")
    parser.add_argument("--max-edge", type=int, default=None,
                        help="screenshot long edge; default 1024 local, 1366 for Claude")
    parser.add_argument("--confirm", choices=["act", "all", "none"], default="act",
                        help="act: ask before anything that changes state (default)")
    parser.add_argument("--max-steps", type=int, default=40, help="actions per task")
    parser.add_argument("--grid", action="store_true",
                        help="overlay a coordinate grid on screenshots (measured worse on "
                             "qwen2.5vl; may help other models)")
    parser.add_argument("--no-cloak", action="store_true",
                        help="do not hide the window while the model looks at the screen")
    args = parser.parse_args()

    # Local models resolve less detail, so give them a smaller image by default;
    # Claude gets the larger one it handles well.
    max_edge = args.max_edge or (1024 if args.provider == "ollama" else 1366)

    enable_dpi_awareness()
    grabber = ScreenGrabber(monitor=args.monitor, max_edge=max_edge, grid=args.grid)
    probe = grabber.grab()

    if args.provider == "ollama":
        from screenpilot.providers.ollama import DEFAULT_HOST, DEFAULT_MODEL
        problem = check_ollama(args.model or DEFAULT_MODEL, args.host or DEFAULT_HOST)
        if problem:
            print("\n" + problem + "\n")
            return 1

    provider = get_provider(args.provider, args.model,
                            screen_size=probe.sent_size, host=args.host)

    kill = KillSwitch()
    gate = Gate(args.confirm)
    geo = grabber.geometry
    subtitle = (provider.label + "   |   monitor " + str(args.monitor) + " "
                + str(geo["width"]) + "x" + str(geo["height"]) + " -> "
                + str(probe.sent_size[0]) + "x" + str(probe.sent_size[1])
                + "   |   confirm: " + args.confirm
                + "   |   abort: ctrl+alt+x or mouse to a screen corner")

    if args.console:
        pilot = ScreenPilot(provider, Desktop(grabber), gate, kill, max_steps=args.max_steps)
        return console_loop(pilot, kill, subtitle)

    from screenpilot.ui import ChatWindow

    desktop = Desktop(grabber)
    pilot = ScreenPilot(provider, desktop, gate, kill, max_steps=args.max_steps)
    window = ChatWindow(pilot, kill, subtitle=subtitle)

    desktop.cloak = window.cloak
    window.cloak.enabled = not args.no_cloak
    pilot.events = window
    gate.ask = window.ask

    window.run()
    return 0


def console_loop(pilot, kill, subtitle) -> int:
    from screenpilot.agent import Events

    class Printer(Events):
        def thinking(self, text): print("  " + text)
        def say(self, text): print("\n  " + text)
        def action(self, label): print("  . " + label)
        def status(self, text): pass

    pilot.events = Printer()
    print("ScreenPilot\n  " + subtitle + "\n")

    while True:
        try:
            task = input("task > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not task:
            continue
        if task.lower() in ("exit", "quit"):
            return 0

        kill.arm()
        try:
            print("\n= " + pilot.run(task) + "\n")
        except Aborted as exc:
            print("\n! aborted: " + str(exc) + "\n")
        except Exception as exc:
            print("\n! " + type(exc).__name__ + ": " + str(exc) + "\n")
        finally:
            kill.disarm()


if __name__ == "__main__":
    sys.exit(main())
