"""Regression cases from the authorized historical plate batch."""
import copy
import json
import math
from pathlib import Path
import unittest
from unittest.mock import patch
from shapely.geometry import box

from zijinhua_tekla.part_drawing.independent_layout.contracts import ContractError, seal
from zijinhua_tekla.part_drawing.independent_layout.expressions import adapt_expressions
from zijinhua_tekla.part_drawing.independent_layout.feature_geometry import source_curves, material_polygon
from zijinhua_tekla.part_drawing.independent_layout.view_primitives import model_point, source_basis
from zijinhua_tekla.part_drawing.analytic_geometry import project
from zijinhua_tekla.part_drawing.independent_layout.complete_trial_readback import verify_complete_trial
from zijinhua_tekla.part_drawing.independent_layout.feature_candidates import note_candidates, outside_labels
from zijinhua_tekla.part_drawing.independent_layout.foundation_studies import _nodes
from zijinhua_tekla.part_drawing.independent_layout.complete_trial import _dimension_candidates
from zijinhua_tekla.part_drawing.independent_layout.feature_locality import check_local_feature_placements


class FeatureTrialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sources=json.loads((Path(__file__).parent/'fixtures/part_drawing/independent-feature-sources.json').read_text('utf8'))
        cls.native=json.loads((Path(__file__).parent/'fixtures/part_drawing/independent-feature-225.json').read_text('utf8'))

    def audit_native(self, saved=None):
        saved=self.native['saved'] if saved is None else saved
        return verify_complete_trial(self.sources['T2-10B-225'],self.native['plan'],[saved,saved,saved])

    def test_actual_native_curves_notes_and_center_axes_survive_reopen(self):
        report=self.audit_native()
        self.assertEqual(45,report['features']['curves'])
        self.assertEqual(7,report['features']['holes'])
        self.assertEqual(20,len(report['displayBindings']))

    def test_missing_curve_shifted_arc_and_solid_centerline_are_rejected(self):
        bad=copy.deepcopy(self.native['saved']);bad['curves'].pop()
        with self.assertRaisesRegex(ContractError,'FEATURE_CURVE_COVERAGE'):self.audit_native(bad)
        bad=copy.deepcopy(self.native['saved'])
        curve=next(c for c in bad['curves'] if c['data']['type'].endswith('.Arc'))
        display=next(s for v in bad['page']['presentation'] for s in v['Primitives'] if s.get('Id')==curve['objectId'])
        arc=next(_nodes(display,'.ArcPrimitive'));arc['Bulge']=-arc['Bulge']
        with self.assertRaisesRegex(ContractError,'FEATURE_CURVE_DISPLAY'):self.audit_native(bad)
        bad=copy.deepcopy(self.native['saved'])
        axis=next(c for c in bad['curves'] if c['key'].endswith('/axis0'))
        display=next(s for v in bad['page']['presentation'] for s in v['Primitives'] if s.get('Id')==axis['objectId'])
        display['Pen']['LineType']=1
        with self.assertRaisesRegex(ContractError,'FEATURE_CURVE_STYLE'):self.audit_native(bad)

    def test_wrong_leader_anchor_and_table_thickness_are_rejected(self):
        bad=copy.deepcopy(self.native['saved'])
        next(r for r in bad['primitives'] if r['key'].startswith('hole-note/'))['children'][0]['data']['StartPoint']['X']+=1
        with self.assertRaisesRegex(ContractError,'FEATURE_NOTE_ANCHOR'):self.audit_native(bad)
        bad=copy.deepcopy(self.native['saved'])
        for t in _nodes(bad['page']['presentation'],'.TextPrimitive'):
            if t.get('Text','').startswith('PL12*'):t['Text']=t['Text'].replace('PL12*','PL13*')
        with self.assertRaisesRegex(ContractError,'TRIAL_TABLE'):self.audit_native(bad)

    def test_native_centered_note_ink_is_inside_candidate_footprint(self):
        source=self.sources['T2-10B-225'];facts=source['requirements']['facts']
        obj=next(o for o in adapt_expressions(source,complete_features=True)['objects'] if o['objectId']=='hole-note/group:0')
        # Measured native 1Phi150 ink relative to its insertion point (page 4).
        binding=next(b for b in self.native['inkBindings'] if b['key']==obj['objectId'])
        saved=next(r for r in self.native['saved']['primitives'] if r['key']==obj['objectId'])
        origin=self.native['saved']['page']['views'][0]['view']['Origin']
        at=saved['data']['InsertionPoint'];center=[origin[k]+at[k]/2 for k in ('X','Y')]
        relative=[binding['inkBox'][i]-center[i%2] for i in range(4)]
        rows=note_candidates(obj,facts,material_polygon(facts,2),2,25,140)
        self.assertTrue(rows)
        for row in rows:
            actual=box(*[relative[i]+row['at'][i%2] for i in range(4)])
            self.assertTrue(row['text'].covers(actual))
        for row in outside_labels({'objectId':'part-label'},[0,0,300,400],25,200):
            self.assertAlmostEqual(row['at'][0],row['text'].centroid.x)

    def test_102_remote_hole_datums_fail_even_when_values_are_correct(self):
        path=Path(__file__).parent/'fixtures/part_drawing/independent-feature-102-rejected.json'
        rejected=json.loads(path.read_text('utf8'))
        source=self.sources['T2-10B-102'];expressions=adapt_expressions(source,complete_features=True)
        with self.assertRaisesRegex(ContractError,'LOCAL_FEATURE_PLACEMENT'):
            verify_complete_trial(source,rejected['plan'],[rejected['saved']]*3)
        corrected=rejected['correctedPlan']
        check_local_feature_placements(expressions,corrected['placements'],corrected['scale'],corrected['height'])
        # Local metadata cannot conceal an actually persisted remote dimension.
        forged=copy.deepcopy(rejected['plan']);forged['placements']=corrected['placements']
        forged=seal({k:v for k,v in forged.items() if k!='digest'})
        with self.assertRaisesRegex(ContractError,'LOCAL_FEATURE_PLACEMENT'):
            verify_complete_trial(source,forged,[rejected['saved']]*3)
        report=verify_complete_trial(source,corrected,[rejected['correctedSaved']]*3)
        self.assertEqual('NATIVE_COMPLETE_PREVIEW',report['status'])

    def test_two_short_intervals_enclose_actual_native_outside_ink(self):
        path=Path(__file__).parent/'fixtures/part_drawing/independent-feature-102-rejected.json'
        f=json.loads(path.read_text('utf8'));source=self.sources['T2-10B-102']
        obj=next(o for o in adapt_expressions(source,complete_features=True)['objects'] if o['objectId']=='dimension/holes-1-3')
        polygon=material_polygon(source['requirements']['facts'],10)
        bounds=list(polygon.bounds)
        observed=next(r for r in f['plan']['placements'] if r['key']==obj['objectId'])
        bounds[0]=observed['ordinate']+25*4
        with patch('zijinhua_tekla.part_drawing.independent_layout.complete_trial._font_width',return_value=67.5):
            rows=_dimension_candidates(obj,bounds,10,25,None,levels=3,local_pair=True)
        chosen=next(r for r in rows if r['side']==-1 and r['level']==3)
        view=f['saved']['page']['views'][0]['view'];cs=view['DisplayCoordinateSystem'];zero=model_point(source,[0,0])
        offset=[view['Origin'][axis]+sum((zero[j]-cs['Origin'][k])*cs['Axis'+axis][k]
                for j,k in enumerate(('X','Y','Z')))/10 for axis in ('X','Y')]
        ink=[b for b in f['inkBindings'] if b['key']==obj['objectId']]
        self.assertEqual(2,len(ink))
        for b in ink:
            actual=box(*[b['inkBox'][i]-offset[i%2] for i in range(4)])
            self.assertTrue(chosen['text'].covers(actual))

    def test_equal_extreme_stations_do_not_attach_upper_chain_to_lower_edge(self):
        s=self.sources['T2-10B-225'];e=adapt_expressions(s,complete_features=True)
        group=next(g for g in e['scheme']['groups'] if g['key']=='boundary-0-1')
        self.assertEqual([1],group['allowedSides'])
        evidence=next(g for g in e['scheme']['boundaryPlanning']['groups'] if g['key']==group['key'])
        self.assertNotIn('edge:1',evidence['segments'])
        self.assertEqual('SEMANTICS_COMPLETE',e['status'])
        dims={o['schemeKey']:o for o in e['objects'] if o['kind']=='DIMENSION'}
        self.assertEqual([213,195],dims['holes-1-1']['displayValues'])
        self.assertEqual([123,90,90,105],dims['holes-1-0']['displayValues'])
        self.assertEqual([498],dims['overall-1']['displayValues'])

    def test_tekla_length_bearing_frame_round_trips_without_stretching(self):
        for name in ('T2-10B-451','T2-10B-450','T2-10B-618'):
            s=self.sources[name];frame=s['requirements']['partType']['frame']
            self.assertGreater(math.hypot(*frame['axisX']),100)
            axes=source_basis(s)
            self.assertAlmostEqual(1,math.hypot(*axes[0]))
            for point in s['requirements']['facts']['points'].values():
                actual=project(model_point(s,point),frame)
                self.assertLess(math.dist(actual[:2],point),1e-6)
        bad=copy.deepcopy(s);bad['requirements']['partType']['frame']['axisY']=frame['axisX']
        with self.assertRaisesRegex(ContractError,'VIEW_BASIS'):model_point(bad,[0,0])

    def test_225_has_six_complete_slots_one_circle_and_fourteen_center_axes(self):
        s=self.sources['T2-10B-225'];curves=source_curves(s,2,25)
        holes=[c for c in curves if c['key'].startswith('hole/')]
        self.assertEqual(12,sum(c['kind']=='arc' for c in holes))
        self.assertEqual(1,sum(c['kind']=='circle' for c in holes))
        self.assertEqual(14,sum(c.get('centerLine',False) for c in holes))
        self.assertEqual(150,next(c['radius']*2 for c in holes if c['kind']=='circle'))
        self.assertEqual(7,len(material_polygon(s['requirements']['facts']).interiors))

    def test_source_arcs_are_not_replaced_by_chords_or_full_circles(self):
        for name,count in [('T2-10B-451',2),('T2-10B-450',2),('T2-10B-618',1)]:
            s=self.sources[name];curves=source_curves(s,1,25)
            arcs=[c for c in curves if c['key'].startswith('segment/') and c['kind']=='arc']
            self.assertEqual(count,len(arcs))
            for c in arcs:
                edge=next(e for e in s['requirements']['facts']['segments'] if e['id']==c['feature'])
                self.assertAlmostEqual(edge['radius'],math.dist(c['start'],c['center']))
                self.assertAlmostEqual(edge['radius'],math.dist(c['end'],c['center']))


if __name__=='__main__':unittest.main()
