import copy
import json
import unittest
from pathlib import Path

from zijinhua_tekla.part_drawing.template_snapshot import build_template_snapshot, overlaps, validate_snapshot
from zijinhua_tekla.part_drawing.view_plan import build_view_plan, audit_view_plan


def evidence():
    return json.loads((Path(__file__).parent/'fixtures/part_drawing/template-live-paper.json').read_text(encoding='utf-8'))


class TemplateSnapshotTests(unittest.TestCase):
    def test_live_frame_is_separated_from_same_segment_title_and_notes(self):
        raw = evidence(); before = copy.deepcopy(raw)
        s = build_template_snapshot(raw, 'fixture-model', 1)
        self.assertEqual(before, raw)
        self.assertEqual([55, 55, 2925, 2055], s['frames'][0])
        text = [r['text'] for r in s['occupiedRegions'] if r['kind'] == 'TextPrimitive']
        self.assertIn('说明：', text)
        self.assertIn('所属构件', text)
        self.assertIn('浙江大东吴建筑科技有限公司', text)
        self.assertNotIn('Tekla structures', text)  # Outside the inner frame.
        self.assertTrue(any(r['kind'] == 'LinePrimitive' for r in s['occupiedRegions']))
        v = build_view_plan(s, [0, 0, 900, 900])
        self.assertGreater(v['availableArea'][1], 694)
        self.assertFalse(any(overlaps(v['availableArea'], r['bounds']) for r in s['occupiedRegions']))

    def test_live_text_motion_changes_available_area_without_part_specific_rules(self):
        raw = evidence()
        def move(o):
            if isinstance(o, dict):
                if o.get('Text') == '说明：':
                    o['Text'] = 'Different project note'; o['Position']['Y'] = 1000
                for v in o.values(): move(v)
            elif isinstance(o, list):
                for v in o: move(v)
        first = build_template_snapshot(raw, 'fixture-model', 1)
        move(raw)
        second = build_template_snapshot(raw, 'fixture-model', 1)
        self.assertNotEqual(first['snapshotDigest'], second['snapshotDigest'])
        self.assertFalse(overlaps(build_view_plan(second, [0, 0, 900, 900])['availableArea'], [55, 970, 2925, 1060]))

    def test_changed_table_geometry_is_retained(self):
        raw = evidence()
        paper = raw['paper']['Primitives']
        paper.append(dict(type='Tekla.Structures.DrawingPresentationModel.LinePrimitive',
                          StartPoint=dict(X=100, Y=1200), EndPoint=dict(X=1500, Y=1200)))
        s = build_template_snapshot(raw, 'fixture-model', 1)
        self.assertTrue(any(r['bounds'][1] == 1187.5 for r in s['occupiedRegions']))

    def test_missing_unknown_nonfinite_or_wrong_source_blocks(self):
        raw = evidence()
        for bad in (dict(raw, partId=2), dict(raw, modelPath='other-model'), dict(raw, signature='')):
            with self.assertRaises(ValueError): build_template_snapshot(bad, 'fixture-model', 1)
        raw['paper']['Primitives'] = []
        with self.assertRaisesRegex(ValueError, 'FRAME_UNAVAILABLE'):
            build_template_snapshot(raw, 'fixture-model', 1)
        raw = evidence(); raw['paper']['Primitives'].append(dict(type='UnknownPrimitive'))
        with self.assertRaisesRegex(ValueError, 'UNSUPPORTED'):
            build_template_snapshot(raw, 'fixture-model', 1)
        raw = evidence(); raw['sheetSize'][0] = float('nan')
        with self.assertRaisesRegex(ValueError, 'SHEET_INVALID'):
            build_template_snapshot(raw, 'fixture-model', 1)

    def test_rotated_template_text_is_not_silently_dropped(self):
        raw = evidence()
        raw['paper']['Primitives'].append(dict(type='TextPrimitive', Position=dict(X=100, Y=1000), Height=25, Angle=.5, Text='rotated'))
        with self.assertRaisesRegex(ValueError, 'ROTATED_TEXT'):
            build_template_snapshot(raw, 'fixture-model', 1)

    def test_fixed_scale_overflow_and_snapshot_tampering(self):
        s = build_template_snapshot(evidence(), 'fixture-model', 1)
        with self.assertRaisesRegex(ValueError, 'LAYOUT_OVERFLOW'):
            build_view_plan(s, [0, 0, 10000, 10000])
        with self.assertRaisesRegex(ValueError, 'LAYOUT_OVERFLOW'):
            build_view_plan(s, [0, 0, 5000, 400])
        fitted = build_view_plan(s, [0, 0, 5000, 400], 2)
        same = build_view_plan(s, [0, 0, 2500, 200])
        self.assertEqual(2, fitted['scale'])
        self.assertEqual([0, 0, 5000, 400], fitted['plannedEnvelope'])
        self.assertEqual(same['availableArea'], fitted['availableArea'])
        raw = dict(origin=fitted['availableArea'][:2], frameOrigin=[0, 0], width=2500, height=200,
                   scale=2, sheetSize=s['sheetSize'], templateSignature=fitted['templateSignature'])
        self.assertEqual([], audit_view_plan(dict(viewPlan=fitted), raw))
        raw['scale'] = 1
        self.assertEqual('VIEW_TEMPLATE_SIZE_OR_SCALE_CHANGED', audit_view_plan(dict(viewPlan=fitted), raw)[0]['code'])
        s['occupiedRegions'] = []
        with self.assertRaisesRegex(ValueError, 'DIGEST'):
            validate_snapshot(s)

    def test_saved_actual_view_cannot_cross_template_or_change_scale(self):
        s = build_template_snapshot(evidence(), 'fixture-model', 1)
        v = build_view_plan(s, [0, 0, 900, 900]); lo=v['availableArea'][:2]
        raw=dict(origin=lo, frameOrigin=[0, 0], width=900, height=900, scale=1,
                 sheetSize=s['sheetSize'], templateSignature=v['templateSignature'])
        self.assertEqual([], audit_view_plan(dict(viewPlan=v), raw))
        raw['origin']=[100, 100]
        self.assertEqual('VIEW_OUTSIDE_TEMPLATE_FREE_AREA', audit_view_plan(dict(viewPlan=v), raw)[0]['code'])
        raw['templateSignature']='changed'; raw['scale']=2
        self.assertEqual(3, len(audit_view_plan(dict(viewPlan=v), raw)))

    def test_invalid_view_measurements_never_pass_containment(self):
        s = build_template_snapshot(evidence(), 'fixture-model', 1)
        with self.assertRaisesRegex(ValueError, 'ENVELOPE_INVALID'):
            build_view_plan(s, [0, 0, float('nan'), 100])
        v = build_view_plan(s, [0, 0, 900, 900])
        raw = dict(origin=v['availableArea'][:2], frameOrigin=[0, 0], width=900, height=900,
                   sheetSize=s['sheetSize'], scale=1, templateSignature=v['templateSignature'])
        for width in (0, -10, float('nan'), float('inf')):
            self.assertEqual([dict(code='VIEW_BOUNDS_INVALID')],
                             audit_view_plan(dict(viewPlan=v), dict(raw, width=width)))
