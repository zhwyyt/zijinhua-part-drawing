"""Independent expectations, real evidence and deliberate corruptions."""

import copy
import gzip
import json
import math
from pathlib import Path
import random
import unittest
from unittest.mock import patch

import numpy as np

from zijinhua_tekla.part_drawing.final_solid_trial.analysis import analyze_snapshot
from zijinhua_tekla.part_drawing.final_solid_trial.shell import read_shell
from test_final_solid_trial import _plate

FIXTURES = Path(__file__).parent / "fixtures"


def real_parts():
    return json.loads(gzip.decompress((FIXTURES / "final_solid_trial/real-parts.json.gz").read_bytes()))[
        "parts"
    ]


def evaluate(part):
    r = analyze_snapshot(dict(parts=[part]))
    return r["geometryFacts"][0], r["expressionRequirements"][0]


def transformed(value, matrix, offset, key=""):
    vectors = {"normal", "axisX", "axisY"}
    points = {"point", "origin", "start", "end", "queryStart", "queryEnd", "firstPosition", "secondPosition"}
    arrays = {"vertices", "positions", "targetPoints", "hardwarePoints"}
    if isinstance(value, dict):
        return {k: transformed(v, matrix, offset, key if k == "value" else k) for k, v in value.items()}
    if isinstance(value, list):
        if key in vectors | points and len(value) == 3 and all(isinstance(v, (float, int)) for v in value):
            return (matrix @ np.array(value) + (offset if key in points else 0)).tolist()
        if key in arrays:
            return [(matrix @ np.array(p) + offset).tolist() for p in value]
        return [transformed(v, matrix, offset, key) for v in value]
    return value


class FinalSolidRepairTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parts = {p["reports"]["PART_POS"]["value"]: p for p in real_parts()}
        cls.report = analyze_snapshot(dict(parts=list(cls.parts.values())))
        cls.facts = {f["partPosition"]: f for f in cls.report["geometryFacts"]}
        cls.expressions = {e["partPosition"]: e for e in cls.report["expressionRequirements"]}

    def test_original_open_shell_false_positive_is_blocked(self):
        p = _plate()
        p["finalSolid"]["value"]["faces"]["value"] = p["finalSolid"]["value"]["faces"]["value"][:3]
        f, e = evaluate(p)
        self.assertEqual("BLOCKED", e["status"])
        self.assertIn("SHELL_NOT_CLOSED", {i["code"] for i in f["issues"]})

    def test_shell_corruptions_are_not_silently_skipped(self):
        for change in (
            "face",
            "duplicate",
            "edge",
            "normal",
            "nan",
            "loop",
            "read",
            "inverted",
            "extra_edge",
        ):
            p = _plate()
            s = p["finalSolid"]["value"]
            fs = s["faces"]["value"]
            if change == "face":
                fs.pop()
            if change == "duplicate":
                fs.append(copy.deepcopy(fs[0]))
            if change == "edge":
                s["edges"]["value"].pop()
            if change == "normal":
                fs[0]["normal"]["value"] = [1, 0, 0]
            if change == "nan":
                fs[0]["loops"]["value"][0]["vertices"]["value"][0][0] = float("nan")
            if change == "loop":
                fs[0]["loops"]["value"] = []
            if change == "read":
                s["faces"]["status"] = "ERROR"
            if change == "inverted":
                for f in fs:
                    f["normal"]["value"] = [-v for v in f["normal"]["value"]]
            if change == "extra_edge":
                s["edges"]["value"].append(dict(start=[200, 0, 0], end=[200, 10, 0]))
            with self.subTest(change=change):
                self.assertEqual("BLOCKED", evaluate(p)[1]["status"])

    def test_all_ten_old_ready_flat_plates_keep_complete_expression(self):
        expected = {
            "T2-10B-182": (40, 0),
            "T2-10B-175": (20, 0),
            "T2-10B-174": (20, 0),
            "T2-10MQ-20": (12, 0),
            "T2-10MQ-3": (20, 8),
            "T2-10B-592": (8, 0),
            "T2-10MQ-2": (20, 8),
            "T2-10B-626": (14, 4),
            "T2-10B-305": (14, 6),
            "T2-10B-105": (7, 9),
        }
        for pos, (thickness, holes) in expected.items():
            f, e = self.facts[pos], self.expressions[pos]
            with self.subTest(part=pos):
                self.assertEqual("READY", e["status"], e["issues"])
                self.assertEqual("FLAT_PLATE", f["partType"])
                self.assertAlmostEqual(thickness, f["thickness"], places=3)
                self.assertEqual(holes, len(e["facts"]["holes"]))
                self.assertEqual(len(f["finalSolid"]["faces"]), len(f["faceRoles"]))
                self.assertTrue(any(r["aspect"] == "shape" for r in e["requirements"]))
                self.assertTrue(any(r["aspect"] == "location_x" for r in e["requirements"]))

    def test_real_bevels_and_slots_keep_section_responsibilities(self):
        for name in ("plate_with_shared_slots", "beam_two_bevels"):
            raw = json.loads((FIXTURES / "native_sections" / f"{name}.json").read_text(encoding="utf-8"))
            f, e = evaluate(raw["parts"][0])
            self.assertEqual("READY", e["status"], e["issues"])
            self.assertTrue(e["sectionRequirements"])
            self.assertEqual("expressionRequirements.v2", e["schemaVersion"])
            for b in f["sectionGeometry"]["bevels"]:
                self.assertAlmostEqual(45, b["angle"], places=4)
            if name == "plate_with_shared_slots":
                self.assertEqual(6, len(e["facts"]["holes"]))
                self.assertEqual("SLOT", e["groups"][0]["kind"])
                self.assertAlmostEqual(26, e["groups"][0]["width"])
                self.assertAlmostEqual(56, e["groups"][0]["length"])
                self.assertAlmostEqual(12, f["sectionGeometry"]["bevels"][0]["depth"])
                self.assertAlmostEqual(2, f["sectionGeometry"]["bevels"][0]["land"])

    def test_six_folded_parts_match_preexisting_measured_reference(self):
        expected = {
            "399": (10, 5, 12387.2),
            "180": (40, 2, 5875.6),
            "194": (16, 2, 6086.9),
            "402": (10, 5, 11984.4),
            "581": (8, 4, 4736.2),
            "409": (10, 2, 1521.2),
        }
        for suffix, (t, count, length) in expected.items():
            f = self.facts["T2-10B-" + suffix]
            with self.subTest(part=suffix):
                self.assertEqual("READY", f["status"], f["issues"])
                self.assertAlmostEqual(t, f["thickness"], places=3)
                self.assertEqual(count, len(f["segments"]))
                self.assertAlmostEqual(length, f["unfolding"]["sizeX"], delta=0.1)
                self.assertEqual(count - 1, len(f["bends"]))
                self.assertEqual("NOT_SPECIFIED", f["unfolding"]["manufacturingStatus"])
        f = self.facts["T2-10B-581"]
        self.assertEqual(8, len(f["holes"]))
        self.assertEqual(
            [4, 4],
            sorted(
                sum(h["segment"] == s["id"] for h in f["holes"])
                for s in f["segments"]
                if any(h["segment"] == s["id"] for h in f["holes"])
            ),
        )

    def test_hole_evidence_target_axis_and_shape_conflicts_block(self):
        for mode in ("target", "axis", "diameter", "missing", "duplicate"):
            p = copy.deepcopy(self.parts["T2-10B-105"])
            g = p["boltGroups"]["value"][-1]
            if mode == "target":
                for k in ("partToBoltTo", "partToBeBolted"):
                    g["targets"][k]["value"] = {"id": -1}
                g["targets"]["otherParts"]["value"] = []
            if mode == "axis":
                g["coordinateSystem"]["value"]["axisY"] = g["coordinateSystem"]["value"]["axisX"]
            if mode == "diameter":
                g["properties"]["Tolerance"]["value"] += 5
            if mode == "missing":
                g["properties"]["BoltSize"]["status"] = "ERROR"
            if mode == "duplicate":
                p["boltGroups"]["value"].append(copy.deepcopy(g))
            with self.subTest(mode=mode):
                self.assertEqual("BLOCKED", evaluate(p)[1]["status"])

    def test_hole_only_missing_cutter_blocks_and_stud_is_not_a_hole(self):
        p = copy.deepcopy(self.parts["T2-10MQ-3"])
        p["boltGroups"]["value"][0].pop("solidWithHighAccuracy")
        self.assertEqual("BLOCKED", evaluate(p)[1]["status"])
        p = copy.deepcopy(self.parts["T2-10B-626"])
        for g in p["boltGroups"]["value"]:
            g["properties"]["BoltStandard"]["value"] = "STUD"
        f, e = evaluate(p)
        self.assertEqual("READY", e["status"], e["issues"])
        self.assertEqual([], e["facts"]["holes"])

    def test_runtime_and_irrelevant_history_do_not_change_geometry(self):
        for pos in ("T2-10B-175", "T2-10B-581", "T2-10B-305"):
            p = copy.deepcopy(self.parts[pos])
            p["runtimeType"] = "SomeOtherModelCommand"
            p.pop("rawSolid", None)
            p.pop("modelingContour", None)
            p["operations"] = {"status": "ERROR", "value": None}
            p["children"] = {"status": "ERROR", "value": None}
            f, e = evaluate(p)
            self.assertEqual("READY", e["status"], e["issues"])
            self.assertEqual(self.facts[pos]["partType"], f["partType"])
            self.assertAlmostEqual(self.facts[pos]["thickness"], f["thickness"])
            self.assertEqual(len(self.expressions[pos]["requirements"]), len(e["requirements"]))

    def test_enumeration_changes_do_not_change_shape_or_fold_count(self):
        for pos in ("T2-10B-175", "T2-10B-180", "T2-10B-581"):
            p = copy.deepcopy(self.parts[pos])
            s = p["finalSolid"]["value"]
            random.Random(17).shuffle(s["faces"]["value"])
            s["edges"]["value"].reverse()
            for f in s["faces"]["value"]:
                for loop in f["loops"]["value"]:
                    pts = loop["vertices"]["value"]
                    loop["vertices"]["value"] = list(reversed(pts[1:] + pts[:1]))
            f, e = evaluate(p)
            self.assertEqual("READY", e["status"], e["issues"])
            self.assertAlmostEqual(self.facts[pos]["thickness"], f["thickness"], delta=0.001)
            if "unfolding" in f:
                self.assertAlmostEqual(
                    self.facts[pos]["unfolding"]["sizeX"], f["unfolding"]["sizeX"], delta=0.1
                )

    def test_rigid_motion_preserves_numerical_facts(self):
        a = 0.63
        b = 0.41
        matrix = np.array(
            [[math.cos(a), -math.sin(a), 0], [math.sin(a), math.cos(a), 0], [0, 0, 1]]
        ) @ np.array([[1, 0, 0], [0, math.cos(b), -math.sin(b)], [0, math.sin(b), math.cos(b)]])
        for pos in ("T2-10B-175", "T2-10B-305", "T2-10B-581", "T2-10B-180"):
            p = transformed(self.parts[pos], matrix, np.array([1e6, -2e6, 3e6]))
            f, e = evaluate(p)
            self.assertEqual("READY", e["status"], e["issues"])
            self.assertAlmostEqual(self.facts[pos]["thickness"], f["thickness"], delta=0.001)
            if "unfolding" in f:
                self.assertAlmostEqual(
                    self.facts[pos]["unfolding"]["sizeX"], f["unfolding"]["sizeX"], delta=0.1
                )

    def test_legacy_audit_classification_and_cut_replay_are_never_called(self):
        names = [
            "native_geometry.audit_native_geometry",
            "native_geometry._material_final_state",
            "native_geometry._observed_flat_material",
            "native_geometry._cut_final_state",
            "geometric_types.collect_shape_facts",
            "geometric_types.classify_shape",
            "native_expressions.build_native_expression_requirements",
        ]
        patches = [
            patch("zijinhua_tekla.part_drawing." + name, side_effect=AssertionError(name)) for name in names
        ]
        for item in patches:
            item.start()
        try:
            for pos in ("T2-10B-305", "T2-10B-626", "T2-10B-581"):
                self.assertEqual("READY", evaluate(self.parts[pos])[1]["status"])
        finally:
            for item in reversed(patches):
                item.stop()

    def test_unimplemented_sections_do_not_claim_geometry_ready(self):
        for pos in ("T2-10B-23", "T2-10B-190"):
            f = self.facts[pos]
            self.assertEqual("MATCHED", f["shellStatus"])
            self.assertEqual("BLOCKED", f["status"])
            self.assertTrue(f["finalSolid"]["faces"])

    def test_coplanar_face_split_preserves_shape_and_source_coverage(self):
        p = _plate()
        s = p["finalSolid"]["value"]
        face = s["faces"]["value"][0]
        ring = face["loops"]["value"][0]["vertices"]["value"]
        a, b, c, d = ring
        face["loops"]["value"][0]["vertices"]["value"] = [a, b, c]
        extra = copy.deepcopy(face)
        extra["id"] = "split"
        extra["loops"]["value"][0]["vertices"]["value"] = [a, c, d]
        s["faces"]["value"].append(extra)
        s["edges"]["value"].append(dict(start=a, end=c, apiType="EDGE_TYPE_NORMAL"))
        f, e = evaluate(p)
        self.assertEqual("READY", e["status"], e["issues"])
        self.assertEqual(7, len(f["faceRoles"]))
        self.assertEqual(6, len(f["regions"]))
        self.assertAlmostEqual(10, f["thickness"])
        self.assertEqual(4, len(e["facts"]["segments"]))

    def test_mirror_preserves_size_and_changes_bend_side(self):
        p = self.parts["T2-10B-581"]
        original = self.facts["T2-10B-581"]
        f, e = evaluate(transformed(p, np.diag([-1.0, 1.0, 1.0]), np.zeros(3)))
        self.assertEqual("READY", e["status"], e["issues"])
        self.assertAlmostEqual(original["unfolding"]["sizeX"], f["unfolding"]["sizeX"], delta=0.1)
        self.assertEqual(8, len(f["holes"]))
        for before, after in zip(original["bends"], f["bends"]):
            self.assertAlmostEqual(before["angle"], after["angle"], delta=0.001)
            self.assertNotEqual(before["direction"], after["direction"])

    def test_cut_arc_supplement_produces_radius_without_cut_replay(self):
        from test_cut_contour_fit import arc_points
        from test_cut_final_state import final_solid
        from test_modeling_independent_plate import fit_plate
        from test_native_geometry import prism
        from test_native_facts import read, unknown

        fillet = arc_points((0, 0), 10, 0, math.pi / 2, 8)
        outer = fillet + [[0, 60], [90, 60], [90, 0]]
        cutter = fillet + [[-4, 10], [-4, -4], [10, -4]]
        operation = dict(
            id=20,
            runtimeType="Tekla.Structures.Model.BooleanPart",
            type=read(dict(name="BOOLEAN_CUT")),
            operativePart=read(dict(id=20)),
            operativeGeometry=unknown(),
            operativeSolid=read(prism(cutter)),
        )
        # Synthetic prism helper assumes counterclockwise material boundaries.
        p = fit_plate([operation], final_solid(list(reversed(outer))))["parts"][0]
        f, e = evaluate(p)
        self.assertEqual("READY", e["status"], e["issues"])
        arcs = [s for s in e["facts"]["segments"] if s["kind"] == "ARC"]
        self.assertEqual(1, len(arcs))
        self.assertAlmostEqual(10, arcs[0]["radius"], delta=0.0001)
        self.assertTrue(any(r["aspect"] == "radius" for r in e["requirements"]))

    def test_missing_required_curve_parameters_cannot_pass_as_lines(self):
        p = copy.deepcopy(self.parts["T2-10B-626"])
        p.pop("modelingContour")
        p.pop("rawSolid")
        p.pop("operations")
        f, e = evaluate(p)
        self.assertEqual("BLOCKED", e["status"])
        self.assertIn("CURVE_PARAMETERS_MISSING", {i["code"] for i in f["issues"]})

    def test_profile_does_not_create_flatness(self):
        p = _plate()
        p["profile"]["value"] = "ANY"
        p["runtimeType"] = "Unknown"
        self.assertEqual("READY", evaluate(p)[1]["status"])
        p["profile"]["value"] = "PL15"
        self.assertEqual("BLOCKED", evaluate(p)[1]["status"])

    def test_flat_semantics_match_old_baseline_except_documented_105_projection(self):
        expected = json.loads(
            (FIXTURES / "final_solid_trial/old-flat-expectations.json").read_text(encoding="utf-8")
        )
        for pos, baseline in expected.items():
            old, new = baseline["facts"], self.expressions[pos]["facts"]
            with self.subTest(part=pos):
                for a, b in zip(old["bounds"], new["bounds"]):
                    if pos != "T2-10B-105":
                        self.assertAlmostEqual(a, b, delta=0.1)
                remaining = list(new["holes"])
                for hole in old["holes"]:
                    center = old["points"][hole["pointId"]]
                    matches = [
                        h
                        for h in remaining
                        if h["kind"] == hole["kind"] and math.dist(center, new["points"][h["pointId"]]) <= 0.1
                    ]
                    self.assertEqual(1, len(matches))
                    match = matches[0]
                    for key in ("diameter", "width", "length", "angle"):
                        if key in hole:
                            self.assertAlmostEqual(hole[key], match[key], delta=0.1)
                    self.assertEqual(hole.get("origin"), match.get("origin"))
                    remaining.remove(match)
                self.assertEqual([], remaining)
                if pos != "T2-10B-105":
                    for segment in old["segments"]:
                        hits = [
                            s
                            for s in new["segments"]
                            if s["kind"] == segment["kind"]
                            and min(
                                max(
                                    math.dist(segment["start"], s["start"]),
                                    math.dist(segment["end"], s["end"]),
                                ),
                                max(
                                    math.dist(segment["start"], s["end"]),
                                    math.dist(segment["end"], s["start"]),
                                ),
                            )
                            <= 0.1
                        ]
                        self.assertEqual(1, len(hits), segment)
                        if segment["kind"] == "ARC":
                            self.assertAlmostEqual(segment["radius"], hits[0]["radius"], delta=0.1)
                    measured = self.facts[pos]["sectionGeometry"]["bevels"]
                    self.assertEqual(len(baseline["bevels"]), len(measured))
                    for a, b in zip(baseline["bevels"], measured):
                        for key in ("angle", "depth", "land", "thickness"):
                            self.assertAlmostEqual(a[key], b[key], delta=0.1)

    def test_105_full_thickness_sloping_end_is_retained_in_section(self):
        f, e = self.facts["T2-10B-105"], self.expressions["T2-10B-105"]
        (bevel,) = f["sectionGeometry"]["bevels"]
        self.assertAlmostEqual(7, bevel["depth"], delta=0.001)
        self.assertAlmostEqual(0, bevel["land"], delta=0.001)
        self.assertAlmostEqual(17.849, bevel["angle"], delta=0.001)
        self.assertEqual(1, len(e["sectionRequirements"]))
        self.assertEqual(9, len(e["facts"]["holes"]))
        self.assertAlmostEqual(0, e["facts"]["bounds"][0], delta=0.001)

    def test_snapshot_errors_and_duplicate_parts_prevent_ready(self):
        for snapshot in (
            dict(parts=[_plate()], readStatus="PARTIAL"),
            dict(parts=[_plate()], errors=["reader failed"]),
            dict(parts=[_plate(), _plate()]),
        ):
            report = analyze_snapshot(snapshot)
            self.assertEqual(0, report["summary"]["expressionReady"])

    def test_uninterpreted_geometry_remains_in_report(self):
        fact = self.facts["T2-9P-49"]
        self.assertEqual("BLOCKED", fact["status"])
        self.assertTrue(fact["rawFinalSolid"]["value"]["faces"]["value"])


if __name__ == "__main__":
    unittest.main()
