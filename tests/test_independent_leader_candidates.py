import copy
import unittest

from shapely.geometry import LineString, Polygon, box
from shapely.affinity import translate
from shapely.geometry import shape

from zijinhua_tekla.part_drawing.independent_layout.contracts import ContractError, digest, seal
from zijinhua_tekla.part_drawing.independent_layout.page_obstacles import geometry_json
from zijinhua_tekla.part_drawing.independent_layout.leader_candidates import check_leader_candidate, generate_leader_candidates


def fixture():
    xyz=lambda x,y:dict(X=x,Y=y)
    capture=dict(page=dict(presentation=[dict(Position=xyz(0,0),Primitives=[dict(Id=91,
        Primitives=[dict(type='T.LinePrimitive',StartPoint=xyz(0,10),EndPoint=xyz(20,10))])])]))
    sample=dict(key='lead',kind='leader',ink=dict(angleDegrees=0,occupancyBox=[30,30,40,35]),
                arrows=[dict(pdfPath=0,points=[[10,10],[9,12],[11,12]])],
                details=dict(route=[[10,10],[10,20],[28,30]],modelView=dict(origin=[0,0],targetObjectId=91)))
    primitives=seal(dict(samples=[sample]))
    def obstacle(identity,geometry,owner=None,kind='STROKE',**extra):
        return dict(id=identity,kind=kind,owner=owner,geometry=geometry_json(geometry),visible=geometry_json(geometry),**extra)
    scene=seal(dict(primitiveDigest=primitives['digest'],captureDigest=digest(capture),page=[0,0,100,100],unresolved=[],
        obstacles=[obstacle('path:0',Polygon(sample['arrows'][0]['points']),'lead'),
                   obstacle('mask',box(30,30,40,35),'lead','MASK'),
                   obstacle('contour',LineString([[0,10],[20,10]]).buffer(.05,cap_style=2),endpoints=[[0,10],[20,10]])],
        visibleStudy=dict(samples=[dict(key='lead',measurements=[dict(kind='line',pdfUserLineWidth=.1,strokeTransform=[1,0,0,1,0,0])])])))
    options=dict(keys=['lead'],clearance=.2,offsets=[[0,0],[10,0]],channels=[20,25,40])
    return scene,primitives,capture,options


def add_obstacle(scene,identity,geometry,kind='MASK'):
    body={k:copy.deepcopy(v) for k,v in scene.items() if k!='digest'}
    body['obstacles'].append(dict(id=identity,kind=kind,owner=None,geometry=geometry_json(geometry),visible=geometry_json(geometry)))
    return seal(body)


class LeaderCandidateTests(unittest.TestCase):
    def test_finite_candidates_keep_anchor_and_are_not_native_approval(self):
        args=fixture();result=generate_leader_candidates(*args)
        self.assertGreater(result['clearCounts']['lead'],0)
        self.assertFalse(result['productionEligible'])
        self.assertTrue(all(c['request']['route'][0]==[10,10] and c['requiresNativeReadback'] for c in result['candidates']))
        self.assertEqual(result,generate_leader_candidates(*args))

    def test_obstacle_on_real_anchor_cannot_be_fixed_by_moving_tip(self):
        args=list(fixture());args[0]=add_obstacle(args[0],'fixed-number',box(8,8,12,13))
        report=generate_leader_candidates(*args)
        self.assertEqual('FINITE_POOL_BLOCKED',report['status'])
        self.assertTrue(any(c.get('obstacle')=='fixed-number' for r in report['candidates'] for c in r['conflicts']))
        candidate=dict(key='lead',translation=[0,0],route=[[11,10],[11,20],[28,30]])
        with self.assertRaisesRegex(ContractError,'ANCHOR'):
            check_leader_candidate(*args[:3],candidate,['lead'],.2)

    def test_text_translation_avoids_obstacle_without_reducing_font(self):
        args=list(fixture());args[0]=add_obstacle(args[0],'text-block',box(35,32,39,36))
        report=generate_leader_candidates(*args)
        clear=[c for c in report['candidates'] if c['status']=='KNOWN_GEOMETRY_CLEAR']
        self.assertTrue(clear)
        self.assertTrue(all(c['request']['translation']==[10,0] for c in clear))

    def test_unrelated_line_at_anchor_is_not_exempted_as_contour(self):
        args=list(fixture());args[0]=add_obstacle(args[0],'unrelated',box(9,10,11,11),'STROKE')
        self.assertEqual('FINITE_POOL_BLOCKED',generate_leader_candidates(*args)['status'])

    def test_candidate_uses_raw_hidden_geometry_and_rejects_self_crossing(self):
        args=list(fixture());args[0]=add_obstacle(args[0],'hidden',box(0,15,100,16),'STROKE')
        body={k:copy.deepcopy(v) for k,v in args[0].items() if k!='digest'}
        body['obstacles'][-1]['visible']=geometry_json(Polygon());args[0]=seal(body)
        self.assertEqual('FINITE_POOL_BLOCKED',generate_leader_candidates(*args)['status'])
        candidate=dict(key='lead',translation=[0,0],route=[[10,10],[10,20],[30,20],[10,15],[28,30]])
        with self.assertRaisesRegex(ContractError,'ROUTE'):
            check_leader_candidate(*args[:3],candidate,['lead'],.2)

    def test_invalid_scope_and_missing_contour_block(self):
        for mode in ('movable','capture','contour'):
            args=list(fixture())
            if mode=='movable':args[3]['keys']=['missing']
            if mode=='capture':args[2]['changed']=True
            if mode=='contour':
                body={k:copy.deepcopy(v) for k,v in args[0].items() if k!='digest'};body['obstacles'].pop();args[0]=seal(body)
            with self.subTest(mode=mode),self.assertRaises(ContractError):generate_leader_candidates(*args)

    def test_anchor_contact_does_not_exempt_later_contour_crossing(self):
        args=fixture();candidate=dict(key='lead',translation=[0,0],route=[[10,10],[10,14],[15,8],[28,30]])
        checked=check_leader_candidate(*args[:3],candidate,['lead'],.2)
        self.assertTrue(any(c.get('obstacle')=='contour' for c in checked['conflicts']))

    def test_native_terminal_gap_does_not_exempt_real_ink(self):
        args=list(fixture());body={k:copy.deepcopy(v) for k,v in args[0].items() if k!='digest'}
        body['obstacles'].append(dict(id='glyph',kind='GLYPH',owner='lead',
            geometry=geometry_json(box(31,31,39,34)),visible=geometry_json(box(31,31,39,34))))
        args[0]=seal(body)
        candidate=dict(key='lead',translation=[0,0],route=[[10,10],[10,20],[25,30],[28,30]])
        self.assertEqual('KNOWN_GEOMETRY_CLEAR',check_leader_candidate(*args[:3],candidate,['lead'],2.5)['status'])
        body['obstacles'][-1]['geometry']=geometry_json(box(27,29,39,34));args[0]=seal(body)
        self.assertTrue(any(c['code']=='OWN_TEXT_ROUTE' for c in check_leader_candidate(*args[:3],candidate,['lead'],2.5)['conflicts']))

    def test_pair_conflicts_do_not_greedily_choose_independent_clear_candidates(self):
        scene,p,capture,options=fixture();body={k:copy.deepcopy(v) for k,v in p.items() if k!='digest'}
        second=copy.deepcopy(body['samples'][0]);second['key']='lead2';second['arrows'][0]['pdfPath']=1
        body['samples'].append(second);p=seal(body)
        body={k:copy.deepcopy(v) for k,v in scene.items() if k!='digest'};body['primitiveDigest']=p['digest']
        arrow=copy.deepcopy(body['obstacles'][0]);arrow.update(id='path:1',owner='lead2');body['obstacles'].append(arrow)
        measurement=copy.deepcopy(body['visibleStudy']['samples'][0]);measurement['key']='lead2';body['visibleStudy']['samples'].append(measurement)
        options['keys'].append('lead2');result=generate_leader_candidates(seal(body),p,capture,options)
        self.assertTrue(result['pairConflicts'])
        self.assertEqual('CANDIDATES_REQUIRE_JOINT_SELECTION',result['status'])
        self.assertNotIn('selected',result)

    def test_translation_of_page_and_obstacles_preserves_candidate_feasibility(self):
        args=list(fixture());first=generate_leader_candidates(*args);dx,dy=50,70
        p={k:copy.deepcopy(v) for k,v in args[1].items() if k!='digest'};s=p['samples'][0]
        move=lambda q:[q[0]+dx,q[1]+dy]
        s['details']['route']=[move(q) for q in s['details']['route']]
        s['details']['modelView']['origin']=move(s['details']['modelView']['origin'])
        s['arrows'][0]['points']=[move(q) for q in s['arrows'][0]['points']]
        s['ink']['occupancyBox']=[*move(s['ink']['occupancyBox'][:2]),*move(s['ink']['occupancyBox'][2:])]
        args[1]=seal(p);args[2]['page']['presentation'][0]['Position']=dict(X=dx,Y=dy)
        body={k:copy.deepcopy(v) for k,v in args[0].items() if k!='digest'}
        body.update(primitiveDigest=args[1]['digest'],captureDigest=digest(args[2]),page=[dx,dy,100+dx,100+dy])
        for o in body['obstacles']:
            for key in ('geometry','visible'):o[key]=geometry_json(translate(shape(o[key]),xoff=dx,yoff=dy))
            if 'endpoints' in o:o['endpoints']=[move(q) for q in o['endpoints']]
        args[0]=seal(body);args[3]['channels']=[v+dy for v in args[3]['channels']]
        second=generate_leader_candidates(*args)
        self.assertEqual(first['clearCounts'],second['clearCounts'])
        self.assertEqual(sorted(c['status'] for c in first['candidates']),sorted(c['status'] for c in second['candidates']))
