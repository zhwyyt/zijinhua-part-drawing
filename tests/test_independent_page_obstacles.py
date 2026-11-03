import copy
import json
import unittest
from pathlib import Path

from test_independent_visible_strokes import paint_fixture
from zijinhua_tekla.part_drawing.independent_layout.contracts import ContractError, digest, seal
from zijinhua_tekla.part_drawing.independent_layout.page_obstacles import (
    ObstacleIndex, _paint_obstacles, audit_page_obstacles, build_page_scene, geometry_json)
from zijinhua_tekla.part_drawing.independent_layout.visible_strokes import read_paint_states
from shapely.geometry import box, shape


def page_fixture(mask_after=True):
    arrow='10 10 m 12 14 l 8 14 l h f '
    cover='q 1 g 7 11 6 4 re f Q '
    data,ink=paint_fixture((arrow+cover if mask_after else cover+arrow)+
        '1 w 10 10 m 20 30 l S q 1 g 20 30 10 12 re f Q '
        'BT /F1 10 Tf 1 0 0 1 20 30 Tm <0001> Tj ET')
    capture=dict(page=dict(drawing=dict(Layout=dict(SheetSize=dict(Width=100,Height=100)))))
    ink=seal(dict(ink,status='ACTUAL_INK_BOUND',frameAlignment={'fixture':True},captureDigest=digest(capture)))
    arrow_path=0 if mask_after else 1
    primitive=seal(dict(status='PRIMITIVES_OBSERVED',inkDigest=ink['digest'],captureDigests=[digest(capture)]*3,
        comparisonToleranceNative=.1,samples=[dict(key='sample',kind='dimension',lines=[[[10,10],[20,30]]],
        arrows=[dict(pdfPath=arrow_path)],textLineIntersections=[],
        ink=dict(glyphIndices=[0],maskBox=[20,30,30,42],occupancyBox=[20,30,30,42]))]))
    return capture,primitive,ink,data


class PageObstacleTests(unittest.TestCase):
    def test_native_severe_clipping_and_cross_text_witnesses_remain_detectable(self):
        path=Path(__file__).parent/'fixtures/part_drawing/independent-obstacles/real-counterexamples.json'
        cases=json.loads(path.read_text('utf-8'))
        for case in cases:
            with self.subTest(case=case['case']):
                if case['kind']=='ARROW_LOSS':
                    raw=shape(case['raw']);visible=raw
                    for mask in case['masks']:visible=visible.difference(shape(mask))
                    self.assertAlmostEqual(case['expectedLoss'],1-visible.area/raw.area,places=12)
                    self.assertGreater(case['expectedLoss'],.75 if not case['accepted'] else .079)
                else:
                    index=ObstacleIndex([dict(id='other-text',geometry=case['text'])])
                    self.assertEqual(['other-text'],[o['id'] for o in index.hits(shape(case['line']))])

    def test_foreign_mask_and_paint_order_preserve_arrow_loss(self):
        for after,loss in ((True,True),(False,False)):
            args=page_fixture(after);scene=build_page_scene(*args);report=audit_page_obstacles(scene,args[1])
            codes=[c['code'] for c in report['samples'][0]['conflicts']]
            self.assertEqual(loss,'ARROW_OCCLUDED' in codes)
            self.assertFalse(report['productionEligible'])

    def test_acceptance_is_exact_dimension_observation_not_percentage_rule(self):
        args=page_fixture();scene=build_page_scene(*args)
        loss=audit_page_obstacles(scene,args[1])['samples'][0]['arrowLosses'][0]
        evidence=dict(key='sample',primitiveDigest=args[1]['digest'],pdfPath=loss['path'],
                      removedAreaFraction=loss['fraction'],basis='USER_ACCEPTED_SHORT_DIMENSION_CLIPPING')
        self.assertTrue(audit_page_obstacles(scene,args[1],[evidence])['samples'][0]['arrowLosses'][0]['accepted'])
        for field,value in (('primitiveDigest','wrong'),('pdfPath',999),('removedAreaFraction',.08)):
            bad=dict(evidence);bad[field]=value
            with self.subTest(field=field),self.assertRaises(ContractError):audit_page_obstacles(scene,args[1],[bad])

    def test_raw_stroke_is_retained_beneath_white_mask_for_candidates(self):
        data,ink=paint_fixture('1 w 10 20 m 30 20 l S q 1 g 5 15 30 10 re f Q')
        obstacles,gaps=_paint_obstacles(read_paint_states(data,ink))
        self.assertFalse(gaps)
        query=box(15,19,25,21)
        self.assertTrue(ObstacleIndex(obstacles).hits(query))
        self.assertFalse(ObstacleIndex([obstacles[0]],'visible').hits(query))

    def test_curves_use_conservative_bound_and_hairline_remains_unknown(self):
        data,ink=paint_fixture('2 w 10 10 m 10 30 30 30 30 10 c S 0 w 0 0 m 20 20 l S')
        obstacles,gaps=_paint_obstacles(read_paint_states(data,ink))
        self.assertFalse(obstacles[0]['exact'])
        self.assertTrue(ObstacleIndex(obstacles).hits(box(19,24,21,26)))
        self.assertEqual('path:1',gaps[0]['id'])

    def test_missing_glyph_outside_targets_is_not_silently_ignored(self):
        args=list(page_fixture());body={k:v for k,v in args[2].items() if k!='digest'}
        body['glyphs'].append(dict(index=1,inkBox=None,text='中',error='missing'))
        args[2]=seal(body);p={k:v for k,v in args[1].items() if k!='digest'};p['inkDigest']=args[2]['digest'];args[1]=seal(p)
        scene=build_page_scene(*args)
        self.assertEqual('glyph:1',scene['unresolved'][0]['id'])

    def test_digest_mismatch_and_ambiguous_mask_block(self):
        args=list(page_fixture());args[0]['changed']=True
        with self.assertRaises(ContractError):build_page_scene(*args)
        args=list(page_fixture());p={k:v for k,v in args[1].items() if k!='digest'}
        p['samples'][0]['ink']['maskBox']=[0,0,1,1];args[1]=seal(p)
        with self.assertRaisesRegex(ContractError,'MASK_OWNER'):build_page_scene(*args)

    def test_cross_object_text_collision_is_reported(self):
        args=page_fixture(False);scene=build_page_scene(*args)
        body={k:v for k,v in scene.items() if k!='digest'}
        g=copy.deepcopy(next(o for o in body['obstacles'] if o['kind']=='GLYPH'));g.update(id='glyph:extra',owner='other')
        body['obstacles'].append(g)
        report=audit_page_obstacles(seal(body),args[1])
        self.assertIn('TEXT_TEXT',[c['code'] for c in report['samples'][0]['conflicts']])

    def test_foreign_text_mask_is_a_reserved_obstacle_even_without_visible_ink(self):
        args=page_fixture(False);scene=build_page_scene(*args)
        body={k:copy.deepcopy(v) for k,v in scene.items() if k!='digest'}
        glyph=next(o for o in body['obstacles'] if o['kind']=='GLYPH')
        body['obstacles'].append(dict(id='mask:foreign',kind='MASK',owner=None,
                                     geometry=glyph['geometry'],visible=glyph['visible']))
        report=audit_page_obstacles(seal(body),args[1])
        self.assertIn('TEXT_FOREIGN_MASK',[c['code'] for c in report['samples'][0]['conflicts']])
