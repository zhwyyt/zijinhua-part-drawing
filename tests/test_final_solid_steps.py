"""Measured thickness steps: independent dimensions and rejection cases."""

import copy
import math
import random
import unittest
from unittest.mock import patch

import numpy as np
from shapely.geometry import Polygon, LineString

from test_native_geometry import prism
from test_final_solid_trial import _field
from test_final_solid_repair import real_parts, evaluate, transformed
from zijinhua_tekla.part_drawing.final_solid_trial.shell import read_shell
from zijinhua_tekla.part_drawing.final_solid_trial.supplements import shape_polygon


def step_plate():
    section = [[0, 4], [30, 4], [30, 0], [170, 0], [170, 4], [200, 4], [200, 16], [0, 16]]
    solid = transformed(prism(section, 0, 100), np.array([[1, 0, 0], [0, 0, 1], [0, 1, 0]]), np.zeros(3))
    return dict(
        id=10,
        profile=_field("PL16"),
        finalSolid=_field(solid),
        boltGroups=_field([]),
        reports={"PART_POS": _field("synthetic-step")},
        dstvCoordinateSystem=_field(dict(origin=[0, 0, 0], axisX=[1, 0, 0], axisY=[0, 1, 0])),
    )


class FinalSolidStepTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.part = next(p for p in real_parts() if p["reports"]["PART_POS"]["value"] == "T2-9P-49")
        cls.fact, cls.expression = evaluate(cls.part)

    def test_real_plate_dimensions_roles_and_complete_expression(self):
        f, e = self.fact, self.expression
        self.assertEqual("READY", f["status"], f["issues"])
        self.assertEqual("READY", e["status"], e["issues"])
        self.assertEqual("STEPPED_PLATE", f["partType"])
        self.assertEqual(set(range(14)), {r["faceIndex"] for r in f["faceRoles"]})
        self.assertEqual(14, len(f["faceRoles"]))
        self.assertEqual(4, sum(r["role"] == "STEP_WALL" for r in f["faceRoles"]))
        self.assertEqual(2, len(f["thicknessRegions"]))
        for zone in f["thicknessRegions"]:
            self.assertAlmostEqual(16 if zone["depth"] < 0.1 else 12, zone["thickness"], delta=0.01)
            if zone["depth"] < 0.1:
                bounds = Polygon(zone["polygon"]).bounds
                for v, expected in zip(bounds, [50, 50, 870, 870]):
                    self.assertAlmostEqual(expected, v, delta=0.1)
            for aspect in ("shape_and_location", "remaining_thickness", "cut_depth", "cut_side"):
                self.assertTrue(
                    any(r["feature"] == zone["id"] and r["aspect"] == aspect for r in e["requirements"])
                )
        self.assertFalse(
            any(r["feature"] == "plate" and r["aspect"] == "thickness" for r in e["requirements"])
        )
        self.assertEqual(5, len(f["material"]["holes"]))
        self.assertEqual([150] * 4 + [250], sorted(h["diameter"] for h in f["material"]["holes"]))
        for h in f["material"]["holes"]:
            self.assertAlmostEqual(16, h["localThickness"], delta=0.01)
            self.assertEqual("HOLE_ONLY_CUTTER_CONFIRMED", h["proof"])
        self.assertEqual(
            {s["id"] for s in f["steps"]}, {i for s in e["sectionRequirements"] for i in s["stepIds"]}
        )
        for s in e["sectionRequirements"]:
            self.assertTrue(Polygon(s["polygon"]).is_valid)
            self.assertLessEqual(s["maxEndpointAdjustmentMm"], 0.1)
            origin, direction = np.array(s["origin"]), np.array(s["direction"])
            cut = LineString([origin - 2000 * direction, origin + 2000 * direction])
            self.assertTrue(
                all(not cut.intersects(shape_polygon(h["segments"])) for h in f["material"]["holes"])
            )

    def test_generic_different_size_step_without_history(self):
        f, e = evaluate(step_plate())
        self.assertEqual("READY", e["status"], e["issues"])
        self.assertEqual([12, 12, 16], sorted(round(z["thickness"], 4) for z in f["thicknessRegions"]))
        self.assertEqual(2, len(f["steps"]))
        for s in e["sectionRequirements"]:
            self.assertAlmostEqual(200 * 16 - 60 * 4, Polygon(s["polygon"]).area, delta=0.01)

    def test_real_variants_do_not_depend_on_identity_history_or_orientation(self):
        angle = 0.47
        rotation = np.array(
            [[math.cos(angle), 0, math.sin(angle)], [0, 1, 0], [-math.sin(angle), 0, math.cos(angle)]]
        )
        for matrix in (rotation, np.diag([-1, 1, 1]), np.diag([1, 1, -1])):
            p = transformed(self.part, matrix, np.array([1e6, -2e6, 3e6]))
            for key in ("rawSolid", "operations", "children", "modelingContour"):
                p.pop(key, None)
            p["reports"]["PART_POS"]["value"] = "not-9p49"
            solid = p["finalSolid"]["value"]
            random.Random(12).shuffle(solid["faces"]["value"])
            solid["edges"]["value"].reverse()
            f, e = evaluate(p)
            self.assertEqual("READY", e["status"], e["issues"])
            self.assertEqual(5, len(f["material"]["holes"]))
            for measured, expected in zip(
                sorted(z["thickness"] for z in f["thicknessRegions"]), [12, 16]
            ):
                self.assertAlmostEqual(expected, measured, delta=0.01)

    def test_plane_tolerance_does_not_relax_topological_stitching(self):
        shell = read_shell(self.part)
        self.assertEqual(0.0001, shell["stitchToleranceMm"])
        self.assertEqual(0.1, shell["modelToleranceMm"])
        self.assertGreater(max(f["fittedPlaneResidualMm"] for f in shell["faces"]), 0.001)
        for delta in (0.005, 2):
            p = copy.deepcopy(self.part)
            face = p["finalSolid"]["value"]["faces"]["value"][1]
            face["loops"]["value"][0]["vertices"]["value"][0][2] += delta
            f, e = evaluate(p)
            self.assertEqual("BLOCKED", e["status"])
            self.assertIn(
                f["issues"][0]["code"],
                ("SHELL_NOT_CLOSED", "FACE_NORMAL_CONFLICT", "NONPLANAR_FACE_UNSUPPORTED"),
            )

    def test_cross_step_hole_is_not_proved_with_global_thickness(self):
        p = copy.deepcopy(self.part)
        # Enlarge the centre hole to cross the 820 mm central region boundary.
        p["boltGroups"]["value"][0]["properties"]["Tolerance"]["value"] = 830
        f, e = evaluate(p)
        self.assertEqual("BLOCKED", e["status"])
        self.assertEqual("STEP_HOLE_REGION_UNRESOLVED", f["issues"][0]["code"])

    def test_missing_step_face_is_blocked(self):
        p = copy.deepcopy(self.part)
        p["finalSolid"]["value"]["faces"]["value"].pop(0)
        self.assertEqual("SHELL_NOT_CLOSED", evaluate(p)[0]["issues"][0]["code"])

    def test_hole_in_reduced_region_uses_twelve_mm_not_sixteen(self):
        p = step_plate()
        group = copy.deepcopy(self.part["boltGroups"]["value"][0])
        group.update(
            coordinateSystem=_field(dict(origin=[15, 50, 10], axisX=[1, 0, 0], axisY=[0, 1, 0])),
            positions=_field([[15, 50, 10]]),
        )
        group["properties"].update(BoltSize=_field(8), Tolerance=_field(2))
        for key in ("partToBoltTo", "partToBeBolted"):
            group["targets"][key]["value"]["id"] = 10
        circle = [
            [15 + 5 * math.cos(i * math.tau / 48), 50 + 5 * math.sin(i * math.tau / 48)]
            for i in range(48)
        ]
        group["solidWithHighAccuracy"] = _field(prism(circle, 4, 16))
        p["boltGroups"] = _field([group])
        f, e = evaluate(p)
        self.assertEqual("READY", e["status"], e["issues"])
        self.assertAlmostEqual(12, f["material"]["holes"][0]["localThickness"])
        self.assertEqual([4, 16], f["material"]["holeEvidence"][0]["cutterProof"]["targetLevels"])
        group["solidWithHighAccuracy"] = _field(prism(circle, 0, 16))
        self.assertEqual("HOLE_ONLY_NOT_OBSERVED", evaluate(p)[0]["issues"][0]["code"])

    def test_sloped_internal_wall_is_not_flattened_into_step(self):
        p = step_plate()

        # Shift only the bottom corner along X, consistently in every face and edge.
        def move(value):
            if isinstance(value, dict):
                return {k: move(v) for k, v in value.items()}
            if isinstance(value, list):
                if len(value) == 3 and all(isinstance(v, (int, float)) for v in value):
                    return [40, value[1], 0] if value[0] == 30 and value[2] == 0 else value
                return [move(v) for v in value]
            return value

        p["finalSolid"] = move(p["finalSolid"])
        # Update the tilted wall's reported normal so this tests feature support,
        # not an inconsistent source normal.
        for face in p["finalSolid"]["value"]["faces"]["value"]:
            pts = np.array(face["loops"]["value"][0]["vertices"]["value"])
            if set(pts[:, 0]) == {30, 40}:
                normal = np.cross(pts[1] - pts[0], pts[2] - pts[0])
                if np.dot(normal, face["normal"]["value"]) < 0:
                    normal = -normal
                face["normal"]["value"] = normal.tolist()
        f, e = evaluate(p)
        self.assertEqual("MATCHED", f["shellStatus"], f["issues"])
        self.assertEqual("BLOCKED", e["status"])
        self.assertIn(
            f["issues"][0]["code"],
            ("STEP_OVERLAP_CONFLICT", "STEP_COVERAGE_CONFLICT", "STEP_SLOPED_WALL_UNSUPPORTED"),
        )

    def test_legacy_reconstruction_is_not_used_for_steps(self):
        with (
            patch(
                "zijinhua_tekla.part_drawing.native_geometry._material_final_state",
                side_effect=AssertionError,
            ),
            patch(
                "zijinhua_tekla.part_drawing.native_geometry.audit_native_geometry",
                side_effect=AssertionError,
            ),
            patch(
                "zijinhua_tekla.part_drawing.geometric_types.classify_shape", side_effect=AssertionError
            ),
        ):
            self.assertEqual("READY", evaluate(self.part)[1]["status"])


if __name__ == "__main__":
    unittest.main()
