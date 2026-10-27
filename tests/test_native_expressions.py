"""Requirements consume audited final boundaries and preserve native provenance."""

import copy
import json
from pathlib import Path
import tempfile
import unittest

from test_native_facts import read
from test_native_material import plate_with_hole
from test_cut_final_state import evidence, final_solid, operation, circle_points
from test_native_geometry import prism, RECT
from zijinhua_tekla.cli import main
from zijinhua_tekla.part_drawing.native_expressions import build_native_expression_requirements
from zijinhua_tekla.part_drawing.expression_coverage import audit_expression_coverage
from zijinhua_tekla.part_drawing.expression_requirements import _hole_groups, _pitch_candidates


class NativeExpressionTests(unittest.TestCase):
    def plan(self, raw):
        return build_native_expression_requirements(raw)['parts'][0]

    def test_direct_native_input_generates_complete_hole_requirements_without_legacy_context(self):
        raw = plate_with_hole()
        before = copy.deepcopy(raw)
        p = self.plan(raw)
        self.assertEqual('READY',p['status'],p['issues'])
        self.assertIn('nativeInputDigest',p['source'])
        self.assertNotIn('snapshotDigest',p['source'])
        self.assertEqual([10], [h['diameter'] for h in p['facts']['holes']])
        aspects = {r['aspect'] for r in p['requirements']}
        self.assertTrue({'shape','center_cross','location_x','location_y','size_count','thickness'} <= aspects)
        self.assertEqual('PENDING',audit_expression_coverage(p)['status'])
        self.assertEqual(before,raw)

    def test_contained_hole_is_not_counted_or_positioned_again(self):
        raw = plate_with_hole()
        part = raw['parts'][0]
        part['operations'] = read([operation(30,center=[40,30],radius=10)])
        part['children'] = read([{'id':30,'runtimeType':'Tekla.Structures.Model.BooleanPart'}])
        part['finalSolid'] = read(final_solid(holes=[(circle_points([40,30],10),30)]))
        p = self.plan(raw)
        self.assertEqual('READY',p['status'])
        self.assertEqual(1,len(p['facts']['holes']))
        self.assertEqual(20,p['facts']['holes'][0]['diameter'])
        self.assertEqual(1,sum(g['count'] for g in p['groups']))
        self.assertEqual(2,len(p['materialEvidence']['sourceMappings']))

    def test_final_notch_and_internal_cutout_have_explicit_shape_and_position_requirements(self):
        raw = evidence([operation(30,points=[[60,20],[75,20],[75,35],[60,35]])],
                       final_solid(holes=[([[60,20],[75,20],[75,35],[60,35]],30)]))
        p = self.plan(raw)
        self.assertEqual('READY',p['status'])
        self.assertEqual(2,len(p['facts']['boundaryLoops']))
        self.assertEqual(8,len(p['facts']['segments']))
        self.assertEqual([],p['facts']['holes'])
        self.assertEqual(4,len([r for r in p['requirements'] if r['feature'].startswith('cutout:') and r['aspect']=='shape']))
        self.assertEqual(8,len([r for r in p['requirements'] if r['feature'].startswith('cutout:') and r['aspect'].startswith('location_')]))

    def test_unresolved_or_stale_source_cannot_be_forced_ready_by_context_flags(self):
        raw = plate_with_hole()
        del raw['parts'][0]['guid']
        r = build_native_expression_requirements(raw,specifications={'checks':{'all_holes':{'status':'VERIFIED'}}})
        self.assertEqual('BLOCKED',r['parts'][0]['status'])
        self.assertEqual({},r['parts'][0]['facts'])
        self.assertEqual([],r['parts'][0]['requirements'])

    def test_stud_generates_no_hole_expressions(self):
        raw = plate_with_hole()
        part = raw['parts'][0]
        part['finalSolid'] = read(prism(RECT))
        part['boltGroups']['value'][0]['properties']['BoltStandard'] = read('STUD')
        p = self.plan(raw)
        self.assertEqual('READY',p['status'])
        self.assertEqual([],p['facts']['holes'])
        self.assertFalse(any(r['aspect'] in ('size_count','center_cross') for r in p['requirements']))

    def test_source_digest_binds_rule_budget_and_rejects_stale_observations(self):
        raw = plate_with_hole()
        p = self.plan(raw)
        other = build_native_expression_requirements(raw,model_tolerance_mm=.05)['parts'][0]
        self.assertNotEqual(p['sourceDigest'],other['sourceDigest'])
        result = audit_expression_coverage(p,{'schemaVersion':'expressionObservations.v1','sourceDigest':other['sourceDigest'],
                                              'stage':'READBACK','objects':[]})
        self.assertEqual('FAIL',result['status'])

    def test_cli_preserves_sources_and_existing_reports(self):
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory)/'native.json', Path(directory)/'plan.json'
            source.write_text(json.dumps(plate_with_hole()),encoding='utf8')
            original = source.read_bytes()
            args = ['plan-native-expressions','--evidence',str(source),'--out',str(output)]
            self.assertEqual(0,main(args))
            self.assertEqual(1,json.loads(output.read_text(encoding='utf8'))['readyCount'])
            self.assertEqual(2,main(args))
            self.assertEqual(original,source.read_bytes())

    def test_native_rectangle_can_use_existing_plan_coverage_without_snapshot_conversion(self):
        p = self.plan(evidence([],final_solid()))
        objects = [{'objectId':'label','kind':'label','text':'TEST-1','leader':False},
                   {'objectId':'t','kind':'thickness','value':10,'displayValue':10,'placement':'table'}]
        for i,(a,b) in enumerate(zip(RECT,RECT[1:]+RECT[:1])):
            objects.append({'objectId':str(i),'kind':'segment','feature':'edge:'+str(i),'visible':True,
                            'geometry':{'kind':'LINE','start':a,'end':b}})
        for axis,index,amount in (('x',1,100),('y',3,60)):
            objects.append({'objectId':axis,'kind':'dimension','axis':axis,'role':'overall',
                            'points':['vertex:0','vertex:'+str(index)],'coordinates':[[0,0],RECT[index]],
                            'values':[amount],'displayValues':[amount]})
        result = audit_expression_coverage(p,{'schemaVersion':'expressionObservations.v1','sourceDigest':p['sourceDigest'],
                                               'stage':'PLAN','objects':objects})
        self.assertEqual('PLAN_COMPLETE',result['status'],result)
        self.assertEqual('PENDING',result['visualStatus'])

    def test_floating_point_noise_does_not_split_identical_holes_or_pitch_rows(self):
        facts = {'holes':[{'id':str(i),'pointId':'hole:'+str(i),'kind':'SLOT','width':22+i*1e-12,
                          'length':81+i*1e-12,'angle':0} for i in range(3)],
                 'points':{'hole:'+str(i):[i*160,50+i*1e-12] for i in range(3)}}
        original = copy.deepcopy(facts)
        groups = _hole_groups(facts)
        self.assertEqual([3],[g['count'] for g in groups])
        pitches = _pitch_candidates(facts,groups)
        self.assertEqual([(3,2,160)],[(p['holeCount'],p['intervalCount'],p['pitch']) for p in pitches])
        self.assertEqual(original,facts)
        facts['holes'][2]['width'] = 23
        self.assertEqual([2,1],[g['count'] for g in _hole_groups(facts)])


if __name__ == '__main__':
    unittest.main()
