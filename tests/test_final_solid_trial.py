import json
import tempfile
import unittest
from pathlib import Path

from zijinhua_tekla.part_drawing.final_solid_trial.analysis import analyze_snapshot, write_trial
from test_native_geometry import prism


def _field(value):
    return {"status": "READ", "value": value}


def _plate(with_bolt=False):
    # The original fixture had only three faces and a placeholder edge. It
    # described an open shell, so READY was itself the false-positive bug.
    solid = prism([[0, 0], [100, 0], [100, 50], [0, 50]], -10, 0)
    part = {
        "id": 1,
        "runtimeType": "Tekla.Structures.Model.ContourPlate",
        "profile": _field("PL10"),
        "material": _field("Q355B"),
        "reports": {"PART_POS": _field("P1")},
        "finalSolid": _field(solid),
        "boltGroups": _field([]),
    }
    if with_bolt:
        part["boltGroups"] = _field(
            [
                {
                    "id": 2,
                    "positions": _field([[20, 20, 0]]),
                    "properties": _field(
                        {
                            "Bolt": _field(True),
                            "BoltSize": _field(20),
                            "HoleType": _field({"name": "HOLE_TYPE_ROUND"}),
                        }
                    ),
                }
            ]
        )
    return part


class FinalSolidTrialTests(unittest.TestCase):
    def test_geometry_uses_final_solid_and_expression_is_ready_without_bolts(self):
        report = analyze_snapshot({"schemaVersion": "teklaNativeEvidence.v1", "parts": [_plate()]})
        fact = report["geometryFacts"][0]
        self.assertEqual(fact["status"], "READY")
        self.assertEqual(fact["partType"], "FLAT_PLATE")
        self.assertAlmostEqual(fact["thickness"], 10.0)
        self.assertEqual(report["expressionRequirements"][0]["status"], "READY")

    def test_bolt_supplement_without_final_loop_blocks_expression(self):
        report = analyze_snapshot(
            {"schemaVersion": "teklaNativeEvidence.v1", "parts": [_plate(with_bolt=True)]}
        )
        self.assertEqual(report["geometryFacts"][0]["shellStatus"], "MATCHED")
        expression = report["expressionRequirements"][0]
        self.assertEqual(expression["status"], "BLOCKED")
        self.assertIn("SOURCE_UNAVAILABLE", {item["code"] for item in expression["issues"]})

    def test_write_trial_rejects_existing_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "input.json"
            source.write_text(json.dumps({"parts": [_plate()]}), encoding="utf-8")
            output = root / "out"
            write_trial(source, output)
            with self.assertRaises(FileExistsError):
                write_trial(source, output)


if __name__ == "__main__":
    unittest.main()
