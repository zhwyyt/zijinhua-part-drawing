import unittest
from dataclasses import replace
from pathlib import Path

from zijinhua_tekla.part_drawing.contracts import HoleSnapshot, Point3D
from zijinhua_tekla.part_drawing.dimension_generator import generate_dimension_intents
from zijinhua_tekla.part_drawing.dimension_planner import Axis, PlanItemKind, plan_dimensions
from zijinhua_tekla.part_drawing.feature_recognizer import recognize_plate_features
from zijinhua_tekla.part_drawing.geometry_analyzer import analyze_plate_geometry
from zijinhua_tekla.part_drawing.snapshot_input import load_part_snapshot


FIXTURE = Path(__file__).parent / "fixtures" / "part_drawing" / "rectangular_plate.json"


def _planned(points):
    base = load_part_snapshot(FIXTURE)
    holes = tuple(
        HoleSnapshot(
            f"H{index}",
            "ROUND",
            Point3D(10 + x, 20 + y, 30),
            diameter=22,
        )
        for index, (x, y) in enumerate(points, 1)
    )
    snapshot = replace(base, holes=holes)
    geometry = analyze_plate_geometry(snapshot).geometry
    features = recognize_plate_features(snapshot, geometry).features
    intents = generate_dimension_intents(snapshot, geometry, features).intents
    return plan_dimensions(geometry, features, intents)


class PartDrawingDimensionPlannerTests(unittest.TestCase):
    def test_complete_three_by_two_grid_becomes_unique_axis_chains(self):
        plan = _planned(
            ((20, 25), (100, 25), (180, 25), (20, 75), (100, 75), (180, 75))
        )
        feature_chains = [
            item for item in plan.items if item.kind == PlanItemKind.FEATURE_CHAIN
        ]
        by_axis = {item.axis: item for item in feature_chains}

        self.assertEqual(
            (20.0, 80.0, 80.0, 20.0),
            tuple(segment.value_mm for segment in by_axis[Axis.X].segments),
        )
        self.assertEqual(
            (25.0, 50.0, 25.0),
            tuple(segment.value_mm for segment in by_axis[Axis.Y].segments),
        )
        self.assertEqual("6\u03a622", plan.callouts[0].display_text)
        self.assertFalse(plan.unresolved_intents)

    def test_missing_cartesian_cell_is_not_planned_as_complete_grid(self):
        plan = _planned(((20, 25), (100, 25), (180, 25), (20, 75), (100, 75)))

        self.assertFalse(
            any(item.kind == PlanItemKind.FEATURE_CHAIN for item in plan.items)
        )
        self.assertEqual("REVIEW_REQUIRED", plan.status.value)
        self.assertTrue(plan.unresolved_intents)

    def test_single_row_hole_group_keeps_edge_and_spacing_chain(self):
        plan = _planned(((20, 50), (100, 50), (180, 50)))
        feature_chains = [
            item for item in plan.items if item.kind == PlanItemKind.FEATURE_CHAIN
        ]
        by_axis = {item.axis: item for item in feature_chains}

        self.assertEqual(
            (20.0, 80.0, 80.0, 20.0),
            tuple(segment.value_mm for segment in by_axis[Axis.X].segments),
        )
        self.assertEqual(
            (50.0, 50.0),
            tuple(segment.value_mm for segment in by_axis[Axis.Y].segments),
        )
        self.assertEqual("OK", plan.status.value)

    def test_overall_dimensions_are_outside_feature_chains(self):
        plan = _planned(
            ((20, 25), (100, 25), (180, 25), (20, 75), (100, 75), (180, 75))
        )
        by_axis = {}
        for item in plan.items:
            by_axis.setdefault(item.axis, []).append(item)

        for axis_items in by_axis.values():
            feature = next(
                item for item in axis_items if item.kind == PlanItemKind.FEATURE_CHAIN
            )
            overall = next(
                item for item in axis_items if item.kind == PlanItemKind.OVERALL
            )
            self.assertGreater(overall.layer, feature.layer)

    def test_every_raw_intent_is_accounted_for(self):
        plan = _planned(
            ((20, 25), (100, 25), (180, 25), (20, 75), (100, 75), (180, 75))
        )
        accounted = set(plan.covered_intent_ids)
        accounted.update(item.intent_id for item in plan.passthrough_intents)
        accounted.update(item.intent_id for item in plan.unresolved_intents)

        self.assertEqual(set(plan.source_intent_ids), accounted)

    def test_single_hole_keeps_two_coordinates_and_one_diameter(self):
        plan = _planned(((60, 70),))

        feature_chains = [
            item for item in plan.items if item.kind == PlanItemKind.FEATURE_CHAIN
        ]
        self.assertEqual(2, len(feature_chains))
        self.assertEqual("\u03a622", plan.callouts[0].display_text)
        self.assertFalse(plan.unresolved_intents)


if __name__ == "__main__":
    unittest.main()
