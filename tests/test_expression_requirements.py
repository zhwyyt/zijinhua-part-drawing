import json
import math
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from zijinhua_tekla.cli import main
from zijinhua_tekla.part_drawing.contracts import ContourSegmentSnapshot, HoleSnapshot, Point3D
from zijinhua_tekla.part_drawing.expression_requirements import build_expression_requirements, snapshot_digest
from zijinhua_tekla.part_drawing.snapshot_input import load_part_snapshot


FIXTURES = Path(__file__).parent / "fixtures"


def context_for(snapshot):
    return {"schemaVersion": "manufacturingContext.v1", "source": {
        "modelIdentifier": snapshot.model_identifier, "partId": snapshot.part_id,
        "partPosition": snapshot.part_position, "snapshotDigest": snapshot_digest(snapshot)},
        "checks": {name: {"status": "VERIFIED", "evidence": ["regression fixture; not live Tekla evidence"]}
                   for name in ("final_contour", "all_holes", "uniform_thickness", "all_operations")}}


class ExpressionRequirementTests(unittest.TestCase):
    def setUp(self):
        self.base = load_part_snapshot(FIXTURES / "part_drawing/rectangular_plate.json")

    def plan(self, snapshot=None):
        snapshot = snapshot or self.base
        return build_expression_requirements(snapshot, context_for(snapshot))

    def test_rectangle_has_independent_boundary_location_and_plate_requirements(self):
        plan = self.plan()
        self.assertEqual("READY", plan["status"])
        self.assertEqual(16, len(plan["requirements"]))
        self.assertEqual(4, len(plan["facts"]["segments"]))
        self.assertEqual([0, 0, 200, 100], plan["facts"]["bounds"])
        self.assertEqual(4, len(plan["unspecified"]))
        self.assertEqual([], plan["groups"])

    def test_empty_hole_list_without_evidence_does_not_prove_no_holes(self):
        plan = build_expression_requirements(self.base)
        self.assertEqual("BLOCKED", plan["status"])
        self.assertEqual(16, len(plan["requirements"]))
        self.assertTrue(any(i["code"] == "EVIDENCE_MISSING" for i in plan["issues"]))

    def test_missing_hole_inventory_or_stale_context_blocks(self):
        context = context_for(self.base)
        del context["checks"]["all_holes"]
        self.assertEqual("BLOCKED", build_expression_requirements(self.base, context)["status"])
        context = context_for(self.base)
        changed = replace(self.base, holes=(HoleSnapshot("H", "ROUND", Point3D(50, 60, 30), diameter=20),))
        self.assertTrue(any(i["code"] == "SOURCE_MISMATCH" for i in build_expression_requirements(changed, context)["issues"]))

    def test_slot_and_rounds_have_separate_sizes_locations_and_directions(self):
        holes = tuple(HoleSnapshot(f"S{i}", "SLOT", Point3D(40 + i*50, 60, 30), width=22, length=81, angle_deg=90) for i in range(3))
        holes += (HoleSnapshot("H1", "ROUND", Point3D(80, 100, 30), diameter=30),)
        plan = self.plan(replace(self.base, holes=holes))
        self.assertEqual("READY", plan["status"])
        self.assertEqual([1, 3], sorted(g["count"] for g in plan["groups"]))
        candidate, = plan["pitchCandidates"]
        self.assertEqual((3, 2, 50), (candidate["holeCount"], candidate["intervalCount"], candidate["pitch"]))
        requirements = {r["id"] for r in plan["requirements"]}
        self.assertIn("hole:H1/location_x", requirements)
        self.assertIn("hole:H1/location_y", requirements)
        self.assertIn("hole:H1/center_cross", requirements)
        self.assertIn("hole:S2/orientation", requirements)
        self.assertNotIn("hole:H1/orientation", requirements)

    def test_direction_and_size_split_groups_without_rounding_to_fake_equality(self):
        holes = (HoleSnapshot("A", "SLOT", Point3D(50, 60, 30), width=20, length=40, angle_deg=0),
                 HoleSnapshot("B", "SLOT", Point3D(100, 60, 30), width=20, length=40, angle_deg=90),
                 HoleSnapshot("C", "SLOT", Point3D(150, 60, 30), width=20.01, length=40, angle_deg=0))
        self.assertEqual(3, len(self.plan(replace(self.base, holes=holes))["groups"]))

    def test_all_chamfers_are_preserved_instead_of_only_first_diagonal(self):
        points = [(10,20), (190,20), (210,40), (210,100), (190,120), (10,120)]
        loop = tuple(ContourSegmentSnapshot("LINE", Point3D(*a,30), Point3D(*b,30)) for a,b in zip(points, points[1:]+points[:1]))
        plan = self.plan(replace(self.base, outer_loop=loop))
        self.assertEqual("READY", plan["status"])
        self.assertEqual(6, len(plan["facts"]["segments"]))
        self.assertIn("vertex:4/location_y", {r["id"] for r in plan["requirements"]})

    def test_reviewed_concave_arcs_and_single_hole_notch(self):
        for filename, radii, holes in [("two-concave-arcs.json", [35,35], 0), ("single-hole-notch.json", [50], 1)]:
            with self.subTest(filename=filename):
                sample = load_part_snapshot(FIXTURES / "expression_requirements" / filename)
                plan = self.plan(sample)
                self.assertEqual("READY", plan["status"], plan["issues"])
                self.assertEqual(radii, [r["expected"] for r in plan["requirements"] if r["aspect"] == "radius"])
                self.assertEqual(holes, len(plan["facts"]["holes"]))

    def test_unknown_hole_stud_duplicate_and_invalid_dimensions_are_blocked(self):
        for hole in (HoleSnapshot("H", "STUD", Point3D(40,60,30), diameter=21),
                     HoleSnapshot("H", "ROUND", Point3D(40,60,30), diameter=-1),
                     HoleSnapshot("H", "SLOT", Point3D(40,60,30), width=22, length=10),
                     HoleSnapshot("H", "ROUND", Point3D(40,60,40), diameter=22)):
            with self.subTest(hole=hole):
                self.assertEqual("BLOCKED", self.plan(replace(self.base, holes=(hole,)))["status"])
        hole = HoleSnapshot("H", "ROUND", Point3D(40,60,30), diameter=22)
        self.assertEqual("BLOCKED", self.plan(replace(self.base, holes=(hole,hole)))["status"])
        self.assertEqual("BLOCKED", self.plan(replace(self.base, holes=(hole,replace(hole,hole_id="another-source"))))["status"])

    def test_missing_arc_center_inconsistent_radius_and_nonfinite_fail_closed(self):
        base = load_part_snapshot(FIXTURES / "expression_requirements/two-concave-arcs.json")
        for center in (None, Point3D(181,96,0)):
            loop = list(base.outer_loop)
            loop[2] = replace(loop[2], center=center)
            self.assertEqual("BLOCKED", self.plan(replace(base, outer_loop=tuple(loop)))["status"])
        with self.assertRaises(ValueError):
            build_expression_requirements(replace(base, thickness=math.nan))

    def test_unsupported_forms_operations_and_inner_loops_are_not_dropped(self):
        for changed in (replace(self.base, part_form="BENT_PLATE"),
                        replace(self.base, cuts=({"operationType":"BEVEL"},)),
                        replace(self.base, inner_loops=(self.base.outer_loop,))):
            self.assertEqual("BLOCKED", self.plan(changed)["status"])

    def test_process_values_require_evidence_and_requested_missing_values_block(self):
        context = context_for(self.base)
        context["requiredSpecifications"] = ["tolerance"]
        self.assertEqual("BLOCKED", build_expression_requirements(self.base, context)["status"])
        context["specifications"] = {"tolerance": {"value":"按项目公差表 A", "evidence":["approved project specification A"]}}
        plan = build_expression_requirements(self.base, context)
        self.assertEqual("READY", plan["status"])
        self.assertEqual("按项目公差表 A", next(r["expected"] for r in plan["requirements"] if r["id"] == "specification/tolerance"))

    def test_deterministic_and_no_part_number_rule_branch(self):
        first = self.plan()
        self.assertEqual(first, self.plan())
        other = self.plan(replace(self.base, part_id="unseen", part_position="NEW-任意"))
        self.assertEqual(first["facts"], other["facts"])
        self.assertEqual([(r["feature"],r["aspect"],r["rule"]) for r in first["requirements"]],
                         [(r["feature"],r["aspect"],r["rule"]) for r in other["requirements"]])
        self.assertNotEqual(first["sourceDigest"], other["sourceDigest"])

    def test_rotated_translated_world_frame_preserves_local_requirements(self):
        from zijinhua_tekla.part_drawing.contracts import LocalFrame, Vector3D
        def move(p):
            return Point3D(1000-p.y,2000+p.x,p.z+100)
        frame = LocalFrame(move(self.base.local_frame.origin),Vector3D(0,1,0),Vector3D(-1,0,0),Vector3D(0,0,1))
        rotated = replace(self.base,local_frame=frame,outer_loop=tuple(
            replace(s,start=move(s.start),end=move(s.end)) for s in self.base.outer_loop))
        self.assertEqual(self.plan()["facts"],self.plan(rotated)["facts"])

    def test_v2_principal_faces_check_and_thickness_conflict(self):
        base = load_part_snapshot(FIXTURES/"part_drawing/v2_plate_geometry.json")
        self.assertEqual("BLOCKED",self.plan(base)["status"])
        # Independent resolved rectangular variant; original fixture intentionally has unknown curves.
        base = replace(base,operations=(),quality_issues=(),final_geometry=replace(base.final_geometry,
            edges=tuple(replace(e,curve_type="LINE",evidence_status="PROVEN") for e in base.final_geometry.edges)))
        plan = self.plan(base)
        self.assertEqual("READY",plan["status"],plan["issues"])
        bad = self.plan(replace(base,thickness=12))
        self.assertIn("THICKNESS_EVIDENCE_CONFLICT",{i["code"] for i in bad["issues"]})

    def test_cli_writes_ready_and_blocked_reports_with_meaningful_exit_codes(self):
        source = FIXTURES / "part_drawing/rectangular_plate.json"
        with tempfile.TemporaryDirectory() as temp:
            context = Path(temp)/"context.json"
            output = Path(temp)/"report.json"
            context.write_text(json.dumps(context_for(self.base)), encoding="utf-8")
            self.assertEqual(0, main(["plan-expressions", "--snapshot", str(source), "--context", str(context), "--out", str(output)]))
            result = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual("READY", result["requirements"]["status"])
            self.assertEqual("PENDING", result["audit"]["status"])
            self.assertEqual(2, main(["plan-expressions", "--snapshot", str(source), "--out", str(output)]))
            bad = Path(temp)/"bad.json"
            bad.write_text('{"schemaVersion":"bad"}', encoding="utf-8")
            self.assertEqual(2, main(["plan-expressions", "--snapshot", str(bad), "--out", str(output)]))
            self.assertEqual("INPUT_INVALID", json.loads(output.read_text(encoding="utf-8"))["issues"][0]["code"])


if __name__ == "__main__":
    unittest.main()
