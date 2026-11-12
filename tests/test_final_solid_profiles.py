"""Profile facts against measured real parts and independent section geometry."""

import copy
import gzip
import json
import math
import random
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from shapely import union_all
from shapely.geometry import Polygon, box, Point

from test_final_solid_repair import real_parts, evaluate, transformed
from test_final_solid_trial import _field
from zijinhua_tekla.part_drawing.final_solid_trial.profile_sections import partition_section


def profile_parts():
    new = json.loads(
        gzip.decompress(
            (Path(__file__).parent / "fixtures/final_solid_trial/profile-selection.json.gz").read_bytes()
        )
    )["parts"]
    return {
        p["reports"]["PART_POS"]["value"]: p
        for p in real_parts() + new
        if not p["profile"]["value"].startswith("PL")
    }


def profile_solid(shape, length=500, slope=0):
    """Independent polyhedral extrusion of a known YZ section with X end slope."""
    rings = [list(shape.exterior.coords)[:-1]] + [list(r.coords)[:-1] for r in shape.interiors]
    faces = []
    edges = []

    def add(loops, normal):
        faces.append(
            dict(
                id=f"F{len(faces)}",
                normal=_field(normal),
                originPartId=_field(1),
                loops=_field([dict(id=f"L{i}", vertices=_field(r)) for i, r in enumerate(loops)]),
            )
        )
        for ring in loops:
            for a, b in zip(ring, ring[1:] + ring[:1]):
                if not any(
                    (e["start"] == a and e["end"] == b) or (e["start"] == b and e["end"] == a)
                    for e in edges
                ):
                    edges.append(dict(start=a, end=b, apiType="EDGE_TYPE_NORMAL"))

    add([[[slope * y, y, z] for y, z in r] for r in rings], [-1, slope, 0])
    add([[[length, y, z] for y, z in r] for r in rings], [1, 0, 0])
    for ri, ring in enumerate(rings):
        signed = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(ring, ring[1:] + ring[:1]))
        orient = (1 if signed > 0 else -1) * (1 if ri == 0 else -1)
        for a, b in zip(ring, ring[1:] + ring[:1]):
            add(
                [[[slope * a[0], *a], [slope * b[0], *b], [length, *b], [length, *a]]],
                [0, orient * (b[1] - a[1]), orient * (a[0] - b[0])],
            )
    return dict(
        id=1,
        profile=_field("MEASURED-SECTION"),
        reports={"PART_POS": _field("synthetic")},
        coordinateSystem=_field(dict(origin=[0, 0, 0], axisX=[1, 0, 0], axisY=[0, 1, 0])),
        finalSolid=_field(dict(faces=_field(faces), edges=_field(edges))),
        boltGroups=_field([]),
    )


def shapes():
    return {
        "I_H": union_all([box(-50, -30, -42, 30), box(-42, -3, 42, 3), box(42, -30, 50, 30)]),
        "L": union_all([box(0, 0, 8, 70), box(8, 0, 100, 8)]),
        "C_U": union_all([box(-50, -30, 50, -24), box(-50, -24, -42, 30), box(42, -24, 50, 30)]),
        "T": union_all([box(-50, 0, 50, 8), box(-4, 8, 4, 70)]),
        "Z": union_all([box(-50, -30, -42, 3), box(-42, -3, 42, 3), box(42, -3, 50, 30)]),
        "BOX": box(-50, -40, 50, 40).difference(box(-44, -34, 44, 34)),
        "RECTANGULAR_SOLID": box(-30, -10, 30, 10),
    }


class FinalSolidProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parts = profile_parts()
        cls.results = {k: evaluate(v) for k, v in cls.parts.items()}

    def test_real_wall_sections_holes_and_studs(self):
        expected = {
            "T2-10B-23": ("I_H", [16, 30, 30], 36, 128, 330, 32240),
            "T2-10B-190": ("I_H", [8, 12, 12], 8, 20, 46, 8208),
            "T2-10B-189": ("L", [12, 12], 0, 6, 8, 2256),
            "T2-9P-1": ("BOX", [40] * 4, 1, 184, 42, 153600),
        }
        for pos, (family, thicknesses, holes, studs, faces, area) in expected.items():
            f, e = self.results[pos]
            with self.subTest(part=pos):
                self.assertEqual("READY", e["status"], e["issues"])
                self.assertEqual("STRAIGHT_PROFILE", f["partType"])
                self.assertEqual(family, f["profileFamily"])
                self.assertEqual(thicknesses, sorted(round(w["thickness"], 3) for w in f["walls"]))
                self.assertAlmostEqual(area, f["section"]["area"], delta=0.01)
                self.assertEqual(holes, len(f["holes"]))
                self.assertEqual(studs, sum(a["count"] for a in f["attachments"]))
                self.assertEqual(faces, len(f["faceRoles"]))
                self.assertEqual(set(range(faces)), {a["faceIndex"] for a in f["faceRoles"]})
                self.assertTrue(all(a["wallAssignments"] for a in f["faceRoles"]))
                self.assertNotIn("unfolding", f)
                self.assertEqual("profileExpressionRequirements.v1", e["schemaVersion"])
                self.assertEqual(len(f["walls"]), len(e["wallFacts"]))
                for h in f["holes"]:
                    wall = next(w for w in f["walls"] if w["id"] == h["wallId"])
                    self.assertEqual(wall["thickness"], h["localThickness"])
                    self.assertTrue(
                        any(
                            r["aspect"] == "wall_ownership" and r["expected"]["wallId"] == wall["id"]
                            for r in e["requirements"]
                        )
                    )
        box_fact = self.results["T2-9P-1"][0]
        self.assertEqual(1, len(box_fact["section"]["interiors"]))
        self.assertAlmostEqual(4495, box_fact["length"], delta=0.01)
        self.assertEqual(150, box_fact["holes"][0]["diameter"])
        bh = self.results["T2-10B-23"][0]
        self.assertEqual(
            [29] * 32 + [300, 300], sorted(h["diameter"] for h in bh["holes"] if h["kind"] == "ROUND")
        )
        self.assertTrue(all(h["wallRole"] == "WEB" for h in bh["holes"]))

    def test_seven_cross_section_families_with_sloped_ends(self):
        for family, shape in shapes().items():
            for slope in (0, 0.15):
                with self.subTest(family=family, slope=slope):
                    f, e = evaluate(profile_solid(shape, slope=slope))
                    self.assertEqual("READY", e["status"], e["issues"])
                    self.assertEqual(family, f["profileFamily"])
                    self.assertAlmostEqual(shape.area, f["section"]["area"], delta=0.01)
                    reconstructed = union_all([box(*w["sectionBounds"]) for w in f["walls"]])
                    self.assertLess(shape.symmetric_difference(reconstructed).area, 0.01)
                    if slope:
                        ends = [
                            c
                            for c in f["endCuts"]
                            if c["kind"] == "END_OR_NOTCH_FACE" and c["side"] == "START"
                        ]
                        self.assertTrue(ends)
                        self.assertTrue(
                            all(
                                abs(c["elevationAngle"] - math.degrees(math.atan(slope))) < 0.001
                                for c in ends
                            )
                        )

    def test_skew_relief_has_analytic_radius_and_observed_faces(self):
        f, e = self.results["T2-10B-190"]
        curves = [c for w in f["walls"] for c in w["analyticReliefs"]]
        self.assertGreaterEqual(len(curves), 24)
        for c in curves:
            self.assertAlmostEqual(35, c["radius"], delta=0.01)
            self.assertLessEqual(c["maxChordDeviationMm"], 0.1)
            self.assertTrue(c["observedLoops"])
            self.assertTrue(
                any(
                    r["aspect"] == "analytic_surface" and r["expected"]["faceIndex"] == c["faceIndex"]
                    for r in e["requirements"]
                )
            )
        self.assertTrue(
            any(abs(c["planAngle"] or 0) > 1 for c in f["endCuts"] if c["kind"] == "END_OR_NOTCH_FACE")
        )
        self.assertTrue(any(r["rule"] == "FINAL-PROFILE-RELIEF-01" for r in e["requirements"]))
        for w in e["wallFacts"]:
            sampled = {
                s["endPoint"] for s in w["facts"]["segments"] if s["kind"] == "SAMPLED_ANALYTIC_BOUNDARY"
            } & {
                s["startPoint"]
                for s in w["facts"]["segments"]
                if s["kind"] == "SAMPLED_ANALYTIC_BOUNDARY"
            }
            self.assertFalse(
                any(
                    r.get("wallId") == w["id"]
                    and r["feature"] in sampled
                    and r["aspect"].startswith("location_")
                    for r in e["requirements"]
                )
            )

    def test_curved_section_is_not_flattened_or_filled(self):
        for shape in (
            Point(0, 0).buffer(30, quad_segs=24),
            Point(0, 0).buffer(30, quad_segs=24).difference(Point(0, 0).buffer(25, quad_segs=24)),
        ):
            f, e = evaluate(profile_solid(shape))
            self.assertEqual("MATCHED", f["shellStatus"])
            self.assertEqual("BLOCKED", e["status"])
            self.assertEqual("PROFILE_CURVED_OR_SLOPED_SECTION", f["issues"][0]["code"])

    def test_rigid_motion_mirror_enumeration_and_irrelevant_history(self):
        a = 0.63
        rotation = np.array([[math.cos(a), -math.sin(a), 0], [math.sin(a), math.cos(a), 0], [0, 0, 1]])
        for pos in ("T2-9P-1", "T2-10B-189", "T2-10B-190"):
            for matrix in (rotation, np.diag([-1, 1, 1])):
                p = transformed(self.parts[pos], matrix, np.array([1e6, -2e6, 3e6]))
                p["runtimeType"] = "arbitrary"
                p["reports"]["PART_POS"]["value"] = "different-identity"
                p.pop("children", None)
                p.pop("rawSolid", None)
                p.pop("modelingContour", None)
                if pos == "T2-10B-189":
                    p.pop("operations", None)
                random.Random(31).shuffle(p["finalSolid"]["value"]["faces"]["value"])
                p["finalSolid"]["value"]["edges"]["value"].reverse()
                f, e = evaluate(p)
                self.assertEqual("READY", e["status"], (pos, e["issues"]))
                baseline = self.results[pos][0]
                self.assertAlmostEqual(baseline["section"]["area"], f["section"]["area"], delta=0.01)
                self.assertEqual(len(baseline["holes"]), len(f["holes"]))
                self.assertEqual(baseline["profileFamily"], f["profileFamily"])

    def test_corrupt_evidence_still_blocks(self):
        for mode in ("missing_face", "target", "hole_size", "curve_source"):
            p = copy.deepcopy(self.parts["T2-10B-190" if mode == "curve_source" else "T2-9P-1"])
            if mode == "missing_face":
                p["finalSolid"]["value"]["faces"]["value"].pop()
            if mode == "target":
                g = p["boltGroups"]["value"][-1]
                for k in ("partToBoltTo", "partToBeBolted"):
                    g["targets"][k]["value"]["id"] = -1
            if mode == "hole_size":
                p["boltGroups"]["value"][-1]["properties"]["Tolerance"]["value"] += 8
            if mode == "curve_source":
                p.pop("operations")
            self.assertEqual("BLOCKED", evaluate(p)[1]["status"], mode)

    def test_old_audit_and_cut_replay_are_not_profile_providers(self):
        with (
            patch(
                "zijinhua_tekla.part_drawing.native_geometry.audit_native_geometry",
                side_effect=AssertionError,
            ),
            patch(
                "zijinhua_tekla.part_drawing.native_geometry._material_final_state",
                side_effect=AssertionError,
            ),
            patch(
                "zijinhua_tekla.part_drawing.geometric_types.classify_shape", side_effect=AssertionError
            ),
        ):
            self.assertEqual("READY", evaluate(self.parts["T2-9P-1"])[1]["status"])

    def test_box_two_wall_holes_require_and_use_separate_material_intervals(self):
        p = profile_solid(shapes()["BOX"])
        group = copy.deepcopy(self.parts["T2-10B-190"]["boltGroups"]["value"][0])
        group["positions"] = _field([[250, 0, 0]])
        group["coordinateSystem"] = _field(dict(origin=[250, 0, 0], axisX=[1, 0, 0], axisY=[0, 0, 1]))
        group["properties"].update(BoltSize=_field(8), Tolerance=_field(2))
        for key in ("partToBoltTo", "partToBeBolted"):
            group["targets"][key]["value"]["id"] = 1
        group["targets"]["otherParts"] = _field([])
        group["axisIntersections"] = dict(
            status="READ",
            source="synthetic/intersections",
            value=[
                dict(
                    index=0,
                    targetPartId=1,
                    targetPoints=[[250, y, 0] for y in (-50, -44, 44, 50)],
                    hardwarePoints=[[250, -60, 0], [250, 60, 0]],
                    queryStart=[250, -80, 0],
                    queryEnd=[250, 80, 0],
                )
            ],
        )
        p["boltGroups"] = _field([group])
        f, e = evaluate(p)
        self.assertEqual("READY", e["status"], e["issues"])
        self.assertEqual(2, len(f["holes"]))
        self.assertEqual(2, len({h["wallId"] for h in f["holes"]}))
        self.assertEqual(1, len({h["sourceGroupId"] for h in f["holes"]}))
        group["axisIntersections"]["value"] = []
        self.assertEqual("PROFILE_MULTI_WALL_HOLE_UNPROVEN", evaluate(p)[0]["issues"][0]["code"])


if __name__ == "__main__":
    unittest.main()
