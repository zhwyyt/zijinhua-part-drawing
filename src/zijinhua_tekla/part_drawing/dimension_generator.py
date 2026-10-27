from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .contracts import DrawingIssue, DrawingStatus, IssueCode, PartDrawingSnapshot
from .feature_recognizer import FeatureType, RecognizedFeature
from .geometry_analyzer import NormalizedPlateGeometry, Point2D


class DimensionKind(str, Enum):
    OVERALL = "OVERALL"
    THICKNESS = "THICKNESS"
    DIAMETER = "DIAMETER"
    RADIUS = "RADIUS"
    DATUM_X = "DATUM_X"
    DATUM_Y = "DATUM_Y"
    LENGTH = "LENGTH"
    WIDTH = "WIDTH"
    PATTERN = "PATTERN"
    WARNING = "WARNING"


@dataclass(frozen=True)
class DimensionIntent:
    intent_id: str
    kind: DimensionKind
    priority: int
    text: str
    model_value: float | None
    anchors: tuple[Point2D, ...]
    source_feature_ids: tuple[str, ...]
    evidence_codes: tuple[str, ...]
    preferred_band: str


@dataclass(frozen=True)
class DimensionGenerationResult:
    intents: tuple[DimensionIntent, ...]
    status: DrawingStatus
    issues: tuple[DrawingIssue, ...]


def generate_dimension_intents(snapshot, geometry, features) -> DimensionGenerationResult:
    width, height = geometry.bounds[2], geometry.bounds[3]
    intents = [
        _intent("overall-x", DimensionKind.OVERALL, 100, _number(width), width, (Point2D(0, 0), Point2D(width, 0)), (), ("NORMALIZED_BOUNDS",), "top"),
        _intent("overall-y", DimensionKind.OVERALL, 100, _number(height), height, (Point2D(0, 0), Point2D(0, height)), (), ("NORMALIZED_BOUNDS",), "right"),
        _intent("thickness", DimensionKind.THICKNESS, 95, f"t={_number(snapshot.thickness)}", snapshot.thickness, (), (), ("TEKLA_PART_THICKNESS",), "note"),
    ]
    issues = []
    grouped_ids = {source for item in features if item.feature_type == FeatureType.HOLE_GROUP for source in item.source_ids}
    for feature in features:
        if feature.feature_type == FeatureType.ROUND_HOLE and feature.feature_id not in grouped_ids:
            intents.extend(_round_hole_intents(feature))
        elif feature.feature_type == FeatureType.HOLE_GROUP:
            intents.extend(_hole_group_intents(feature))
        elif feature.feature_type == FeatureType.SLOT:
            intents.extend(_slot_intents(feature))
        elif feature.feature_type in {FeatureType.CHAMFER, FeatureType.NOTCH, FeatureType.POLYGON_CUTOUT}:
            intents.extend(_linear_feature_intents(feature))
        elif feature.feature_type == FeatureType.ARC_CUTOUT:
            radius = float(feature.parameters["radius"])
            intents.append(_intent(f"arc-{feature.feature_id}-radius", DimensionKind.RADIUS, 80, f"R{_number(radius)}", radius, feature.anchors[:1], (feature.feature_id,), feature.evidence_codes, "note"))
        elif feature.feature_type == FeatureType.BEVEL and not {"angleDeg", "depth"}.issubset(feature.parameters):
            issues.append(DrawingIssue(IssueCode.DIMENSION_INCOMPLETE, "bevel requires explicit angleDeg and depth", False, (snapshot.part_id,), feature.evidence_codes))
    return DimensionGenerationResult(tuple(_deduplicate(intents)), DrawingStatus.REVIEW_REQUIRED if issues else DrawingStatus.OK, tuple(issues))


def _round_hole_intents(feature):
    center = feature.anchors[0]
    diameter = float(feature.parameters["diameter"])
    prefix = f"hole-{feature.feature_id}"
    return [
        _intent(f"{prefix}-dia", DimensionKind.DIAMETER, 90, f"DIA{_number(diameter)}", diameter, (center,), (feature.feature_id,), feature.evidence_codes, "note"),
        _intent(f"{prefix}-x", DimensionKind.DATUM_X, 85, _number(center.x), center.x, (Point2D(0, center.y), center), (feature.feature_id,), feature.evidence_codes, "bottom"),
        _intent(f"{prefix}-y", DimensionKind.DATUM_Y, 85, _number(center.y), center.y, (Point2D(center.x, 0), center), (feature.feature_id,), feature.evidence_codes, "left"),
    ]


def _hole_group_intents(feature):
    first = feature.anchors[0]
    count = int(feature.parameters["count"])
    diameter = float(feature.parameters["diameter"])
    spacing = float(feature.parameters["spacing"])
    return [
        _intent(f"{feature.feature_id}-pattern", DimensionKind.PATTERN, 92, f"{count}xDIA{_number(diameter)} @{_number(spacing)}", spacing, feature.anchors, feature.source_ids, feature.evidence_codes, "note"),
        _intent(f"{feature.feature_id}-x", DimensionKind.DATUM_X, 85, _number(first.x), first.x, (Point2D(0, first.y), first), feature.source_ids, feature.evidence_codes, "bottom"),
        _intent(f"{feature.feature_id}-y", DimensionKind.DATUM_Y, 85, _number(first.y), first.y, (Point2D(first.x, 0), first), feature.source_ids, feature.evidence_codes, "left"),
    ]


def _slot_intents(feature):
    center = feature.anchors[0]
    result = _round_hole_intents(RecognizedFeature(feature.feature_id, FeatureType.ROUND_HOLE, feature.anchors, {"diameter": feature.parameters["width"]}, feature.source_ids, feature.evidence_codes, feature.confidence))[1:]
    for kind, key in ((DimensionKind.LENGTH, "length"), (DimensionKind.WIDTH, "width")):
        value = float(feature.parameters[key])
        result.append(_intent(f"slot-{feature.feature_id}-{key}", kind, 88, _number(value), value, (center,), (feature.feature_id,), feature.evidence_codes, "note"))
    return result


def _linear_feature_intents(feature):
    if not feature.anchors:
        return []
    xs, ys = [p.x for p in feature.anchors], [p.y for p in feature.anchors]
    return [
        _intent(f"{feature.feature_id}-length", DimensionKind.LENGTH, 80, _number(max(xs) - min(xs)), max(xs) - min(xs), feature.anchors, (feature.feature_id,), feature.evidence_codes, "bottom"),
        _intent(f"{feature.feature_id}-width", DimensionKind.WIDTH, 80, _number(max(ys) - min(ys)), max(ys) - min(ys), feature.anchors, (feature.feature_id,), feature.evidence_codes, "right"),
    ]


def _intent(intent_id, kind, priority, text, value, anchors, sources, evidence, band):
    return DimensionIntent(intent_id, kind, priority, text, value, tuple(anchors), tuple(sources), tuple(evidence), band)


def _deduplicate(intents):
    result, seen = [], set()
    for item in intents:
        key = (item.kind, item.text, item.anchors, item.source_feature_ids)
        if key not in seen:
            result.append(item)
            seen.add(key)
    return result


def _number(value):
    number = float(value)
    return str(int(number)) if number.is_integer() else f"{number:.3f}".rstrip("0").rstrip(".")
