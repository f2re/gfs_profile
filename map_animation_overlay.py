from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

PANEL_RGBA = (10, 25, 40, 218)
PANEL_BORDER_RGBA = (217, 229, 242, 95)
TEXT_RGBA = (246, 250, 255, 255)
MUTED_TEXT_RGBA = (177, 196, 216, 255)
CYAN_RGBA = (37, 208, 255, 255)
TRACK_RGBA = (92, 115, 139, 210)
TICK_RGBA = (232, 239, 247, 135)
AMBER_RGBA = (255, 176, 32, 255)
SHADOW_RGBA = (0, 0, 0, 140)

FONT_REGULAR_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf",
)
FONT_BOLD_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf",
)


def _font(size: int, *, bold: bool = False):
    candidates = FONT_BOLD_CANDIDATES if bold else FONT_REGULAR_CANDIDATES
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _text_size(draw: ImageDraw.ImageDraw, text: str, font) -> tuple[int, int]:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _text_with_shadow(draw: ImageDraw.ImageDraw, xy: tuple[float, float], text: str, font, fill=TEXT_RGBA, anchor: str | None = None) -> None:
    x, y = xy
    draw.text((x + 2, y + 2), text, font=font, fill=SHADOW_RGBA, anchor=anchor)
    draw.text((x, y), text, font=font, fill=fill, anchor=anchor)


def _valid_time(frame: dict[str, Any]) -> datetime | None:
    value = frame.get("valid_time")
    return value if isinstance(value, datetime) else None


def _run_label(frame: dict[str, Any]) -> str:
    run = frame.get("run")
    date = getattr(run, "date", "")
    cycle = getattr(run, "cycle", "")
    if date and cycle:
        return f"GFS {date} {cycle}Z"
    return "GFS"


def _date_labels(frame: dict[str, Any]) -> tuple[str, str]:
    valid = _valid_time(frame)
    if valid is None:
        return "UTC", "срок прогноза"
    return valid.strftime("%d.%m"), valid.strftime("%H:%M UTC")


def _lead_label(frame: dict[str, Any]) -> str:
    try:
        return f"+{int(frame.get('lead_hour', 0)):03d} ч"
    except Exception:
        return "+??? ч"


def _draw_progress_bar(draw: ImageDraw.ImageDraw, *, x0: int, x1: int, y: int, height: int, index: int, total: int, radius: int) -> None:
    total = max(1, int(total))
    index = max(1, min(int(index), total))
    bar_w = max(1, x1 - x0)
    draw.rounded_rectangle((x0, y, x1, y + height), radius=radius, fill=TRACK_RGBA)
    filled_x = x0 + int(round(bar_w * index / total))
    draw.rounded_rectangle((x0, y, filled_x, y + height), radius=radius, fill=CYAN_RGBA)
    if total <= 24:
        for step in range(1, total):
            tick_x = x0 + int(round(bar_w * step / total))
            draw.line((tick_x, y - 1, tick_x, y + height + 1), fill=TICK_RGBA, width=max(1, height // 5))
    marker_x = x0 + int(round(bar_w * (index - 0.5) / total))
    draw.ellipse((marker_x - height, y - height // 2, marker_x + height, y + height + height // 2), fill=AMBER_RGBA)


def decorate_map_animation_frame(path: Path, frame: dict[str, Any], index: int, total: int) -> Path:
    """Add a readable animation HUD to a map frame.

    The overlay occupies the existing top title zone: it adds a dark translucent
    panel, a segmented progress bar, a large UTC day and a large forecast lead.
    This keeps the animation state visible in Telegram without hiding the map
    body, contours, cities or hazards.
    """

    image = Image.open(path).convert("RGBA")
    width, height = image.size
    scale = max(0.55, min(width / 1280.0, 1.4))
    margin = max(10, int(22 * scale))
    panel_h = max(78, int(112 * scale))
    top = max(8, int(14 * scale))
    radius = max(14, int(20 * scale))
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    panel_box = (margin, top, width - margin, top + panel_h)
    draw.rounded_rectangle(panel_box, radius=radius, fill=PANEL_RGBA, outline=PANEL_BORDER_RGBA, width=max(1, int(2 * scale)))

    day_label, time_label = _date_labels(frame)
    lead_label = _lead_label(frame)
    frame_label = f"кадр {max(1, min(index, total))}/{max(1, total)}"
    run_label = _run_label(frame)

    day_font = _font(max(28, int(46 * scale)), bold=True)
    lead_font = _font(max(30, int(50 * scale)), bold=True)
    small_font = _font(max(14, int(18 * scale)), bold=True)
    tiny_font = _font(max(12, int(15 * scale)), bold=False)

    text_y = top + int(18 * scale)
    left_x = margin + int(28 * scale)
    _text_with_shadow(draw, (left_x, text_y), day_label, day_font, fill=TEXT_RGBA)
    _text_with_shadow(draw, (left_x, text_y + int(52 * scale)), time_label, small_font, fill=MUTED_TEXT_RGBA)

    center_x = width // 2
    _text_with_shadow(draw, (center_x, text_y - int(2 * scale)), lead_label, lead_font, fill=CYAN_RGBA, anchor="ma")
    _text_with_shadow(draw, (center_x, text_y + int(53 * scale)), frame_label, small_font, fill=MUTED_TEXT_RGBA, anchor="ma")

    right_x = width - margin - int(28 * scale)
    run_w, _ = _text_size(draw, run_label, small_font)
    _text_with_shadow(draw, (right_x - run_w, text_y + int(2 * scale)), run_label, small_font, fill=TEXT_RGBA)
    valid = _valid_time(frame)
    valid_label = valid.strftime("valid %Y-%m-%d %H:%M UTC") if valid else "valid UTC"
    valid_w, _ = _text_size(draw, valid_label, tiny_font)
    _text_with_shadow(draw, (right_x - valid_w, text_y + int(33 * scale)), valid_label, tiny_font, fill=MUTED_TEXT_RGBA)

    bar_y = top + panel_h - max(18, int(21 * scale))
    bar_h = max(7, int(9 * scale))
    _draw_progress_bar(draw, x0=margin + int(26 * scale), x1=width - margin - int(26 * scale), y=bar_y, height=bar_h, index=index, total=total, radius=bar_h // 2)

    Image.alpha_composite(image, overlay).convert("RGB").save(path, optimize=True)
    return path
