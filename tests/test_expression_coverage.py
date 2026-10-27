import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from zijinhua_tekla.cli import main
from zijinhua_tekla.part_drawing.contracts import HoleSnapshot, Point3D
from zijinhua_tekla.part_drawing.expression_coverage import audit_expression_coverage
from zijinhua_tekla.part_drawing.expression_requirements import build_expression_requirements
from zijinhua_tekla.part_drawing.snapshot_input import load_part_snapshot
from test_expression_requirements import FIXTURES, context_for


def dimension(object_id, axis, points, coordinates, values, role="location"):
    return {"objectId": object_id, "kind": "dimension", "axis": axis, "points": points,
            "coordinates": coordinates, "values": values, "displayValues": values, "role": role}


def round_note(members, diameter=22):
    return {"objectId": "hole-note", "kind": "hole_callout", "members": members,
            "holeKind": "ROUND", "count": len(members), "diameter": diameter,
            "text": f"{len(members)}Φ{diameter}"}


def center_lines(hole_id, x, y):
    return {"objectId": f"cross:{hole_id}", "kind": "center_lines", "holeId": hole_id,
            "style": "DASH_DOT", "lines": [[[x-15,y],[x+15,y]], [[x,y-15],[x,y+15]]]}


def round_shape(hole_id, x, y, diameter=22):
    return {"objectId":f"hole-shape:{hole_id}", "kind":"hole_shape", "holeId":hole_id,
            "holeKind":"ROUND", "visible":True, "center":[x,y], "diameter":diameter}


def rectangle_objects():
    vertices = [[0,0],[200,0],[200,100],[0,100]]
    return [
        {"objectId":"label", "kind":"label", "text":"P-1001", "leader":False},
        {"objectId":"thickness", "kind":"thickness", "value":10, "displayValue":10, "placement":"table"},
        *[{"objectId":f"outline:{i}", "kind":"segment", "feature":f"edge:{i}", "visible":True,
           "geometry":{"kind":"LINE", "start":a, "end":b}}
          for i,(a,b) in enumerate(zip(vertices,vertices[1:]+vertices[:1]))],
        dimension("overall-x", "x", ["vertex:0","vertex:1"], [[0,0],[200,0]], [200], "overall"),
        dimension("overall-y", "y", ["vertex:0","vertex:3"], [[0,0],[0,100]], [100], "overall"),
    ]


class ExpressionCoverageTests(unittest.TestCase):
    def setUp(self):
        self.base = load_part_snapshot(FIXTURES / "part_drawing/rectangular_plate.json")

    def plan(self, snapshot=None):
        snapshot = snapshot or self.base
        return build_expression_requirements(snapshot, context_for(snapshot))

    def audit(self, plan, objects, stage="READBACK"):
        return audit_expression_coverage(plan, {"schemaVersion":"expressionObservations.v1", "sourceDigest":plan["sourceDigest"],
                                                "stage":stage, "objects":objects})

    def missing(self, audit):
        return {row["requirementId"] for row in audit["coverage"] if row["status"] == "MISSING"}

    def test_two_dimensions_locate_four_vertices_without_eight_repeated_dimensions(self):
        plan = self.plan()
        result = self.audit(plan, rectangle_objects())
        self.assertEqual("PASS", result["status"], result)
        self.assertEqual("NOT_ASSESSED", result["drawingStatus"])
        self.assertEqual("PENDING", result["visualStatus"])
        self.assertEqual("PLAN_COMPLETE", self.audit(plan, rectangle_objects(), "PLAN")["status"])
        self.assertEqual("PENDING", audit_expression_coverage(plan)["status"])

    def test_hole_spacing_without_datum_is_not_positioned(self):
        snapshot = replace(self.base, holes=tuple(HoleSnapshot(f"H{i}","ROUND",Point3D(x,70,30),diameter=22) for i,x in enumerate((60,110,160))))
        plan = self.plan(snapshot)
        objects = rectangle_objects() + [round_note(["H0","H1","H2"])]
        objects += [center_lines(f"H{i}", x, 50) for i,x in enumerate((50,100,150))]
        objects += [round_shape(f"H{i}", x, 50) for i,x in enumerate((50,100,150))]
        chain = dimension("hole-chain", "x", ["hole:H0","hole:H1","hole:H2"], [[50,50],[100,50],[150,50]], [50,50])
        chain["combined"] = {"intervalCount":2, "pitch":50, "text":"2@50"}
        objects.append(chain)
        result = self.audit(plan, objects)
        self.assertEqual("FAIL", result["status"])
        self.assertIn("hole:H2/location_x", self.missing(result))
        objects += [dimension("x-margin","x",["vertex:0","hole:H0"],[[0,0],[50,50]],[50]),
                    dimension("y-margin","y",["vertex:0","hole:H0"],[[0,0],[50,50]],[50])]
        self.assertEqual("PASS", self.audit(plan, objects)["status"])
        chain["combined"]["intervalCount"] = 3
        self.assertEqual("FAIL", self.audit(plan, objects)["status"])

    def test_holes_with_centers_but_no_diameter_are_incomplete(self):
        snapshot = replace(self.base, holes=(HoleSnapshot("H","ROUND",Point3D(60,70,30),diameter=30),))
        plan = self.plan(snapshot)
        objects = rectangle_objects()+[center_lines("H",50,50),round_shape("H",50,50,30),
            dimension("hx","x",["vertex:0","hole:H"],[[0,0],[50,50]],[50]),
            dimension("hy","y",["vertex:0","hole:H"],[[0,0],[50,50]],[50])]
        result = self.audit(plan, objects)
        self.assertEqual({"group:0/size_count"}, self.missing(result))
        objects.append(round_note(["H"],30))
        self.assertEqual("PASS", self.audit(plan, objects)["status"])

    def test_fake_anchor_value_and_display_override_fail(self):
        for field, value in [("coordinates",[[0,0],[201,0]]), ("points",["vertex:0","virtual-corner"]),
                             ("values",[199]), ("displayValues",[199])]:
            with self.subTest(field=field):
                objects = rectangle_objects()
                objects[-2][field] = value
                result = self.audit(self.plan(), objects)
                self.assertEqual("FAIL", result["status"])
                self.assertTrue(result["errors"])

    def test_identity_thickness_missing_edge_and_extra_unsupported_object_fail(self):
        for mutate in (lambda o:o[0].update(text="P-1002"), lambda o:o[1].update(value=12),
                       lambda o:o.pop(2), lambda o:o.append({"objectId":"circle","kind":"circle"})):
            objects = rectangle_objects()
            mutate(objects)
            self.assertEqual("FAIL", self.audit(self.plan(),objects)["status"])

    def test_duplicate_object_ids_and_stale_digest_cannot_pass(self):
        plan = self.plan()
        self.assertEqual("FAIL",self.audit(plan,rectangle_objects()+[rectangle_objects()[0]])["status"])
        self.assertEqual("FAIL",audit_expression_coverage(plan,{"schemaVersion":"expressionObservations.v1","sourceDigest":"stale","stage":"READBACK","objects":rectangle_objects()})["status"])

    def test_slot_orientation_and_actual_center_cross_are_required(self):
        snapshot = replace(self.base, holes=(HoleSnapshot("S","SLOT",Point3D(60,70,30),width=22,length=81,angle_deg=45),))
        plan = self.plan(snapshot)
        objects = rectangle_objects()+[center_lines("S",50,50)]
        result = self.audit(plan,objects)
        self.assertIn("hole:S/orientation",self.missing(result))
        self.assertTrue(result["errors"])
        objects[-1]["lines"] = [[[40,40],[60,60]],[[40,60],[60,40]]]
        result = self.audit(plan,objects)
        self.assertNotIn("hole:S/orientation",self.missing(result))
        self.assertNotIn("hole:S/center_cross",self.missing(result))
        objects[-1]["lines"][0] = [[41,40],[61,60]]
        self.assertTrue(self.audit(plan,objects)["errors"])

    def test_slot_can_be_fully_expressed_and_disappearing_hole_fails(self):
        snapshot = replace(self.base, holes=(HoleSnapshot("S","SLOT",Point3D(60,70,30),width=22,length=81,angle_deg=0),))
        plan = self.plan(snapshot)
        objects = rectangle_objects()+[center_lines("S",50,50),
            {"objectId":"slot","kind":"hole_shape","holeId":"S","holeKind":"SLOT","center":[50,50],
             "width":22,"length":81,"angle":0,"visible":True},
            {"objectId":"slot-note","kind":"hole_callout","members":["S"],"holeKind":"SLOT","count":1,
             "width":22,"length":81,"angle":0,"text":"1×D22(81×22)"},
            dimension("hx","x",["vertex:0","hole:S"],[[0,0],[50,50]],[50]),
            dimension("hy","y",["vertex:0","hole:S"],[[0,0],[50,50]],[50])]
        self.assertEqual("PASS",self.audit(plan,objects)["status"])
        objects = [item for item in objects if item["kind"] != "hole_shape"]
        self.assertEqual({"hole:S/shape"},self.missing(self.audit(plan,objects)))

    def test_explicit_process_specification_cannot_be_satisfied_by_geometric_dimensions(self):
        context = context_for(self.base)
        context["specifications"] = {"edge_finish":{"value":"去毛刺", "evidence":["project requirement"]}}
        plan = build_expression_requirements(self.base,context)
        self.assertEqual({"specification/edge_finish"},self.missing(self.audit(plan,rectangle_objects())))
        objects = rectangle_objects()+[{"objectId":"finish","kind":"specification","aspect":"edge_finish","text":"去毛刺"}]
        self.assertEqual("PASS",self.audit(plan,objects)["status"])

    def test_two_concave_arcs_need_only_reviewed_four_linear_dimensions(self):
        snapshot = load_part_snapshot(FIXTURES / "expression_requirements/two-concave-arcs.json")
        plan = self.plan(snapshot)
        edges = [([0,0],[180,0]),([180,0],[180,61]),([180,61],[145,96]),
                 ([145,96],[35,96]),([35,96],[0,61]),([0,61],[0,0])]
        objects = [{"objectId":"label","kind":"label","text":"T3-P-4327","leader":False},
                   {"objectId":"t","kind":"thickness","value":10,"displayValue":10,"placement":"table"}]
        for i,(start,end) in enumerate(edges):
            geometry = {"kind":"LINE","start":start,"end":end}
            if i in (2,4):
                geometry.update(kind="ARC",center=[180 if i==2 else 0,96],radius=35,clockwise=True)
                objects.append({"objectId":f"r{i}","kind":"radius","feature":f"edge:{i}","value":35,
                                "text":"R=35","anchor":start,"auxiliaryCircle":False})
            objects.append({"objectId":f"e{i}","kind":"segment","feature":f"edge:{i}","visible":True,"geometry":geometry})
        objects += [dimension("ox","x",["vertex:0","vertex:1"],[[0,0],[180,0]],[180],"overall"),
                    dimension("oy","y",["vertex:0","vertex:3"],[[0,0],[145,96]],[96],"overall"),
                    dimension("side","y",["vertex:0","vertex:5"],[[0,0],[0,61]],[61]),
                    dimension("top","x",["vertex:4","vertex:3"],[[35,96],[145,96]],[110])]
        result = self.audit(plan,objects)
        self.assertEqual("PASS",result["status"],result)

    def test_wrong_count_text_and_duplicate_group_members_fail(self):
        snapshot = replace(self.base, holes=(HoleSnapshot("H","ROUND",Point3D(60,70,30),diameter=30),))
        for update in ({"count":2},{"text":"2Φ30"},{"members":["H","H"]},{"diameter":22}):
            note = round_note(["H"],30)
            note.update(update)
            self.assertTrue(self.audit(self.plan(snapshot),[note])["errors"])
        notes = [round_note(["H"],30),{**round_note(["H"],30),"objectId":"second-note"}]
        self.assertTrue(self.audit(self.plan(snapshot),notes)["errors"])

    def test_edge_margin_must_not_be_combined_as_hole_pitch(self):
        snapshot = replace(self.base, holes=(HoleSnapshot("H","ROUND",Point3D(60,70,30),diameter=30),))
        chain = dimension("bad-combination","x",["vertex:0","hole:H","vertex:1"],[[0,0],[50,50],[200,0]],[50,150])
        chain["combined"] = {"intervalCount":2,"pitch":50,"text":"2@50"}
        self.assertTrue(self.audit(self.plan(snapshot),[chain])["errors"])

    def test_equal_pitch_cannot_skip_intermediate_holes(self):
        snapshot = replace(self.base,holes=tuple(HoleSnapshot(f"H{i}","ROUND",Point3D(30+i*30,70,30),diameter=10) for i in range(5)))
        chain = dimension("skipped","x",["hole:H0","hole:H2","hole:H4"],[[20,50],[80,50],[140,50]],[60,60])
        chain["combined"] = {"intervalCount":2,"pitch":60,"text":"2@60"}
        result = self.audit(self.plan(snapshot),[chain])
        self.assertIn("skips an intermediate hole",result["errors"][0]["message"])

    def test_arc_leader_must_be_on_arc_not_other_half_of_circle(self):
        snapshot = load_part_snapshot(FIXTURES / "expression_requirements/single-hole-notch.json")
        plan = self.plan(snapshot)
        radius = {"objectId":"R50","kind":"radius","feature":"edge:1","value":50,"text":"R=50",
                  "anchor":[100,50],"auxiliaryCircle":False}
        result = self.audit(plan,[radius])
        self.assertNotIn("edge:1/radius",self.missing(result))
        radius["anchor"] = [100,-50]
        self.assertTrue(self.audit(plan,[radius])["errors"])
        radius["anchor"] = [100,50]
        radius["auxiliaryCircle"] = True
        self.assertTrue(self.audit(plan,[radius])["errors"])

    def test_single_hole_notch_complete_with_shared_dimensions(self):
        snapshot = load_part_snapshot(FIXTURES / "expression_requirements/single-hole-notch.json")
        plan = self.plan(snapshot)
        edges = [([0,0],[50,0]),([50,0],[150,0]),([150,0],[275,0]),
                 ([275,0],[275,120]),([275,120],[0,120]),([0,120],[0,0])]
        objects = [{"objectId":"label","kind":"label","text":"T3-KB-7","leader":False},
                   {"objectId":"t","kind":"thickness","value":10,"displayValue":10,"placement":"table"}]
        for i,(start,end) in enumerate(edges):
            geometry = {"kind":"LINE","start":start,"end":end}
            if i==1:
                geometry.update(kind="ARC",center=[100,0],radius=50,clockwise=True)
            objects.append({"objectId":f"edge-{i}","kind":"segment","feature":f"edge:{i}","geometry":geometry,"visible":True})
        objects += [dimension("ox","x",["vertex:0","vertex:3"],[[0,0],[275,0]],[275],"overall"),
                    dimension("oy","y",["vertex:0","vertex:5"],[[0,0],[0,120]],[120],"overall"),
                    dimension("chain","x",["vertex:0","vertex:1","vertex:2","vertex:3"],[[0,0],[50,0],[150,0],[275,0]],[50,100,125]),
                    dimension("hx","x",["vertex:0","hole:H1"],[[0,0],[230,75]],[230]),
                    dimension("hy","y",["vertex:0","hole:H1"],[[0,0],[230,75]],[75]),
                    round_note(["H1"],30),center_lines("H1",230,75),round_shape("H1",230,75,30),
                    {"objectId":"R","kind":"radius","feature":"edge:1","value":50,"text":"R=50","anchor":[100,50],"auxiliaryCircle":False}]
        result = self.audit(plan,objects)
        self.assertEqual("PASS",result["status"],result)
        objects[-1]["value"] = 35
        self.assertEqual("FAIL",self.audit(plan,objects)["status"])

    def test_cli_audit_exit_code_reports_missing_annotations(self):
        with tempfile.TemporaryDirectory() as temp:
            context = Path(temp)/"context.json"
            observations = Path(temp)/"objects.json"
            output = Path(temp)/"report.json"
            context.write_text(json.dumps(context_for(self.base)),encoding="utf-8")
            observations.write_text(json.dumps({"schemaVersion":"expressionObservations.v1","sourceDigest":self.plan()["sourceDigest"],
                                                "stage":"READBACK","objects":rectangle_objects()[:-1]}),encoding="utf-8")
            args = ["plan-expressions","--snapshot",str(FIXTURES/"part_drawing/rectangular_plate.json"),
                    "--context",str(context),"--observations",str(observations),"--out",str(output)]
            self.assertEqual(1,main(args))
            self.assertEqual("FAIL",json.loads(output.read_text(encoding="utf-8"))["audit"]["status"])


if __name__ == "__main__":
    unittest.main()
