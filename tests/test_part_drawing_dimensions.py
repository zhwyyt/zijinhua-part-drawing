import unittest
from dataclasses import replace
from pathlib import Path

from zijinhua_tekla.part_drawing.contracts import HoleSnapshot, Point3D
from zijinhua_tekla.part_drawing.dimension_generator import generate_dimension_intents
from zijinhua_tekla.part_drawing.feature_recognizer import FeatureType, RecognizedFeature, recognize_plate_features
from zijinhua_tekla.part_drawing.geometry_analyzer import Point2D, analyze_plate_geometry
from zijinhua_tekla.part_drawing.snapshot_input import load_part_snapshot


FIXTURE = Path(__file__).parent / "fixtures" / "part_drawing" / "rectangular_plate.json"


class PartDrawingDimensionTests(unittest.TestCase):
    def setUp(self):
        base = load_part_snapshot(FIXTURE)
        holes = tuple(HoleSnapshot(f"H{i}", "ROUND", Point3D(x, 70, 30), diameter=22) for i, x in enumerate((60, 110, 160), 1))
        self.snapshot = replace(base, holes=holes)
        self.geometry = analyze_plate_geometry(self.snapshot).geometry
        self.features = recognize_plate_features(self.snapshot, self.geometry).features

    def test_generates_required_overall_and_thickness_dimensions(self):
        result = generate_dimension_intents(self.snapshot, self.geometry, ())
        by_id = {item.intent_id: item for item in result.intents}
        self.assertEqual(200.0, by_id["overall-x"].model_value)
        self.assertEqual(100.0, by_id["overall-y"].model_value)
        self.assertEqual(10.0, by_id["thickness"].model_value)

    def test_round_hole_uses_diameter_and_two_datum_coordinates(self):
        hole = next(item for item in self.features if item.feature_type == FeatureType.ROUND_HOLE)
        ids = {item.intent_id for item in generate_dimension_intents(self.snapshot, self.geometry, (hole,)).intents}
        self.assertTrue({"hole-H1-dia", "hole-H1-x", "hole-H1-y"}.issubset(ids))

    def test_hole_group_uses_first_hole_spacing_and_count(self):
        texts = {item.text for item in generate_dimension_intents(self.snapshot, self.geometry, self.features).intents}
        self.assertIn("3xDIA22 @50", texts)

    def test_generates_slot_and_arc_cutout_dimensions(self):
        slot = RecognizedFeature("S1", FeatureType.SLOT, (Point2D(100, 50),), {"length": 40.0, "width": 18.0, "angle_deg": 0.0}, ("S1",), ("TEKLA_SLOT_OPERATION",), 1.0)
        arc = RecognizedFeature("A1", FeatureType.ARC_CUTOUT, (Point2D(40, 40),), {"radius": 20.0}, ("A1",), ("CLOSED_ARC_LOOP",), 1.0)
        kinds = {item.kind.value for item in generate_dimension_intents(self.snapshot, self.geometry, (slot, arc)).intents}
        self.assertTrue({"LENGTH", "WIDTH", "RADIUS"}.issubset(kinds))


if __name__ == "__main__":
    unittest.main()
