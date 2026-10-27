import copy
import json
import unittest
from pathlib import Path
from shapely.geometry import LineString

from test_native_material import plate_with_hole
from test_template_snapshot import evidence
from zijinhua_tekla.part_drawing.expression_requirements import content_digest
from zijinhua_tekla.part_drawing.native_expressions import build_native_expression_requirements
from zijinhua_tekla.part_drawing.native_layout import build_layout
from zijinhua_tekla.part_drawing.template_snapshot import build_template_snapshot, overlaps
from zijinhua_tekla.part_drawing.native_readback import _witness_visible


class NativeTemplateLayoutTests(unittest.TestCase):
    def data(self, font_height=2.5):
        raw = plate_with_hole()
        batch = build_native_expression_requirements(raw)
        part = raw['parts'][0]
        template = evidence()
        template['partId'] = part['id']
        snapshot = build_template_snapshot(template, 'fixture-model', part['id'], font_height)
        return batch['parts'][0], batch['geometryAudit']['parts'][0], part, snapshot

    def test_live_template_changes_paper_without_changing_scheme_anchors(self):
        req, geo, part, snapshot = self.data()
        before = copy.deepcopy((req, geo, part, snapshot))
        legacy = build_layout(req, geo, part, scale=1)
        plan = build_layout(req, geo, part, template=snapshot)
        self.assertEqual('READY', plan['status'])
        self.assertEqual(1, plan['scale'])
        self.assertEqual(1, plan['viewPlan']['scale'])
        self.assertEqual([2980, 2110], plan['sheet'])
        fields = ('key', 'axis', 'points', 'coordinates', 'values', 'displayValues')
        project = lambda p: [{k: d[k] for k in fields} for d in p['dimensions']]
        self.assertEqual(project(legacy), project(plan))
        self.assertFalse(any(overlaps(plan['viewPlan']['availableArea'], r['bounds'])
                             for r in snapshot['occupiedRegions']))
        self.assertEqual(before, (req, geo, part, snapshot))

    def test_style_changes_offsets_and_text_but_not_measurements_or_view_scale(self):
        req, geo, part, snapshot = self.data(3)
        legacy = build_layout(req, geo, part, scale=1)
        plan = build_layout(req, geo, part, template=snapshot)
        self.assertEqual('READY', plan['status'])
        self.assertEqual(1, plan['scale'])
        self.assertEqual(3, plan['viewPlan']['fontHeight'])
        self.assertEqual([d['values'] for d in legacy['dimensions']],
                         [d['values'] for d in plan['dimensions']])
        self.assertNotEqual([d['distance'] for d in legacy['dimensions']],
                            [d['distance'] for d in plan['dimensions']])
        self.assertEqual(3, next(n['height'] for n in plan['notes'] if n['kind'] == 'thickness'))

    def test_template_cannot_target_another_part_or_request_reduced_scale(self):
        req, geo, part, snapshot = self.data()
        for changed, scale in ((dict(part, id=999), None), (part, 2)):
            with self.subTest(scale=scale), self.assertRaisesRegex(ValueError, 'PART_OR_SCALE_MISMATCH'):
                build_layout(req, geo, changed, scale=scale, template=snapshot)

    def test_template_uses_the_smallest_series_scale_that_fits(self):
        req, geo, part, snapshot = self.data()
        fitted = build_layout(req, geo, part, template=snapshot)
        envelope = fitted['layout']['envelope']
        tight = _open_sheet(snapshot, envelope[2]-envelope[0]-1, envelope[3]-envelope[1]-1)
        with self.assertRaisesRegex(ValueError, 'LAYOUT_OVERFLOW'):
            build_layout(req, geo, part, scale=1, template=tight)
        plan = build_layout(req, geo, part, template=tight)
        series = (1, 2, 2.5, 5, 10, 20, 50)
        self.assertEqual('READY', plan['status'])
        self.assertIn(plan['scale'], series)
        self.assertGreater(plan['scale'], 1)
        self.assertEqual(plan['scale'], plan['viewPlan']['scale'])
        self.assertEqual(snapshot['sheetSize'], plan['sheet'])
        self.assertEqual([d['values'] for d in fitted['dimensions']],
                         [d['values'] for d in plan['dimensions']])
        self.assertNotEqual([d['distance'] for d in fitted['dimensions']],
                            [d['distance'] for d in plan['dimensions']])
        with self.assertRaisesRegex(ValueError, 'LAYOUT_OVERFLOW'):
            build_layout(req, geo, part, template=tight, _view_scale=series[series.index(plan['scale'])-1])

    def test_scale_50_still_overflows(self):
        req, geo, part, snapshot = self.data()
        with self.assertRaisesRegex(ValueError, 'LAYOUT_OVERFLOW'):
            build_layout(req, geo, part, template=_open_sheet(snapshot, 1, 1))

    def test_live_concave_plate_has_complete_readable_plan_at_template_style_25(self):
        raw = json.loads((Path(__file__).parent/'fixtures/part_drawing/template-source-live.json').read_text(encoding='utf-8-sig'))
        before = copy.deepcopy(raw)
        batch = build_native_expression_requirements(raw)
        part = raw['parts'][0]
        template = evidence()
        template.update(partId=part['id'], modelPath=raw['model']['value']['path'])
        snapshot = build_template_snapshot(template, template['modelPath'], part['id'])
        plan = build_layout(batch['parts'][0], batch['geometryAudit']['parts'][0], part, template=snapshot)
        self.assertEqual('READY', plan['status'])
        self.assertEqual('PLAN_COMPLETE', plan['coverage']['status'])
        self.assertEqual('READABLE', plan['readability']['status'])
        self.assertEqual(7, len(plan['dimensions']))
        self.assertEqual(25, plan['viewPlan']['fontHeight'])
        self.assertTrue(plan['schemeDecisions'])
        self.assertEqual(before, raw)

    def test_exported_witness_gap_requires_actual_text_mask_and_remaining_line_evidence(self):
        expected = LineString([(50, -30), (50, -240)])
        render = dict(lines=[[(50, -30), (50, -79)], [(50, -114), (50, -240)]],
                      texts=[dict(min=[32.5, -109.4], max=[77.5, -83.9], height=25)])
        self.assertTrue(_witness_visible(expected, render, .1, 1))
        self.assertFalse(_witness_visible(expected, dict(render, texts=[]), .1, 1))
        self.assertFalse(_witness_visible(expected, dict(render, lines=[]), .1, 1))
        bad = dict(render, lines=[[(50, -30), (50, -70)], [(50, -114), (50, -240)]])
        self.assertFalse(_witness_visible(expected, bad, .1, 1))


def _open_sheet(snapshot, width, height):
    changed = copy.deepcopy(snapshot)
    changed['safePage'] = [0, 0, width, height]
    changed['occupiedRegions'] = []
    body = {key: value for key, value in changed.items() if key != 'snapshotDigest'}
    changed['snapshotDigest'] = content_digest(body)
    return changed
