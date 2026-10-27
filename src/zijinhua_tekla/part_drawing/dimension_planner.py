from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .contracts import DrawingIssue, DrawingStatus, IssueCode
from .feature_recognizer import FeatureType, RecognizedFeature
from .geometry_analyzer import NormalizedPlateGeometry, Point2D


GRID_TOLERANCE_MM = 0.1


class Axis(str, Enum):
    X = "X"
    Y = "Y"


class PlanSide(str, Enum):
    TOP = "TOP"
    BOTTOM = "BOTTOM"
    LEFT = "LEFT"
    RIGHT = "RIGHT"


class PlanItemKind(str, Enum):
    FEATURE_CHAIN = "FEATURE_CHAIN"
    OVERALL = "OVERALL"


@dataclass(frozen=True)
class PlannedSegment:
    segment_id: str
    start: Point2D
    end: Point2D
    value_mm: float
    source_feature_ids: tuple[str, ...]
    evidence_codes: tuple[str, ...]


@dataclass(frozen=True)
class PlannedChain:
    chain_id: str
    kind: PlanItemKind
    axis: Axis
    side: PlanSide
    layer: int
    segments: tuple[PlannedSegment, ...]


@dataclass(frozen=True)
class PlannedCallout:
    callout_id: str
    center: Point2D
    diameter_mm: float
    display_text: str
    source_feature_ids: tuple[str, ...]
    evidence_codes: tuple[str, ...]


@dataclass(frozen=True)
class DimensionPlan:
    items: tuple[PlannedChain, ...]
    callouts: tuple[PlannedCallout, ...]
    passthrough_intents: tuple
    covered_intent_ids: tuple[str, ...]
    unresolved_intents: tuple
    source_intent_ids: tuple[str, ...]
    status: DrawingStatus
    issues: tuple[DrawingIssue, ...]


def plan_dimensions(
    geometry: NormalizedPlateGeometry,
    features: tuple[RecognizedFeature, ...],
    intents: tuple,
) -> DimensionPlan:
    holes = tuple(
        item for item in features if item.feature_type == FeatureType.ROUND_HOLE
    )
    source_ids = tuple(item.intent_id for item in intents)
    hole_intents = tuple(item for item in intents if _is_hole_intent(item, holes))
    overall_intents = tuple(item for item in intents if item.intent_id in {"overall-x", "overall-y"})
    passthrough = tuple(
        item for item in intents if item not in hole_intents and item not in overall_intents
    )
    overall_chains = _overall_chains(geometry)
    overall_ids = tuple(item.intent_id for item in overall_intents)
    grid = _complete_round_hole_grid(holes)
    if grid is None:
        if len(holes) > 1:
            issue = DrawingIssue(
                IssueCode.DIMENSION_PLAN_INCOMPLETE,
                "round holes do not form one complete rectangular grid",
                False,
                evidence=tuple(item.feature_id for item in holes),
            )
            return _checked_plan(DimensionPlan(
                overall_chains,
                (),
                passthrough,
                overall_ids,
                hole_intents,
                source_ids,
                DrawingStatus.REVIEW_REQUIRED,
                (issue,),
            ))
        return _single_or_no_hole_plan(
            geometry,
            holes,
            hole_intents,
            passthrough,
            overall_intents,
            source_ids,
        )

    x_values, y_values, diameter = grid
    hole_ids = tuple(item.feature_id for item in holes)
    feature_chains = (
        _feature_chain(
            Axis.X,
            PlanSide.BOTTOM,
            geometry.bounds[2],
            x_values,
            hole_ids,
        ),
        _feature_chain(
            Axis.Y,
            PlanSide.LEFT,
            geometry.bounds[3],
            y_values,
            hole_ids,
        ),
    )
    callout = PlannedCallout(
        "hole-grid-diameter",
        holes[0].anchors[0],
        diameter,
        f"{len(holes)}\u03a6{_number(diameter)}",
        hole_ids,
        ("COMPLETE_RECTANGULAR_HOLE_GRID", "EQUAL_DIAMETER"),
    )
    covered = overall_ids + tuple(item.intent_id for item in hole_intents)
    return _checked_plan(DimensionPlan(
        feature_chains + overall_chains,
        (callout,),
        passthrough,
        covered,
        (),
        source_ids,
        DrawingStatus.OK,
        (),
    ))


def _overall_chains(geometry):
    width, height = geometry.bounds[2], geometry.bounds[3]
    return (
        PlannedChain(
            "overall-x",
            PlanItemKind.OVERALL,
            Axis.X,
            PlanSide.BOTTOM,
            2,
            (
                PlannedSegment(
                    "overall-x",
                    Point2D(0.0, 0.0),
                    Point2D(width, 0.0),
                    width,
                    (),
                    ("NORMALIZED_BOUNDS",),
                ),
            ),
        ),
        PlannedChain(
            "overall-y",
            PlanItemKind.OVERALL,
            Axis.Y,
            PlanSide.LEFT,
            2,
            (
                PlannedSegment(
                    "overall-y",
                    Point2D(0.0, 0.0),
                    Point2D(0.0, height),
                    height,
                    (),
                    ("NORMALIZED_BOUNDS",),
                ),
            ),
        ),
    )


def _single_or_no_hole_plan(
    geometry,
    holes,
    hole_intents,
    passthrough,
    overall_intents,
    source_ids,
):
    chains = _overall_chains(geometry)
    covered = tuple(item.intent_id for item in overall_intents)
    callouts = ()
    if holes:
        hole = holes[0]
        center = hole.anchors[0]
        chains = (
            PlannedChain(
                "single-hole-x",
                PlanItemKind.FEATURE_CHAIN,
                Axis.X,
                PlanSide.BOTTOM,
                1,
                (
                    PlannedSegment(
                        "single-hole-x",
                        Point2D(0.0, center.y),
                        center,
                        center.x,
                        (hole.feature_id,),
                        ("SINGLE_HOLE_DATUM",),
                    ),
                ),
            ),
            PlannedChain(
                "single-hole-y",
                PlanItemKind.FEATURE_CHAIN,
                Axis.Y,
                PlanSide.LEFT,
                1,
                (
                    PlannedSegment(
                        "single-hole-y",
                        Point2D(center.x, 0.0),
                        center,
                        center.y,
                        (hole.feature_id,),
                        ("SINGLE_HOLE_DATUM",),
                    ),
                ),
            ),
        ) + chains
        diameter = float(hole.parameters["diameter"])
        callouts = (
            PlannedCallout(
                "single-hole-diameter",
                center,
                diameter,
                f"\u03a6{_number(diameter)}",
                (hole.feature_id,),
                ("TEKLA_HOLE_OPERATION",),
            ),
        )
        covered += tuple(item.intent_id for item in hole_intents)
    return _checked_plan(DimensionPlan(
        chains,
        callouts,
        passthrough,
        covered,
        (),
        source_ids,
        DrawingStatus.OK,
        (),
    ))


def _checked_plan(plan):
    accounted = set(plan.covered_intent_ids)
    accounted.update(item.intent_id for item in plan.passthrough_intents)
    accounted.update(item.intent_id for item in plan.unresolved_intents)
    if accounted != set(plan.source_intent_ids):
        raise AssertionError("dimension intent accounting mismatch")
    return plan


def _complete_round_hole_grid(holes):
    if len(holes) < 2:
        return None
    diameters = [float(item.parameters["diameter"]) for item in holes]
    if max(diameters) - min(diameters) > GRID_TOLERANCE_MM:
        return None
    x_values = _clustered(item.anchors[0].x for item in holes)
    y_values = _clustered(item.anchors[0].y for item in holes)
    if len(x_values) < 2 and len(y_values) < 2:
        return None
    occupied = {
        (_nearest(item.anchors[0].x, x_values), _nearest(item.anchors[0].y, y_values))
        for item in holes
    }
    expected = {(x, y) for x in x_values for y in y_values}
    if occupied != expected or len(occupied) != len(holes):
        return None
    return x_values, y_values, diameters[0]


def _clustered(values):
    result: list[float] = []
    for value in sorted(float(item) for item in values):
        if not result or value - result[-1] > GRID_TOLERANCE_MM:
            result.append(value)
    return tuple(result)


def _nearest(value, candidates):
    return min(candidates, key=lambda item: abs(item - value))


def _feature_chain(axis, side, extent, coordinates, hole_ids):
    values = (0.0,) + tuple(coordinates) + (float(extent),)
    segments = []
    for index, (start_value, end_value) in enumerate(zip(values, values[1:]), 1):
        if end_value - start_value <= GRID_TOLERANCE_MM:
            continue
        if axis == Axis.X:
            start, end = Point2D(start_value, 0.0), Point2D(end_value, 0.0)
        else:
            start, end = Point2D(0.0, start_value), Point2D(0.0, end_value)
        segments.append(
            PlannedSegment(
                f"hole-grid-{axis.value.lower()}-{index}",
                start,
                end,
                end_value - start_value,
                hole_ids,
                ("COMPLETE_RECTANGULAR_HOLE_GRID",),
            )
        )
    return PlannedChain(
        f"hole-grid-{axis.value.lower()}",
        PlanItemKind.FEATURE_CHAIN,
        axis,
        side,
        1,
        tuple(segments),
    )


def _is_hole_intent(intent, holes):
    hole_ids = {item.feature_id for item in holes}
    return bool(hole_ids.intersection(intent.source_feature_ids))


def _number(value):
    return str(int(value)) if float(value).is_integer() else f"{value:.3f}".rstrip("0").rstrip(".")
