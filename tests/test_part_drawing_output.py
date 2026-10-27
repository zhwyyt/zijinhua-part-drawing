from dataclasses import replace
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ezdxf
from pypdf import PdfReader

from zijinhua_tekla.part_drawing import drawing_output
from zijinhua_tekla.part_drawing.contracts import (
    ContourSegmentSnapshot,
    DrawingStatus,
    HoleSnapshot,
    Point3D,
)
from zijinhua_tekla.part_drawing.dimension_generator import generate_dimension_intents
from zijinhua_tekla.part_drawing.dimension_optimizer import optimize_dimension_layout
from zijinhua_tekla.part_drawing.dimension_planner import plan_dimensions
from zijinhua_tekla.part_drawing.drawing_output import (
    PartDrawingRenderError,
    build_part_drawing_document,
    render_part_drawing,
)
from zijinhua_tekla.part_drawing.feature_recognizer import (
    FeatureType,
    RecognizedFeature,
    recognize_plate_features,
)
from zijinhua_tekla.part_drawing.geometry_analyzer import Point2D, analyze_plate_geometry
from zijinhua_tekla.part_drawing.snapshot_input import load_part_snapshot


FIXTURE = Path(__file__).parent / "fixtures" / "part_drawing" / "rectangular_plate.json"
CJK_FONT = Path("C:/Windows/Fonts/simhei.ttf")


class PartDrawingOutputTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = load_part_snapshot(FIXTURE)
        self.geometry = analyze_plate_geometry(self.snapshot).geometry
        self.features = ()
        intents = generate_dimension_intents(self.snapshot, self.geometry, self.features).intents
        self.layout = optimize_dimension_layout(self.snapshot, self.geometry, intents, quantity=2)
        self.document = build_part_drawing_document(
            self.snapshot,
            self.geometry,
            self.features,
            self.layout,
            quantity=2,
        )

    def test_writes_dxf_pdf_and_json_from_one_document(self):
        self.assertEqual(2, len(self.document.placed_dimensions))
        self.assertEqual(
            "partCadDimensionStyle.v1",
            self.document.dimension_style.style_id,
        )
        with tempfile.TemporaryDirectory() as directory:
            paths = render_part_drawing(self.document, Path(directory))
            self.assertTrue(paths.dxf_path.exists())
            self.assertTrue(paths.pdf_path.exists())
            self.assertTrue(paths.json_path.exists())
            dxf = ezdxf.readfile(paths.dxf_path)
            self.assertFalse(dxf.audit().has_errors)
            dimensions = list(dxf.modelspace().query("DIMENSION"))
            self.assertEqual(2, len(dimensions))
            self.assertFalse(
                list(dxf.modelspace().query('LINE[layer=="DIMENSION"]'))
            )
            style = dxf.dimstyles.get("PART-CAD-V1")
            self.assertEqual(2.5, style.dxf.dimtxt)
            self.assertEqual(2.0, style.dxf.dimasz)
            self.assertEqual(2.5, style.dxf.dimexo)
            self.assertEqual(2.0, style.dxf.dimexe)
            self.assertEqual(1.0, style.dxf.dimgap)
            self.assertEqual(0, style.dxf.dimdec)
            self.assertEqual(8, style.dxf.dimzin)
            self.assertEqual("_OBLIQUE", style.dxf.dimblk)
            self.assertEqual(0.65, dxf.styles.get("TArial").dxf.width)
            raw_measurements = sorted(
                float(item.get_measurement()) for item in dimensions
            )
            self.assertEqual([100.0, 200.0], raw_measurements)
            for entity in dimensions:
                overrides = entity.get_acad_dstyle(
                    dxf.dimstyles.get(entity.dxf.dimstyle)
                )
                self.assertEqual(1.0, overrides["dimlfac"])
            payload = json.loads(paths.json_path.read_text(encoding="utf-8"))
            self.assertEqual(self.document.part_position, payload["part_position"])
            self.assertEqual(self.document.annotation_texts(), payload["annotation_texts"])
            self.assertEqual("partDrawingSnapshot.v1", payload["source"]["schema_version"])
            self.assertEqual("partDrawingRules.v1", payload["rule_version"])
            self.assertIn("geometry_match_mm", payload["tolerances"])
            self.assertEqual(
                [item.display_text for item in self.document.placed_dimensions],
                payload["placed_dimensions"],
            )
            self.assertEqual(
                "partCadDimensionStyle.v1",
                payload["dimension_style"]["style_id"],
            )
            self.assertEqual(2, payload["entity_counts"]["dimensions"])
            self.assertGreater(paths.pdf_path.stat().st_size, 1000)
            pdf = PdfReader(str(paths.pdf_path))
            self.assertEqual(1, len(pdf.pages))
            page_text = pdf.pages[0].extract_text()
            self.assertIn("P-1001", page_text)
            self.assertIn("200", page_text)
            self.assertIn("100", page_text)

    def test_pdf_renderer_consumes_each_shared_placed_dimension(self):
        with tempfile.TemporaryDirectory() as directory, patch(
            "zijinhua_tekla.part_drawing.drawing_output._draw_pdf_dimension",
            wraps=drawing_output._draw_pdf_dimension,
        ) as draw_dimension:
            render_part_drawing(self.document, Path(directory))
            self.assertEqual(
                len(self.document.placed_dimensions),
                draw_dimension.call_count,
            )

    def test_dxf_keeps_style_formatted_linear_value_measurement_driven(self):
        dimension = replace(
            self.document.placed_dimensions[0],
            measured_value_mm=243.174,
            display_text="243",
        )
        self.assertEqual(
            "<>",
            drawing_output._dxf_dimension_text(
                dimension,
                self.document.dimension_style,
            ),
        )

    def test_renderer_does_not_emit_files_for_rejected_document(self):
        document = replace(self.document, status=DrawingStatus.REJECTED)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "REJECTED"):
                render_part_drawing(document, Path(directory))
            self.assertFalse(list(Path(directory).iterdir()))

    def test_explicit_round_hole_is_present_in_shared_document_and_dxf(self):
        snapshot = replace(
            self.snapshot,
            holes=(HoleSnapshot("H1", "ROUND", Point3D(60.0, 70.0, 30.0), diameter=22.0),),
        )
        geometry = analyze_plate_geometry(snapshot).geometry
        features = recognize_plate_features(snapshot, geometry).features
        intents = generate_dimension_intents(snapshot, geometry, features).intents
        plan = plan_dimensions(geometry, features, intents)
        layout = optimize_dimension_layout(snapshot, geometry, plan, quantity=1)
        document = build_part_drawing_document(snapshot, geometry, features, layout, quantity=1)
        self.assertTrue(any(item.__class__.__name__ == "DrawingCircle" for item in document.inner_segments))
        center_lines = [item for item in document.annotation_lines if item.layer == "PART_CENTER"]
        self.assertEqual(2, len(center_lines))
        with tempfile.TemporaryDirectory() as directory:
            paths = render_part_drawing(document, Path(directory), cjk_font_path=CJK_FONT)
            modelspace = ezdxf.readfile(paths.dxf_path).modelspace()
            entities = list(modelspace.query("CIRCLE"))
            self.assertEqual(1, len(entities))
            dimensions = list(modelspace.query("DIMENSION"))
            self.assertEqual(4, len(dimensions))
            self.assertEqual(1, len(list(modelspace.query("MULTILEADER"))))

    def test_planned_hole_grid_renders_native_dimensions_with_provenance(self):
        snapshot = replace(
            self.snapshot,
            holes=tuple(
                HoleSnapshot(
                    f"H{index}",
                    "ROUND",
                    Point3D(10 + x, 20 + y, 30),
                    diameter=22,
                )
                for index, (x, y) in enumerate(
                    ((20, 25), (100, 25), (180, 25), (20, 75), (100, 75), (180, 75)),
                    1,
                )
            ),
        )
        geometry = analyze_plate_geometry(snapshot).geometry
        features = recognize_plate_features(snapshot, geometry).features
        intents = generate_dimension_intents(snapshot, geometry, features).intents
        plan = plan_dimensions(geometry, features, intents)
        layout = optimize_dimension_layout(snapshot, geometry, plan, quantity=1)
        document = build_part_drawing_document(snapshot, geometry, features, layout, quantity=1)

        with tempfile.TemporaryDirectory() as directory:
            paths = render_part_drawing(document, Path(directory), cjk_font_path=CJK_FONT)
            dxf = ezdxf.readfile(paths.dxf_path)
            dimensions = list(dxf.modelspace().query("DIMENSION"))
            multileaders = list(dxf.modelspace().query("MULTILEADER"))
            center_lines = list(dxf.modelspace().query('LINE[layer=="PART_CENTER"]'))
            payload = json.loads(paths.json_path.read_text(encoding="utf-8"))

            self.assertEqual(len(document.placed_dimensions), len(dimensions))
            self.assertFalse(list(dxf.modelspace().query('LINE[layer=="DIMENSION"]')))
            self.assertNotIn("6-\u00d822", {item.dxf.text for item in dimensions})
            self.assertTrue(all(item.dxf.dimstyle == "PART-CAD-V1" for item in dimensions))
            self.assertEqual(1, len(multileaders))
            self.assertEqual(12, len(center_lines))
            callout = next(
                item
                for item in payload["placed_callout_items"]
                if item["callout_id"] == "hole-grid-diameter"
            )
            self.assertEqual("6\u03a622", callout["display_text"])
            self.assertEqual(["H1", "H2", "H3", "H4", "H5", "H6"], callout["source_feature_ids"])

    def test_chinese_pdf_text_requires_explicit_cjk_font(self):
        document = replace(
            self.document,
            title_fields={**self.document.title_fields, "name": "连接板"},
        )
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(PartDrawingRenderError, "CJK"):
                render_part_drawing(document, Path(directory), cjk_font_path=None)
            self.assertFalse(list(Path(directory).iterdir()))

    def test_slot_is_rendered_as_two_lines_and_two_arcs(self):
        snapshot = replace(
            self.snapshot,
            holes=(HoleSnapshot("S1", "SLOT", Point3D(110, 70, 30), length=40, width=18),),
        )
        document = self._document_for(snapshot)
        slot_primitives = document.inner_segments
        self.assertEqual(2, sum(item.__class__.__name__ == "DrawingLine" for item in slot_primitives))
        self.assertEqual(2, sum(item.__class__.__name__ == "DrawingArc" for item in slot_primitives))

    def test_arc_inner_loop_remains_arc_entities_in_dxf(self):
        points = [Point3D(90, 70, 30), Point3D(110, 90, 30), Point3D(130, 70, 30), Point3D(110, 50, 30)]
        center = Point3D(110, 70, 30)
        loop = tuple(
            ContourSegmentSnapshot(
                "ARC",
                points[index],
                points[(index + 1) % 4],
                center=center,
                clockwise=True,
            )
            for index in range(4)
        )
        document = self._document_for(replace(self.snapshot, inner_loops=(loop,)))
        with tempfile.TemporaryDirectory() as directory:
            paths = render_part_drawing(document, Path(directory))
            self.assertEqual(4, len(list(ezdxf.readfile(paths.dxf_path).modelspace().query("ARC"))))

    def test_evidence_backed_bevel_renders_on_native_layer_and_serializes_evidence(self):
        feature = RecognizedFeature(
            "bevel-principal-face-1",
            FeatureType.BEVEL,
            (Point2D(10, 0), Point2D(10, 100)),
            {
                "setback": 10.0,
                "depth": 10.0,
                "angleDeg": 45.0,
                "angle_status": "PROVEN",
                "face_side": "NORMAL_NEGATIVE",
            },
            ("F1", "F2", "E8", "E4"),
            ("PRINCIPAL_FACE_SINGLE_EDGE_SETBACK",),
            1.0,
        )
        document = build_part_drawing_document(
            self.snapshot,
            self.geometry,
            (feature,),
            self.layout,
            quantity=1,
        )

        with tempfile.TemporaryDirectory() as directory:
            paths = render_part_drawing(document, Path(directory))
            modelspace = ezdxf.readfile(paths.dxf_path).modelspace()
            payload = json.loads(paths.json_path.read_text(encoding="utf-8"))

            self.assertEqual(1, len(list(modelspace.query('LINE[layer=="BEVEL"]'))))
            item = payload["recognized_feature_items"][0]
            self.assertEqual("BEVEL", item["feature_type"])
            self.assertEqual(["F1", "F2", "E8", "E4"], item["source_ids"])
            self.assertEqual("PROVEN", item["parameters"]["angle_status"])

    def _document_for(self, snapshot):
        geometry = analyze_plate_geometry(snapshot).geometry
        features = recognize_plate_features(snapshot, geometry).features
        intents = generate_dimension_intents(snapshot, geometry, features).intents
        layout = optimize_dimension_layout(snapshot, geometry, intents, quantity=1)
        return build_part_drawing_document(snapshot, geometry, features, layout, quantity=1)


if __name__ == "__main__":
    unittest.main()
