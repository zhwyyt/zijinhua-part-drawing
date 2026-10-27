"""Independent plate/cutter fixtures and damaged final solids for cut verification."""

import base64
import copy
import json
import math
import unittest

from test_native_facts import read, fixture
from test_native_geometry import FRAME, RECT, prism, native_capture, add_hole
from test_nc_witness import file_record
from zijinhua_tekla.part_drawing.analytic_geometry import line, hole_segments, inside_ring
from zijinhua_tekla.part_drawing.planar_cut_region import cut_region, _area
from zijinhua_tekla.part_drawing.native_geometry import audit_native_geometry


def polygon(points):
    return [line(a, b, ['fixture']) for a, b in zip(points, points[1:]+points[:1])]


def circle_points(center, radius, count=32):
    return [[center[0]+radius*math.cos(i*2*math.pi/count),
             center[1]+radius*math.sin(i*2*math.pi/count)] for i in range(count)]


def operation(identity, points=None, center=None, radius=10):
    if center is not None:
        points = circle_points(center, radius)
        control = [[center[0]+radius, center[1]], [center[0], center[1]+radius],
                   [center[0]-radius, center[1]], [center[0], center[1]-radius]]
    else:
        control = points
    solid = prism(points, -5, 15)
    for face in solid['faces']['value']:
        face['originPartId'] = read(identity)
    contour = [{'point': p+[0], 'chamfer': {k: read(v) for k, v in {
        'Type': {'name': 'CHAMFER_ARC_POINT' if center is not None and i % 2 == 0 else 'CHAMFER_NONE'},
        'X': 0, 'Y': 0, 'DZ1': 0, 'DZ2': 0}.items()}} for i, p in enumerate(control)]
    return {'id': identity, 'runtimeType': 'Tekla.Structures.Model.BooleanPart',
            'type': read({'name': 'BOOLEAN_CUT'}), 'operativePart': read({'id': identity}),
            'operativeSolid': read(solid), 'operativeGeometry': read({
                'runtimeType': 'Tekla.Structures.Model.ContourPlate', 'coordinateSystem': read(FRAME),
                'profile': read('PL20'), 'modelingContour': read(read(contour))})}


def final_solid(outer=RECT, holes=(), outer_origins=None):
    solid = prism(outer)
    if outer_origins:
        for face, owner in zip(solid['faces']['value'][2:], outer_origins):
            face['originPartId'] = read(owner)
    for points, owners in holes:
        inner = prism(points)
        for i in (0, 1):
            solid['faces']['value'][i]['loops']['value'].append(copy.deepcopy(inner['faces']['value'][i]['loops']['value'][0]))
        for i, face in enumerate(inner['faces']['value'][2:]):
            face['originPartId'] = read(owners if isinstance(owners, int) else owners[i])
            face['normal']['value'] = [-v for v in face['normal']['value']]
            solid['faces']['value'].append(face)
        solid['edges']['value'].extend(inner['edges']['value'])
    return solid


def evidence(operations, solid):
    raw = json.loads(base64.b64decode(native_capture()['before']['bytesBase64']))
    part = raw['parts'][0]
    part['operations'] = read(operations)
    part['children'] = read([{'id': o['id'], 'runtimeType': 'Tekla.Structures.Model.BooleanPart'} for o in operations])
    part['finalSolid'] = read(solid)
    return raw


class PlanarCutRegionTests(unittest.TestCase):
    def region(self, cuts):
        return cut_region({'segments': polygon(RECT)}, cuts, .1, 10)

    def test_round_and_polygon_internal_cuts(self):
        r = self.region([{'id': 20, 'segments': hole_segments([25, 30], 10, 0, [1, 0])},
                         {'id': 21, 'segments': polygon([[60,20],[80,20],[80,40],[60,40]])}])
        self.assertEqual(2, len(r['holes']))
        self.assertEqual({'ROUND', 'CUTOUT'}, {h['kind'] for h in r['holes']})

    def test_overlapping_circles_form_one_boundary_with_both_sources(self):
        r = self.region([{'id': i, 'segments': hole_segments(c, 20, 0, [1, 0])}
                         for i, c in ((20, [30,30]), (21, [40,30]))])
        self.assertEqual(1, len(r['holes']))
        hole = r['holes'][0]
        self.assertEqual([20, 21], hole['ownerIds'])
        expected = 200*math.pi-(200*math.acos(.5)-50*math.sqrt(3))
        self.assertAlmostEqual(expected, abs(_area(hole['segments'])), places=6)

    def test_contained_duplicate_and_external_operations_are_accounted_for(self):
        cuts = [{'id': i, 'segments': hole_segments(c, d, 0, [1,0])}
                for i,c,d in ((20,[30,30],20),(21,[30,30],20),(22,[30,30],6),(23,[140,30],10))]
        r = self.region(cuts)
        self.assertEqual(1, len(r['holes']))
        self.assertEqual([20,21], r['holes'][0]['ownerIds'])
        self.assertEqual(['BOUNDARY_CONTRIBUTOR']*2+['NO_EXPOSED_BOUNDARY']*2,
                         [m['status'] for m in r['operationMappings']])
        self.assertEqual(r, self.region(cuts[::-1]))

    def test_shared_straight_boundary_of_adjacent_cuts_disappears(self):
        r = self.region([{'id':20,'segments':polygon([[20,20],[40,20],[40,40],[20,40]])},
                         {'id':21,'segments':polygon([[40,20],[60,20],[60,40],[40,40]])}])
        self.assertEqual(1,len(r['holes']))
        self.assertAlmostEqual(800,abs(_area(r['holes'][0]['segments'])))

    def test_external_notch_changes_outer_contour(self):
        r=self.region([{'id':20,'segments':polygon([[80,40],[110,40],[110,70],[80,70]])}])
        self.assertEqual([],r['holes'])
        self.assertAlmostEqual(5600,abs(_area(r['outline']['segments'])))
        self.assertFalse(inside_ring([90,50],r['outline']['segments'],.001))

    def test_split_plate_complete_removal_and_tangent_holes_are_unresolved(self):
        for cuts in ([{'id':20,'segments':polygon([[40,-10],[60,-10],[60,70],[40,70]])}],
                     [{'id':20,'segments':polygon([[-10,-10],[110,-10],[110,70],[-10,70]])}],
                     [{'id':i,'segments':hole_segments(c,20,0,[1,0])} for i,c in ((20,[30,30]),(21,[50,30]))]):
            with self.subTest(cuts=cuts), self.assertRaises(ValueError): self.region(cuts)


class CutFinalStateTests(unittest.TestCase):
    def circle(self):
        return evidence([operation(20,center=[50,30])], final_solid(holes=[(circle_points([50,30],10),20)]))

    def report(self, raw):
        return audit_native_geometry(raw)['parts'][0]

    def test_complete_hole_has_inner_walls_and_all_edge_coverage(self):
        raw=self.circle(); before=copy.deepcopy(raw); p=self.report(raw)
        self.assertEqual('MATCHED',p['cutFinalState']['status'],p['issues'])
        self.assertEqual('MATCHED',p['operationCheck']['status'])
        self.assertEqual('EVIDENCE_MATCHED',p['geometryStatus'])
        self.assertEqual('SUFFICIENT',p['evidenceSufficiency'])
        self.assertEqual('BLOCKED',p['manufacturingStatus'])
        self.assertEqual(['RECHECK_BEFORE_DRAWING'],p['remainingProofs'])
        self.assertEqual(36,len(p['cutFinalState']['solidCheck']['sideEvidence']))
        self.assertEqual(before,raw)

    def test_two_holes_and_rectangular_notch(self):
        cut1=operation(20,center=[25,30],radius=5)
        cut2=operation(21,points=[[60,20],[75,20],[75,35],[60,35]])
        raw=evidence([cut1,cut2],final_solid(holes=[(circle_points([25,30],5),20),
                       ([[60,20],[75,20],[75,35],[60,35]],21)]))
        self.assertEqual('MATCHED',self.report(raw)['cutFinalState']['status'])
        raw=evidence([operation(20,points=[[80,40],[110,40],[110,70],[80,70]])],
                     final_solid([[0,0],[100,0],[100,40],[80,40],[80,60],[0,60]],outer_origins=[10,10,20,20,10,10]))
        p=self.report(raw)
        self.assertEqual('MATCHED',p['cutFinalState']['status'],p['issues'])
        self.assertEqual(6,len(p['finalOutline']['segments']))
        self.assertEqual(4,len(p['outline']['segments']))

    def test_overlapping_circular_cuts_match_independent_union_mesh(self):
        height=math.sqrt(75)
        points=[]
        for center,start in (([30,30],math.pi/3),([40,30],4*math.pi/3)):
            points.extend([[center[0]+10*math.cos(start+i*(4*math.pi/3)/16),
                            center[1]+10*math.sin(start+i*(4*math.pi/3)/16)] for i in range(16)])
        self.assertAlmostEqual(30+height,points[0][1])
        raw=evidence([operation(20,center=[30,30]),operation(21,center=[40,30])],
                     final_solid(holes=[(points,[20]*16+[21]*16)]))
        p=self.report(raw)
        self.assertEqual('MATCHED',p['cutFinalState']['status'],p['cutFinalState'])
        self.assertEqual(1,p['finishedHoleCandidates']['candidateCount'])

    def test_cap_only_fixture_missing_inner_walls_is_rejected(self):
        raw=self.circle()
        raw['parts'][0]['finalSolid']['value']['faces']['value']=raw['parts'][0]['finalSolid']['value']['faces']['value'][:6]
        p=self.report(raw)
        self.assertEqual('MATCHED_BOUNDARIES',p['circularCuts'][0]['innerBoundaryCheck']['status'])
        self.assertEqual('UNRESOLVED',p['cutFinalState']['status'])

    def test_slot_cut_retains_analytic_hole_dimensions(self):
        points=[]
        for c,start in (([20,30],math.pi/2),([60,30],3*math.pi/2)):
            points.extend([[c[0]+10*math.cos(start+i*math.pi/16),c[1]+10*math.sin(start+i*math.pi/16)]
                           for i in range(17)])
        op=operation(20,points=points)
        controls=[[20,20],[10,30],[20,40],[60,40],[70,30],[60,20]]
        op['operativeGeometry']['value']['modelingContour']=read(read([
            {'point':p+[0], 'chamfer':{k:read(v) for k,v in {
                'Type':{'name':'CHAMFER_ARC_POINT' if i in (1,4) else 'CHAMFER_NONE'},
                'X':0,'Y':0,'DZ1':0,'DZ2':0}.items()}} for i,p in enumerate(controls)]))
        p=self.report(evidence([op],final_solid(holes=[(points,20)])))
        self.assertEqual('MATCHED',p['cutFinalState']['status'],p['planarCuts'])
        h=p['finishedHoleCandidates']['candidates'][0]
        self.assertEqual(('SLOT',20.,40.),(h['kind'],h['diameter'],h['extension']))

    def test_independent_inner_disk_faces_do_not_prove_holes_in_the_main_caps(self):
        raw=self.circle(); faces=raw['parts'][0]['finalSolid']['value']['faces']['value']
        for i in (0,1):
            inner=copy.deepcopy(faces[i]);inner['loops']['value']=[inner['loops']['value'][1]]
            faces[i]['loops']['value'].pop();faces.append(inner)
        self.assertEqual('UNRESOLVED',self.report(raw)['cutFinalState']['status'])

    def test_damaged_final_geometry_and_foreign_side_sources_are_rejected(self):
        for mode in ('missing-wall','duplicate-wall','missing-edge','extra-edge','foreign-wall',
                     'wrong-normal','reversed-normal','blind-wall','extra-loop','wrong-radius'):
            raw=self.circle(); solid=raw['parts'][0]['finalSolid']['value']; faces=solid['faces']['value']
            if mode=='missing-wall':faces.pop()
            elif mode=='duplicate-wall':faces.append(copy.deepcopy(faces[-1]))
            elif mode=='missing-edge':solid['edges']['value'].pop()
            elif mode=='extra-edge':solid['edges']['value'].append({'start':[1,2,3],'end':[4,5,6]})
            elif mode=='foreign-wall':faces[-1]['originPartId']=read(999)
            elif mode=='wrong-normal':faces[-1]['normal']=read([0,0,1])
            elif mode=='reversed-normal':faces[-1]['normal']['value']=[-v for v in faces[-1]['normal']['value']]
            elif mode=='blind-wall':faces[-1]['loops']['value'][0]['vertices']['value'][0][2]=5
            elif mode=='extra-loop':faces[0]['loops']['value'].append(copy.deepcopy(faces[0]['loops']['value'][1]))
            else:faces[0]['loops']['value'][1]['vertices']['value'][0][0]+=.3
            with self.subTest(mode=mode):
                self.assertEqual('UNRESOLVED',self.report(raw)['cutFinalState']['status'])

    def test_unsupported_or_missing_operation_cannot_be_silently_ignored(self):
        for mode in ('add','missing-data','unknown-child','duplicate-operation','short-cutter'):
            raw=self.circle(); part=raw['parts'][0]; op=part['operations']['value'][0]
            if mode=='add':op['type']=read({'name':'BOOLEAN_ADD'})
            elif mode=='missing-data':del op['operativeGeometry']
            elif mode=='unknown-child':part['children']['value'].append({'id':999,'runtimeType':'Tekla.Structures.Model.Fitting'})
            elif mode=='duplicate-operation':part['operations']['value'].append(copy.deepcopy(op))
            else:
                replacement=prism(circle_points([50,30],10),0,9)
                for f in replacement['faces']['value']:f['originPartId']=read(20)
                op['operativeSolid']=read(replacement)
            with self.subTest(mode=mode):
                report=self.report(raw)
                self.assertEqual('UNRESOLVED',report['geometryStatus'])
                if mode == 'duplicate-operation':
                    self.assertIn('PART_GEOMETRY_UNRESOLVED',[i['code'] for i in report['issues']])
                else:
                    self.assertEqual('UNRESOLVED',report['cutFinalState']['status'])

    def test_joint_cut_and_bolt_rule_resolve_interactions_but_not_source_gaps(self):
        raw=self.circle()
        bolt=fixture()['parts'][0]['boltGroups']['value'][0]
        bolt.update(id=30,coordinateSystem=read(FRAME),positions=read([[20,15,10]]),
                    solidWithHighAccuracy=read(prism(circle_points([20,15],5))))
        bolt['properties'].update(BoltSize=read(8),Tolerance=read(2),SlottedHoleX=read(0),Bolt=read(True))
        raw['parts'][0]['boltGroups']=read([bolt])
        p=self.report(raw)
        self.assertEqual('MATCHED',p['cutFinalState']['status'])
        self.assertNotIn('HOLE_CUT_INTERACTIONS_UNPROVEN',p['remainingProofs'])
        self.assertEqual('MATCHED',p['materialFinalState']['status'])
        self.assertEqual('SUFFICIENT',p['evidenceSufficiency'])
        raw=self.circle();del raw['parts'][0]['guid']
        p=self.report(raw)
        self.assertEqual('MATCHED',p['cutFinalState']['status'])
        self.assertEqual('INSUFFICIENT',p['evidenceSufficiency'])
        self.assertIn('NATIVE_SOURCE_UNCONFIRMED',p['remainingProofs'])

    def test_optional_nc_matches_finished_cut_holes_and_preserves_conflicts(self):
        capture=native_capture();add_hole(capture)
        raw=evidence([operation(20,center=[40,30],radius=5)],
                     final_solid(holes=[(circle_points([40,30],5),20)]))
        for name in ('before','after'):
            old=json.loads(base64.b64decode(capture[name]['bytesBase64']))
            current=copy.deepcopy(raw)
            for key in ('startedAtUtc','completedAtUtc'):current[key]=old[key]
            capture[name]=file_record(name+'.json',json.dumps(current))
        p=audit_native_geometry(capture,.1,.01,.01)['parts'][0]
        self.assertEqual('MATCHED',p['ncCheck']['status'],p['ncCheck'])
        self.assertEqual('SUFFICIENT',p['evidenceSufficiency'])
        feature=next(f for f in p['featureEvidence'] if f['id'].startswith('materialHole:'))
        self.assertTrue(feature['ncEvidence'])
        text=base64.b64decode(capture['files'][0]['bytesBase64']).decode()
        text=text.replace('40.00s 35.00 5.00','40.00s 36.00 6.00').replace('40.00 25.00 5.00','40.00 24.00 6.00').replace('40.00 35.00 0.00','40.00 36.00 0.00')
        capture['files']=[file_record('TEST-1.nc1',text)]
        p=audit_native_geometry(capture,.1,.01,.01)['parts'][0]
        self.assertEqual('MATCHED',p['cutFinalState']['status'])
        self.assertEqual('CONFLICT',p['evidenceSufficiency'])
        self.assertEqual('CONFLICT',next(f for f in p['featureEvidence'] if f['id'].startswith('materialHole:'))['proofStatus'])


if __name__ == '__main__':
    unittest.main()
