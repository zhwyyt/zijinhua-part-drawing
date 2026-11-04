"""Independent internal features keep their dimensions nearby regardless of shape."""
import copy
import unittest
from unittest.mock import patch

from shapely.geometry import LineString
from test_cut_final_state import polygon
from test_independent_cutouts import sample
from test_independent_expressions import source as simple_source, synthetic_facts
from zijinhua_tekla.part_drawing.native_expressions import _facts
from zijinhua_tekla.part_drawing.independent_layout.contracts import ContractError
from zijinhua_tekla.part_drawing.independent_layout.expressions import adapt_expressions, _dimension
from zijinhua_tekla.part_drawing.independent_layout.sources import freeze_native_sources
from zijinhua_tekla.part_drawing.independent_layout.feature_locality import (
    internal_feature_localities, check_local_feature_placements)
from zijinhua_tekla.part_drawing.independent_layout.complete_trial import (
    _dimension_candidates, _feature_dimension_candidates, _solve, plan_complete_trial)


def policy_case(kind, sign=1, dx=0, dy=0):
    transform=lambda p:[sign*p[0]+dx,p[1]+dy]
    opening=[[400,250],[600,250],[600,350],[400,350]]
    if kind=='CHAMFERED':
        opening=[[410,250],[590,250],[600,260],[600,340],[590,350],[410,350],[400,340],[400,260]]
    hole=dict(kind=kind,center=transform([500,300]),diameter=100,extension=100,
              direction=[1,0],sourceRefs=['fixture'])
    if kind in ('RECTANGLE','CHAMFERED'):
        hole=dict(kind='CUTOUT',segments=polygon([transform(p) for p in opening]))
    facts=_facts(dict(outline=dict(segments=polygon([transform(p) for p in [[0,0],[1000,0],[1000,600],[0,600]]])),holes=[hole]))
    if facts['holes']:
        refs=['vertex:0',facts['holes'][0]['pointId']];evidence={};role='location'
    else:
        loop=facts['boundaryLoops'][1];edge=next(s for s in facts['segments'] if s['id']==loop['segments'][0])
        refs=[edge['startPoint'],edge['endPoint']]
        evidence=dict(cutoutEvidence=dict(loopId=loop['id'],segmentId=edge['id']));role='cutout_size'
    group=dict(key='fixture',axis=0,points=sorted(refs,key=lambda k:facts['points'][k][0]),
               side=1,allowedSides=[-1,1],role=role,localAllowed=False,**evidence)
    obj=_dimension(group,facts,0)
    return dict(expressionRequirements=dict(facts=facts),scheme=dict(groups=[group]),objects=[obj])


class InternalFeatureLocalityTests(unittest.TestCase):
    def candidates(self,e):
        obj=e['objects'][0];facts=e['expressionRequirements']['facts']
        locality=internal_feature_localities(e)[obj['objectId']]
        with patch('zijinhua_tekla.part_drawing.independent_layout.complete_trial._font_width',return_value=10):
            return _feature_dimension_candidates(obj,facts['bounds'],1,10,None,True,4,locality,facts)

    def test_round_slot_rectangle_chamfer_share_priority_without_allow_flag(self):
        for kind in ('ROUND','SLOT','RECTANGLE','CHAMFERED'):
            with self.subTest(kind=kind):
                e=policy_case(kind);before=copy.deepcopy(e);rows=self.candidates(e)
                self.assertTrue(rows)
                for row in rows:
                    check_local_feature_placements(e,[row],1,10)
                    self.assertLess(abs(row['ordinate']-300),100)
                self.assertEqual(before,e,'Local layout must not change geometry/anchors')
                with self.assertRaisesRegex(ContractError,'LOCAL_FEATURE_PLACEMENT'):
                    check_local_feature_placements(e,[dict(key=e['objects'][0]['objectId'],ordinate=700)],1,10)

    def test_real_sharp_and_rounded_cutout_semantics_bind_size_and_location(self):
        for rounded in (False,True):
            source=freeze_native_sources(sample(rounded),extend_cutouts=True)['entries'][0]
            e=adapt_expressions(source);local=internal_feature_localities(e)
            dims=[o for o in e['objects'] if o.get('role') in ('cutout_size','cutout_location')]
            self.assertEqual(4,len(dims))
            self.assertTrue(all(o['objectId'] in local for o in dims))
            self.assertEqual(1,len({local[o['objectId']]['featureId'] for o in dims}))
            # Common policy is not a new native cutout admission route.
            with self.assertRaisesRegex(ContractError,'FEATURE_SCOPE'):
                plan_complete_trial(source,'unused-font',[0,0,2000,1000],complete_features=True)

    def test_chamfer_policy_does_not_hide_missing_machining_contract(self):
        e=policy_case('CHAMFERED');facts=e['expressionRequirements']['facts']
        result=adapt_expressions(synthetic_facts(simple_source(),facts),complete_cutouts=True)
        self.assertEqual('UPSTREAM_CONTRACT_GAP',result['status'])
        self.assertTrue(result['scheme']['cutoutDiagnostics'])

    def test_translation_and_mirror_keep_feature_local_candidates(self):
        for kind in ('ROUND','SLOT','RECTANGLE','CHAMFERED'):
            for sign,dx,dy in ((1,1200,-300),(-1,0,0)):
                e=policy_case(kind,sign,dx,dy);rows=self.candidates(e)
                self.assertTrue(rows)
                for row in rows:check_local_feature_placements(e,[row],1,10)
                bounds=internal_feature_localities(e)[e['objects'][0]['objectId']]['bounds']
                self.assertAlmostEqual((bounds[0]+bounds[2])/2,sign*500+dx)
                self.assertAlmostEqual((bounds[1]+bounds[3])/2,300+dy)

    def test_wrong_loop_and_outer_loop_binding_are_rejected(self):
        for wrong in ('outer','another-opening'):
            e=policy_case('RECTANGLE');e['scheme']['groups'][0]['cutoutEvidence']['loopId']=wrong
            with self.assertRaisesRegex(ContractError,'LOCAL_FEATURE_BINDING'):internal_feature_localities(e)

    def test_multiple_same_shape_openings_bind_to_their_own_loop(self):
        material=dict(outline=dict(segments=polygon([[0,0],[100,0],[100,60],[0,60]])),
                      holes=[dict(kind='CUTOUT',segments=polygon([[x,20],[x+10,20],[x+10,40],[x,40]])) for x in (20,70)])
        facts=_facts(material)
        e=adapt_expressions(synthetic_facts(simple_source(),facts),complete_cutouts=True)
        local=internal_feature_localities(e)
        for g in e['scheme']['groups']:
            if 'cutoutEvidence' in g:
                self.assertEqual(g['cutoutEvidence']['loopId'],local['dimension/'+g['key']]['featureId'])

    def test_multi_hole_band_and_overall_are_not_single_feature_dimensions(self):
        e=policy_case('ROUND');facts=e['expressionRequirements']['facts'];obj=e['objects'][0]
        hole=copy.deepcopy(facts['holes'][0]);hole.update(id='second',pointId='second-center')
        facts['holes'].append(hole);facts['points']['second-center']=[700,300]
        # A second hole may share a projected station without a second anchor.
        obj['band']=[facts['holes'][0]['pointId'],'second-center']
        self.assertEqual({},internal_feature_localities(e))
        obj['band']=[];obj['role']='overall'
        self.assertEqual({},internal_feature_localities(e))

    def test_cheaper_remote_candidates_do_not_compete_and_empty_local_pool_blocks(self):
        e=policy_case('RECTANGLE');obj=e['objects'][0];facts=e['expressionRequirements']['facts']
        rows=self.candidates(e)
        for row in rows:row['cost']=1000000
        with patch('zijinhua_tekla.part_drawing.independent_layout.complete_trial._font_width',return_value=10):
            remote=_dimension_candidates(obj,facts['bounds'],1,10,None)
        self.assertLess(min(r['cost'] for r in remote),min(r['cost'] for r in rows))
        contour=LineString([[-100,-100],[-100,100]])
        chosen,_=_solve(rows,[obj['objectId']],[0,0,2000,1000],10,contour)
        self.assertIsNotNone(chosen);check_local_feature_placements(e,chosen,1,10)
        with patch('zijinhua_tekla.part_drawing.independent_layout.complete_trial._dimension_candidates',return_value=[]):
            self.assertEqual([],self.candidates(e))
        chosen,report=_solve([],[obj['objectId']],[0,0,2000,1000],10,contour)
        self.assertIsNone(chosen);self.assertEqual('INFEASIBLE',report['status'])


if __name__=='__main__':unittest.main()
