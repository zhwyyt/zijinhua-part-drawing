import copy
import json
import math
from pathlib import Path
import tempfile
import unittest

from test_nc_witness import capture_fixture, file_record, native_evidence
from test_native_facts import read, unknown
from zijinhua_tekla.cli import main
from zijinhua_tekla.part_drawing.analytic_geometry import (
    contour_segments, frame_axes, hole_segments, line, match_loop, parameter, three_point_arc,
    check_hole_topology, simple_ring)
from zijinhua_tekla.part_drawing.native_geometry import audit_native_geometry


FRAME = {"origin": [0, 0, 0], "axisX": [2, 0, 0], "axisY": [0, 3, 0]}
RECT = [[0, 0], [100, 0], [100, 60], [0, 60]]


def prism(points, low=0, high=10):
    bottom, top = [p+[low] for p in points], [p+[high] for p in points]
    faces, edges = [], []

    def face(pts, normal):
        faces.append({"id": f"F{len(faces)}", "normal": read(normal), "originPartId": read(10),
                      "surfaceType": unknown(), "loops": read([{"id": "L0", "vertices": read(pts)}])})
        for a, b in zip(pts, pts[1:]+pts[:1]):
            if any((e['start'], e['end']) in ((a,b),(b,a)) for e in edges):
                continue
            edges.append({"id": f"E{len(edges)}", "apiType": "EDGE_TYPE_NORMAL", "apiTypeCode": 1,
                          "start": a, "end": b, "curveParameters": unknown()})
    face(bottom, [0, 0, -1])
    face(top[::-1], [0, 0, 1])
    for i, a in enumerate(points):
        b = points[(i+1) % len(points)]
        face([a+[low], b+[low], b+[high], a+[high]], [b[1]-a[1], a[0]-b[0], 0])
    return {"faces": read(faces), "edges": read(edges),
            "bounds": read({"min": [min(p[0] for p in points), min(p[1] for p in points), low],
                            "max": [max(p[0] for p in points), max(p[1] for p in points), high]})}


def native_capture():
    capture = capture_fixture()
    raw = native_evidence()
    raw['readerVersion'] = 'native-reader.r2b'
    part = raw['parts'][0]
    part.update(profile=read('PL10'), coordinateSystem=read(FRAME), dstvCoordinateSystem=read(FRAME),
                finalSolid=read(prism(RECT)), boltGroups=read([]))
    part['reports']['PROFILE.WIDTH'] = read(10)
    part['modelingContour'] = read(read([{'point': p+[10], 'chamfer': {k:read(v) for k,v in
        {'Type':{'name':'CHAMFER_NONE','code':0},'X':30,'Y':30,'DZ1':0,'DZ2':0}.items()}} for p in RECT]))
    after = copy.deepcopy(raw)
    after['startedAtUtc'],after['completedAtUtc'] = '2026-10-17T00:00:06+00:00','2026-10-17T00:00:07+00:00'
    capture['before'] = file_record('before.json',json.dumps(raw))
    capture['after'] = file_record('after.json',json.dumps(after))
    header = ['order','drawing','phase','TEST-1','steel','1','PL10','B']+['10.00']*14
    rows = ['v 0.00u 0.00 0.00 0.00 0.00 0.00 0.00']
    rows += [f'{p[0]:.2f} {p[1]:.2f} 0.00 0.00 0.00 0.00 0.00' for p in RECT[1:]+RECT[:1]]
    nc = 'ST\n'+'\n'.join(header)+'\nAK\n'+'\n'.join(rows)+'\nEN\n'
    capture['files'] = [file_record('TEST-1.nc1',nc)]
    return capture


def change_part(capture, change):
    import base64
    for key in ('before','after'):
        raw=json.loads(base64.b64decode(capture[key]['bytesBase64']))
        change(raw['parts'][0])
        capture[key]=file_record(key+'.json',json.dumps(raw))


def add_hole(capture, extension=0, vertical=False):
    import base64
    center, diameter = [40, 30], 10
    direction = [0, 1] if vertical else [1, 0]
    segments = hole_segments(center, diameter, extension, direction)
    points = []
    for s in segments:
        if s['kind'] == 'LINE':
            points.append(s['start'])
        else:
            start = math.atan2(s['start'][1]-s['center'][1], s['start'][0]-s['center'][0])
            for i in range(8):
                angle = start + s['sweepRadians']*i/8
                points.append([s['center'][0]+s['radius']*math.cos(angle),
                               s['center'][1]+s['radius']*math.sin(angle)])
    from test_native_facts import fixture
    bolt = fixture()['parts'][0]['boltGroups']['value'][0]
    bolt['positions'] = read([center+[10]])
    bolt['coordinateSystem'] = read(dict(FRAME, axisX=[0,1,0], axisY=[-1,0,0]) if vertical else FRAME)
    bolt['properties'].update(BoltSize=read(8),Tolerance=read(2),SlottedHoleX=read(extension),SlottedHoleY=read(0))
    bolt['solidWithHighAccuracy'] = read(prism(points))
    change_part(capture,lambda p:p.update(boltGroups=read([bolt])))
    text=base64.b64decode(capture['files'][0]['bytesBase64']).decode()
    if extension:
        x,y=(40,30-extension/2) if vertical else (40-extension/2,30)
        dx,dy=(0,extension) if vertical else (extension,0)
        extra=f'BO\nv {x:.2f}s {y:.2f} 10.00 0.00l {dx:.2f} {dy:.2f} 0.00\n'
    else:
        extra='IK\nv 40.00s 35.00 5.00 0.00 0.00 0.00 0.00\n40.00 25.00 5.00 0.00 0.00 0.00 0.00\n40.00 35.00 0.00 0.00 0.00 0.00 0.00\n'
    capture['files']=[file_record('TEST-1.nc1',text.replace('EN\n',extra+'EN\n'))]
    return bolt


class AnalyticGeometryTests(unittest.TestCase):
    def test_arc_point_uses_three_points_not_x(self):
        a=three_point_arc([-5,0],[0,5],[5,0])
        self.assertAlmostEqual(5,a['radius'])
        self.assertAlmostEqual(-math.pi,a['sweepRadians'])
        self.assertEqual([0,0],a['center'])
        self.assertIsNone(parameter([0,-5],a,1e-8))

    def test_major_arc_retains_midpoint_domain(self):
        a=three_point_arc([1,0],[-1,0],[0,1])
        self.assertAlmostEqual(-1.5*math.pi,a['sweepRadians'])
        self.assertIsNotNone(parameter([-1,0],a,1e-8))

    def test_collinear_arc_rejected(self):
        with self.assertRaises(ValueError): three_point_arc([0,0],[1,0],[2,0])

    def test_concave_arc_and_convex_rounding_distinct(self):
        for kind,center,sweep in [('CHAMFER_ARC',[100,60],-math.pi/2),
                                  ('CHAMFER_ROUNDING',[90,50],math.pi/2)]:
            c=[('CHAMFER_NONE',0,0)]*4
            c[2]=(kind,10,30)
            arcs=[s for s in contour_segments(RECT,c,['a','b','c','d']) if s['kind']=='ARC']
            self.assertEqual(1,len(arcs))
            for actual,expected in zip(arcs[0]['center'],center): self.assertAlmostEqual(expected,actual)
            self.assertAlmostEqual(sweep,arcs[0]['sweepRadians'])

    def test_none_does_not_activate_stale_xy(self):
        self.assertEqual(4,len(contour_segments(RECT,[('CHAMFER_NONE',30,30)]*4,['x']*4)))

    def test_straight_chamfer_has_two_distances(self):
        c=[('CHAMFER_NONE',0,0)]*4;c[2]=('CHAMFER_LINE',10,20)
        segments=contour_segments(RECT,c,['a','b','c','d'])
        self.assertIn(([100,50],[80,60]),[(s['start'],s['end']) for s in segments])

    def test_chamfer_overlap_and_unknown_rejected(self):
        for c in [[('CHAMFER_LINE',60,60)]*4,[('UNKNOWN',1,1)]*4]:
            with self.assertRaises(ValueError): contour_segments(RECT,c,['x']*4)

    def test_slot_length_not_diameter_and_orientation(self):
        segments=hole_segments([0,0],22,59,[0,1])
        self.assertEqual(4,len(segments))
        self.assertEqual(2,sum(s['kind']=='ARC' for s in segments))
        self.assertAlmostEqual(81,max(s['center'][1]+s['radius'] for s in segments if s['kind']=='ARC')-
                               min(s['center'][1]-s['radius'] for s in segments if s['kind']=='ARC'))

    def test_domain_coverage_rejects_missing_corner_and_backtracking(self):
        segments=contour_segments(RECT,[('CHAMFER_NONE',0,0)]*4,['x']*4)
        self.assertEqual(4,len(match_loop(RECT[::-1],segments,1e-8)))
        for pts in (RECT[:3],RECT+RECT, [RECT[0],RECT[1],RECT[0],RECT[3]]):
            with self.assertRaises(ValueError): match_loop(pts,segments,1e-8)

    def test_frame_normalizes_but_does_not_orthogonalize(self):
        self.assertEqual(([1,0,0],[0,1,0],[0,0,1]),frame_axes(FRAME))
        with self.assertRaises(ValueError): frame_axes(dict(FRAME,axisY=[1,1,0]))

    def test_self_intersection_and_noncontained_holes_rejected(self):
        bow=[[0,0],[10,10],[0,10],[10,0]]
        with self.assertRaises(ValueError):simple_ring([line(a,b) for a,b in zip(bow,bow[1:]+bow[:1])],1e-7)
        outline=contour_segments(RECT,[('CHAMFER_NONE',0,0)]*4,['x']*4)
        def hole(c,d=10): return {'segments':hole_segments(c,d,0,[1,0])}
        for holes in ([hole([150,30])],[hole([3,30])],[hole([40,30]),hole([44,30])],
                      [hole([40,30],20),hole([40,30],10)],[hole([40,30]),hole([40,30])]):
            with self.assertRaises(ValueError):check_hole_topology(outline,holes,1e-7)
        self.assertEqual('MATCHED',check_hole_topology(outline,[hole([20,20]),hole([60,30])],1e-7)['status'])


class NativeGeometryTests(unittest.TestCase):
    def setUp(self): self.capture=native_capture()

    def report(self): return audit_native_geometry(self.capture,'0.00001','0.01','0.01')

    def test_analytic_evidence_match_is_not_manufacturing_proof(self):
        original=copy.deepcopy(self.capture)
        r=self.report()
        self.assertEqual(1,r['matchedCount'])
        p=r['parts'][0]
        self.assertEqual('BLOCKED',p['manufacturingStatus'])
        self.assertEqual('SUFFICIENT',p['evidenceSufficiency'])
        self.assertEqual(['RECHECK_BEFORE_DRAWING'],p['remainingProofs'])
        self.assertEqual(10,p['thickness']['value'])
        self.assertEqual(original,self.capture)

    def test_missing_dz_and_nonzero_dz_are_not_assumed_zero(self):
        for missing in (True,False):
            self.capture=native_capture()
            def change(p):
                c=p['modelingContour']['value']['value'][0]['chamfer']
                if missing: del c['DZ1']
                else: c['DZ1']=read(1)
            change_part(self.capture,change)
            self.assertEqual(1,self.report()['unresolvedCount'])

    def test_source_gate_cannot_be_overridden_by_geometry_match(self):
        self.capture['selectionRestored']=False
        self.assertEqual(1,self.report()['unresolvedCount'])

    def test_profile_thickness_mismatch_rejected(self):
        change_part(self.capture,lambda p:p.update(profile=read('PL12')))
        self.assertEqual('UNRESOLVED',self.report()['parts'][0]['thickness']['status'])

    def test_missing_side_cap_and_wrong_face_origin_rejected(self):
        for mode in ('side','cap','origin'):
            self.capture=native_capture()
            def change(p):
                faces=p['finalSolid']['value']['faces']['value']
                if mode=='side':faces.pop()
                elif mode=='cap':faces.pop(0)
                else:faces[0]['originPartId']=read(999)
            change_part(self.capture,change)
            self.assertEqual(1,self.report()['unresolvedCount'],mode)

    def test_intermediate_vertices_and_changed_outline_rejected(self):
        for xyz in ([0,0,5],[1,0,0]):
            self.capture=native_capture()
            def change(p): p['finalSolid']['value']['faces']['value'][0]['loops']['value'][0]['vertices']['value'][0]=xyz
            change_part(self.capture,change)
            self.assertEqual(1,self.report()['unresolvedCount'])

    def test_unknown_operation_or_child_blocks(self):
        for field in ('operations','children'):
            self.capture=native_capture()
            change_part(self.capture,lambda p:p.update({field:read([{'id':999,'runtimeType':'unknown'}])}))
            self.assertEqual(1,self.report()['unresolvedCount'])

    def test_nonfinite_and_invalid_budgets_rejected(self):
        for t in ('NaN','Infinity','-1','0'):
            with self.assertRaises(ValueError):audit_native_geometry(self.capture,t,'0.01','0.01')

    def test_r2b_identity_still_mandatory(self):
        change_part(self.capture,lambda p:p.pop('guid'))
        self.assertEqual(1,self.report()['unresolvedCount'])

    def test_round_and_rotated_slot_match_individual_holes(self):
        for extension,vertical in ((0,False),(20,False),(20,True)):
            self.capture=native_capture()
            add_hole(self.capture,extension,vertical)
            report=self.report();p=report['parts'][0]
            self.assertEqual(1,report['matchedCount'],p.get('issues'))
            self.assertEqual(1,p['ncCheck']['holeCount'])
            self.assertEqual('SUFFICIENT',p['evidenceSufficiency'])
            self.assertEqual('MATCHED',p['holeGroups'][0]['solidCheck']['status'])

    def test_false_flags_do_not_drop_hole(self):
        add_hole(self.capture)
        self.assertEqual(1,self.report()['parts'][0]['ncCheck']['holeCount'])

    def tilted_hole(self, angle):
        add_hole(self.capture)
        def vector(p):
            x, y, z = p
            return [x, y*math.cos(angle)-z*math.sin(angle), y*math.sin(angle)+z*math.cos(angle)]
        def point(p):
            return [a+b for a,b in zip(vector([p[0]-40,p[1]-30,p[2]-5]), [40,30,5])]
        def change(p):
            b=p['boltGroups']['value'][0]
            f=b['coordinateSystem']['value']
            f.update(origin=point(f['origin']), axisX=vector(f['axisX']), axisY=vector(f['axisY']))
            b['positions']['value']=[point(x) for x in b['positions']['value']]
            s=b['solidWithHighAccuracy']['value']
            for face in s['faces']['value']:
                face['normal']['value']=vector(face['normal']['value'])
                for loop in face['loops']['value']:
                    loop['vertices']['value']=[point(x) for x in loop['vertices']['value']]
            for edge in s['edges']['value']:
                for key in ('start','end'): edge[key]=point(edge[key])
        change_part(self.capture,change)

    def test_small_cutter_tilt_checked_in_own_frame_with_target_mm_budget(self):
        self.tilted_hole(.00005)
        original=copy.deepcopy(self.capture)
        part=audit_native_geometry(self.capture,.1,.01,.01)['parts'][0]
        check=part['holeGroups'][0]['solidCheck']
        self.assertEqual('MATCHED',check['status'])
        residual=max(c['maxThicknessResidualMm'] for c in check['targetCapEvidence'])
        self.assertGreater(residual,.00001)
        self.assertLess(residual,.1)
        self.assertEqual({0,1},{c['targetLevel'] for c in check['targetCapEvidence']})
        self.assertEqual(original,self.capture)
        self.assertEqual('INSUFFICIENT',part['holeGroups'][0]['effectEvidence']['status'])
        self.assertEqual('SUFFICIENT',part['evidenceSufficiency'])

    def test_tilt_over_length_budget_remains_unresolved(self):
        self.tilted_hole(.00005)
        check=self.report()['parts'][0]['holeGroups'][0]['solidCheck']
        self.assertEqual('UNRESOLVED',check['status'])
        self.assertIn('thickness faces',check['reason'])

    def test_reversed_cutter_axis_maps_to_opposite_target_caps(self):
        self.tilted_hole(math.pi)
        check=self.report()['parts'][0]['holeGroups'][0]['solidCheck']
        self.assertEqual('MATCHED',check['status'])
        for cap, projected in zip(check['capEvidence'],check['targetCapEvidence']):
            self.assertEqual(1-cap['level'],projected['targetLevel'])

    def test_missing_solid_edges_are_not_full_prism_evidence(self):
        change_part(self.capture,lambda p:p['finalSolid']['value'].update(edges=read([])))
        self.assertEqual(1,self.report()['unresolvedCount'])

    def test_repeated_hole_observations_preserved_not_counted_as_holes(self):
        add_hole(self.capture)
        def change(p):
            solid=p['boltGroups']['value'][0]['solidWithHighAccuracy']['value']
            solid['faces']['value']+=copy.deepcopy(solid['faces']['value'])
        change_part(self.capture,change)
        p=self.report()['parts'][0]
        self.assertEqual('EVIDENCE_MATCHED',p['geometryStatus'])
        self.assertEqual(4,len(p['holeGroups'][0]['solidCheck']['capEvidence']))
        self.assertEqual(1,p['ncCheck']['holeCount'])

    def test_duplicate_positions_ambiguous(self):
        add_hole(self.capture)
        def change(p):
            positions=p['boltGroups']['value'][0]['positions']['value'];positions+=copy.deepcopy(positions)
        change_part(self.capture,change)
        self.assertEqual(1,self.report()['unresolvedCount'])

    def test_hole_target_axis_rotation_and_fastener_ambiguity_blocked(self):
        changes=[lambda b:b['targets'].update(partToBoltTo=read({'id':999})),
                 lambda b:b.update(coordinateSystem=read(dict(FRAME,axisY=[0,0,1]))),
                 lambda b:b['properties'].update(RotateSlots=read({'name':'ROTATE_SLOTS_ODD'})),
                 lambda b:b['properties'].update(Bolt=read(True)),
                 lambda b:b['properties'].update(BoltStandard=read('STUD'))]
        for change in changes:
            self.capture=native_capture();add_hole(self.capture,20)
            change_part(self.capture,lambda p:change(p['boltGroups']['value'][0]))
            self.assertEqual(1,self.report()['unresolvedCount'])

    def test_hole_wrong_radius_partial_depth_and_position_blocked(self):
        changes=[lambda b:b['properties'].update(Tolerance=read(3)),
                 lambda b:b['positions'].update(value=[[41,30,10]]),
                 lambda b:b['positions'].update(value=[[40,30,1000]]),
                 lambda b:b['properties'].update(BoltStandard=read('')),
                 lambda b:b['properties'].update(BoltSize=read(-1),Tolerance=read(11)),
                 lambda b:b.update(solidWithHighAccuracy=read(prism(
                     [[40+5*math.cos(i*math.pi/8),30+5*math.sin(i*math.pi/8)] for i in range(16)],0,9)))]
        for change in changes:
            self.capture=native_capture();add_hole(self.capture)
            change_part(self.capture,lambda p:change(p['boltGroups']['value'][0]))
            self.assertEqual(1,self.report()['unresolvedCount'])

    def test_fastener_solid_is_not_cutter_but_confirmed_bolt_hole_is_through(self):
        add_hole(self.capture)
        change_part(self.capture,lambda p:p['boltGroups']['value'][0]['properties'].update(Bolt=read(True)))
        p=self.report()['parts'][0]
        self.assertEqual(1,len(p['holeGroups'][0]['candidates']))
        self.assertEqual('UNRESOLVED',p['holeGroups'][0]['solidCheck']['status'])
        self.assertEqual('MATCHED',p['ncCheck']['status'])
        self.assertEqual('EVIDENCE_MATCHED',p['geometryStatus'])
        self.assertEqual('BOLT-THROUGH-01',p['materialFinalState']['holeEvidence'][0]['rule'])

    def test_nc_wrong_coordinate_radius_and_extra_hole_rejected(self):
        import base64
        text=base64.b64decode(self.capture['files'][0]['bytesBase64']).decode()
        for changed in (text.replace('100.00 0.00','99.00 0.00'),
                        text.replace('100.00 0.00 0.00','100.00 0.00 10.00'),
                        text.replace('EN\n','BO\nv 20.00s 20.00 10.00 0.00 0.00 0.00 0.00\nEN\n')):
            self.capture['files']=[file_record('TEST-1.nc1',changed)]
            self.assertEqual(1,self.report()['unresolvedCount'])

    def test_nc_reference_modifiers_and_closing_curvature_make_witness_unusable(self):
        import base64
        text=base64.b64decode(self.capture['files'][0]['bytesBase64']).decode()
        for changed in (text.replace('0.00u','0.00o'),
                        text.replace('0.00 0.00 0.00 0.00 0.00 0.00 0.00\nEN',
                                     '0.00 0.00 1.00 0.00 0.00 0.00 0.00\nEN')):
            self.capture['files']=[file_record('TEST-1.nc1',changed)]
            part = self.report()['parts'][0]
            self.assertEqual('EVIDENCE_MATCHED', part['geometryStatus'])
            self.assertEqual('UNRESOLVED', part['ncCheck']['status'])
            self.assertTrue(part['ncCheck']['reason'])
            self.assertFalse(any(f['ncEvidence'] for f in part['featureEvidence']))

    def test_side_normal_contradiction_rejected(self):
        change_part(self.capture,lambda p:p['finalSolid']['value']['faces']['value'][2].update(normal=read([1,0,0])))
        self.assertEqual(1,self.report()['unresolvedCount'])

    def test_cli_exit_and_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            source,out=Path(directory)/'capture.json',Path(directory)/'audit.json'
            source.write_text(json.dumps(self.capture),encoding='utf8')
            args=['audit-native-geometry','--capture',str(source),'--model-tolerance-mm','0.00001',
                  '--nc-tolerance-mm','0.01','--max-resolution-mm','0.01','--out',str(out)]
            self.assertEqual(0,main(args))
            self.assertEqual('BLOCKED',json.loads(out.read_text('utf8'))['manufacturingStatus'])
            self.assertEqual(2,main(args))
            args[-1]=str(Path(directory)/'invalid.json');source.write_text('{}',encoding='utf8')
            self.assertEqual(2,main(args))


if __name__=='__main__': unittest.main()
