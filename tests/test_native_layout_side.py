import copy
import base64
import json
import tempfile
import unittest
from pathlib import Path

import test_native_layout

from zijinhua_tekla.part_drawing.dimension_scheme import build_scheme
from zijinhua_tekla.part_drawing.native_expressions import build_native_expression_requirements
from zijinhua_tekla.part_drawing.expression_requirements import _requirements
from zijinhua_tekla.part_drawing.native_layout import (build_layout, place_dimension_side,
                                                       boundary_edge_side, plate_polygon)
from zijinhua_tekla.part_drawing.plate_features import build_feature_model
from zijinhua_tekla.part_drawing.template_snapshot import build_template_snapshot
from test_template_snapshot import evidence
from zijinhua_tekla.part_drawing.native_readback import audit_native_drawing, _witness_visible
from zijinhua_tekla.part_drawing.native_render import load_render
from shapely.geometry import LineString


FIXTURES = Path(__file__).parent/'fixtures/part_drawing'


class NativeLayoutSideTests(unittest.TestCase):
    def test_saved_side_change_uses_actual_text_layout_box_to_explain_clipping(self):
        root = FIXTURES/'layout-side-saved-live'
        e = json.loads((root/'evidence.json').read_text(encoding='utf-8'))
        with tempfile.TemporaryDirectory() as directory:
            dxf = Path(directory)/'drawing.dxf'
            dxf.write_bytes(base64.b64decode(e['dxfBase64']))
            result = audit_native_drawing(e['requirements'], e['plan'], e['readback'], dxf)
            render = load_render(dxf, e['readback'])
        self.assertEqual('PASS', result['status'], result)
        row = next(o for o in e['readback']['objects'] if o['key'] == 'witness:boundary-0-3:1')
        line = LineString([row['start'][:2], row['end'][:2]])
        self.assertTrue(_witness_visible(line, render, .1, 1))
        glyph_only = [{k: v for k, v in t.items() if k not in ('layoutMin', 'layoutMax')} for t in render['texts']]
        self.assertFalse(_witness_visible(line, dict(render, texts=glyph_only), .1, 1))
        self.assertFalse(_witness_visible(line, dict(render, texts=[]), .1, 1))
        self.assertFalse(_witness_visible(line, dict(render, lines=[]), .1, 1))
        # Real measured typography cannot excuse a gap away from the text.
        clipped = [p for p in render['lines'] if not (abs(p[0][0]-line.coords[0][0]) < .1 and
                                                     min(q[1] for q in p) < 300)]
        self.assertFalse(_witness_visible(line, dict(render, lines=clipped), .1, 1))

    def test_live_concave_chain_moves_above_with_exact_same_content(self):
        raw = json.loads((FIXTURES/'template-source-live.json').read_text(encoding='utf-8-sig'))
        saved = json.loads((FIXTURES/'m4-saved-live/evidence.json').read_text(encoding='utf-8-sig'))
        batch = build_native_expression_requirements(raw)
        req, geo, part = batch['parts'][0], batch['geometryAudit']['parts'][0], raw['parts'][0]
        paper = evidence()
        paper.update(partId=part['id'], modelPath=raw['model']['value']['path'])
        template = build_template_snapshot(paper, paper['modelPath'], part['id'])
        plan = build_layout(req, geo, part, template=template)
        fields = ('key', 'axis', 'points', 'coordinates', 'values', 'displayValues', 'role', 'combine', 'combineRuns')
        content = lambda p: [{k: d[k] for k in fields} for d in p['dimensions']]
        self.assertEqual(content(saved['plan']), content(plan))
        self.assertEqual(saved['plan']['notes'], plan['notes'])
        dims = {d['key']: d for d in plan['dimensions']}
        self.assertEqual([109, 58, 400], dims['boundary-0-0']['displayValues'])
        self.assertEqual([0, 1], dims['boundary-0-0']['up'])
        self.assertEqual([0, -1], dims['boundary-0-1']['up'])
        self.assertEqual([0, 1], dims['boundary-0-3']['up'])
        self.assertEqual('READY', plan['status'])
        self.assertEqual('PLAN_COMPLETE', plan['coverage']['status'])
        self.assertEqual('READABLE', plan['readability']['status'])
        scheme = build_scheme(req, build_feature_model(req['facts'], req['modelToleranceMm']))
        for group in scheme['groups']:
            self.assertEqual([-1, 1] if group['key'].startswith('boundary') else [group['side']],
                             group['allowedSides'])

    def test_concave_notch_side_follows_rotation_mirror_translation_and_scale(self):
        # Independent top notch: its outward normal points down, its anchors are near the top.
        outline = [(0, 0), (100, 0), (100, 80), (60, 80), (60, 60), (30, 60), (30, 80), (0, 80)]
        anchors = [outline[i] for i in (7, 5, 4, 2)]
        for axis in (0, 1):
            for sign in (-1, 1):
                for scale in (1, 7):
                    with self.subTest(axis=axis, sign=sign, scale=scale):
                        def transform(p):
                            q = [p[0]*scale+123, p[1]*sign*scale-76]
                            return q if axis == 0 else q[::-1]
                        pts, ring = list(map(transform, anchors)), list(map(transform, outline))
                        bounds = [min(p[0] for p in ring), min(p[1] for p in ring),
                                  max(p[0] for p in ring), max(p[1] for p in ring)]
                        group = dict(key='notch', axis=axis, side=-sign, allowedSides=[-1, 1])
                        before = copy.deepcopy((group, pts, bounds))
                        decision = place_dimension_side(group, pts, bounds, .1*scale)
                        self.assertEqual(sign, decision['selected'])
                        self.assertEqual(before, (group, pts, bounds))

    def test_sloping_plate_puts_each_edge_on_its_own_side(self):
        # Lower-edge stations stay nearer the global top on a slope. They still
        # belong below; extension lines leave those anchors and miss the upper edge.
        req, geo, raw = test_native_layout.NativeLayoutTests().shoulder_data()
        xy = [(0, 50), (45, 0), (120, 0), (170, -30), (200, -55), (198, -20), (130, 20), (60, 50)]
        points = {f'vertex:{i}': [x, y] for i, (x, y) in enumerate(xy)}
        segments = []
        for i in range(len(xy)):
            a, b = f'vertex:{i}', f'vertex:{(i+1) % len(xy)}'
            segments.append(dict(id=f'edge:{i}', kind='LINE', start=points[a], end=points[b],
                                 startPoint=a, endPoint=b))
        req['facts'] = dict(points=points, segments=segments, holes=[], datum='vertex:0',
                            bounds=[min(p[0] for p in xy), min(p[1] for p in xy),
                                    max(p[0] for p in xy), max(p[1] for p in xy)],
                            boundaryLoops=[dict(segments=[s['id'] for s in segments])])
        req['requirements'], req['groups'] = _requirements(req['source']['partPosition'], 8, req['facts'], {})
        req['sectionRequirements'] = []
        plan = build_layout(req, geo, raw)
        self.assertEqual('READY', plan['status'], plan.get('coverage'))
        dims = [d for d in plan['dimensions'] if d['key'].startswith('boundary-0-')]
        lower = next(d for d in dims if any(abs(p[0]-120) < .1 and abs(p[1]) < .1 for p in d['coordinates']))
        upper = next(d for d in dims if any(abs(p[0]-130) < .1 and abs(p[1]-20) < .1 for p in d['coordinates']))
        self.assertEqual([0, -1], lower['up'])
        self.assertEqual([0, 1], upper['up'])
        interior = plate_polygon(req['facts']).buffer(-req['modelToleranceMm'])
        for dim, sign in ((lower, -1), (upper, 1)):
            for curve in plan['curves']:
                if curve['role'] != 'witness' or curve['feature'] != dim['key']:
                    continue
                index = int(curve['key'].rsplit(':', 1)[-1])
                point = dim['coordinates'][index]
                self.assertGreater(sign*(curve['start'][1]-point[1]), 0)
                self.assertFalse(interior.intersects(LineString([curve['start'], curve['end']])))
        upper_stations = {round(p[0], 1) for p in upper['coordinates']}
        self.assertTrue({45.0, 120.0, 170.0}.isdisjoint(upper_stations))

    def test_edge_vote_abstains_without_a_mate_and_ignores_shared_corners(self):
        lower = [[0, 50], [45, 0], [120, 0], [200, -55]]
        upper = [[0, 50], [60, 50], [130, 20], [200, -55]]
        self.assertEqual(-1, boundary_edge_side(lower, [upper], 0, .1))
        self.assertEqual(1, boundary_edge_side(upper, [lower], 0, .1))
        self.assertIsNone(boundary_edge_side(lower, [], 0, .1))
        self.assertIsNone(boundary_edge_side([[0, 0], [10, 5], [20, 0]], [[[0, 1], [10, 4], [20, -1]]], 0, .1))

    def test_locked_and_legacy_groups_keep_their_side_and_ties_keep_preference(self):
        for extra in ({}, dict(allowedSides=[-1])):
            group = dict(key='locked', axis=0, side=-1, **extra)
            self.assertEqual(-1, place_dimension_side(group, [[0, 90], [100, 90]], [0, 0, 100, 100], .1)['selected'])
        for side in (-1, 1):
            group = dict(key='tie', axis=0, side=side, allowedSides=[-1, 1])
            self.assertEqual(side, place_dimension_side(group, [[0, 50], [100, 50.05]], [0, 0, 100, 100], .1)['selected'])
