import json
import unittest
from pathlib import Path

from zijinhua_tekla.part_drawing.snapshot_input import (
    load_part_snapshot,
    parse_part_snapshot,
    safe_part_position,
)


FIXTURE = Path(__file__).parent / "fixtures" / "part_drawing" / "rectangular_plate.json"
V2_FIXTURE = Path(__file__).parent / "fixtures" / "part_drawing" / "v2_plate_geometry.json"
CSHARP_V2_FIXTURE = (
    Path(__file__).parent / "fixtures" / "part_drawing" / "v2_csharp_exporter.json"
)


class PartDrawingContractTests(unittest.TestCase):
    def test_loads_versioned_flat_plate_snapshot(self):
        snapshot = load_part_snapshot(FIXTURE)
        self.assertEqual("partDrawingSnapshot.v1", snapshot.schema_version)
        self.assertEqual("P-1001", snapshot.part_position)
        self.assertEqual(10.0, snapshot.thickness)
        self.assertEqual(4, len(snapshot.outer_loop))

    def test_rejects_missing_part_position(self):
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        data["part"]["partPosition"] = ""
        with self.assertRaisesRegex(ValueError, "partPosition"):
            parse_part_snapshot(data)

    def test_rejects_unknown_schema(self):
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        data["schemaVersion"] = "partDrawingSnapshot.v3"
        with self.assertRaisesRegex(ValueError, "schemaVersion"):
            parse_part_snapshot(data)

    def test_loads_v2_complete_geometry_and_operation_evidence(self):
        snapshot = load_part_snapshot(V2_FIXTURE)

        self.assertEqual("partDrawingSnapshot.v2", snapshot.schema_version)
        self.assertEqual("FLAT_PLATE", snapshot.part_form)
        self.assertEqual(("F1", "F2"), snapshot.final_geometry.principal_face_ids)
        self.assertEqual(2, len(snapshot.final_geometry.faces))
        self.assertEqual("UNRESOLVED", snapshot.final_geometry.edges[3].evidence_status)
        self.assertEqual("BOOLEAN_CUT", snapshot.operations[0].operation_type)
        self.assertEqual(1, len(snapshot.operations[0].operative_geometry.vertices))
        self.assertEqual("CURVE_PARAMETERS_UNRESOLVED", snapshot.quality_issues[0].code)

    def test_loads_exact_csharp_v2_serialization_without_field_conversion(self):
        snapshot = load_part_snapshot(CSHARP_V2_FIXTURE)

        self.assertEqual("partDrawingSnapshot.v2", snapshot.schema_version)
        self.assertEqual(("F1", "F2"), snapshot.final_geometry.principal_face_ids)
        self.assertEqual("UNRESOLVED", snapshot.final_geometry.edges[3].evidence_status)
        self.assertEqual("BOOLEAN_CUT", snapshot.operations[0].operation_type)
        self.assertGreaterEqual(len(snapshot.operations[0].operative_geometry.faces), 2)
        self.assertIn(
            "TEKLA_BOOLEAN_OPERATIVE_SOLID",
            snapshot.operations[0].source_evidence,
        )

    def test_sanitizes_file_name_without_changing_business_position(self):
        self.assertEqual("P_1001_2", safe_part_position("P/1001:2"))


if __name__ == "__main__":
    unittest.main()
