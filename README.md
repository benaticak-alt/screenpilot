# ScreenPilot

A chat window where you type to an AI that can see your screen live — and click and
type on it. Runs on a free local model by default; nothing leaves your machine.

## Run it

```
cd C:\path\to\screenpilot
.venv\Scripts\python.exe main.py
```

That is the whole setup. No API key, no account. It needs Ollama running:

```
ollama serve
```

## What it looks like

A dark chat window. Type at the bottom, `Enter` sends, `Shift+Enter` makes a newline.
The right-hand panel shows the exact screenshot the model is looking at, so you can
see what it sees. Green lines are actions it took, dim italics are its reasoning.

The window hides itself for a fraction of a second whenever the model takes a
screenshot — otherwise the agent spends every turn reading its own chat log.
Turn that off with `--no-cloak` if the flicker bothers you.

## How well does it actually work?

Worth being straight about this, because it decides what you should use it for.

I measured pointing accuracy on your actual screen — asking the model to point at a
known target and comparing to ground truth, on a 1024×576 screenshot:

| Model | Mean error | Speed | Notes |
|---|---|---|---|
| `qwen2.5vl:7b` | 209 px | ~3 s | often returned no coordinate at all |
| `qwen3-vl:8b` (default) | **85 px** | ~9–25 s | always returns a coordinate |

85px on a 1024px-wide image is roughly 8% of the screen. So:

- **"What's on my screen?" / "read this error" / "what does this dialog say"** — works well.
  This is the thing local models are genuinely good at.
- **"Click that button"** — lands on big targets (a window, a large panel, a text area),
  misses small ones (a toolbar icon, a menu item, a checkbox).

Keep `--confirm act` on, which is the default: you see every click before it happens
and can skip the wrong ones. Treat the local model as a capable observer and a clumsy
driver.

For reliable clicking, switch to Claude:

```
.venv\Scripts\python.exe main.py --provider anthropic
```

That needs `ANTHROPIC_API_KEY` in `.env` — **not** in `.env.example`, which is the
shareable template and must stay placeholder-only.

### Things I tried that did not work

A labelled coordinate grid drawn over the screenshot (a standard trick for weak
vision models) measured *worse* than a plain screenshot: 105px error vs 66px on the
same target. It is still available behind `--grid` because the effect is
model-dependent and it costs nothing to re-test, but it is off by default.

Disabling qwen3-vl's thinking made it faster but noticeably less accurate
(135px vs 85px), so thinking stays on.

## Stopping it

Two independent kill switches, because it is driving your real machine:

- **ctrl+alt+x** — aborts at the next step.
- **Slam the mouse into any screen corner** — pyautogui's failsafe. Fires mid-action
  and needs no keyboard hook, so it works when everything else is stuck.
- The **Stop** button in the header does the same as the chord.

It is a chord rather than bare ESC on purpose: the model presses ESC itself, and a
global ESC hook cannot tell an injected keystroke from yours — it would abort itself.

## Approval

`--confirm act` (default) autoruns anything that only looks — screenshot, zoom,
scroll, mouse move — and asks before anything that changes state: clicks, typing,
key combos, drags. A bar appears at the bottom of the window with the action spelled
out in plain language and four buttons: **Run**, **Skip**, **Trust rest** (for this
task only), **Abort**.

- `--confirm all` asks before every action, screenshots included.
- `--confirm none` autoruns everything. Only for a task you have already watched.

The system prompt also refuses credentials, payments, sending messages, deleting
files, and system settings. That is a second layer, not the primary one — a small
local model follows instructions loosely, so the approval gate is what actually
enforces anything.

## Options

| Flag | Default | Notes |
|---|---|---|
| `--provider` | `ollama` | `ollama` (free, local) or `anthropic` (Claude Opus 5) |
| `--model` | `qwen3-vl:8b` | any vision model in your Ollama library |
| `--console` | off | terminal instead of the window |
| `--monitor` | `1` | 1 is primary; offsets handled, so monitor 2 clicks land right |
| `--max-edge` | 1024 local, 1366 Claude | screenshot long edge |
| `--confirm` | `act` | `act` / `all` / `none` |
| `--max-steps` | `40` | actions per task |
| `--grid` | off | coordinate grid overlay; measured worse, see above |
| `--no-cloak` | off | stop hiding the window during screenshots |

## Cost

Local model: free, and nothing leaves the machine. It occupies ~6GB of your 16GB
of VRAM while loaded.

Claude: your 2560×1440 downscales to 1366×768 = 1372 image tokens, about $0.007 per
screenshot. A ten-step task lands in the low tens of cents. Only the newest 3
screenshots stay in the transcript, and the tool definition carries a cache
breakpoint.

## How it fits together

| File | Job |
|---|---|
| `screenpilot/capture.py` | DPI awareness, monitor grab, downscale, coordinate mapping, grid overlay |
| `screenpilot/actions.py` | Executes actions via pyautogui; X11→pyautogui key names |
| `screenpilot/safety.py` | Approval gate, kill switch, plain-language action descriptions |
| `screenpilot/agent.py` | Provider-agnostic loop, screenshot pruning, error containment |
| `screenpilot/providers/` | `ollama.py` (JSON protocol), `anthropic_api.py` (computer toolset) |
| `screenpilot/ui.py` | The chat window |
| `main.py` | Wiring and CLI |
| `selftest.py` | Offline check — real capture and dispatch, no network, no clicking |

### Two protocols, one loop

Claude gets the native `computer_toolset_20260801` tool family, where the action
vocabulary is defined server-side. Everything else gets a JSON action protocol —
one JSON object per turn, decoded under Ollama's `format: json` — because most open
vision models either lack tool calling or are unreliable at it. Both funnel into the
same `Desktop.run()` dispatcher, so an action behaves identically whichever model
asked for it.

### The DPI thing

`enable_dpi_awareness()` runs before anything touches the display. Without it Windows
reports a virtualised resolution for screenshots while the mouse moves in physical
pixels, so on a scaled display every click lands somewhere near, but wrong. If clicks
start missing by a consistent proportion, look there first.

### Coordinates

The model works in the pixel space of the screenshot it was sent, never your real
resolution. `Grab.to_screen()` is the single place that converts back. Zoom results
deliberately do not redefine that space. The Ollama provider additionally rescales
0–1000 normalised coordinates (a Qwen2-era habit) and clamps anything out of bounds,
so a bad number never becomes an off-screen click.

## Known limits

- The Anthropic path's live API round-trip is still untested — no key on this machine.
  Everything up to the network call is covered by `selftest.py`, and the request shapes
  were checked against the installed SDK's own type definitions.
- Small local models are unreliable at precise clicking. See the table above.
- Admin-elevated windows ignore synthetic input unless you run this elevated too.
- Your taskbar is set to auto-hide, so the model cannot see it in a screenshot unless
  it is showing at capture time.
