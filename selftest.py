"""Offline check: real capture + real action dispatch, scripted model turns.

Runs everything except the network and anything that clicks or types.
"""
from screenpilot.actions import Desktop
from screenpilot.agent import ScreenPilot, prune_images
from screenpilot.capture import ScreenGrabber, enable_dpi_awareness
from screenpilot.providers import Call, Provider, Turn
from screenpilot.providers.ollama import OllamaProvider
from screenpilot.safety import Gate, KillSwitch, describe


class ScriptedProvider(Provider):
    """Replays a fixed list of turns instead of calling a model."""

    name = label = "scripted"

    def __init__(self, turns):
        self.turns = iter(turns)

    def step(self, history):
        return next(self.turns)


def call(name, **args):
    return Call(id=name[:6], name=name, args=args)


SCRIPT = [
    Turn(calls=[call("screenshot")]),
    Turn(calls=[call("zoom", region=[100, 100, 400, 300])]),
    Turn(calls=[call("cursor_position")]),
    Turn(calls=[call("wait", duration=0.1)]),
    Turn(calls=[call("nonsense_action")]),
    Turn(text="Done looking around."),
]


def test_loop():
    enable_dpi_awareness()
    grabber = ScreenGrabber(monitor=1, max_edge=1024)
    pilot = ScreenPilot(ScriptedProvider(SCRIPT), Desktop(grabber), Gate("none"), KillSwitch())

    answer = pilot.run("look around")
    print("final answer:", answer)

    results = [b for m in pilot.history for b in m["content"] if b.get("type") == "tool_result"]
    print("tool results:", len(results))
    for r in results:
        kind = "image" if r.get("image") else repr(r.get("text"))[:56]
        print("  " + r["name"] + ": " + kind + "  error=" + str(r.get("is_error")))
    assert results[-1]["is_error"], "unknown action should be an error, not a crash"

    kept = prune_images(pilot.history, keep=1)
    live = sum(1 for m in pilot.history for b in m["content"] if b.get("image"))
    print("images after prune(1):", live)
    assert live == 1 == kept, "prune kept " + str(live)


def test_gate():
    g = Gate("act")
    assert g.approve("screenshot", {})[0], "observation should autorun"
    assert g.approve("scroll", {"scroll_direction": "down", "scroll_amount": 3})[0]
    assert Gate("none").approve("left_click", {"coordinate": [1, 1]})[0]
    trusted = Gate("act")
    trusted.trusted = True
    assert trusted.approve("type", {"text": "x"})[0]

    asked = []
    gated = Gate("act", ask=lambda n, a: (asked.append(n), ("skip", "nope"))[1])
    allowed, reason = gated.approve("left_click", {"coordinate": [5, 5]})
    assert not allowed and reason == "nope" and asked == ["left_click"]
    print("gate: observation auto, state-changing gated, skip reason forwarded")


def test_json_parsing():
    """The local-model protocol has to survive whatever a small model emits."""
    p = OllamaProvider(screen_size=(1024, 576))

    turn = p._parse('{"thought":"I see a button","action":"left_click","coordinate":[500,300]}')
    assert turn.calls[0].name == "left_click" and turn.calls[0].args["coordinate"] == [500, 300]

    turn = p._parse('Sure! ```json\n{"action":"type","text":"hello"}\n```')
    assert turn.calls[0].args["text"] == "hello", "must recover JSON from chatter"

    turn = p._parse('{"action":"done","message":"Finished."}')
    assert not turn.calls and turn.text == "Finished."

    # 0-1000 normalised coords (a Qwen2-era habit) must rescale, not fly off-screen
    turn = p._parse('{"action":"left_click","coordinate":[1000,1000]}')
    assert turn.calls[0].args["coordinate"] == [1023, 575], turn.calls[0].args

    # out-of-bounds junk gets clamped rather than clicked
    turn = p._parse('{"action":"left_click","coordinate":[99999,-40]}')
    x, y = turn.calls[0].args["coordinate"]
    assert 0 <= x < 1024 and 0 <= y < 576, (x, y)

    turn = p._parse("total gibberish, no json here")
    assert not turn.calls and "could not parse" in turn.text

    turn = p._parse('{"action":"scroll","scroll_direction":"down","scroll_amount":3,"bogus":1}')
    assert "bogus" not in turn.calls[0].args, "unknown args must be dropped"
    print("json protocol: chatter, done, normalised coords, junk, bad args all handled")


def test_history_translation():
    p = OllamaProvider(screen_size=(1024, 576))
    history = [
        {"role": "user", "content": [{"type": "text", "text": "what is on screen"}]},
        {"role": "assistant", "content": [{"type": "tool_call", "id": "a", "name": "screenshot", "args": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "id": "a", "name": "screenshot",
                                      "text": None, "image": "FAKEB64", "is_error": False}]},
    ]
    msgs = p._messages(history)
    assert msgs[0]["role"] == "system" and "1024 x 576" in msgs[0]["content"]
    assert msgs[-1]["images"] == ["FAKEB64"], "screenshot must reach the model"
    print("history translation: system sized, image attached")


if __name__ == "__main__":
    test_loop()
    test_gate()
    test_json_parsing()
    test_history_translation()
    print("describe():", describe("left_click", {"coordinate": [640, 400]}),
          "|", describe("type", {"text": "hello"}))
    print("\nSELFTEST PASSED")
