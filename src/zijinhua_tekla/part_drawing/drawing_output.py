from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import ezdxf
from ezdxf.math import Vec2
from ezdxf.render.arrows import ARROWS
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm

from .contracts import DrawingIssue, DrawingStatus, PartDrawingSnapshot
from .dimension_style import CadDimensionStyle, PART_CAD_DIMENSION_STYLE_V1
from .dimension_optimizer import (
    DrawingArc,
    DrawingCircle,
    DrawingLine,
    DrawingText,
    ModelTransform,
    PartDrawingLayout,
    PlacedCallout,
    PlacedDimension,
    PlacedDimensionKind,
)
from .feature_recognizer import FeatureType, RecognizedFeature
from .geometry_analyzer import NormalizedPlateGeometry, NormalizedSegment, Point2D
from .snapshot_input import safe_part_position


LAYERS = {
    "PART_CONTOUR": 7,
    "PART_INNER": 3,
    "PART_CENTER": 4,
    "BEVEL": 5,
    "DIMENSION": 2,
    "ANNOTATION": 6,
    "TITLE_BLOCK": 7,
    "REVIEW": 1,
}

DXF_DIMSTYLE_NAME = "PART-CAD-V1"
DXF_DIMTEXT_STYLE_NAME = "TArial"
DXF_MLEADER_STYLE_NAME = "PART-CALLOUT-V1"


@dataclass(frozen=True)
class PartDrawingDocument:
    schema_version: str
    rule_version: str
    part_position: str
    part_ids: tuple[str, ...]
    quantity: int
    status: DrawingStatus
    page_width: float
    page_height: float
    scale: float
    geometry_fingerprint: str
    source: dict[str, str]
    tolerances: dict[str, float]
    recognized_feature_ids: tuple[str, ...]
    recognized_feature_items: tuple[dict[str, Any], ...]
    contour_segments: tuple[DrawingLine | DrawingArc, ...]
    inner_segments: tuple[DrawingLine | DrawingArc | DrawingCircle, ...]
    placed_dimensions: tuple[PlacedDimension, ...]
    placed_callouts: tuple[PlacedCallout, ...]
    dimension_style: CadDimensionStyle
    annotation_lines: tuple[DrawingLine, ...]
    annotation_text_items: tuple[DrawingText, ...]
    title_fields: dict[str, str]
    issues: tuple[DrawingIssue, ...]

    def annotation_texts(self) -> list[str]:
        return (
            [item.display_text for item in self.placed_dimensions]
            + [item.display_text for item in self.placed_callouts]
            + [item.text for item in self.annotation_text_items]
        )


@dataclass(frozen=True)
class PartDrawingPaths:
    dxf_path: Path
    pdf_path: Path
    json_path: Path


class PartDrawingRenderError(RuntimeError):
    pass


def build_part_drawing_document(
    snapshot: PartDrawingSnapshot,
    geometry: NormalizedPlateGeometry,
    features: tuple[RecognizedFeature, ...],
    layout: PartDrawingLayout,
    quantity: int,
    status: DrawingStatus | None = None,
    issues: tuple[DrawingIssue, ...] = (),
    part_ids: tuple[str, ...] = (),
) -> PartDrawingDocument:
    contour_segments = tuple(
        _transform_segment(segment, layout.model_transform, "PART_CONTOUR")
        for segment in geometry.outer_loop
    )
    inner_segments = tuple(
        _transform_segment(segment, layout.model_transform, "PART_INNER")
        for loop in geometry.inner_loops
        for segment in loop
    ) + tuple(
        primitive
        for feature in features
        if feature.feature_type in {FeatureType.ROUND_HOLE, FeatureType.SLOT}
        for primitive in _feature_primitives(feature, layout.model_transform)
    )
    center_lines = tuple(
        line
        for feature in features
        if feature.feature_type == FeatureType.ROUND_HOLE
        for line in _round_hole_center_lines(feature, layout.model_transform)
    )
    bevel_lines = tuple(
        _bevel_line(feature, layout.model_transform)
        for feature in features
        if feature.feature_type == FeatureType.BEVEL and len(feature.anchors) == 2
    )
    return PartDrawingDocument(
        schema_version="partDrawingDocument.v1",
        rule_version="partDrawingRules.v1",
        part_position=snapshot.part_position,
        part_ids=part_ids or (snapshot.part_id,),
        quantity=quantity,
        status=status or layout.status,
        page_width=layout.page_width,
        page_height=layout.page_height,
        scale=layout.scale,
        geometry_fingerprint=geometry.fingerprint,
        source={
            "schema_version": snapshot.schema_version,
            "exporter_version": snapshot.exporter_version,
            "model_identifier": snapshot.model_identifier,
            "exported_at": snapshot.exported_at,
        },
        tolerances={
            "coordinate_noise_mm": 0.01,
            "geometry_match_mm": 0.1,
            "angle_match_deg": 0.05,
            "thickness_match_mm": 0.01,
        },
        recognized_feature_ids=tuple(feature.feature_id for feature in features),
        recognized_feature_items=tuple(_feature_evidence(feature) for feature in features),
        contour_segments=contour_segments,
        inner_segments=inner_segments,
        placed_dimensions=layout.placed_dimensions,
        placed_callouts=layout.placed_callouts,
        dimension_style=PART_CAD_DIMENSION_STYLE_V1,
        annotation_lines=layout.annotation_lines + center_lines + bevel_lines,
        annotation_text_items=layout.annotation_texts,
        title_fields=layout.title_fields,
        issues=issues,
    )


def render_part_drawing(
    document: PartDrawingDocument,
    output_directory: Path,
    cjk_font_path: Path | None = None,
) -> PartDrawingPaths:
    if document.status == DrawingStatus.REJECTED:
        raise ValueError("REJECTED document cannot be rendered")
    font_name = _resolve_pdf_font(document, cjk_font_path)
    output_directory.mkdir(parents=True, exist_ok=True)
    stem = safe_part_position(document.part_position)
    paths = PartDrawingPaths(
        output_directory / f"{stem}.dxf",
        output_directory / f"{stem}.pdf",
        output_directory / f"{stem}.drawing.json",
    )
    temporary = PartDrawingPaths(
        paths.dxf_path.with_suffix(".dxf.tmp"),
        paths.pdf_path.with_suffix(".pdf.tmp"),
        paths.json_path.with_suffix(".json.tmp"),
    )
    try:
        _render_dxf(document, temporary.dxf_path)
        _render_pdf(document, temporary.pdf_path, font_name)
        temporary.json_path.write_text(
            json.dumps(_document_to_jsonable(document), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.dxf_path.replace(paths.dxf_path)
        temporary.pdf_path.replace(paths.pdf_path)
        temporary.json_path.replace(paths.json_path)
    except Exception as exc:
        for path in (temporary.dxf_path, temporary.pdf_path, temporary.json_path):
            if path.exists():
                path.unlink()
        if isinstance(exc, (ValueError, PartDrawingRenderError)):
            raise
        raise PartDrawingRenderError(str(exc)) from exc
    return paths


def _transform_segment(segment: NormalizedSegment, transform: ModelTransform, layer: str):
    start = transform.point(segment.start)
    end = transform.point(segment.end)
    if segment.kind == "LINE":
        return DrawingLine(*start, *end, layer)
    if segment.center is None:
        raise PartDrawingRenderError("ARC segment requires center")
    center = transform.point(segment.center)
    radius = math.hypot(start[0] - center[0], start[1] - center[1])
    start_angle = math.degrees(math.atan2(start[1] - center[1], start[0] - center[0])) % 360
    end_angle = math.degrees(math.atan2(end[1] - center[1], end[0] - center[0])) % 360
    if not segment.clockwise:
        start_angle, end_angle = end_angle, start_angle
    return DrawingArc(center[0], center[1], radius, start_angle, end_angle, layer)


def _feature_primitives(feature: RecognizedFeature, transform: ModelTransform):
    center = transform.point(feature.anchors[0])
    if feature.feature_type == FeatureType.ROUND_HOLE:
        return (DrawingCircle(center[0], center[1], float(feature.parameters["diameter"]) * transform.scale / 2.0, "PART_INNER"),)
    length = float(feature.parameters["length"]) * transform.scale
    width = float(feature.parameters["width"]) * transform.scale
    angle = math.radians(-float(feature.parameters.get("angle_deg", 0.0)))
    ux, uy = math.cos(angle), math.sin(angle)
    px, py = -uy, ux
    radius = width / 2.0
    half_straight = max(0.0, (length - width) / 2.0)
    first = (center[0] - ux * half_straight, center[1] - uy * half_straight)
    second = (center[0] + ux * half_straight, center[1] + uy * half_straight)
    direction_angle = math.degrees(angle)
    return (
        DrawingLine(first[0] + px * radius, first[1] + py * radius, second[0] + px * radius, second[1] + py * radius, "PART_INNER"),
        DrawingLine(first[0] - px * radius, first[1] - py * radius, second[0] - px * radius, second[1] - py * radius, "PART_INNER"),
        DrawingArc(first[0], first[1], radius, direction_angle + 90, direction_angle + 270, "PART_INNER"),
        DrawingArc(second[0], second[1], radius, direction_angle - 90, direction_angle + 90, "PART_INNER"),
    )


def _round_hole_center_lines(feature: RecognizedFeature, transform: ModelTransform):
    center = transform.point(feature.anchors[0])
    radius = float(feature.parameters["diameter"]) * transform.scale / 2.0
    extent = radius + 2.0
    return (
        DrawingLine(center[0] - extent, center[1], center[0] + extent, center[1], "PART_CENTER"),
        DrawingLine(center[0], center[1] - extent, center[0], center[1] + extent, "PART_CENTER"),
    )


def _bevel_line(feature: RecognizedFeature, transform: ModelTransform):
    start = transform.point(feature.anchors[0])
    end = transform.point(feature.anchors[1])
    return DrawingLine(*start, *end, "BEVEL")


def _feature_evidence(feature: RecognizedFeature) -> dict[str, Any]:
    return {
        "feature_id": feature.feature_id,
        "feature_type": feature.feature_type.value,
        "anchors": [[point.x, point.y] for point in feature.anchors],
        "parameters": dict(feature.parameters),
        "source_ids": list(feature.source_ids),
        "evidence_codes": list(feature.evidence_codes),
        "confidence": feature.confidence,
    }


def _resolve_pdf_font(document: PartDrawingDocument, cjk_font_path: Path | None) -> str:
    texts = document.annotation_texts() + list(document.title_fields.values())
    if not _requires_cjk_font(texts):
        return "Helvetica"
    if cjk_font_path is None or not cjk_font_path.is_file():
        raise PartDrawingRenderError("CJK font is required for non-ASCII PDF text")
    try:
        pdfmetrics.registerFont(TTFont("PartDrawingCJK", str(cjk_font_path)))
    except Exception as exc:
        raise PartDrawingRenderError(f"CJK font cannot be loaded: {exc}") from exc
    return "PartDrawingCJK"


def _requires_cjk_font(texts) -> bool:
    for text in texts:
        try:
            text.encode("cp1252")
        except UnicodeEncodeError:
            return True
    return False


def _render_dxf(document: PartDrawingDocument, path: Path) -> None:
    dxf = ezdxf.new("R2007", setup=True)
    for name, color in LAYERS.items():
        if name not in dxf.layers:
            dxf.layers.add(name, color=color)
    _ensure_dxf_dimension_style(dxf, document)
    _ensure_dxf_mleader_style(dxf, document)
    modelspace = dxf.modelspace()
    for primitive in document.contour_segments + document.inner_segments + document.annotation_lines:
        _add_dxf_primitive(modelspace, primitive)
    for item in document.annotation_text_items:
        modelspace.add_text(item.text, dxfattribs={"insert": (item.x, item.y), "height": item.height, "layer": item.layer})
    for dimension in document.placed_dimensions:
        _add_dxf_dimension(modelspace, document, dimension)
    for callout in document.placed_callouts:
        _add_dxf_callout(modelspace, callout)
    _add_dxf_title(modelspace, document)
    dxf.saveas(path)
    audit = ezdxf.readfile(path).audit()
    if audit.has_errors:
        raise PartDrawingRenderError(f"DXF audit failed with {len(audit.errors)} errors")


def _ensure_dxf_dimension_style(dxf, document) -> None:
    style = document.dimension_style
    if DXF_DIMTEXT_STYLE_NAME not in dxf.styles:
        dxf.styles.new(
            DXF_DIMTEXT_STYLE_NAME,
            dxfattribs={"font": "arial.ttf", "width": style.text_width_factor},
        )
    if DXF_DIMSTYLE_NAME in dxf.dimstyles:
        dimstyle = dxf.dimstyles.get(DXF_DIMSTYLE_NAME)
    else:
        dimstyle = dxf.dimstyles.new(DXF_DIMSTYLE_NAME)
    dimstyle.dxf.dimtxsty = DXF_DIMTEXT_STYLE_NAME
    dimstyle.dxf.dimtxt = style.text_height_mm
    dimstyle.dxf.dimasz = style.arrow_size_mm
    dimstyle.dxf.dimexo = style.extension_offset_mm
    dimstyle.dxf.dimexe = style.extension_beyond_mm
    dimstyle.dxf.dimgap = style.text_gap_mm
    dimstyle.dxf.dimdec = style.decimal_places
    dimstyle.dxf.dimzin = 8 if style.suppress_trailing_zeros else 0
    dimstyle.dxf.dimtad = 1 if style.text_above_line else 0
    dimstyle.dxf.dimscale = 1.0
    dimstyle.dxf.dimclrd = 256
    dimstyle.dxf.dimclre = 256
    dimstyle.dxf.dimclrt = 256
    dimstyle.set_arrows(
        blk=style.linear_arrow_name,
        ldrblk=style.leader_arrow_name,
    )


def _ensure_dxf_mleader_style(dxf, document) -> None:
    if DXF_MLEADER_STYLE_NAME in dxf.mleader_styles:
        style = dxf.mleader_styles.get(DXF_MLEADER_STYLE_NAME)
    else:
        style = dxf.mleader_styles.new(DXF_MLEADER_STYLE_NAME)
    style.dxf.char_height = document.dimension_style.text_height_mm
    style.dxf.arrow_head_size = document.dimension_style.arrow_size_mm
    style.dxf.landing_gap_size = document.dimension_style.text_gap_mm
    style.dxf.dogleg_length = 4.0
    style.dxf.text_style_handle = dxf.styles.get(DXF_DIMTEXT_STYLE_NAME).dxf.handle


def _add_dxf_callout(modelspace, callout) -> None:
    builder = modelspace.add_multileader_mtext(
        style=DXF_MLEADER_STYLE_NAME,
        dxfattribs={"layer": "ANNOTATION"},
    )
    target = Vec2(callout.target_point)
    text_point = Vec2(callout.text_point)
    builder.quick_leader(
        callout.display_text,
        target=target,
        segment1=text_point - target,
    )


def _add_dxf_dimension(modelspace, document, dimension) -> None:
    factor = document.dimension_style.measurement_factor(document.scale)
    override = {"dimlfac": factor}
    text = _dxf_dimension_text(dimension, document.dimension_style)
    if dimension.kind == PlacedDimensionKind.LINEAR_HORIZONTAL:
        rendered = modelspace.add_linear_dim(
            base=dimension.dimension_line_point,
            p1=dimension.source_points[0],
            p2=dimension.source_points[1],
            angle=0.0,
            text=text,
            dimstyle=DXF_DIMSTYLE_NAME,
            override=override,
            dxfattribs={"layer": "DIMENSION"},
        )
    elif dimension.kind == PlacedDimensionKind.LINEAR_VERTICAL:
        rendered = modelspace.add_linear_dim(
            base=dimension.dimension_line_point,
            p1=dimension.source_points[0],
            p2=dimension.source_points[1],
            angle=90.0,
            text_rotation=90.0,
            text=text,
            dimstyle=DXF_DIMSTYLE_NAME,
            override=override,
            dxfattribs={"layer": "DIMENSION"},
        )
    else:
        center = dimension.source_points[0]
        method = (
            modelspace.add_diameter_dim
            if dimension.kind == PlacedDimensionKind.DIAMETER
            else modelspace.add_radius_dim
        )
        rendered = method(
            center=center,
            radius=dimension.paper_radius,
            angle=0.0,
            location=dimension.dimension_line_point,
            text=text,
            dimstyle=DXF_DIMSTYLE_NAME,
            override={**override, "dimblk": ARROWS.closed_filled},
            dxfattribs={"layer": "DIMENSION"},
        )
    rendered.render()


def _dxf_dimension_text(dimension, style) -> str:
    plain = style.format_measurement(dimension.measured_value_mm)
    return "<>" if dimension.display_text == plain else dimension.display_text


def _add_dxf_primitive(modelspace, primitive) -> None:
    if isinstance(primitive, DrawingLine):
        modelspace.add_line((primitive.x1, primitive.y1), (primitive.x2, primitive.y2), dxfattribs={"layer": primitive.layer})
    elif isinstance(primitive, DrawingCircle):
        modelspace.add_circle((primitive.cx, primitive.cy), primitive.radius, dxfattribs={"layer": primitive.layer})
    elif isinstance(primitive, DrawingArc):
        modelspace.add_arc((primitive.cx, primitive.cy), primitive.radius, primitive.start_angle, primitive.end_angle, dxfattribs={"layer": primitive.layer})


def _add_dxf_title(modelspace, document) -> None:
    modelspace.add_line((12, 242), (408, 242), dxfattribs={"layer": "TITLE_BLOCK"})
    for index, (key, value) in enumerate(document.title_fields.items()):
        modelspace.add_text(f"{key}: {value}", dxfattribs={"insert": (220, 250 + index * 6), "height": 3.5, "layer": "TITLE_BLOCK"})


def _render_pdf(document: PartDrawingDocument, path: Path, font_name: str) -> None:
    pdf = canvas.Canvas(str(path), pagesize=(document.page_width * mm, document.page_height * mm), pageCompression=0)
    pdf.setLineWidth(0.25 * mm)
    for primitive in document.contour_segments + document.inner_segments + document.annotation_lines:
        _add_pdf_primitive(pdf, document, primitive)
    for dimension in document.placed_dimensions:
        _draw_pdf_dimension(pdf, document, dimension, font_name)
    for callout in document.placed_callouts:
        _draw_pdf_callout(pdf, document, callout, font_name)
    for item in document.annotation_text_items:
        _pdf_text(pdf, document, item.x, item.y, item.text, item.height, font_name)
    pdf.line(12 * mm, (document.page_height - 242) * mm, 408 * mm, (document.page_height - 242) * mm)
    for index, (key, value) in enumerate(document.title_fields.items()):
        _pdf_text(pdf, document, 220, 250 + index * 6, f"{key}: {value}", 3.5, font_name)
    pdf.showPage()
    pdf.save()
    if not path.exists() or path.stat().st_size <= 1000:
        raise PartDrawingRenderError("PDF output is empty")


def _add_pdf_primitive(pdf, document, primitive) -> None:
    if isinstance(primitive, DrawingLine):
        pdf.line(primitive.x1 * mm, (document.page_height - primitive.y1) * mm, primitive.x2 * mm, (document.page_height - primitive.y2) * mm)
    elif isinstance(primitive, DrawingCircle):
        pdf.circle(primitive.cx * mm, (document.page_height - primitive.cy) * mm, primitive.radius * mm, stroke=1, fill=0)
    elif isinstance(primitive, DrawingArc):
        left = (primitive.cx - primitive.radius) * mm
        bottom = (document.page_height - primitive.cy - primitive.radius) * mm
        right = (primitive.cx + primitive.radius) * mm
        top = (document.page_height - primitive.cy + primitive.radius) * mm
        extent = (primitive.end_angle - primitive.start_angle) % 360
        pdf.arc(left, bottom, right, top, startAng=-primitive.end_angle, extent=extent)


def _draw_pdf_dimension(pdf, document, dimension, font_name) -> None:
    style = document.dimension_style
    if dimension.kind in {
        PlacedDimensionKind.LINEAR_HORIZONTAL,
        PlacedDimensionKind.LINEAR_VERTICAL,
    }:
        _draw_pdf_linear_dimension(pdf, document, dimension, style, font_name)
    else:
        _draw_pdf_radial_dimension(pdf, document, dimension, style, font_name)


def _draw_pdf_linear_dimension(pdf, document, dimension, style, font_name) -> None:
    vertical = dimension.kind == PlacedDimensionKind.LINEAR_VERTICAL
    p1, p2 = dimension.source_points
    base = dimension.dimension_line_point
    axis = 1 if vertical else 0
    cross = 0 if vertical else 1
    line_cross = base[cross]
    for point in (p1, p2):
        direction = 1.0 if line_cross >= point[cross] else -1.0
        start = list(point)
        end = list(point)
        start[cross] += direction * style.extension_offset_mm
        end[cross] = line_cross + direction * style.extension_beyond_mm
        _pdf_line(pdf, document, tuple(start), tuple(end))
    first = [p1[0], p1[1]]
    second = [p2[0], p2[1]]
    first[cross] = line_cross
    second[cross] = line_cross
    _draw_pdf_dimension_line_with_text_gap(
        pdf,
        document,
        tuple(first),
        tuple(second),
        dimension.text_box,
        vertical,
        style.text_gap_mm,
    )
    _draw_pdf_oblique_tick(pdf, document, tuple(first), style.arrow_size_mm)
    _draw_pdf_oblique_tick(pdf, document, tuple(second), style.arrow_size_mm)
    _pdf_dimension_text(pdf, document, dimension, style, font_name)


def _draw_pdf_radial_dimension(pdf, document, dimension, style, font_name) -> None:
    center = dimension.source_points[0]
    location = dimension.dimension_line_point
    dx, dy = location[0] - center[0], location[1] - center[1]
    length = math.hypot(dx, dy)
    if length <= 1e-9:
        raise PartDrawingRenderError("radial dimension leader has zero length")
    edge = (
        center[0] + dx / length * dimension.paper_radius,
        center[1] + dy / length * dimension.paper_radius,
    )
    _pdf_line(pdf, document, edge, location)
    _draw_pdf_filled_arrow(pdf, document, edge, location, style.arrow_size_mm)
    _pdf_dimension_text(pdf, document, dimension, style, font_name)


def _draw_pdf_callout(pdf, document, callout, font_name) -> None:
    _pdf_line(pdf, document, callout.target_point, callout.text_point)
    _draw_pdf_filled_arrow(
        pdf,
        document,
        callout.target_point,
        callout.text_point,
        document.dimension_style.arrow_size_mm,
    )
    _pdf_text(
        pdf,
        document,
        callout.text_point[0],
        callout.text_point[1],
        callout.display_text,
        document.dimension_style.text_height_mm,
        font_name,
    )


def _pdf_line(pdf, document, start, end) -> None:
    pdf.line(
        start[0] * mm,
        (document.page_height - start[1]) * mm,
        end[0] * mm,
        (document.page_height - end[1]) * mm,
    )


def _draw_pdf_dimension_line_with_text_gap(
    pdf,
    document,
    first,
    second,
    box,
    vertical,
    gap,
) -> None:
    axis = 1 if vertical else 0
    cross = 0 if vertical else 1
    low, high = sorted((first[axis], second[axis]))
    gap_low = (box.top if vertical else box.left) - gap
    gap_high = (box.bottom if vertical else box.right) + gap
    fixed = first[cross]
    if low < gap_low:
        start = [fixed, fixed]
        end = [fixed, fixed]
        start[axis], start[cross] = low, fixed
        end[axis], end[cross] = min(gap_low, high), fixed
        _pdf_line(pdf, document, tuple(start), tuple(end))
    if gap_high < high:
        start = [fixed, fixed]
        end = [fixed, fixed]
        start[axis], start[cross] = max(gap_high, low), fixed
        end[axis], end[cross] = high, fixed
        _pdf_line(pdf, document, tuple(start), tuple(end))


def _draw_pdf_oblique_tick(pdf, document, point, size) -> None:
    half = size / math.sqrt(2.0)
    _pdf_line(
        pdf,
        document,
        (point[0] - half, point[1] + half),
        (point[0] + half, point[1] - half),
    )


def _draw_pdf_filled_arrow(pdf, document, tip, tail, size) -> None:
    dx, dy = tail[0] - tip[0], tail[1] - tip[1]
    length = math.hypot(dx, dy)
    if length <= 1e-9:
        raise PartDrawingRenderError("radial dimension leader has zero length")
    ux, uy = dx / length, dy / length
    px, py = -uy, ux
    base = (tip[0] + ux * size, tip[1] + uy * size)
    half_width = size / 2.0
    left = (base[0] + px * half_width, base[1] + py * half_width)
    right = (base[0] - px * half_width, base[1] - py * half_width)
    path = pdf.beginPath()
    path.moveTo(tip[0] * mm, (document.page_height - tip[1]) * mm)
    path.lineTo(left[0] * mm, (document.page_height - left[1]) * mm)
    path.lineTo(right[0] * mm, (document.page_height - right[1]) * mm)
    path.close()
    pdf.drawPath(path, stroke=1, fill=1)


def _pdf_dimension_text(pdf, document, dimension, style, font_name) -> None:
    pdf.saveState()
    pdf.translate(
        dimension.dimension_line_point[0] * mm,
        (document.page_height - dimension.dimension_line_point[1]) * mm,
    )
    pdf.rotate(dimension.text_rotation)
    text = pdf.beginText()
    text.setFont(font_name, style.text_height_mm * mm)
    text.setHorizScale(style.text_width_factor * 100.0)
    width = pdfmetrics.stringWidth(
        dimension.display_text,
        font_name,
        style.text_height_mm * mm,
    ) * style.text_width_factor
    text.setTextOrigin(-width / 2.0, style.text_gap_mm * mm)
    text.textOut(dimension.display_text)
    pdf.drawText(text)
    pdf.restoreState()


def _pdf_text(pdf, document, x, y, text, height, font_name) -> None:
    pdf.setFont(font_name, height * mm)
    pdf.drawString(x * mm, (document.page_height - y) * mm, text)


def _document_to_jsonable(document: PartDrawingDocument) -> dict[str, Any]:
    return {
        "schema_version": document.schema_version,
        "rule_version": document.rule_version,
        "part_position": document.part_position,
        "part_ids": list(document.part_ids),
        "quantity": document.quantity,
        "status": document.status.value,
        "page": {"width": document.page_width, "height": document.page_height, "scale": document.scale},
        "geometry_fingerprint": document.geometry_fingerprint,
        "source": document.source,
        "tolerances": document.tolerances,
        "recognized_feature_ids": list(document.recognized_feature_ids),
        "recognized_feature_items": list(document.recognized_feature_items),
        "annotation_texts": document.annotation_texts(),
        "placed_dimensions": [
            item.display_text for item in document.placed_dimensions
        ],
        "placed_callout_items": [
            {
                "callout_id": item.callout_id,
                "display_text": item.display_text,
                "target_point": list(item.target_point),
                "text_point": list(item.text_point),
                "source_feature_ids": list(item.source_feature_ids),
                "evidence_codes": list(item.evidence_codes),
            }
            for item in document.placed_callouts
        ],
        "placed_dimension_items": [
            {
                "dimension_id": item.dimension_id,
                "kind": item.kind.value,
                "display_text": item.display_text,
                "measured_value_mm": item.measured_value_mm,
                "source_feature_ids": list(item.source_feature_ids),
                "evidence_codes": list(item.evidence_codes),
            }
            for item in document.placed_dimensions
        ],
        "dimension_style": asdict(document.dimension_style),
        "entity_counts": _entity_counts(document),
        "issues": [
            {"code": item.code.value, "message": item.message, "blocking": item.blocking, "part_ids": list(item.part_ids), "evidence": list(item.evidence)}
            for item in document.issues
        ],
    }


def _entity_counts(document: PartDrawingDocument) -> dict[str, int]:
    primitives = document.contour_segments + document.inner_segments + document.annotation_lines
    return {
        "lines": sum(isinstance(item, DrawingLine) for item in primitives),
        "arcs": sum(isinstance(item, DrawingArc) for item in primitives),
        "circles": sum(isinstance(item, DrawingCircle) for item in primitives),
        "dimensions": len(document.placed_dimensions),
        "callouts": len(document.placed_callouts),
        "texts": len(document.annotation_text_items) + len(document.title_fields),
    }
