import unittest
from dataclasses import replace
import math
from pathlib import Path

from zijinhua_tekla.part_drawing.contracts import HoleSnapshot, Point3D
from zijinhua_tekla.part_drawing.dimension_generator import generate_dimension_intents
from zijinhua_tekla.part_drawing.dimension_optimizer import optimize_dimension_layout
from zijinhua_tekla.part_drawing.dimension_planner import plan_dimensions
from zijinhua_tekla.part_drawing.feature_recognizer import recognize_plate_features
from zijinhua_tekla.part_drawing.geometry_analyzer import Point2D, analyze_plate_geometry
from zijinhua_tekla.part_drawing.snapshot_input import load_part_snapshot


FIXTURE = Path(__file__).parent / "fixtures" / "part_drawing" / "rectangular_plate.json"


class PartDrawingLayoutTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = load_part_snapshot(FIXTURE)
        self.geometry = analyze_plate_geometry(self.snapshot).geometry
        self.intents = generate_dimension_intents(self.snapshot, self.geometry, ()).intents

    def test_places_rectangle_dimensions_without_collision(self):
        layout = optimize_dimension_layout(self.snapshot, self.geometry, self.intents, quantity=2)
        self.assertEqual("OK", layout.status.value)
        self.assertEqual(0, layout.collision_count)
        self.assertFalse(layout.unplaced_intents)
        self.assertIn(layout.scale, (1.0, 0.5, 0.2, 0.1, 0.05))

    def test_places_rectangle_dimensions_as_semantic_linear_dimensions(self):
        layout = optimize_dimension_layout(self.snapshot, self.geometry, self.intents, quantity=2)
        by_id = {item.dimension_id: item for item in layout.placed_dimensions}
        self.assertEqual("LINEAR_HORIZONTAL", by_id["overall-x"].kind.value)
        self.assertEqual("LINEAR_VERTICAL", by_id["overall-y"].kind.value)
        self.assertEqual(200.0, by_id["overall-x"].measured_value_mm)
        self.assertEqual(100.0, by_id["overall-y"].measured_value_mm)
        self.assertEqual(2, len(by_id["overall-x"].source_points))
        self.assertNotEqual(
            by_id["overall-x"].source_points[0],
            by_id["overall-x"].source_points[1],
        )

    def test_semantic_dimension_uses_cad_style_display_precision(self):
        intent = replace(
            self.intents[0],
            model_value=243.174,
            text="243.174",
        )
        layout = optimize_dimension_layout(
            self.snapshot,
            self.geometry,
            (intent,),
            quantity=1,
        )
        self.assertEqual("243", layout.placed_dimensions[0].display_text)
        self.assertEqual(243.174, layout.placed_dimensions[0].measured_value_mm)

    def test_places_hole_diameter_as_semantic_diameter_dimension(self):
        snapshot = replace(
            self.snapshot,
            holes=(HoleSnapshot("H1", "ROUND", Point3D(60, 70, 30), diameter=22),),
        )
        geometry = analyze_plate_geometry(snapshot).geometry
        features = recognize_plate_features(snapshot, geometry).features
        intents = generate_dimension_intents(snapshot, geometry, features).intents
        layout = optimize_dimension_layout(snapshot, geometry, intents, quantity=1)
        diameter = next(
            item for item in layout.placed_dimensions if item.dimension_id == "hole-H1-dia"
        )
        self.assertEqual("DIAMETER", diameter.kind.value)
        self.assertEqual(22.0, diameter.measured_value_mm)
        self.assertEqual(11.0 * layout.scale, diameter.paper_radius)
        center = diameter.source_points[0]
        leader_distance = math.hypot(
            diameter.dimension_line_point[0] - center[0],
            diameter.dimension_line_point[1] - center[1],
        )
        self.assertLessEqual(leader_distance, diameter.paper_radius + 25.0)

    def test_dense_intents_request_review_instead_of_overlap(self):
        dense = tuple(replace(self.intents[0], intent_id=f"dense-{index}", text=str(index)) for index in range(80))
        layout = optimize_dimension_layout(self.snapshot, self.geometry, dense, quantity=1)
        self.assertEqual("REVIEW_REQUIRED", layout.status.value)
        self.assertGreater(len(layout.unplaced_intents), 0)
        self.assertLessEqual(len(layout.placed_dimensions) + len(layout.annotation_texts), 26)

    def test_bottom_dimension_layers_do_not_enter_title_block(self):
        bottom_intents = tuple(
            replace(
                self.intents[0],
                intent_id=f"bottom-{index}",
                text=str(20 + index),
                model_value=float(20 + index),
                anchors=(Point2D(0, 20 + index), Point2D(20 + index, 20 + index)),
                preferred_band="bottom",
            )
            for index in range(6)
        )
        layout = optimize_dimension_layout(
            self.snapshot,
            self.geometry,
            bottom_intents,
            quantity=1,
        )
        self.assertTrue(layout.unplaced_intents)
        self.assertTrue(
            all(item.text_box.bottom < 242.0 for item in layout.placed_dimensions)
        )

    def test_planned_feature_chain_is_inside_overall_chain(self):
        snapshot = replace(
            self.snapshot,
            holes=tuple(
                HoleSnapshot(
                    f"H{index}",
                    "ROUND",
                    Point3D(10 + x, 20 + y, 30),
                    diameter=22,
                )
                for index, (x, y) in enumerate(
                    (
                        (20, 25),
                        (100, 25),
                        (180, 25),
                        (20, 75),
                        (100, 75),
                        (180, 75),
                    ),
                    1,
                )
            ),
        )
        geometry = analyze_plate_geometry(snapshot).geometry
        features = recognize_plate_features(snapshot, geometry).features
        intents = generate_dimension_intents(snapshot, geometry, features).intents
        plan = plan_dimensions(geometry, features, intents)

        layout = optimize_dimension_layout(snapshot, geometry, plan, quantity=1)
        by_id = {item.dimension_id: item for item in layout.placed_dimensions}

        self.assertLess(
            by_id["hole-grid-x-1"].dimension_line_point[1],
            by_id["overall-x"].dimension_line_point[1],
        )
        self.assertGreater(
            by_id["hole-grid-y-1"].dimension_line_point[0],
            by_id["overall-y"].dimension_line_point[0],
        )
        self.assertFalse(layout.unplaced_intents)


if __name__ == "__main__":
    unittest.main()
