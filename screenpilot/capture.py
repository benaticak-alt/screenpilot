"""Screen capture, DPI handling and screenshot downscaling."""
from __future__ import annotations

import base64
import ctypes
import io
import math
from dataclasses import dataclass

import mss
from PIL import Image

# Image limits for the models that support computer_toolset_20260801.
MAX_LONG_EDGE = 2576
MAX_VISUAL_TOKENS = 4784


def enable_dpi_awareness() -> None:
    """Make this process per-monitor DPI aware.

    Without this, Windows lies to us about screen size on scaled displays:
    screenshots come back at the virtualised resolution while the mouse moves
    in physical pixels, so every click lands in the wrong place. Must run
    before mss or pyautogui touch the display.
    """
    try:
        # PROCESS_PER_MONITOR_DPI_AWARE = 2
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def scale_factor(width: int, height: int, max_long_edge: int = MAX_LONG_EDGE) -> float:
    """Shrink factor that keeps an image inside both the pixel and token caps."""
    long_edge = max(width, height)
    visual_tokens = math.ceil(width / 28) * math.ceil(height / 28)

    edge_scale = max_long_edge / long_edge if long_edge > max_long_edge else 1.0
    token_scale = (
        math.sqrt(MAX_VISUAL_TOKENS / visual_tokens) if visual_tokens > MAX_VISUAL_TOKENS else 1.0
    )
    return min(1.0, edge_scale, token_scale)


@dataclass
class Grab:
    """One screenshot, plus what we need to map coordinates back to the screen."""

    png_b64: str
    scale: float           # sent_pixels / physical_pixels
    offset: tuple[int, int]  # physical screen position of the capture's top-left
    sent_size: tuple[int, int]

    def to_screen(self, x: float, y: float) -> tuple[int, int]:
        """Model coordinate (screenshot pixel space) -> physical screen pixel."""
        return (
            int(round(self.offset[0] + x / self.scale)),
            int(round(self.offset[1] + y / self.scale)),
        )


class ScreenGrabber:
    """Captures one monitor and downscales it for the API.

    `max_edge` trades accuracy for cost: the docs suggest 1280-1366 for most
    desktop and web work, and warn against going above 1920x1080.
    """

    def __init__(self, monitor: int = 1, max_edge: int = 1366, grid: bool = False):
        self.monitor = monitor
        self.max_edge = max_edge
        # Off by default: measured *worse* than plain screenshots on qwen2.5vl.
        # Kept because it is model-dependent and cheap to re-test.
        self.grid = grid
        self._sct = mss.mss()

    @property
    def geometry(self) -> dict:
        mons = self._sct.monitors
        idx = self.monitor if self.monitor < len(mons) else 1
        return mons[idx]

    def grab(self, region: tuple[int, int, int, int] | None = None) -> Grab:
        """Capture the monitor, or `region` (x0, y0, x1, y1) in screen pixels."""
        geo = self.geometry
        if region is None:
            box = geo
            offset = (geo["left"], geo["top"])
        else:
            x0, y0, x1, y1 = region
            box = {"left": x0, "top": y0, "width": max(1, x1 - x0), "height": max(1, y1 - y0)}
            offset = (x0, y0)

        raw = self._sct.grab(box)
        img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")

        scale = scale_factor(img.width, img.height, self.max_edge)
        if scale < 1.0:
            img = img.resize(
                (max(1, int(img.width * scale)), max(1, int(img.height * scale))),
                Image.LANCZOS,
            )

        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        png_b64 = base64.b64encode(buf.getvalue()).decode()
        if self.grid:
            png_b64 = draw_grid(png_b64)
        return Grab(
            png_b64=png_b64,
            scale=scale,
            offset=offset,
            sent_size=(img.width, img.height),
        )

    def grab_zoom(self, region_in_model_space, last: Grab) -> Grab:
        """Zoom: re-capture a sub-region at native resolution.

        The model gives the region in the coordinate space of the *full*
        screenshot, so translate it back to screen pixels first.
        """
        x0, y0, x1, y1 = region_in_model_space
        sx0, sy0 = last.to_screen(x0, y0)
        sx1, sy1 = last.to_screen(x1, y1)
        return self.grab(region=(sx0, sy0, sx1, sy1))


def draw_grid(png_b64: str, step: int = 128) -> str:
    """Overlay a labelled coordinate grid on a screenshot.

    Small vision models describe position ("bottom-right") far better than they
    regress raw pixel coordinates. Giving them visible reference lines to
    interpolate between turns a guess into a much shorter guess. Claude does not
    need this and reads better without the clutter.
    """
    from PIL import ImageDraw, ImageFont

    img = Image.open(io.BytesIO(base64.b64decode(png_b64))).convert("RGB")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    try:
        font = ImageFont.load_default(size=13)
    except TypeError:  # older Pillow: no size argument
        font = ImageFont.load_default()

    for x in range(step, img.width, step):
        draw.line([(x, 0), (x, img.height)], fill=(255, 0, 255, 70), width=1)
    for y in range(step, img.height, step):
        draw.line([(0, y), (img.width, y)], fill=(255, 0, 255, 70), width=1)

    # Label each intersection, outlined so it survives any background.
    for x in range(step, img.width, step):
        for y in range(step, img.height, step):
            draw.text((x + 3, y + 2), f"{x},{y}", font=font,
                      fill=(255, 255, 0, 255), stroke_width=2, stroke_fill=(0, 0, 0, 255))

    out = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    buf = io.BytesIO()
    out.save(buf, format="PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode()
