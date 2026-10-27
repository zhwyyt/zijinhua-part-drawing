import unittest
from dataclasses import replace
from pathlib import Path

from zijinhua_tekla.part_drawing.contracts import HoleSnapshot, Point3D, Vector3D
from zijinhua_tekla.part_drawing.quality import group_part_snapshots
from zijinhua_tekla.part_drawing.snapshot_input import load_part_snapshot


FIXTURE = Path(__file__).parent / "fixtures" / "part_drawing" / "rectangular_plate.json"


class PartDrawingQualityTests(unittest.TestCase):
    def snapshot(self, part_id: str, position: str = "P-1001", thickness: float = 10.0):
        return replace(
            load_part_snapshot(FIXTURE),
            part_id=part_id,
            part_position=position,
            thickness=thickness,
        )

    def test_groups_equal_geometry_and_counts_entities(self):
        group = group_part_snapshots([self.snapshot("1"), self.snapshot("2")])[0]
        self.assertEqual("OK", group.status.value)
        self.assertEqual(2, group.quantity)
        self.assertFalse(group.issues)

    def test_rejects_same_position_with_different_thickness(self):
        group = group_part_snapshots([
            self.snapshot("1", thickness=10.0),
            self.snapshot("2", thickness=12.0),
        ])[0]
        self.assertEqual("REJECTED", group.status.value)
        self.assertEqual("PART_POSITION_CONFLICT", group.issues[0].code.value)

    def test_never_merges_different_part_positions(self):
        groups = group_part_snapshots([
            self.snapshot("1", position="P1"),
            self.snapshot("2", position="P2"),
        ])
        self.assertEqual(2, len(groups))

    def test_rejects_same_position_with_different_holes(self):
        first = replace(
            self.snapshot("1"),
            holes=(HoleSnapshot("H1", "ROUND", Point3D(60.0, 70.0, 30.0), diameter=22.0),),
        )
        second = replace(
            self.snapshot("2"),
            holes=(HoleSnapshot("H1", "ROUND", Point3D(80.0, 70.0, 30.0), diameter=22.0),),
        )
        group = group_part_snapshots([first, second])[0]
        self.assertEqual("REJECTED", group.status.value)
        self.assertEqual("PART_POSITION_CONFLICT", group.issues[0].code.value)

    def test_groups_rotated_equivalent_geometry_with_same_hole(self):
        first = replace(
            self.snapshot("1"),
            holes=(HoleSnapshot("H1", "ROUND", Point3D(60.0, 70.0, 30.0), diameter=22.0),),
        )
        rotated_frame = replace(
            first.local_frame,
            x_axis=Vector3D(0.0, 1.0, 0.0),
            y_axis=Vector3D(-1.0, 0.0, 0.0),
        )
        second = replace(first, part_id="2", local_frame=rotated_frame)
        group = group_part_snapshots([first, second])[0]
        self.assertEqual("OK", group.status.value)


if __name__ == "__main__":
    unittest.main()
