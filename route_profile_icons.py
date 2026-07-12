from __future__ import annotations

"""Deterministic Matplotlib vector pictograms for route-profile PNGs."""

import math

from matplotlib.lines import Line2D
from matplotlib.offsetbox import AnnotationBbox, DrawingArea
from matplotlib.patches import Circle, Ellipse, FancyBboxPatch, PathPatch, Polygon
from matplotlib.path import Path


def _place(ax, drawing: DrawingArea, x: float, y: float, *, transform=None, zorder: int = 20) -> AnnotationBbox:
    box = AnnotationBbox(
        drawing,
        (x, y),
        xycoords=transform or "data",
        frameon=False,
        box_alignment=(0.5, 0.5),
        pad=0.0,
        zorder=zorder,
    )
    ax.add_artist(box)
    return box


def add_cloud_icon(ax, x: float, y: float, *, transform=None, size: float = 18.0, color: str = "#71879A", alpha: float = 0.75, zorder: int = 20):
    width = size * 1.55
    height = size * 0.88
    drawing = DrawingArea(width, height, clip=False)
    base_y = height * 0.24
    drawing.add_artist(FancyBboxPatch((width * 0.13, base_y), width * 0.72, height * 0.34, boxstyle="round,pad=0.0,rounding_size=3", facecolor=color, edgecolor="none", alpha=alpha))
    drawing.add_artist(Circle((width * 0.33, height * 0.57), height * 0.24, facecolor=color, edgecolor="none", alpha=alpha))
    drawing.add_artist(Circle((width * 0.52, height * 0.68), height * 0.30, facecolor=color, edgecolor="none", alpha=alpha))
    drawing.add_artist(Circle((width * 0.70, height * 0.55), height * 0.22, facecolor=color, edgecolor="none", alpha=alpha))
    return _place(ax, drawing, x, y, transform=transform, zorder=zorder)


def add_snowflake_icon(ax, x: float, y: float, *, transform=None, size: float = 18.0, color: str = "#1976D2", alpha: float = 0.95, zorder: int = 22):
    drawing = DrawingArea(size, size, clip=False)
    cx = cy = size / 2.0
    radius = size * 0.40
    for angle_deg in (0, 60, 120):
        angle = math.radians(angle_deg)
        dx = math.cos(angle) * radius
        dy = math.sin(angle) * radius
        drawing.add_artist(Line2D([cx - dx, cx + dx], [cy - dy, cy + dy], color=color, linewidth=1.55, alpha=alpha))
        for sign in (-1, 1):
            px = cx + sign * dx * 0.63
            py = cy + sign * dy * 0.63
            for branch_sign in (-1, 1):
                branch_angle = angle + branch_sign * math.radians(35)
                bx = math.cos(branch_angle) * radius * 0.22 * sign
                by = math.sin(branch_angle) * radius * 0.22 * sign
                drawing.add_artist(Line2D([px, px - bx], [py, py - by], color=color, linewidth=1.05, alpha=alpha))
    return _place(ax, drawing, x, y, transform=transform, zorder=zorder)


def add_turbulence_icon(ax, x: float, y: float, *, transform=None, size: float = 19.0, color: str = "#D97706", alpha: float = 0.95, zorder: int = 22):
    width = size * 1.35
    height = size
    drawing = DrawingArea(width, height, clip=False)
    for offset, scale in ((0.00, 1.0), (0.23, 0.78), (0.45, 0.58)):
        y0 = height * (0.24 + offset)
        vertices = [
            (width * 0.08, y0),
            (width * 0.31, y0 + height * 0.28 * scale),
            (width * 0.60, y0 - height * 0.22 * scale),
            (width * 0.92, y0),
        ]
        drawing.add_artist(PathPatch(Path(vertices, [Path.MOVETO, Path.CURVE4, Path.CURVE4, Path.CURVE4]), facecolor="none", edgecolor=color, linewidth=1.45, alpha=alpha, capstyle="round"))
    return _place(ax, drawing, x, y, transform=transform, zorder=zorder)


def add_wind_icon(ax, x: float, y: float, *, transform=None, size: float = 19.0, color: str = "#173B67", alpha: float = 0.95, direction: int = 1, zorder: int = 22):
    width = size * 1.55
    height = size
    drawing = DrawingArea(width, height, clip=False)
    for row, fraction in ((0.72, 0.92), (0.50, 0.74), (0.30, 0.56)):
        x0 = width * (0.05 if direction >= 0 else 0.95)
        x1 = width * (fraction if direction >= 0 else 1.0 - fraction)
        mid = (x0 + x1) / 2.0
        drawing.add_artist(PathPatch(Path([(x0, height * row), (mid, height * (row + 0.08)), (x1, height * row)], [Path.MOVETO, Path.CURVE3, Path.CURVE3]), facecolor="none", edgecolor=color, linewidth=1.4, alpha=alpha, capstyle="round"))
    tip_x = width * (0.95 if direction >= 0 else 0.05)
    base_x = tip_x - direction * width * 0.18
    drawing.add_artist(Polygon([(tip_x, height * 0.50), (base_x, height * 0.62), (base_x, height * 0.38)], closed=True, facecolor=color, edgecolor="none", alpha=alpha))
    return _place(ax, drawing, x, y, transform=transform, zorder=zorder)


def add_storm_icon(ax, x: float, y: float, *, transform=None, size: float = 20.0, cloud_color: str = "#66517E", lightning_color: str = "#F2C94C", alpha: float = 0.96, zorder: int = 24):
    width = size * 1.35
    height = size * 1.25
    drawing = DrawingArea(width, height, clip=False)
    drawing.add_artist(Ellipse((width * 0.50, height * 0.70), width * 0.68, height * 0.36, facecolor=cloud_color, edgecolor="none", alpha=alpha))
    drawing.add_artist(Circle((width * 0.36, height * 0.78), height * 0.20, facecolor=cloud_color, edgecolor="none", alpha=alpha))
    drawing.add_artist(Circle((width * 0.56, height * 0.84), height * 0.25, facecolor=cloud_color, edgecolor="none", alpha=alpha))
    bolt = [(width * 0.55, height * 0.60), (width * 0.42, height * 0.34), (width * 0.54, height * 0.34), (width * 0.45, height * 0.06), (width * 0.70, height * 0.42), (width * 0.57, height * 0.42)]
    drawing.add_artist(Polygon(bolt, closed=True, facecolor=lightning_color, edgecolor="#A56B00", linewidth=0.4, alpha=alpha))
    return _place(ax, drawing, x, y, transform=transform, zorder=zorder)


def add_precip_icon(ax, x: float, y: float, *, transform=None, size: float = 18.0, cloud_color: str = "#71879A", rain_color: str = "#2F80ED", alpha: float = 0.9, zorder: int = 22):
    width = size * 1.4
    height = size * 1.15
    drawing = DrawingArea(width, height, clip=False)
    drawing.add_artist(Ellipse((width * 0.50, height * 0.72), width * 0.68, height * 0.34, facecolor=cloud_color, edgecolor="none", alpha=alpha))
    drawing.add_artist(Circle((width * 0.37, height * 0.79), height * 0.18, facecolor=cloud_color, edgecolor="none", alpha=alpha))
    drawing.add_artist(Circle((width * 0.58, height * 0.84), height * 0.22, facecolor=cloud_color, edgecolor="none", alpha=alpha))
    for dx in (0.35, 0.52, 0.69):
        drawing.add_artist(Line2D([width * dx, width * (dx - 0.05)], [height * 0.46, height * 0.18], color=rain_color, linewidth=1.3, alpha=alpha))
    return _place(ax, drawing, x, y, transform=transform, zorder=zorder)


def _point_size(size: float) -> float:
    return float(size * 600.0 if size < 1.0 else size)


def draw_cloud_icon(ax, x: float, y: float, *, size: float = 0.03, transform=None, color: str = "#71879A", alpha: float = 0.75, zorder: int = 20):
    return add_cloud_icon(ax, x, y, size=_point_size(size), transform=transform, color=color, alpha=alpha, zorder=zorder)


def draw_hazard_icon(ax, key: str, x: float, y: float, *, size: float = 0.03, transform=None, color: str | None = None, alpha: float = 0.95, zorder: int = 22):
    points = _point_size(size)
    name = str(key).strip().lower()
    if name == "cloud":
        return add_cloud_icon(ax, x, y, size=points, transform=transform, color=color or "#71879A", alpha=alpha, zorder=zorder)
    if name == "icing":
        return add_snowflake_icon(ax, x, y, size=points, transform=transform, color=color or "#1976D2", alpha=alpha, zorder=zorder)
    if name == "turbulence":
        return add_turbulence_icon(ax, x, y, size=points, transform=transform, color=color or "#D97706", alpha=alpha, zorder=zorder)
    if name in {"wind", "wind_reverse"}:
        return add_wind_icon(ax, x, y, size=points, transform=transform, color=color or "#173B67", alpha=alpha, direction=-1 if name == "wind_reverse" else 1, zorder=zorder)
    if name == "thunder":
        return add_storm_icon(ax, x, y, size=points, transform=transform, cloud_color=color or "#66517E", alpha=alpha, zorder=zorder)
    if name == "precip":
        return add_precip_icon(ax, x, y, size=points, transform=transform, rain_color=color or "#2F80ED", alpha=alpha, zorder=zorder)
    return add_cloud_icon(ax, x, y, size=points * 0.8, transform=transform, color=color or "#71879A", alpha=alpha, zorder=zorder)
