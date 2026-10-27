"""Independent positive/negative fixtures for manufacturing feature candidates."""

import base64
import copy
import math
import json
import unittest

from test_native_facts import read, fixture
from test_native_geometry import FRAME, RECT, prism, native_capture
from zijinhua_tekla.part_drawing.analytic_geometry import contour_segments, hole_segments
from zijinhua_tekla.part_drawing.finished_holes import finished_hole_candidates
from zijinhua_tekla.part_drawing.native_geometry import (_hole_candidates, _circular_cut,
                                                       _point_set_equal, audit_native_geometry)


def hole(identity, center, diameter):
    return {'id': identity, 'kind': 'ROUND', 'center': center, 'diameter': diameter,
            'extension': 0, 'direction': [1, 0], 'segments': hole_segments(center, diameter, 0, [1, 0]),
            'sourceRefs': ['test/'+identity]}


def circular_cut_fixture():
    raw = json.loads(base64.b64decode(native_capture()['before']['bytesBase64']))
    part = raw['parts'][0]
    c, r = [50, 30], 10
    points = [[c[0]+r*math.cos(i*math.pi/8), c[1]+r*math.sin(i*math.pi/8)] for i in range(16)]
    cutter = prism(points, -5, 15)
    for f in cutter['faces']['value']:
        f['originPartId'] = read(20)
    contour = []
    for i, xy in enumerate(([60,30], [50,40], [40,30], [50,20])):
        contour.append({'point': xy+[0], 'chamfer': {k: read(v) for k,v in {
            'Type': {'name': 'CHAMFER_ARC_POINT' if i % 2 == 0 else 'CHAMFER_NONE'},
            'X': 0, 'Y': 0, 'DZ1': 0, 'DZ2': 0}.items()}})
    operation = {'id': 20, 'runtimeType': 'Tekla.Structures.Model.BooleanPart', 'type': read({'name': 'BOOLEAN_CUT'}),
                 'operativePart': read({'id': 20}), 'operativeSolid': read(cutter),
                 'operativeGeometry': read({'runtimeType': 'Tekla.Structures.Model.ContourPlate',
                    'coordinateSystem': read(FRAME), 'profile': read('BL20'),
                    'modelingContour': read(read(contour))})}
    final = part['finalSolid']['value']
    for i, level in enumerate((0, 10)):
        final['faces']['value'][i]['loops']['value'].append({'vertices': read([p+[level] for p in points])})
    part['operations'] = read([operation])
    return raw, part, operation


class FlatPlateFeatureTests(unittest.TestCase):
    def test_model_point_comparison_includes_tenth_mm_boundary(self):
        self.assertTrue(_point_set_equal([[0, 0]], [[0.1, 0]], 0.1))
        self.assertFalse(_point_set_equal([[0, 0]], [[0.100001, 0]], 0.1))

    def test_default_accepts_small_circle_residual_without_changing_sources(self):
        raw, part, op = circular_cut_fixture()
        op['operativeGeometry']['value']['modelingContour']['value']['value'][0]['point'][0] += .005
        before = copy.deepcopy(raw)
        audit = audit_native_geometry(raw)
        cut = audit['parts'][0]['circularCuts'][0]
        self.assertEqual(.1, audit['modelToleranceMm'])
        self.assertGreater(cut['circularityResiduals']['centerSpreadMm'], .00001)
        self.assertEqual('MATCHED_BOUNDARIES', cut['innerBoundaryCheck']['status'])
        self.assertEqual(before, raw)
        self.assertEqual('UNRESOLVED', audit_native_geometry(raw, .00001)['parts'][0]['circularCuts'][0]['status'])
        self.assertEqual('BLOCKED', audit['parts'][0]['manufacturingStatus'])

    def test_default_does_not_accept_circle_residual_above_tenth_mm(self):
        raw, part, op = circular_cut_fixture()
        op['operativeGeometry']['value']['modelingContour']['value']['value'][0]['point'][0] += .2
        cut = audit_native_geometry(raw)['parts'][0]['circularCuts'][0]
        self.assertGreater(cut['circularityResiduals']['centerSpreadMm'], .1)
        self.assertEqual('UNRESOLVED', cut['status'])
        self.assertNotIn('targetCandidate', cut)

    def test_zero_rounding_is_inactive_but_not_unknown_or_partial(self):
        chamfers = [('CHAMFER_NONE',0,0)]*4
        chamfers[2] = ('CHAMFER_ROUNDING',0,0)
        self.assertEqual(['LINE']*4, [s['kind'] for s in contour_segments(RECT,chamfers,['c']*4)])
        for bad in [('CHAMFER_ROUNDING',0,2), ('CHAMFER_ROUNDING',-1,0), ('UNKNOWN',0,0)]:
            chamfers[2] = bad
            with self.assertRaises(ValueError): contour_segments(RECT,chamfers,['c']*4)

    def test_zero_rounding_still_requires_matching_final_corner(self):
        raw = json.loads(base64.b64decode(native_capture()['before']['bytesBase64']))
        part = raw['parts'][0]
        ch = part['modelingContour']['value']['value'][2]['chamfer']
        ch.update(Type=read({'name':'CHAMFER_ROUNDING'}), X=read(0), Y=read(0))
        self.assertEqual('MATCHED', audit_native_geometry(raw,'0.00001')['parts'][0]['shellCheck']['status'])
        part['finalSolid']['value']['faces']['value'][0]['loops']['value'][0]['vertices']['value'][2][0] -= 1
        self.assertEqual('UNRESOLVED', audit_native_geometry(raw,'0.00001')['parts'][0]['shellCheck']['status'])

    def test_oversized_is_round_and_rotation_does_not_make_slot(self):
        bolt = fixture()['parts'][0]['boltGroups']['value'][0]
        bolt['coordinateSystem'] = read(FRAME)
        bolt['properties'].update(BoltSize=read(20),Tolerance=read(2),SlottedHoleX=read(18),
                                 SlottedHoleY=read(0),HoleType=read({'name':'HOLE_TYPE_OVERSIZED'}),
                                 RotateSlots=read({'name':'UNUSED'}))
        candidates = _hole_candidates(bolt, FRAME, 'bolt')
        self.assertTrue(all(h['kind']=='ROUND' and h['diameter']==40 and h['extension']==0 for h in candidates))
        bolt['properties']['SlottedHoleY'] = read(3)
        with self.assertRaises(ValueError): _hole_candidates(bolt, FRAME, 'bolt')

    def test_contained_hole_preserves_all_sources_and_input(self):
        holes = [hole('a',[20,20],22), hole('b',[60,20],22), hole('c',[100,20],22), hole('d',[100,15],40)]
        old = copy.deepcopy(holes)
        result = finished_hole_candidates(holes,.001)
        self.assertEqual(3,result['candidateCount'])
        self.assertEqual(['a','b','d'],[h['id'] for h in result['candidates']])
        self.assertEqual(['c','d'],result['candidates'][2]['sourceHoleIds'])
        self.assertEqual(old,holes)
        self.assertEqual(result,finished_hole_candidates(holes[::-1],.001))

    def test_duplicate_and_nested_chain_map_to_one_maximal_circle(self):
        holes=[hole('a',[0,0],2),hole('b',[0,0],4),hole('c',[0,0],8),hole('d',[0,0],8)]
        result=finished_hole_candidates(holes,.001)
        self.assertEqual(1,result['candidateCount'])
        self.assertEqual(['a','b','c','d'],result['candidates'][0]['sourceHoleIds'])

    def test_partial_intersection_tangency_and_tolerance_boundary_are_not_merged(self):
        for center,diameter in (([7,0],10),([10,0],10),([0.001,0],10),([5,0],20)):
            result=finished_hole_candidates([hole('a',[0,0],10),hole('b',center,diameter)],.01)
            self.assertEqual('UNRESOLVED',result['status'])
            self.assertEqual(2,result['candidateCount'])

    def test_incomplete_sources_cannot_silently_reduce_inventory(self):
        result=finished_hole_candidates([hole('a',[0,0],2),hole('b',[0,0],8)],.001,False)
        self.assertEqual('UNRESOLVED',result['status'])
        self.assertEqual(2,result['candidateCount'])
        with self.assertRaises(ValueError): finished_hole_candidates([hole('a',[0,0],2)]*2,.001)

    def test_circular_cut_parameters_and_opposite_inner_boundaries(self):
        raw,part,op=circular_cut_fixture()
        result=_circular_cut(op,part,FRAME,'op',.00001)
        self.assertEqual(20,result['targetCandidate']['diameter'])
        self.assertEqual('MATCHED_BOUNDARIES',result['innerBoundaryCheck']['status'])
        self.assertEqual([50,30],result['targetCandidate']['center'])
        audit=audit_native_geometry(raw,.00001)['parts'][0]
        self.assertEqual(1,audit['finishedHoleCandidates']['candidateCount'])
        self.assertEqual('UNRESOLVED',audit['operationCheck']['status'])
        self.assertEqual('BLOCKED',audit['manufacturingStatus'])

    def test_missing_second_inner_loop_and_blind_cutter_remain_unresolved(self):
        for mode in ('loop','blind','duplicate','wrong-origin'):
            raw,part,op=circular_cut_fixture()
            faces=part['finalSolid']['value']['faces']['value']
            if mode=='loop':faces[1]['loops']['value'].pop()
            elif mode=='duplicate':faces[1]['loops']['value'].append(copy.deepcopy(faces[1]['loops']['value'][1]))
            elif mode=='wrong-origin':faces[1]['originPartId']=read(999)
            else:
                for face in op['operativeSolid']['value']['faces']['value']:
                    for loop in face['loops']['value']:
                        for point in loop['vertices']['value']:
                            if point[2]==15:point[2]=5
                for edge in op['operativeSolid']['value']['edges']['value']:
                    for key in ('start','end'):
                        if edge[key][2]==15:edge[key][2]=5
            result=_circular_cut(op,part,FRAME,'op',.00001)
            self.assertEqual('UNRESOLVED',result['innerBoundaryCheck']['status'],mode)

    def test_known_operation_child_is_mapped_but_unknown_child_blocks_inventory(self):
        raw,part,op=circular_cut_fixture()
        part['children']=read([{'id':op['id'],'runtimeType':'Tekla.Structures.Model.BooleanPart'}])
        audit=audit_native_geometry(raw,.00001)['parts'][0]
        self.assertTrue(audit['finishedHoleCandidates']['inputComplete'])
        part['children']['value'].append({'id':999,'runtimeType':'Tekla.Structures.Model.Fitting'})
        audit=audit_native_geometry(raw,.00001)['parts'][0]
        self.assertFalse(audit['finishedHoleCandidates']['inputComplete'])
        self.assertEqual('UNRESOLVED',audit['finishedHoleCandidates']['status'])

    def test_old_reader_noncut_and_oblique_projection_do_not_pass(self):
        raw,part,op=circular_cut_fixture()
        frame=dict(FRAME,axisY=[0,math.cos(.1),math.sin(.1)])
        result=_circular_cut(op,part,frame,'op',.00001)
        self.assertIn('Oblique',result['innerBoundaryCheck']['reason'])
        old=copy.deepcopy(op);del old['operativeGeometry']
        with self.assertRaises(KeyError):_circular_cut(old,part,FRAME,'op',.00001)
        op['type']=read({'name':'BOOLEAN_ADD'})
        with self.assertRaises(ValueError):_circular_cut(op,part,FRAME,'op',.00001)

    def test_noncircular_operative_contour_is_not_fitted_to_circle(self):
        raw,part,op=circular_cut_fixture()
        op['operativeGeometry']['value']['modelingContour']['value']['value'][0]['point'][0]+=2
        result=_circular_cut(op,part,FRAME,'op',.00001)
        self.assertEqual('UNRESOLVED',result['status'])
        self.assertEqual(2,len(result['operativeContour']['segments']))
        self.assertGreater(result['circularityResiduals']['centerSpreadMm'],.00001)
        self.assertNotIn('targetCandidate',result)


if __name__=='__main__': unittest.main()
