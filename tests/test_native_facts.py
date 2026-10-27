import copy
import json
import math
from pathlib import Path
import tempfile
import unittest

from zijinhua_tekla.cli import main
from zijinhua_tekla.part_drawing.native_facts import build_native_facts


def read(value):
    return {"status": "READ", "source": "synthetic-test-api", "value": value}


def unknown():
    return {"status": "UNAVAILABLE", "source": "synthetic-test-api", "value": None, "reason": "not exposed"}


def fixture():
    # Synthetic evidence only. Coordinates deliberately retain more than six decimal places.
    a, b = [1.0000000000000002, 0, 0], [20, 0, 0]
    edge = {"id": "E0", "apiType": "EDGE_TYPE_NORMAL", "apiTypeCode": 1,
            "start": a, "end": b, "curveParameters": unknown()}
    face = {"id": "F0", "normal": read([0, 0, 1]), "surfaceType": unknown(),
            "loops": read([{"id": "L0", "vertices": read([a, b])}])}
    solid = {"bounds": read({"min": a, "max": b}), "edges": read([edge]), "faces": read([face])}
    properties = {k: read(v) for k, v in {"BoltStandard": "B", "BoltSize": 20, "Tolerance": 2,
                  "SlottedHoleX": 59, "SlottedHoleY": 0, "HoleType": {"name": "HOLE_TYPE_SLOTTED", "code": 1},
                  "RotateSlots": {"name": "ROTATE_SLOTS_PARALLEL", "code": 2}, "Bolt": False,
                  **{f"Hole{i}": False for i in range(1, 6)}}.items()}
    bolt = {"id": 20, "properties": properties, "positions": read([a, b]),
            "coordinateSystem": read({"origin": a, "axisX": [1, 0, 0], "axisY": [0, 1, 0]}),
            "targets": {"partToBoltTo": read({"id": 10}), "partToBeBolted": read({"id": 10}), "otherParts": read([])},
            "solidWithHighAccuracy": read(solid)}
    part = {"id": 10, "runtimeType": "Tekla.Structures.Model.ContourPlate", "reports": {"PART_POS": read("TEST-1")},
            "coordinateSystem": bolt["coordinateSystem"], "modelingContour": read(read([])), "finalSolid": read(solid),
            "boltGroups": read([bolt]), "operations": read([]), "children": read([]), "readStatus": "COMPLETE"}
    plane = read({"origin": [0, 0, 0], "axisX": [1, 0, 0], "axisY": [0, 1, 0], "axisZ": [0, 0, 1]})
    return copy.deepcopy({"schemaVersion": "teklaNativeEvidence.v1", "coordinateSpace": "GLOBAL", "readerVersion": "test",
                          "sdkVersion": "test", "model": read({"name": "synthetic", "path": "synthetic"}),
                          "connectionStatus": "CONNECTED", "selection": [{"requestedId": 10, "resolved": {"id": 10}}],
                          "parts": [part], "errors": [], "readStatus": "COMPLETE",
                          "workPlaneBefore": plane, "workPlaneRestore": plane})


class NativeFactTests(unittest.TestCase):
    def setUp(self):
        self.raw = fixture()
        self.part = self.raw["parts"][0]
        self.bolt = self.part["boltGroups"]["value"][0]

    def result(self):
        return build_native_facts(self.raw)

    def test_source_precision_and_input_unchanged(self):
        original = copy.deepcopy(self.raw)
        output = self.result()
        self.assertEqual(original, self.raw)
        self.assertEqual(self.part, output["parts"][0]["rawEvidence"]["value"])
        value = output["parts"][0]["solidCatalog"]["edges"][0]["evidence"]["value"]["start"][0]
        self.assertEqual(float.hex(1.0000000000000002), float.hex(value))
        self.assertEqual("COMPLETE", output["readStatus"])
        self.assertEqual("BLOCKED", output["manufacturingStatus"])

    def test_api_edge_type_does_not_prove_line_or_face_plane(self):
        solid = self.result()["parts"][0]["solidCatalog"]
        self.assertEqual("UNKNOWN", solid["edges"][0]["geometryKind"])
        self.assertEqual("UNKNOWN", solid["faces"][0]["surfaceKind"])

    def test_same_endpoint_edges_preserved_and_ambiguous(self):
        solid = self.part["finalSolid"]["value"]
        duplicate = copy.deepcopy(solid["edges"]["value"][0])
        duplicate.update(id="E1", apiType="EDGE_TYPE_CURVED_SURFACE")
        solid["edges"]["value"].append(duplicate)
        output = self.result()["parts"][0]["solidCatalog"]
        self.assertEqual(2, len(output["edges"]))
        candidate = output["faces"][0]["loops"][0]["endpointCandidates"][0]
        self.assertEqual("UNKNOWN", candidate["status"])
        self.assertEqual(2, len(candidate["edgeRefs"]))

    def test_nearby_endpoint_not_snapped(self):
        self.part["finalSolid"]["value"]["faces"]["value"][0]["loops"]["value"][0]["vertices"]["value"][0] = [1, 0, 0]
        candidate = self.result()["parts"][0]["solidCatalog"]["faces"][0]["loops"][0]["endpointCandidates"][0]
        self.assertEqual([], candidate["edgeRefs"])

    def test_false_hole_switches_retained_and_tolerance_included(self):
        bolt = self.result()["parts"][0]["boltGroups"][0]
        self.assertEqual(2, len(bolt["evidence"]["value"]["positions"]["value"]))
        self.assertEqual(22, bolt["parameterCandidate"]["baseDiameter"])
        self.assertEqual(81, bolt["parameterCandidate"]["sizeAlongGroupX"])
        self.assertEqual("UNPROVEN", bolt["actualHoleStatus"])

    def test_stud_not_drilled_hole(self):
        self.bolt["properties"]["BoltStandard"] = read("STUD")
        bolt = self.result()["parts"][0]["boltGroups"][0]
        self.assertEqual("ATTACHMENT_STUD", bolt["classification"]["value"])
        self.assertIsNone(bolt["parameterCandidate"])

    def test_missing_standard_and_tolerance_do_not_get_defaults(self):
        del self.bolt["properties"]["BoltStandard"]
        del self.bolt["properties"]["Tolerance"]
        output = self.result()
        bolt = output["parts"][0]["boltGroups"][0]
        self.assertEqual("UNKNOWN", bolt["classification"]["value"])
        self.assertIsNone(bolt["parameterCandidate"])
        self.assertEqual("PARTIAL", output["readStatus"])

    def test_nested_error_overrides_complete_root(self):
        self.bolt["positions"] = {"status": "ERROR", "source": "test", "value": [[1, 2, 3]], "error": "interrupted"}
        output = self.result()
        self.assertEqual("PARTIAL", output["readStatus"])
        self.assertEqual([[1, 2, 3]], output["parts"][0]["boltGroups"][0]["evidence"]["value"]["positions"]["value"])

    def test_unknown_operation_preserved(self):
        self.part["operations"] = read([{"id": 30, "runtimeType": "FutureOperation", "detail": unknown()}])
        self.assertEqual(self.part["operations"], self.result()["parts"][0]["operations"]["value"])
        self.assertEqual("BLOCKED", self.result()["parts"][0]["manufacturingStatus"])

    def test_missing_target_retained_as_unresolved(self):
        self.bolt["targets"]["partToBoltTo"] = read(None)
        self.assertIn("TARGET_UNRESOLVED", {i["code"] for i in self.result()["issues"]})

    def test_missing_solid_not_success(self):
        del self.part["finalSolid"]
        self.assertEqual("PARTIAL", self.result()["readStatus"])

    def test_null_collection_cannot_be_successful_empty_list(self):
        self.part["boltGroups"] = read(None)
        self.assertEqual("PARTIAL", self.result()["readStatus"])

    def test_derived_overflow_rejected(self):
        self.bolt["properties"]["BoltSize"] = read(1e308)
        self.bolt["properties"]["Tolerance"] = read(1e308)
        with self.assertRaises(ValueError):
            self.result()

    def test_identity_selection_and_number_conflicts(self):
        self.raw["selection"][0]["resolved"]["id"] = 11
        self.part["reports"]["PART_POSITION"] = read("OTHER")
        codes = {i["code"] for i in self.result()["issues"]}
        self.assertTrue({"PART_POSITION_UNRESOLVED", "SELECTION_UNRESOLVED"} <= codes)
        self.assertEqual("PARTIAL", self.result()["readStatus"])

    def test_plane_restore_mismatch(self):
        self.raw["workPlaneRestore"] = read({"origin": [1, 0, 0], "axisX": [1, 0, 0], "axisY": [0, 1, 0], "axisZ": [0, 0, 1]})
        self.assertEqual("PARTIAL", self.result()["readStatus"])

    def test_invalid_schema_space_status_and_nonfinite_rejected(self):
        cases = [("schemaVersion", "old"), ("coordinateSpace", "LOCAL")]
        for key, value in cases:
            with self.subTest(key=key):
                raw = fixture(); raw[key] = value
                with self.assertRaises(ValueError):
                    build_native_facts(raw)
        self.part["reports"]["PART_POS"]["status"] = "PROVEN"
        with self.assertRaises(ValueError):
            self.result()
        self.raw = fixture(); self.raw["extra"] = math.nan
        with self.assertRaises(ValueError):
            self.result()

    def test_digest_changes_for_sub_micron_value(self):
        first = self.result()["rawDigest"]
        self.part["finalSolid"]["value"]["edges"]["value"][0]["start"][0] = 1.0000000000000004
        self.assertNotEqual(first, self.result()["rawDigest"])

    def test_partial_edge_enumeration_keeps_successful_records(self):
        self.part["finalSolid"]["value"]["edges"]["status"] = "ERROR"
        output = self.result()
        self.assertEqual("PARTIAL", output["readStatus"])
        self.assertEqual(1, len(output["parts"][0]["solidCatalog"]["edges"]))

    def test_source_refs_resolve_to_actual_raw_values(self):
        def walk(value):
            if isinstance(value, dict):
                if "basis" in value and "sourceRef" in value:
                    node = self.raw
                    for token in value["sourceRef"].split("/")[1:]:
                        token = token.replace("~1", "/").replace("~0", "~")
                        node = node[int(token)] if isinstance(node, list) else node[token]
                    if value["basis"] == "DIRECT" and not value["sourceRef"].endswith("/PART_POS"):
                        self.assertEqual(node, value["value"])
                for child in value.values(): walk(child)
            elif isinstance(value, list):
                for child in value: walk(child)
        walk(self.result())

    def test_cli_invalid_source_writes_diagnostic(self):
        with tempfile.TemporaryDirectory() as directory:
            source, out = Path(directory) / "bad.json", Path(directory) / "result.json"
            source.write_text('{"schemaVersion":"old"}', encoding="utf-8")
            self.assertEqual(2, main(["read-native-facts", "--evidence", str(source), "--out", str(out)]))
            self.assertEqual("INVALID", json.loads(out.read_text(encoding="utf-8"))["readStatus"])

    def test_cli_success_is_only_read_success_and_preserves_existing_files(self):
        with tempfile.TemporaryDirectory() as directory:
            source, out = Path(directory) / "raw.json", Path(directory) / "facts.json"
            source.write_text(json.dumps(self.raw), encoding="utf-8")
            before = source.read_bytes()
            self.assertEqual(0, main(["read-native-facts", "--evidence", str(source), "--out", str(out)]))
            report = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual("BLOCKED", report["manufacturingStatus"])
            self.assertEqual(64, len(report["rawFileSha256"]))
            self.assertEqual(2, main(["read-native-facts", "--evidence", str(source), "--out", str(source)]))
            self.assertEqual(2, main(["read-native-facts", "--evidence", str(source), "--out", str(out)]))
            self.assertEqual(before, source.read_bytes())


if __name__ == "__main__":
    unittest.main()
