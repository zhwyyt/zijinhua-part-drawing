import unittest
from dataclasses import replace
from pathlib import Path

from zijinhua_tekla.part_drawing.contracts import LocalFrame, Vector3D
from zijinhua_tekla.part_drawing.geometry_analyzer import analyze_plate_geometry
from zijinhua_tekla.part_drawing.snapshot_input import load_part_snapshot


FIXTURE = Path(__file__).parent / "fixtures" / "part_drawing" / "rectangular_plate.json"


class PartDrawingGeometryTests(unittest.TestCase):
    def test_projects_model_points_to_normalized_plate_coordinates(self):
        result = analyze_plate_geometry(load_part_snapshot(FIXTURE))
        self.assertFalse(result.issues)
        self.assertEqual((0.0, 0.0, 200.0, 100.0), result.geometry.bounds)
        self.assertAlmostEqual(20000.0, result.geometry.area)

    def test_rejects_open_outer_loop(self):
        snapshot = load_part_snapshot(FIXTURE)
        result = analyze_plate_geometry(replace(snapshot, outer_loop=snapshot.outer_loop[:-1]))
        self.assertIsNone(result.geometry)
        self.assertTrue(any(issue.code.value == "GEOMETRY_INVALID" for issue in result.issues))

    def test_rejects_non_orthonormal_local_frame(self):
        snapshot = load_part_snapshot(FIXTURE)
        invalid_frame = LocalFrame(
            snapshot.local_frame.origin,
            Vector3D(1.0, 0.0, 0.0),
            Vector3D(1.0, 0.0, 0.0),
            Vector3D(0.0, 0.0, 1.0),
        )
        result = analyze_plate_geometry(replace(snapshot, local_frame=invalid_frame))
        self.assertIsNone(result.geometry)
        self.assertTrue(any("orthonormal" in issue.message for issue in result.issues))

    def test_fingerprint_is_rotation_independent(self):
        snapshot = load_part_snapshot(FIXTURE)
        rotated_frame = replace(
            snapshot.local_frame,
            x_axis=Vector3D(0.0, 1.0, 0.0),
            y_axis=Vector3D(-1.0, 0.0, 0.0),
        )
        first = analyze_plate_geometry(snapshot).geometry
        rotated = analyze_plate_geometry(replace(snapshot, local_frame=rotated_frame)).geometry
        self.assertEqual(64, len(first.fingerprint))
        self.assertEqual(first.fingerprint, rotated.fingerprint)


if __name__ == "__main__":
    unittest.main()
