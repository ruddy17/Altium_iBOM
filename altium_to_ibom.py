from __future__ import annotations

import argparse
import json
import math
import os
import re
import runpy
import sys
import types
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent


def add_dependency_paths() -> None:
    candidates = [
        Path(path)
        for path in (
            str(Path(__file__).resolve().parent / "altium_monkey" / "src" / "py"),
            str(Path(__file__).resolve().parent.parent / "altium_monkey" / "src" / "py"),
            str(Path(__file__).resolve().parent / "interactivehtmlbom"),
            str(Path(__file__).resolve().parent.parent / "interactivehtmlbom"),
        )
    ]
    for env_name in ("ALTIUM_MONKEY_PATH", "INTERACTIVEHTMLBOM_PATH"):
        env_value = __import__("os").environ.get(env_name)
        if env_value:
            candidates.insert(0, Path(env_value))
    for candidate in reversed(candidates):
        if candidate.exists():
            sys.path.insert(0, str(candidate))


add_dependency_paths()

from altium_monkey import AltiumDesign, PcbLayer  # noqa: E402
from altium_monkey.altium_resolved_layer_stack import resolved_layer_stack_from_pcbdoc  # noqa: E402
from altium_monkey.altium_text_to_polygon import (  # noqa: E402
    StrokeTextRenderer,
    StrokeTextResult,
    TextPolygonResult,
    render_pcb_text,
)


MIL_TO_MM = 0.0254


def bbox_from_extents(extents: tuple[float, float, float, float] | None) -> dict[str, object]:
    if extents is None:
        return {"pos": [0, 0], "relpos": [0, 0], "size": [1, 1], "angle": 0}
    minx, miny, maxx, maxy = extents
    return {
        "pos": [round(minx, 6), round(miny, 6)],
        "relpos": [0, 0],
        "size": [round(max(maxx - minx, 1), 6), round(max(maxy - miny, 1), 6)],
        "angle": 0,
    }


def extend_extents(
    extents: tuple[float, float, float, float] | None,
    minx: float,
    miny: float,
    maxx: float,
    maxy: float,
) -> tuple[float, float, float, float]:
    if extents is None:
        return (minx, miny, maxx, maxy)
    return (min(extents[0], minx), min(extents[1], miny), max(extents[2], maxx), max(extents[3], maxy))


def layer_enum(layer: object) -> PcbLayer | None:
    try:
        return PcbLayer(int(layer))
    except Exception:
        return None


def layer_side(layer: object) -> str | None:
    enum = layer_enum(layer)
    if enum is None:
        return None
    if enum in (PcbLayer.TOP, PcbLayer.TOP_OVERLAY, PcbLayer.TOP_PASTE, PcbLayer.TOP_SOLDER):
        return "F"
    if enum in (PcbLayer.BOTTOM, PcbLayer.BOTTOM_OVERLAY, PcbLayer.BOTTOM_PASTE, PcbLayer.BOTTOM_SOLDER):
        return "B"
    if enum == PcbLayer.MULTI_LAYER:
        return "FB"
    return None


def point_iu(x_iu: int | float, y_iu: int | float, ox: float, oy: float) -> list[float]:
    return point_mils(float(x_iu) / 10000.0, float(y_iu) / 10000.0, ox, oy)


def point_mils(x_mils: int | float, y_mils: int | float, ox: float, oy: float) -> list[float]:
    # InteractiveHtmlBom uses screen-like coordinates with Y increasing downward.
    return [round(float(x_mils) - ox, 6), round(oy - float(y_mils), 6)]


def angle_for_ibom(angle_deg: float) -> float:
    return round((-float(angle_deg or 0.0)) % 360.0, 6)


def arc_angles_for_ibom(start_deg: float, end_deg: float) -> tuple[float, float]:
    # Altium arc angles are CCW in a Y-up plane. After mirroring into iBOM's
    # Y-down plane, the endpoints must be swapped because iBOM draws clockwise.
    return angle_for_ibom(end_deg), angle_for_ibom(start_deg)


def is_full_circle_arc(arc: object) -> bool:
    start = float(getattr(arc, "start_angle", 0.0) or 0.0)
    end = float(getattr(arc, "end_angle", 0.0) or 0.0)
    return abs(end - start) >= 359.999


def layer_name(stack: object, layer: object) -> str:
    enum = layer_enum(layer)
    if enum is None:
        return ""
    resolved = stack.layer_by_legacy_id(enum.value) if stack else None
    return (getattr(resolved, "display_name", "") or enum.name).lower()


def drawing_bucket(stack: object, layer: object) -> tuple[str, str] | None:
    enum = layer_enum(layer)
    if enum is None:
        return None
    side = layer_side(enum)
    name = layer_name(stack, enum)
    if "top designator" in name:
        return ("silkscreen", "F")
    if "bottom designator" in name:
        return ("silkscreen", "B")
    if enum == PcbLayer.TOP_OVERLAY:
        return ("silkscreen", "F")
    if enum == PcbLayer.BOTTOM_OVERLAY:
        return ("silkscreen", "B")
    if "top componentscontours" in name or "top assembly" in name:
        return ("fabrication", "F")
    if "bottom componentscontours" in name or "bottom assembly" in name:
        return ("fabrication", "B")
    if side in ("F", "B") and enum.is_overlay():
        return ("silkscreen", side)
    return None


def is_component_outline_layer(stack: object, layer: object) -> bool:
    name = layer_name(stack, layer)
    return "componentscontours" in name or "assembly" in name


def is_designator_helper_layer(stack: object, layer: object) -> bool:
    name = layer_name(stack, layer)
    return "top designator" in name or "bottom designator" in name


def is_edge_layer(stack: object, layer: object) -> bool:
    enum = layer_enum(layer)
    if enum is None:
        return False
    name = layer_name(stack, enum)
    normalized = re.sub(r"[^a-z0-9]+", "", name)
    return enum == PcbLayer.KEEPOUT or normalized in {"pcbcontour", "boardoutline", "edgecuts"}


def net_name(pcbdoc: object, net_index: int | None) -> str | None:
    if net_index is None:
        return None
    if 0 <= int(net_index) < len(pcbdoc.nets):
        return pcbdoc.nets[int(net_index)].name
    return None


def polygon_path(polygons: list[list[list[float]]]) -> str:
    parts = []
    for polygon in polygons:
        if not polygon:
            continue
        first = polygon[0]
        parts.append(f"M {first[0]:.6f} {first[1]:.6f}")
        for x, y in polygon[1:]:
            parts.append(f"L {x:.6f} {y:.6f}")
        parts.append("Z")
    return " ".join(parts)


def vertices_to_polygon(vertices: list[object], ox: float, oy: float) -> list[list[float]]:
    points = []
    for vertex in vertices or []:
        if hasattr(vertex, "x_mils") and hasattr(vertex, "y_mils"):
            points.append(point_mils(vertex.x_mils, vertex.y_mils, ox, oy))
        elif hasattr(vertex, "x") and hasattr(vertex, "y"):
            points.append(point_iu(vertex.x, vertex.y, ox, oy))
    if len(points) >= 2 and points[0] == points[-1]:
        points.pop()
    return points


def region_polygons(region: object, ox: float, oy: float) -> list[list[list[float]]]:
    if hasattr(region, "outline"):
        polygons = [vertices_to_polygon(getattr(region, "outline", []), ox, oy)]
        for hole in getattr(region, "holes", []) or []:
            polygons.append(vertices_to_polygon(hole, ox, oy))
        return [poly for poly in polygons if len(poly) >= 3]
    polygons = [vertices_to_polygon(getattr(region, "outline_vertices", []), ox, oy)]
    for hole in getattr(region, "hole_vertices", []) or []:
        polygons.append(vertices_to_polygon(hole, ox, oy))
    return [poly for poly in polygons if len(poly) >= 3]


def make_region_zone(pcbdoc: object, region: object, ox: float, oy: float) -> dict[str, object] | None:
    polygons = region_polygons(region, ox, oy)
    if not polygons:
        return None
    zone: dict[str, object] = {"polygons": polygons, "fillrule": "evenodd"}
    name = net_name(pcbdoc, getattr(region, "net_index", None))
    if name:
        zone["net"] = name
    return zone


def make_region_drawing(region: object, ox: float, oy: float, filled: int = 1) -> dict[str, object] | None:
    polygons = region_polygons(region, ox, oy)
    if not polygons:
        return None
    return {"type": "polygon", "filled": filled, "pos": [0, 0], "angle": 0, "polygons": polygons}


def pad_shape(pad: object) -> str:
    shape = int(getattr(pad, "shape", 1) or 1)
    if shape == 10 or getattr(pad, "custom_shape", None) is not None:
        return "custom"
    effective_shape = effective_pad_shape_value(pad)
    if effective_shape == 2:
        return "rect"
    if effective_shape in (4, 9):
        return "roundrect"
    if effective_shape == 3:
        return "chamfrect"
    width = pad_width(pad)
    height = pad_height(pad)
    if abs(width - height) < 0.001:
        return "circle"
    return "oval"


def pad_layer_index(pad: object) -> int:
    try:
        layer = int(getattr(pad, "layer", 1) or 1)
    except Exception:
        layer = 1
    if layer == PcbLayer.BOTTOM.value:
        return 31
    if PcbLayer.TOP.value <= layer <= PcbLayer.MID30.value:
        return layer - 1
    return 0


def pad_list_value(pad: object, attr: str, default: int = 0) -> int:
    values = getattr(pad, attr, []) or []
    index = pad_layer_index(pad)
    if 0 <= index < len(values):
        return int(values[index] or default)
    return default


def effective_pad_shape_value(pad: object) -> int:
    alt_shape = pad_list_value(pad, "alt_shape", 0)
    if alt_shape:
        return alt_shape
    try:
        layer = int(getattr(pad, "layer", 1) or 1)
    except Exception:
        layer = 1
    if layer == PcbLayer.BOTTOM.value:
        return int(getattr(pad, "bot_shape", 0) or getattr(pad, "shape", 1) or 1)
    return int(getattr(pad, "top_shape", 0) or getattr(pad, "shape", 1) or 1)


def pad_corner_radius(pad: object) -> float:
    percent = pad_list_value(pad, "corner_radius", 0)
    if percent <= 0:
        return 0.0
    # Altium stores a percent-like corner value. iBOM wants an absolute radius;
    # using half the percent keeps 50% pads visibly rectangular instead of pill-shaped.
    return round(min(pad_width(pad), pad_height(pad)) * min(percent, 100) / 200.0, 6)


def pad_width(pad: object) -> float:
    return float(getattr(pad, "top_width", 0) or getattr(pad, "width", 0) or 0) / 10000.0


def pad_height(pad: object) -> float:
    return float(getattr(pad, "top_height", 0) or getattr(pad, "height", 0) or 0) / 10000.0


def is_pin1_pad(pad: object) -> bool:
    designator = str(getattr(pad, "designator", "") or getattr(pad, "name", "") or "").strip()
    return designator == "1"


def make_pad(pcbdoc: object, pad: object, ox: float, oy: float) -> dict[str, object]:
    side = layer_side(getattr(pad, "layer", None))
    layers = ["F", "B"] if side == "FB" else [side or "F"]
    result: dict[str, object] = {
        "layers": layers,
        "pos": point_iu(pad.x, pad.y, ox, oy),
        "size": [round(pad_width(pad), 6), round(pad_height(pad), 6)],
        "angle": angle_for_ibom(getattr(pad, "rotation", 0.0)),
        "shape": pad_shape(pad),
        "type": "th" if int(getattr(pad, "hole_size", 0) or 0) > 0 else "smd",
    }
    if result["shape"] == "roundrect":
        result["radius"] = pad_corner_radius(pad) or round(min(pad_width(pad), pad_height(pad)) * 0.15, 6)
    elif result["shape"] == "chamfrect":
        result["chamfpos"] = 15
        result["chamfratio"] = 0.2
        result["radius"] = pad_corner_radius(pad)
    elif result["shape"] == "custom":
        polygons = custom_pad_polygons(pad, ox, oy)
        if polygons:
            result["polygons"] = polygons
            result["pos"] = [0, 0]
            result["angle"] = 0
        else:
            result["shape"] = "rect"
    drill = float(getattr(pad, "hole_size", 0) or 0) / 10000.0
    if drill > 0:
        result["drillshape"] = "circle"
        result["drillsize"] = [round(drill, 6), 0]
    name = net_name(pcbdoc, getattr(pad, "net_index", None))
    if name:
        result["net"] = name
    if is_pin1_pad(pad):
        result["pin1"] = 1
    return result


def custom_pad_polygons(pad: object, ox: float, oy: float) -> list[list[list[float]]]:
    custom = getattr(pad, "custom_shape", None)
    if custom is None:
        return []
    item = getattr(custom, "primary_layer_shape", None)
    for candidate in (
        getattr(item, "shape_region", None),
        getattr(item, "region", None),
        getattr(custom, "shape_region", None),
        getattr(custom, "region", None),
    ):
        polygons = region_polygons(candidate, ox, oy) if candidate is not None else []
        if polygons:
            return polygons
    return []


def make_track(pcbdoc: object, track: object, ox: float, oy: float) -> dict[str, object]:
    result: dict[str, object] = {
        "start": point_iu(track.start_x, track.start_y, ox, oy),
        "end": point_iu(track.end_x, track.end_y, ox, oy),
        "width": round(float(track.width) / 10000.0, 6),
    }
    name = net_name(pcbdoc, getattr(track, "net_index", None))
    if name:
        result["net"] = name
    return result


def make_track_drawing(track: object, ox: float, oy: float) -> dict[str, object]:
    return {
        "type": "segment",
        "start": point_iu(track.start_x, track.start_y, ox, oy),
        "end": point_iu(track.end_x, track.end_y, ox, oy),
        "width": round(float(track.width) / 10000.0, 6),
    }


def make_arc(pcbdoc: object, arc: object, ox: float, oy: float) -> dict[str, object]:
    startangle, endangle = (0.0, 360.0) if is_full_circle_arc(arc) else arc_angles_for_ibom(arc.start_angle, arc.end_angle)
    result: dict[str, object] = {
        "center": point_iu(arc.center_x, arc.center_y, ox, oy),
        "radius": round(float(arc.radius) / 10000.0, 6),
        "startangle": startangle,
        "endangle": endangle,
        "width": round(float(arc.width) / 10000.0, 6),
    }
    name = net_name(pcbdoc, getattr(arc, "net_index", None))
    if name:
        result["net"] = name
    return result


def make_arc_drawing(arc: object, ox: float, oy: float) -> dict[str, object]:
    if is_full_circle_arc(arc):
        return {
            "type": "circle",
            "start": point_iu(arc.center_x, arc.center_y, ox, oy),
            "radius": round(float(arc.radius) / 10000.0, 6),
            "width": round(float(arc.width) / 10000.0, 6),
        }
    startangle, endangle = arc_angles_for_ibom(arc.start_angle, arc.end_angle)
    return {
        "type": "arc",
        "start": point_iu(arc.center_x, arc.center_y, ox, oy),
        "radius": round(float(arc.radius) / 10000.0, 6),
        "startangle": startangle,
        "endangle": endangle,
        "width": round(float(arc.width) / 10000.0, 6),
    }


def make_via_pad(pcbdoc: object, via: object, ox: float, oy: float) -> dict[str, object]:
    width = float(getattr(via, "diameter", 0) or getattr(via, "width", 0) or 0) / 10000.0
    drill = float(getattr(via, "hole_size", 0) or 0) / 10000.0
    result: dict[str, object] = {
        "layers": ["F", "B"],
        "pos": point_iu(via.x, via.y, ox, oy),
        "size": [round(width or drill, 6), round(width or drill, 6)],
        "angle": 0,
        "shape": "circle",
        "type": "th",
    }
    if drill:
        result["drillshape"] = "circle"
        result["drillsize"] = [round(drill, 6), 0]
    name = net_name(pcbdoc, getattr(via, "net_index", None))
    if name:
        result["net"] = name
    return result


def board_primitive_pads(pcbdoc: object, ox: float, oy: float) -> list[dict[str, object]]:
    pads = []
    for pad in pcbdoc.pads:
        if getattr(pad, "component_index", None) is not None:
            continue
        converted = make_pad(pcbdoc, pad, ox, oy)
        converted.pop("pin1", None)
        pads.append(converted)
    pads.extend(make_via_pad(pcbdoc, via, ox, oy) for via in pcbdoc.vias)
    return pads


def make_fill_drawing(fill: object, ox: float, oy: float) -> dict[str, object]:
    return {
        "type": "polygon",
        "filled": 1,
        "pos": [0, 0],
        "angle": 0,
        "polygons": [[
            point_iu(fill.pos1_x, fill.pos1_y, ox, oy),
            point_iu(fill.pos2_x, fill.pos1_y, ox, oy),
            point_iu(fill.pos2_x, fill.pos2_y, ox, oy),
            point_iu(fill.pos1_x, fill.pos2_y, ox, oy),
        ]],
    }


def point_mm(x_mm: float, y_mm: float, ox: float, oy: float) -> list[float]:
    return point_mils(x_mm / MIL_TO_MM, y_mm / MIL_TO_MM, ox, oy)


def component_for_text(pcbdoc: object, text: object) -> object | None:
    component_index = getattr(text, "component_index", None)
    if component_index is None:
        return None
    try:
        index = int(component_index)
    except Exception:
        return None
    if 0 <= index < len(getattr(pcbdoc, "components", [])):
        return pcbdoc.components[index]
    return None


def altium_bool(value: object, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().upper()
    if normalized in {"FALSE", "0", "NO", "OFF"}:
        return False
    if normalized in {"TRUE", "1", "YES", "ON"}:
        return True
    return default


def component_text_is_visible(pcbdoc: object, text: object) -> bool:
    comp = component_for_text(pcbdoc, text)
    if comp is None:
        return True
    raw_record = getattr(comp, "raw_record", {}) or {}
    raw_content = str(getattr(text, "text_content", "") or "").strip().lower()
    if getattr(text, "is_designator", False) or raw_content == ".designator":
        return altium_bool(raw_record.get("NAMEON"), True)
    if getattr(text, "is_comment", False) or raw_content.startswith(".comment"):
        return altium_bool(raw_record.get("COMMENTON"), True)
    return True


def resolve_simple_altium_expression(value: str, lookup: dict[str, str], depth: int = 0) -> str:
    if depth > 4:
        return value
    stripped = value.strip()
    if stripped.startswith("="):
        name = stripped[1:].strip().lower()
        if name in lookup:
            return resolve_simple_altium_expression(lookup[name], lookup, depth + 1)
    if stripped.startswith("."):
        name = stripped[1:].strip().lower()
        if name in lookup:
            return resolve_simple_altium_expression(lookup[name], lookup, depth + 1)
    return value


def resolve_text_content(pcbdoc: object, text: object, bom_by_ref: dict[str, dict[str, object]]) -> str:
    content = str(getattr(text, "text_content", "") or "")
    if not content:
        return ""
    comp = component_for_text(pcbdoc, text)
    if comp is None and not re.search(r"\.[A-Za-z_][A-Za-z0-9_]*", content):
        return content

    row = bom_by_ref.get(getattr(comp, "designator", ""), {}) if comp is not None else {}
    parameters = dict(row.get("parameters", {}) or {})
    lookup = {str(k).lower(): str(v) for k, v in parameters.items() if v is not None}
    if comp is not None:
        actual_footprint = str(getattr(comp, "footprint", "") or row.get("footprint") or "")
        lookup["designator"] = str(getattr(comp, "designator", "") or "")
        lookup["value"] = str(row.get("value") or getattr(comp, "description", "") or "")
        lookup["description"] = str(row.get("description") or getattr(comp, "description", "") or "")
        lookup["currentfootprint"] = actual_footprint
        lookup["footprint"] = actual_footprint
        lookup.setdefault("comment", str(row.get("value") or getattr(comp, "description", "") or ""))

    def replace_macro(match: re.Match[str]) -> str:
        name = match.group(1).lower()
        return resolve_simple_altium_expression(lookup.get(name, match.group(0)), lookup)

    return re.sub(r"\.([A-Za-z_][A-Za-z0-9_]*)", replace_macro, content).strip()


def stroke_text_svgpath(result: StrokeTextResult, ox: float, oy: float) -> str:
    parts = []
    for x1, y1, x2, y2 in result.lines:
        start = point_mm(x1, y1, ox, oy)
        end = point_mm(x2, y2, ox, oy)
        parts.append(f"M {start[0]:.6f} {start[1]:.6f} L {end[0]:.6f} {end[1]:.6f}")
    return " ".join(parts)


def polygon_text_svgpath(result: TextPolygonResult, ox: float, oy: float) -> str:
    parts = []
    for character in result.characters:
        for glyph in character:
            contours = [glyph.outline] + list(glyph.holes)
            for contour in contours:
                if not contour:
                    continue
                first = point_mm(contour[0][0], contour[0][1], ox, oy)
                parts.append(f"M {first[0]:.6f} {first[1]:.6f}")
                for x_mm, y_mm in contour[1:]:
                    point = point_mm(x_mm, y_mm, ox, oy)
                    parts.append(f"L {point[0]:.6f} {point[1]:.6f}")
                parts.append("Z")
    return " ".join(parts)


def make_text_drawing(
    pcbdoc: object,
    text: object,
    bom_by_ref: dict[str, dict[str, object]],
    stroke_renderer: StrokeTextRenderer,
    ox: float,
    oy: float,
    respect_component_visibility: bool = True,
) -> dict[str, object] | None:
    if respect_component_visibility and not component_text_is_visible(pcbdoc, text):
        return None
    content = resolve_text_content(pcbdoc, text, bom_by_ref)
    if not content:
        return None
    text_result = render_pcb_text(text, stroke_renderer=stroke_renderer, text_override=content)
    if isinstance(text_result, StrokeTextResult):
        path = stroke_text_svgpath(text_result, ox, oy)
        result: dict[str, object] = {"svgpath": path, "thickness": round(text_result.stroke_width_mm / MIL_TO_MM, 6)}
    elif isinstance(text_result, TextPolygonResult):
        path = polygon_text_svgpath(text_result, ox, oy)
        result = {"svgpath": path, "fillrule": "evenodd"}
    else:
        return None
    if not path:
        return None
    if getattr(text, "is_designator", False):
        result["ref"] = 1
    raw_content = str(getattr(text, "text_content", "") or "").strip().lower()
    if getattr(text, "is_comment", False) or raw_content.startswith(".comment"):
        result["val"] = 1
    return result


def collect_layer_drawings(
    pcbdoc: object,
    stack: object,
    bom_by_ref: dict[str, dict[str, object]],
    ox: float,
    oy: float,
) -> tuple[dict[str, dict[str, list]], list[dict[str, object]]]:
    drawings = {"silkscreen": {"F": [], "B": []}, "fabrication": {"F": [], "B": []}}
    edges: list[dict[str, object]] = []
    stroke_renderer = StrokeTextRenderer()
    for track in pcbdoc.tracks:
        drawing = make_track_drawing(track, ox, oy)
        if is_edge_layer(stack, getattr(track, "layer", None)):
            continue
        bucket = drawing_bucket(stack, getattr(track, "layer", None))
        if bucket:
            drawings[bucket[0]][bucket[1]].append(drawing)
    for arc in pcbdoc.arcs:
        drawing = make_arc_drawing(arc, ox, oy)
        if is_edge_layer(stack, getattr(arc, "layer", None)):
            continue
        bucket = drawing_bucket(stack, getattr(arc, "layer", None))
        if bucket:
            drawings[bucket[0]][bucket[1]].append(drawing)
    for fill in pcbdoc.fills:
        bucket = drawing_bucket(stack, getattr(fill, "layer", None))
        if bucket:
            drawings[bucket[0]][bucket[1]].append(make_fill_drawing(fill, ox, oy))
    for region in pcbdoc.regions:
        layer = getattr(region, "layer", None)
        if is_edge_layer(stack, layer):
            continue
        bucket = drawing_bucket(stack, layer)
        drawing = make_region_drawing(region, ox, oy)
        if bucket and drawing:
            drawings[bucket[0]][bucket[1]].append(drawing)
    for text in pcbdoc.texts:
        layer = getattr(text, "layer", None)
        bucket = drawing_bucket(stack, layer)
        drawing = make_text_drawing(
            pcbdoc,
            text,
            bom_by_ref,
            stroke_renderer,
            ox,
            oy,
            respect_component_visibility=not is_designator_helper_layer(stack, layer),
        )
        if bucket and drawing:
            drawings[bucket[0]][bucket[1]].append(drawing)
    return drawings, edges


def copper_side(layer: object) -> str | None:
    enum = layer_enum(layer)
    if enum == PcbLayer.TOP:
        return "F"
    if enum == PcbLayer.BOTTOM:
        return "B"
    return None


def region_is_copper(region: object) -> bool:
    kind = getattr(region, "kind", 0)
    try:
        return int(kind) == 0
    except Exception:
        return str(kind).upper().endswith("COPPER")


def collect_zones(pcbdoc: object, ox: float, oy: float) -> dict[str, list]:
    zones = {"F": [], "B": []}
    source = pcbdoc.shapebased_regions or pcbdoc.regions
    for region in source:
        side = copper_side(getattr(region, "layer", None))
        if side is None or not region_is_copper(region):
            continue
        zone = make_region_zone(pcbdoc, region, ox, oy)
        if zone:
            zones[side].append(zone)
    return zones


def footprint_copper_drawings(prims: dict[str, list], ox: float, oy: float) -> list[dict[str, object]]:
    drawings = []
    for track in prims.get("tracks", []):
        side = copper_side(getattr(track, "layer", None))
        if side:
            drawings.append({"layer": side, "drawing": make_track_drawing(track, ox, oy)})
    for arc in prims.get("arcs", []):
        side = copper_side(getattr(arc, "layer", None))
        if side:
            drawings.append({"layer": side, "drawing": make_arc_drawing(arc, ox, oy)})
    for region in prims.get("regions", []):
        side = copper_side(getattr(region, "layer", None))
        drawing = make_region_drawing(region, ox, oy)
        if side and drawing:
            drawings.append({"layer": side, "drawing": drawing})
    return drawings


def pad_extents(pad: object, ox: float, oy: float) -> tuple[float, float, float, float]:
    polygons = custom_pad_polygons(pad, ox, oy) if pad_shape(pad) == "custom" else []
    points: list[list[float]] = []
    for polygon in polygons:
        points.extend(polygon)
    if not points:
        pos = point_iu(pad.x, pad.y, ox, oy)
        half_w = pad_width(pad) / 2.0
        half_h = pad_height(pad) / 2.0
        angle = -math.radians(angle_for_ibom(getattr(pad, "rotation", 0.0)))
        cos_a = math.cos(angle)
        sin_a = math.sin(angle)
        for local_x, local_y in ((-half_w, -half_h), (half_w, -half_h), (half_w, half_h), (-half_w, half_h)):
            points.append([
                pos[0] + local_x * cos_a - local_y * sin_a,
                pos[1] + local_x * sin_a + local_y * cos_a,
            ])
    return (
        min(point[0] for point in points),
        min(point[1] for point in points),
        max(point[0] for point in points),
        max(point[1] for point in points),
    )


def track_extents(track: object, ox: float, oy: float) -> tuple[float, float, float, float]:
    start = point_iu(track.start_x, track.start_y, ox, oy)
    end = point_iu(track.end_x, track.end_y, ox, oy)
    margin = float(getattr(track, "width", 0) or 0) / 20000.0
    return (
        min(start[0], end[0]) - margin,
        min(start[1], end[1]) - margin,
        max(start[0], end[0]) + margin,
        max(start[1], end[1]) + margin,
    )


def arc_extents(arc: object, ox: float, oy: float) -> tuple[float, float, float, float]:
    center = point_iu(arc.center_x, arc.center_y, ox, oy)
    radius = float(getattr(arc, "radius", 0) or 0) / 10000.0
    margin = float(getattr(arc, "width", 0) or 0) / 20000.0
    extent = radius + margin
    return (center[0] - extent, center[1] - extent, center[0] + extent, center[1] + extent)


def fill_extents(fill: object, ox: float, oy: float) -> tuple[float, float, float, float]:
    points = [
        point_iu(fill.pos1_x, fill.pos1_y, ox, oy),
        point_iu(fill.pos2_x, fill.pos2_y, ox, oy),
    ]
    return (
        min(point[0] for point in points),
        min(point[1] for point in points),
        max(point[0] for point in points),
        max(point[1] for point in points),
    )


def region_extents(region: object, ox: float, oy: float) -> tuple[float, float, float, float] | None:
    polygons = region_polygons(region, ox, oy)
    points = [point for polygon in polygons for point in polygon]
    if not points:
        return None
    return (
        min(point[0] for point in points),
        min(point[1] for point in points),
        max(point[0] for point in points),
        max(point[1] for point in points),
    )


def footprint_bbox(prims: dict[str, list], stack: object, ox: float, oy: float, fallback_center: list[float]) -> dict[str, object]:
    outline_extents: tuple[float, float, float, float] | None = None
    for track in prims.get("tracks", []):
        if is_component_outline_layer(stack, getattr(track, "layer", None)):
            outline_extents = extend_extents(outline_extents, *track_extents(track, ox, oy))
    for arc in prims.get("arcs", []):
        if is_component_outline_layer(stack, getattr(arc, "layer", None)):
            outline_extents = extend_extents(outline_extents, *arc_extents(arc, ox, oy))
    for fill in prims.get("fills", []):
        if is_component_outline_layer(stack, getattr(fill, "layer", None)):
            outline_extents = extend_extents(outline_extents, *fill_extents(fill, ox, oy))
    for region in prims.get("regions", []):
        if is_component_outline_layer(stack, getattr(region, "layer", None)):
            region_box = region_extents(region, ox, oy)
            if region_box:
                outline_extents = extend_extents(outline_extents, *region_box)
    if outline_extents is not None:
        return bbox_from_extents(outline_extents)

    pad_box: tuple[float, float, float, float] | None = None
    for pad in prims.get("pads", []):
        pad_box = extend_extents(pad_box, *pad_extents(pad, ox, oy))
    return bbox_from_extents(pad_box or (fallback_center[0], fallback_center[1], fallback_center[0], fallback_center[1]))


def add_outline_edges_from_board(pcbdoc: object, ox: float, oy: float, edges: list[dict[str, object]]) -> list[tuple[float, float]]:
    outline_points: list[tuple[float, float]] = []
    outline = getattr(getattr(pcbdoc, "board", None), "outline", None)
    if outline and outline.vertices:
        vertices = list(outline.vertices)
        converted = [point_mils(v.x_mils, v.y_mils, ox, oy) for v in vertices]
        outline_points.extend((p[0], p[1]) for p in converted)
        for index, vertex in enumerate(vertices):
            a = converted[index]
            b = converted[(index + 1) % len(converted)]
            if getattr(vertex, "is_arc", False):
                start_angle, end_angle = arc_angles_for_ibom(vertex.start_angle_deg or 0.0, vertex.end_angle_deg or 0.0)
                edges.append(
                    {
                        "type": "arc",
                        "start": point_mils(vertex.center_x_mils, vertex.center_y_mils, ox, oy),
                        "radius": round(float(vertex.radius_mils), 6),
                        "startangle": start_angle,
                        "endangle": end_angle,
                        "width": 1,
                    }
                )
            else:
                edges.append({"type": "segment", "start": a, "end": b, "width": 1})
        for cutout in getattr(outline, "cutouts", []) or []:
            cutout_vertices = list(cutout)
            converted_cutout = [point_mils(v.x_mils, v.y_mils, ox, oy) for v in cutout_vertices]
            outline_points.extend((p[0], p[1]) for p in converted_cutout)
            for index, vertex in enumerate(cutout_vertices):
                a = converted_cutout[index]
                b = converted_cutout[(index + 1) % len(converted_cutout)]
                if getattr(vertex, "is_arc", False):
                    start_angle, end_angle = arc_angles_for_ibom(vertex.start_angle_deg or 0.0, vertex.end_angle_deg or 0.0)
                    edges.append(
                        {
                            "type": "arc",
                            "start": point_mils(vertex.center_x_mils, vertex.center_y_mils, ox, oy),
                            "radius": round(float(vertex.radius_mils), 6),
                            "startangle": start_angle,
                            "endangle": end_angle,
                            "width": 1,
                        }
                    )
                else:
                    edges.append({"type": "segment", "start": a, "end": b, "width": 1})
    return outline_points


def load_design(source: Path) -> AltiumDesign:
    suffix = source.suffix.lower()
    if suffix == ".prjpcb":
        return AltiumDesign.from_prjpcb(source)
    if suffix == ".pcbdoc":
        return AltiumDesign.from_pcbdoc(source)
    raise ValueError(f"Unsupported Altium input: {source}. Expected a .PrjPcb or .PcbDoc file.")


def build_payload(project: Path) -> dict[str, object]:
    design = load_design(project)
    pcbdoc = design.load_pcbdoc()
    stack = resolved_layer_stack_from_pcbdoc(pcbdoc)
    ox = float(pcbdoc.board.origin_x if pcbdoc.board else 0.0)
    oy = float(pcbdoc.board.origin_y if pcbdoc.board else 0.0)
    bom_rows = design.to_bom() if design.schdocs else []
    bom_by_ref = {row["designator"]: row for row in bom_rows}

    drawings, edges = collect_layer_drawings(pcbdoc, stack, bom_by_ref, ox, oy)
    outline_points = add_outline_edges_from_board(pcbdoc, ox, oy, edges)

    footprints = []
    components = []
    for idx, comp in enumerate(pcbdoc.components):
        prims = pcbdoc.get_component_primitives(idx)
        pads = [make_pad(pcbdoc, pad, ox, oy) for pad in prims["pads"]]
        center = point_mils(float(str(comp.x).strip("mil")), float(str(comp.y).strip("mil")), ox, oy)
        row = bom_by_ref.get(comp.designator, {})
        layer = "B" if comp.get_layer_normalized() == "bottom" else "F"
        extra_fields = dict(row.get("parameters", {}) or {})
        if row.get("description"):
            extra_fields.setdefault("Description", row["description"])
        actual_footprint = str(comp.footprint or row.get("footprint") or "")
        component_lookup = {str(k).lower(): str(v) for k, v in extra_fields.items() if v is not None}
        component_lookup.update(
            {
                "designator": str(comp.designator or ""),
                "value": str(row.get("value") or getattr(comp, "description", "") or ""),
                "description": str(row.get("description") or getattr(comp, "description", "") or ""),
                "currentfootprint": actual_footprint,
                "footprint": actual_footprint,
            }
        )
        component_lookup.setdefault("comment", str(row.get("value") or getattr(comp, "description", "") or ""))
        component_value = resolve_simple_altium_expression(str(row.get("value") or getattr(comp, "description", "") or ""), component_lookup)
        extra_fields = {
            key: resolve_simple_altium_expression(str(value), component_lookup)
            for key, value in extra_fields.items()
            if value is not None
        }
        footprints.append(
            {
                "ref": comp.designator,
                "center": center,
                "bbox": footprint_bbox(prims, stack, ox, oy, center),
                "pads": pads,
                "drawings": footprint_copper_drawings(prims, ox, oy),
                "layer": layer,
            }
        )
        components.append(
            {
                "ref": comp.designator,
                "val": component_value,
                "footprint": actual_footprint,
                "layer": layer,
                "extra_fields": extra_fields,
            }
        )

    free_pads = board_primitive_pads(pcbdoc, ox, oy)
    if free_pads:
        # iBOM renders pads only as part of a footprint. A virtual component keeps
        # board-level pads and vias visible without adding a row to the BOM.
        footprints.append(
            {
                "ref": "__BOARD_PRIMITIVES__",
                "center": [0, 0],
                "bbox": {"pos": [1_000_000_000, 1_000_000_000], "relpos": [0, 0], "size": [1, 1], "angle": 0},
                "pads": free_pads,
                "drawings": [],
                "layer": "F",
            }
        )
        components.append(
            {
                "ref": "__BOARD_PRIMITIVES__",
                "val": "",
                "footprint": "",
                "layer": "F",
                "attr": "Virtual",
                "extra_fields": {},
            }
        )

    tracks = {"F": [], "B": []}
    for track in pcbdoc.tracks:
        side = layer_side(getattr(track, "layer", None))
        if side in tracks and net_name(pcbdoc, getattr(track, "net_index", None)):
            tracks[side].append(make_track(pcbdoc, track, ox, oy))
    for arc in pcbdoc.arcs:
        side = layer_side(getattr(arc, "layer", None))
        if side in tracks and net_name(pcbdoc, getattr(arc, "net_index", None)):
            tracks[side].append(make_arc(pcbdoc, arc, ox, oy))

    minx, miny, maxx, maxy = (
        (min(x for x, _ in outline_points), min(y for _, y in outline_points), max(x for x, _ in outline_points), max(y for _, y in outline_points))
        if outline_points
        else (-1000, -1000, 1000, 1000)
    )
    payload = {
        "spec_version": 1,
        "pcbdata": {
            "edges_bbox": {"minx": minx, "miny": miny, "maxx": maxx, "maxy": maxy},
            "edges": edges,
            "drawings": drawings,
            "footprints": footprints,
            "tracks": tracks,
            "zones": collect_zones(pcbdoc, ox, oy),
            "nets": [net.name for net in pcbdoc.nets],
            "metadata": {
                "title": project.stem,
                "revision": "",
                "company": "",
                "date": "",
            },
        },
        "components": components,
    }
    return payload


def generate_html(json_path: Path, dest_dir: Path, name_format: str) -> None:
    os.environ.setdefault("INTERACTIVE_HTML_BOM_NO_DISPLAY", "1")
    pcbnew = types.ModuleType("pcbnew")
    pcbnew.ActionPlugin = object
    sys.modules["pcbnew"] = pcbnew
    sys.argv = [
        "generate_interactive_bom",
        str(json_path),
        "--no-browser",
        "--dest-dir",
        str(dest_dir),
        "--name-format",
        name_format,
        "--include-tracks",
        "--include-nets",
    ]
    runpy.run_module("InteractiveHtmlBom.generate_interactive_bom", run_name="__main__")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert an Altium .PrjPcb/.PcbDoc project to InteractiveHtmlBom generic JSON.")
    parser.add_argument("project", type=Path, help="Path to an Altium .PrjPcb or .PcbDoc file")
    parser.add_argument("-o", "--output", type=Path, help="Output generic iBOM JSON path")
    parser.add_argument("--html", action="store_true", help="Also generate an InteractiveHtmlBom HTML file")
    parser.add_argument("--html-name", help="HTML output name-format without extension")
    parser.add_argument("--html-dir", type=Path, help="Directory for generated HTML; defaults to JSON output directory")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    project = args.project.resolve()
    output = (args.output or Path.cwd() / f"{project.stem}.ibom-input.json").resolve()
    payload = build_payload(project)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tracks = payload["pcbdata"]["tracks"]
    print(f"wrote {output}")
    print(f"components={len(payload['components'])} footprints={len(payload['pcbdata']['footprints'])} nets={len(payload['pcbdata']['nets'])}")
    print(f"tracks F={len(tracks['F'])} B={len(tracks['B'])}")
    if args.html:
        html_dir = (args.html_dir or output.parent).resolve()
        html_name = args.html_name or output.with_suffix("").name.replace(".ibom-input", ".ibom")
        generate_html(output, html_dir, html_name)


if __name__ == "__main__":
    main()
