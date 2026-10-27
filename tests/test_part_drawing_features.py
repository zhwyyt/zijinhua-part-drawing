import unittest
from dataclasses import replace
from pathlib import Path

from zijinhua_tekla.part_drawing.contracts import (
    ContourSegmentSnapshot,
    HoleSnapshot,
    Point3D,
    SolidEdgeSnapshot,
    SolidFaceSnapshot,
    SolidGeometrySnapshot,
    SolidLoopSnapshot,
    Vector3D,
)
from zijinhua_tekla.part_drawing.feature_recognizer import recognize_plate_features
from zijinhua_tekla.part_drawing.geometry_analyzer import analyze_plate_geometry
from zijinhua_tekla.part_drawing.snapshot_input import load_part_snapshot


FIXTURE = Path(__file__).parent / "fixtures" / "part_drawing" / "rectangular_plate.json"


class PartDrawingFeatureTests(unittest.TestCase):
    def setUp(self):
        self.base = load_part_snapshot(FIXTURE)

    def recognize(self, snapshot):
        analysis = analyze_plate_geometry(snapshot)
        self.assertIsNotNone(analysis.geometry, analysis.issues)
        return recognize_plate_features(snapshot, analysis.geometry)

    def round_hole(self, hole_id, x, y):
        return HoleSnapshot(hole_id, "ROUND", Point3D(x, y, 30.0), diameter=22.0)

    def line_loop(self, points):
        values = [Point3D(x, y, 30.0) for x, y in points]
        return tuple(ContourSegmentSnapshot("LINE", start, end) for start, end in zip(values, values[1:] + values[:1]))

    def arc_loop(self, centers_equal=True):
        points = [Point3D(90, 70, 30), Point3D(110, 90, 30), Point3D(130, 70, 30), Point3D(110, 50, 30)]
        center = Point3D(110, 70, 30)
        return tuple(
            ContourSegmentSnapshot(
                "ARC", points[index], points[(index + 1) % 4],
                center=center if centers_equal or index != 2 else Point3D(111, 70, 30),
                clockwise=True,
            )
            for index in range(4)
        )

    def test_recognizes_explicit_round_hole_and_slot(self):
        snapshot = replace(self.base, holes=(
            self.round_hole("H1", 60, 70),
            HoleSnapshot("S1", "SLOT", Point3D(150, 70, 30), length=40, width=18),
        ))
        result = self.recognize(snapshot)
        self.assertEqual(["ROUND_HOLE", "SLOT"], [item.feature_type.value for item in result.features])

    def test_groups_equal_round_holes_on_one_axis(self):
        snapshot = replace(self.base, holes=tuple(self.round_hole(f"H{i}", x, 70) for i, x in enumerate((60, 110, 160), 1)))
        result = self.recognize(snapshot)
        group = next(item for item in result.features if item.feature_type.value == "HOLE_GROUP")
        self.assertEqual(3, group.parameters["count"])
        self.assertEqual(50.0, group.parameters["spacing"])

    def test_recognizes_polygon_cutout(self):
        loop = self.line_loop(((80, 55), (120, 55), (120, 85), (80, 85)))
        result = self.recognize(replace(self.base, inner_loops=(loop,)))
        self.assertIn("POLYGON_CUTOUT", [item.feature_type.value for item in result.features])

    def test_recognizes_chamfer_and_notch_without_inferring_bevel(self):
        chamfer = self.line_loop(((10, 20), (190, 20), (210, 40), (210, 120), (10, 120)))
        notch = self.line_loop(((10, 20), (90, 20), (90, 40), (130, 40), (130, 20), (210, 20), (210, 120), (10, 120)))
        types = [item.feature_type.value for item in self.recognize(replace(self.base, outer_loop=chamfer)).features]
        notch_types = [item.feature_type.value for item in self.recognize(replace(self.base, outer_loop=notch)).features]
        self.assertIn("CHAMFER", types)
        self.assertIn("NOTCH", notch_types)
        self.assertNotIn("BEVEL", types + notch_types)

    def test_recognizes_consistent_arc_cutout_and_reviews_ambiguous_arc(self):
        valid = self.recognize(replace(self.base, inner_loops=(self.arc_loop(),)))
        ambiguous = self.recognize(replace(self.base, inner_loops=(self.arc_loop(False),)))
        self.assertIn("ARC_CUTOUT", [item.feature_type.value for item in valid.features])
        self.assertEqual("REVIEW_REQUIRED", ambiguous.status.value)

    def test_recognizes_single_edge_setback_as_evidence_backed_bevel(self):
        snapshot = self.v2_with_principal_faces(
            ((10, 20), (210, 20), (210, 120), (10, 120)),
            ((20, 20), (210, 20), (210, 120), (20, 120)),
        )

        result = self.recognize(snapshot)

        bevel = next(item for item in result.features if item.feature_type.value == "BEVEL")
        self.assertEqual(10.0, bevel.parameters["setback"])
        self.assertEqual(45.0, bevel.parameters["angleDeg"])
        self.assertEqual("PROVEN", bevel.parameters["angle_status"])
        self.assertEqual((10.0, 0.0), (bevel.anchors[0].x, bevel.anchors[0].y))
        self.assertIn("F2", bevel.source_ids)

    def test_does_not_infer_bevel_from_congruent_principal_faces(self):
        points = ((10, 20), (210, 20), (210, 120), (10, 120))
        result = self.recognize(self.v2_with_principal_faces(points, points))

        self.assertNotIn("BEVEL", [item.feature_type.value for item in result.features])
        self.assertEqual("OK", result.status.value)

    def test_reviews_principal_face_topology_mismatch_instead_of_guessing_bevel(self):
        snapshot = self.v2_with_principal_faces(
            ((10, 20), (210, 20), (210, 120), (10, 120)),
            ((20, 20), (210, 20), (210, 120)),
        )

        result = self.recognize(snapshot)

        self.assertNotIn("BEVEL", [item.feature_type.value for item in result.features])
        self.assertEqual("REVIEW_REQUIRED", result.status.value)

    def test_reviews_multiple_principal_face_offsets_instead_of_guessing_bevel(self):
        snapshot = self.v2_with_principal_faces(
            ((10, 20), (210, 20), (210, 120), (10, 120)),
            ((20, 30), (200, 30), (200, 110), (20, 110)),
        )

        result = self.recognize(snapshot)

        self.assertNotIn("BEVEL", [item.feature_type.value for item in result.features])
        self.assertEqual("REVIEW_REQUIRED", result.status.value)

    def v2_with_principal_faces(self, top_xy, bottom_xy):
        top = tuple(Point3D(x, y, 30.0) for x, y in top_xy)
        bottom = tuple(Point3D(x, y, 20.0) for x, y in bottom_xy)
        edges = []
        faces = []
        edge_index = 0
        for face_id, normal, points in (
            ("F1", Vector3D(0, 0, 1), top),
            ("F2", Vector3D(0, 0, -1), bottom),
        ):
            edge_ids = []
            for index, start in enumerate(points):
                edge_index += 1
                edge_id = f"E{edge_index}"
                edge_ids.append(edge_id)
                edges.append(SolidEdgeSnapshot(
                    edge_id=edge_id,
                    curve_type="LINE",
                    start=start,
                    end=points[(index + 1) % len(points)],
                    adjacent_face_ids=(face_id,),
                    evidence_status="PROVEN",
                ))
            faces.append(SolidFaceSnapshot(
                face_id=face_id,
                surface_type="PLANE",
                normal=normal,
                relation_to_part_frame=(
                    "NORMAL_POSITIVE" if face_id == "F1" else "NORMAL_NEGATIVE"
                ),
                loops=(SolidLoopSnapshot(
                    loop_id=f"L{face_id[1:]}",
                    ordered_edge_ids=tuple(edge_ids),
                    ordered_vertices=points,
                ),),
            ))
        return replace(
            self.base,
            schema_version="partDrawingSnapshot.v2",
            exporter_version="test-fixture.v2",
            part_form="FLAT_PLATE",
            final_geometry=SolidGeometrySnapshot(
                edges=tuple(edges),
                faces=tuple(faces),
                principal_face_ids=("F1", "F2"),
            ),
        )


if __name__ == "__main__":
    unittest.main()
