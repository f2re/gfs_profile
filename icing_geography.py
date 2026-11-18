"""Offline water polygons and legible city labels for regional icing maps.

Natural Earth water rings keep their winding, so islands and continental holes
stay unfilled. City population is only a source snapshot used to prioritise
labels. This module never downloads a dataset while rendering a map.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import numpy as np

WATER_COLOR = "#d8edf5"
CITY_COLOR = "#193b4d"
SELECTED_COLOR = "#bc4315"


def _resolution(layer: str) -> str | None:
    from basemap_cache import basemap_data_dir, basemap_resolution, LAYER_SPECS
    for resolution in dict.fromkeys(("50m", basemap_resolution(), "110m", "10m")):
        base = basemap_data_dir() / "natural_earth" / resolution / f"ne_{resolution}_{LAYER_SPECS[layer]['name']}"
        if all(base.with_suffix(ext).is_file() for ext in (".shp", ".shx", ".dbf")):
            return resolution
    return None


def _clip_ring(points, bbox):
    """Clip a polygon ring before projecting; retain its exterior/hole winding."""
    west, east, south, north = bbox
    output = [tuple(map(float, p)) for p in points]
    if len(output) > 1 and output[-1] == output[0]:
        output.pop()
    for dimension, limit, sign in ((0, west, 1), (0, east, -1), (1, south, 1), (1, north, -1)):
        ring, output = output, []
        if not ring:
            break
        previous = ring[-1]
        previous_inside = sign * (previous[dimension] - limit) >= 0
        for current in ring:
            current_inside = sign * (current[dimension] - limit) >= 0
            if current_inside != previous_inside:
                fraction = (limit - previous[dimension]) / (current[dimension] - previous[dimension])
                point = tuple(previous[k] + fraction * (current[k] - previous[k]) for k in (0, 1))
                output.append(point)
            if current_inside:
                output.append(current)
            previous, previous_inside = current, current_inside
    return np.asarray(output, dtype=float)


def _water_patch(ax, rings, bbox, project, clip):
    from matplotlib.path import Path as MplPath
    from matplotlib.patches import PathPatch
    paths = []
    for ring in rings:
        points = _clip_ring(ring, bbox)
        if len(points) < 3:
            continue
        xy = np.column_stack(project(points[:, 0], points[:, 1]))
        xy = np.vstack((xy, xy[0]))
        codes = np.full(len(xy), MplPath.LINETO, dtype=np.uint8)
        codes[0], codes[-1] = MplPath.MOVETO, MplPath.CLOSEPOLY
        paths.append(MplPath(xy, codes))
    if not paths:
        return None
    patch = PathPatch(MplPath.make_compound_path(*paths), facecolor=WATER_COLOR,
                      edgecolor="none", linewidth=0, zorder=0.7)
    ax.add_patch(patch)
    patch.set_clip_path(clip)
    return patch


def draw_basemap(ax, bbox, project, clip):
    """Return installed layers; an incomplete cache remains visible and offline."""
    from basemap_cache import iter_layer_features
    from matplotlib.collections import LineCollection
    west, east, south, north = bbox
    query = (south, north, west, east)
    found = set()
    for layer, color, width in (("ocean", None, 0), ("lakes", "#80b3c8", 0.4),
                                ("rivers", "#83b6ce", 0.35), ("coastline", "#7296a7", 0.55),
                                ("admin0", "#a3adb0", 0.35)):
        resolution = _resolution(layer)
        if resolution is None:
            continue
        try:
            features = list(iter_layer_features(layer, query, resolution))
        except (OSError, ValueError):
            continue
        # A valid ocean layer need not contain water inside a small inland map.
        found.add(layer)
        rings = [feature.points for feature in features if len(feature.points) >= 3]
        if layer in {"ocean", "lakes"}:
            _water_patch(ax, rings, bbox, project, clip)
        if color is None:
            continue
        lines = []
        for feature in features:
            points = np.asarray(feature.points, dtype=float)
            if len(points) >= 2:
                lines.append(np.column_stack(project(points[:, 0], points[:, 1])))
        if lines:
            collection = LineCollection(lines, colors=color, linewidths=width, zorder=5)
            ax.add_collection(collection)
            collection.set_clip_path(clip)
    return frozenset(found)


@lru_cache(maxsize=1)
def city_catalog():
    path = Path(__file__).resolve().parent / "data" / "icing_cities.json"
    with path.open(encoding="utf-8") as stream:
        return tuple(tuple(row) for row in json.load(stream)["cities"])


def city_candidates(bbox):
    west, east, south, north = bbox
    return [row for row in city_catalog() if west < row[1] < east and south < row[2] < north]


def _label_extent(label, renderer):
    """Measure the text box without a callout's connecting arrow."""
    # Annotation recomputes its offset transform here after set_position().
    # Updating only the bbox patch would otherwise measure the previous place.
    extent = label.get_window_extent(renderer)
    if label.get_bbox_patch() is not None:
        label.update_bbox_position_size(renderer)
        return label.get_bbox_patch().get_window_extent(renderer)
    return extent


def draw_selected_point(ax, point, bbox, project, clip=None):
    """Mark exact coordinates without putting a label over the weather field."""
    lat, lon = map(float, point)
    west, east, south, north = bbox
    if not (np.isfinite([lat, lon]).all() and west <= lon <= east and south <= lat <= north):
        raise ValueError("Выбранный пункт находится вне района карты")
    x, y = project(lon, lat)
    markers = []
    for size, face, edge, width in ((10.5, "white", "#6c2810", 0.8),
                                    (6.8, SELECTED_COLOR, "white", 1.1)):
        # Keep the symbol complete at a boundary point; its centre still marks
        # the exact coordinates and the small halo can cross the map frame.
        # It also stays above the empty-field message (zorder 20).
        marker, = ax.plot(x, y, "o", ms=size, markerfacecolor=face, markeredgecolor=edge,
                          markeredgewidth=width, zorder=25, clip_on=False)
        marker.set_gid("icing_selected_point_marker")
        markers.append(marker)
    ax.figure.canvas.draw()
    renderer = ax.figure.canvas.get_renderer()
    selected_boxes = [marker.get_window_extent(renderer).expanded(1.12, 1.12) for marker in markers]
    for contour in ax.texts:
        if contour.get_gid() == "icing_contour" and any(contour.get_window_extent(renderer).overlaps(box) for box in selected_boxes):
            contour.set_visible(False)
    return tuple(markers)


def draw_cities(ax, bbox, project, clip=None):
    """Place important labels first; use their actual rendered bounding boxes.

    Each candidate tries several positions. A label is dropped only if none of
    them fits without covering another city, the grid labels or the map legend.
    Contour labels yield to city labels; the underlying contour is retained.
    """
    from matplotlib import patheffects
    from matplotlib.transforms import Bbox
    fig = ax.figure
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    contour_labels = [text for text in ax.texts if text.get_gid() == "icing_contour"]
    occupied = [_label_extent(text, renderer).expanded(1.06, 1.18) for text in ax.texts
                if text.get_visible() and text.get_gid() != "icing_contour"]
    occupied.extend(line.get_window_extent(renderer).expanded(1.2, 1.2) for line in ax.lines
                    if line.get_gid() == "icing_selected_point_marker")
    if ax.get_legend() is not None:
        occupied.append(ax.get_legend().get_window_extent(renderer).expanded(1.03, 1.06))
    map_box = ax.get_window_extent(renderer)
    inset = Bbox.from_extents(map_box.x0 + 4, map_box.y0 + 4, map_box.x1 - 4, map_box.y1 - 4)
    outline = clip.get_path().transformed(clip.get_transform()) if clip is not None else None
    positions = ((4, 4, "left", "bottom"), (4, -4, "left", "top"),
                 (-4, 4, "right", "bottom"), (-4, -4, "right", "top"),
                 (0, 7, "center", "bottom"), (0, -7, "center", "top"),
                 (6, 13, "left", "bottom"), (-6, -13, "right", "top"))
    labels = []
    # A wide regional map has room for major cities and Arctic reference points,
    # while the collision test naturally generalises a dense urban region.
    for name, lon, lat, population, priority, _rank in city_candidates(bbox):
        if len(labels) >= 52:
            break
        x, y = project(lon, lat)
        display_name = name.replace("Нижний Новгород", "Нижний\nНовгород")
        label = ax.annotate(display_name, (x, y), xytext=(4, 4), textcoords="offset points",
                            fontsize=9.1 if priority == 0 else 8.5, color=CITY_COLOR,
                            zorder=12, annotation_clip=True)
        label.set_path_effects([patheffects.withStroke(linewidth=2.7, foreground="white", alpha=0.97)])
        placed = False
        for dx, dy, ha, va in positions:
            label.set_position((dx, dy)); label.set_ha(ha); label.set_va(va)
            extent = label.get_window_extent(renderer).expanded(1.04, 1.18)
            corners = np.asarray([[extent.x0, extent.y0], [extent.x0, extent.y1],
                                  [extent.x1, extent.y0], [extent.x1, extent.y1]])
            if not np.all([inset.contains(*corner) for corner in corners]):
                continue
            if outline is not None and not outline.contains_points(corners).all():
                continue
            if any(extent.overlaps(other) for other in occupied):
                continue
            occupied.append(extent)
            placed = True
            break
        if not placed:
            label.remove()
            continue
        ax.plot(x, y, "o", color=CITY_COLOR, ms=2.7 if priority == 0 else 2.2,
                markeredgecolor="white", markeredgewidth=0.35, zorder=13)
        labels.append(label)
    city_boxes = [label.get_window_extent(renderer).expanded(1.04, 1.18) for label in labels]
    for label in contour_labels:
        if any(label.get_window_extent(renderer).overlaps(box) for box in city_boxes):
            label.set_visible(False)
    return labels
