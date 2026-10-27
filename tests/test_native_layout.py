import copy
import math
import tempfile
import unittest
from pathlib import Path

import ezdxf
from shapely.geometry import LineString, box

from test_native_material import plate_with_hole
from zijinhua_tekla.part_drawing.native_expressions import build_native_expression_requirements
from zijinhua_tekla.part_drawing.native_layout import build_layout, near_hole_anchor, hole_boundary, plate_polygon
from zijinhua_tekla.part_drawing.native_readback import _hole_shapes, readback_observations, visual_audit
from zijinhua_tekla.part_drawing.native_render import boundary_matches, load_render, sample_arc
from zijinhua_tekla.part_drawing.expression_requirements import _requirements, axis_aligned_arc
from zijinhua_tekla.part_drawing.expression_coverage import audit_expression_coverage


def symbol_bolts(plan):
    # Saved state of every bolt the plan shows as a Tekla symbol.
    return [dict(modelId=b['modelId'],hidden=False,color='Green',representation='Symbol2',hole=True,axis=True)
            for b in plan['bolts'] if b['display']=='SYMBOL']


class NativeLayoutTests(unittest.TestCase):
    def shoulder_data(self, factor=1):
        raw=plate_with_hole();batch=build_native_expression_requirements(raw)
        req=batch['parts'][0];req['source']['partPosition']='UNRELATED-SHOULDER'
        xy=[(18,106),(8,96),(0,96),(0,30),(30,0),(196,0),(226,30),(226,96),(218,96),(208,106),(208,216),(18,216)]
        points={f'vertex:{i}':[factor*x,factor*y] for i,(x,y) in enumerate(xy)}
        segments=[]
        for i in range(len(xy)):
            a,b=f'vertex:{i}',f'vertex:{(i+1)%len(xy)}'
            s=dict(id=f'edge:{i}',kind='LINE',start=points[a],end=points[b],startPoint=a,endPoint=b)
            if i in (0,8):s.update(kind='ARC',center=[factor*(8 if i==0 else 218),factor*106],radius=factor*10,clockwise=True)
            segments.append(s)
        points.update({'hole:a':[73*factor,161*factor],'hole:b':[153*factor,161*factor]})
        req['facts']=dict(points=points,segments=segments,holes=[dict(id=k,pointId='hole:'+k,kind='ROUND',diameter=26*factor) for k in ('a','b')],
                          datum='vertex:3',bounds=[0,0,226*factor,216*factor],boundaryLoops=[dict(segments=[s['id'] for s in segments])])
        req['requirements'],req['groups']=_requirements(req['source']['partPosition'],10,req['facts'],{})
        return req,batch['geometryAudit']['parts'][0],raw['parts'][0]

    def test_shoulder_uses_feature_chains_and_envelope_hole_chain(self):
        # Convention v1 (shop drawings): hole and contour chains run envelope edge to
        # edge (rule A); R tangent points are not located, tangent flats not dimensioned.
        for factor in (1,1.7):
            req,geo,raw=self.shoulder_data(factor)
            plan=build_layout(req,geo,raw,scale=2*factor)
            self.assertEqual('READY',plan['status'],plan['coverage'])
            self.assertEqual('READABLE',plan['readability']['status'],plan['readability'])
            # User-confirmed: same-side contour and hole chains read as one chain.
            self.assertEqual(6,len(plan['dimensions']))
            values=[[round(v/factor,5) for v in d['values']] for d in plan['dimensions']]
            for expected in ([226],[216],[30,166,30],[18,55,80,55,18],[30,66,120],[161,55]):
                self.assertIn(expected,values)
            for removed in ([55,80,55],[73,80,73],[18,190,18],[110],[8],[190]):
                self.assertNotIn(removed,values)
            self.assertEqual(0,sum(d.get('local',False) for d in plan['dimensions']))

    def test_r_transition_is_one_closed_chain_without_tangent_point(self):
        for factor in (1,1.7):
            req,geo,raw=self.shoulder_data(factor)
            plan=build_layout(req,geo,raw,scale=2*factor)
            right=[d for d in plan['dimensions'] if d['axis']=='y' and d['up']==[1,0] and d['role']!='overall']
            self.assertEqual([[30,66,120]],[[round(v/factor,5) for v in d['values']] for d in right])
            self.assertNotIn('vertex:9',right[0]['points'])
            column=lambda d:d['coordinates'][0][0]+d['distance']
            overall=next(d for d in plan['dimensions'] if d['key']=='overall-1')
            self.assertGreater(column(overall),column(right[0]))
            self.assertEqual('PLAN_COMPLETE',plan['coverage']['status'])

    def test_unrelated_arc_does_not_justify_removing_a_contour_interval(self):
        from zijinhua_tekla.part_drawing.native_dimensions import direct_tangent_pair,tangent_straights
        req,_,_=self.shoulder_data();facts=req['facts'];straights=tangent_straights(facts,1,.1)
        # 30..216 contains the straight, but its missing span is not the adjacent R10.
        pair=['vertex:6','vertex:10']
        self.assertEqual(pair,direct_tangent_pair(facts,pair,straights,1,.1))

    def test_r_transition_chain_rule_rotates_with_geometry(self):
        req,geo,raw=self.shoulder_data();facts=req['facts']
        rotate=lambda p:[-p[1],p[0]]
        facts['points']={k:rotate(p) for k,p in facts['points'].items()}
        for s in facts['segments']:
            for key in ('start','end','center'):
                if key in s:s[key]=rotate(s[key])
        facts['bounds']=[-216,0,0,226]
        req['requirements'],req['groups']=_requirements(req['source']['partPosition'],10,facts,{})
        plan=build_layout(req,geo,raw)
        pieces=[d for d in plan['dimensions'] if d['axis']=='x' and d['role']!='overall']
        # Rotated, the R-transition run shares its side with the holes and merges.
        self.assertEqual([[55,65,66,30]],[[round(v,5) for v in d['values']] for d in pieces])
        self.assertNotIn('vertex:9',pieces[0]['points'])
        self.assertEqual('PLAN_COMPLETE',plan['coverage']['status'])

    def test_saved_dimension_cannot_drift_from_planned_column_or_flip_side(self):
        req,plan=self.data()
        rows=[dict(key=c['key'],id=c['key']) for domain in ('dimensions','notes','curves') for c in plan[domain]]
        dimension=plan['dimensions'][0];row=next(r for r in rows if r['key']==dimension['key'])
        row.update(kind='dimension',arrow='FilledArrow',extension='Yes',textColor='Black',textHeight=2.5,combine='Off',
                   up=dimension['up'],distance=dimension['distance']+2,children=[])
        raw=dict(stage='SAVED_REOPENED',id=plan['id'],sourceDigest=req['sourceDigest'],planDigest=plan['planDigest'],
                 scale=plan['scale'],geometryMode=plan['geometryMode'],linkedTargetCount=1,visiblePartCount=1,
                 sourceBolts=symbol_bolts(plan),objects=rows)
        with self.assertRaisesRegex(ValueError,'Dimension line placement changed'):readback_observations(req,plan,raw,{})
        row.update(distance=dimension['distance'],up=[-v for v in dimension['up']])
        with self.assertRaisesRegex(ValueError,'Dimension line placement changed'):readback_observations(req,plan,raw,{})

    def test_tangent_flats_are_not_dimensioned_directly(self):
        for factor in (1,1.7):
            req,geo,raw=self.shoulder_data(factor)
            plan=build_layout(req,geo,raw,scale=2*factor)
            self.assertFalse([d for d in plan['dimensions'] if len(d['values'])==1 and abs(d['values'][0]-8*factor)<.001])
            top=next(d for d in plan['dimensions'] if [round(v/factor,5) for v in d['values']]==[18,55,80,55,18])
            self.assertFalse({'vertex:1','vertex:8'}&set(top['points']))
            self.assertEqual('PLAN_COMPLETE',plan['coverage']['status'])

    def test_same_side_contour_and_hole_chains_merge_into_one(self):
        req,geo,raw=self.shoulder_data();plan=build_layout(req,geo,raw)
        holes=next(d for d in plan['dimensions'] if d['key']=='holes-0')
        self.assertEqual(['vertex:2','vertex:11','hole:a','hole:b','vertex:10','vertex:7'],holes['points'])
        self.assertFalse([d for d in plan['dimensions'] if d['axis']=='x' and d['up']==holes['up'] and d is not holes])
        self.assertEqual('READABLE',plan['readability']['status'])
    def test_sloping_opposite_edge_does_not_close_hole_chain(self):
        from zijinhua_tekla.part_drawing.native_dimensions import hole_datums
        req,_,_=self.shoulder_data();facts=req['facts']
        facts['points']['vertex:9'][0]-=20
        self.assertEqual(['vertex:11'],hole_datums(facts,['hole:a','hole:b'],0,.1))

    def test_staggered_holes_do_not_gain_opposite_edge_margin(self):
        from zijinhua_tekla.part_drawing.native_dimensions import hole_datums
        req,_,_=self.shoulder_data();facts=req['facts']
        facts['points']['hole:b'][1]-=10
        self.assertEqual(1,len(hole_datums(facts,['hole:a','hole:b'],0,.1)))

    def test_removed_radius_makes_arc_definition_incomplete(self):
        req,geo,raw=self.shoulder_data()
        plan=build_layout(req,geo,raw)
        observation=copy.deepcopy(plan['observations'])
        observation['objects']=[o for o in observation['objects'] if o['kind']!='radius']
        result=audit_expression_coverage(req,observation)
        self.assertNotEqual('PLAN_COMPLETE',result['status'])
        self.assertTrue(any(c['requirementId'].endswith('/radius') and c['status']=='MISSING' for c in result['coverage']))

    def test_contour_witnesses_start_outside_material_without_moving_anchors(self):
        req,geo,raw=self.shoulder_data();plan=build_layout(req,geo,raw)
        interior=plate_polygon(req['facts']).buffer(-req['modelToleranceMm'])
        for d in plan['dimensions']:
            self.assertTrue(d['suppressWitnesses'])
            self.assertEqual([req['facts']['points'][p] for p in d['points']],d['coordinates'])
            if d.get('local') or d['key'].startswith('holes'):continue
            axis=0 if d['axis']=='x' else 1;side=d['up'][1-axis]
            lines=[c for c in plan['curves'] if c['role']=='witness' and c['feature']==d['key']]
            self.assertEqual(len(d['coordinates']),len(lines))
            if d['key'].startswith('boundary'):
                for curve in lines:
                    index=int(curve['key'].rsplit(':',1)[-1])
                    point=d['coordinates'][index]
                    self.assertGreaterEqual(side*(curve['start'][1-axis]-point[1-axis]),3*plan['scale']-1e-6)
                    self.assertFalse(interior.intersects(LineString([curve['start'],curve['end']])))
            else:
                boundary=req['facts']['bounds'][1-axis+(2 if side>0 else 0)]
                self.assertTrue(all(side*(c['start'][1-axis]-boundary)>=3*plan['scale'] for c in lines))

    def test_shoulder_leaders_do_not_cross_dimension_linework(self):
        req,geo,raw=self.shoulder_data();plan=build_layout(req,geo,raw)
        barriers=[LineString([c['start'],c['end']]) for c in plan['curves'] if c['role']=='witness']
        for d in plan['dimensions']:
            axis=0 if d['axis']=='x' else 1;side=d['up'][1-axis]
            ends=[list(d['coordinates'][i]) for i in (0,-1)]
            for p in ends:p[1-axis]=d['coordinates'][0][1-axis]+side*d['distance']
            barriers.append(LineString(ends))
        for note in plan['notes']:
            if note['tip'] is not None:
                self.assertFalse(any(LineString([note['tip'],note['at']]).crosses(line) for line in barriers),note)

    def test_radius_axis_relation_respects_source_budget(self):
        arc=dict(center=[0,0],start=[10,.04],end=[.03,10])
        self.assertTrue(axis_aligned_arc(arc,.1))
        self.assertFalse(axis_aligned_arc(arc))
        arc['end']=[.2,10]
        self.assertFalse(axis_aligned_arc(arc,.1))

    def test_actual_leader_crossing_is_rejected_even_without_text_overlap(self):
        raw=dict(scale=1,origin=[30,50],frameOrigin=[0,0],width=100,height=80,sheetSize=[420,297],objects=[
            dict(kind='text',key='radius',text='R=10',at=[15,5],tip=[5,5],boxMin=[14,4],boxMax=[16,6]),
            dict(kind='line',key='witness:one',start=[10,0],end=[10,10])])
        render=dict(texts=[],lines=[],source='synthetic-readback')
        audit=visual_audit({},raw,render)
        self.assertEqual(['LEADER_CROSSES_DIMENSION'],[e['code'] for e in audit['errors']])

    def data(self):
        raw=plate_with_hole();batch=build_native_expression_requirements(raw)
        plan=build_layout(batch['parts'][0],batch['geometryAudit']['parts'][0],raw['parts'][0])
        return batch['parts'][0],plan

    def test_rectangle_is_complete_without_redundant_boundary_dimensions(self):
        req,plan=self.data()
        self.assertEqual('READY',plan['status'])
        self.assertEqual('PLAN_COMPLETE',plan['coverage']['status'])
        self.assertEqual(2,sum(d['role']=='overall' for d in plan['dimensions']))
        self.assertFalse(any(d['key'].startswith('boundary') for d in plan['dimensions']))
        # P6: the bolt hole is shown by its Tekla symbol, not owned centre lines.
        self.assertEqual(['SYMBOL'],[b['display'] for b in plan['bolts']])
        self.assertEqual(0,sum(c['role'] in ('center','hole') for c in plan['curves']))
        self.assertEqual(1,sum(o['kind']=='bolt_symbol' for o in plan['observations']['objects']))
        self.assertIsNone(next(n for n in plan['notes'] if n['kind']=='label')['tip'])

    def test_planning_is_deterministic_and_does_not_mutate_source(self):
        raw=plate_with_hole();batch=build_native_expression_requirements(raw);before=copy.deepcopy(batch)
        args=(batch['parts'][0],batch['geometryAudit']['parts'][0],raw['parts'][0])
        self.assertEqual(build_layout(*args),build_layout(*args))
        self.assertEqual(before,batch)

    def test_boolean_result_is_drawn_once_without_duplicate_model_holes(self):
        raw=plate_with_hole();batch=build_native_expression_requirements(raw)
        geometry=batch['geometryAudit']['parts'][0];geometry['planarCuts']=[{'id':'synthetic-cut'}]
        plan=build_layout(batch['parts'][0],geometry,raw['parts'][0])
        self.assertEqual('ANALYTIC_NATIVE_CURVES',plan['geometryMode'])
        self.assertEqual(4,sum(c['role']=='boundary' for c in plan['curves']))
        symbols=sum(len(b['holes']) for b in plan['bolts'])
        self.assertEqual(1,sum(c['role']=='hole' for c in plan['curves'])//4+symbols)

    def test_blocked_source_never_reaches_layout(self):
        raw=plate_with_hole();batch=build_native_expression_requirements(raw);batch['parts'][0]['status']='BLOCKED'
        with self.assertRaises(ValueError):build_layout(batch['parts'][0],batch['geometryAudit']['parts'][0],raw['parts'][0])

    def test_required_note_collision_is_relocated_without_silently_omitting(self):
        raw=plate_with_hole();batch=build_native_expression_requirements(raw)
        batch['parts'][0]['specifications']={'cutting_method':dict(value='SAW',evidence='user')}
        plan=build_layout(batch['parts'][0],batch['geometryAudit']['parts'][0],raw['parts'][0],spacing=.15)
        self.assertEqual('READY',plan['status'])
        self.assertIn('SAW',[n['text'] for n in plan['notes']])
        self.assertEqual('SOLVED',plan['layout']['status'])
        self.assertTrue(any(s['choice'] for s in plan['layout']['selections'] if s['key']=='spec:cutting_method'))

    def test_slot_leader_uses_capsule_not_bounding_rectangle(self):
        h=dict(kind='SLOT',width=10,length=40,angle=0)
        tip=near_hole_anchor(h,[0,0],[40,20])
        self.assertAlmostEqual(5,tip[1])
        tip=near_hole_anchor(h,[0,0],[100,10])
        self.assertAlmostEqual(5,((tip[0]-15)**2+tip[1]**2)**.5)
        self.assertGreater(tip[0],15)

    def test_slot_native_arcs_use_clockwise_outer_caps(self):
        curves=hole_boundary(dict(kind='SLOT',width=10,length=40,angle=0),[0,0])
        right,left=curves[1],curves[3]
        self.assertEqual([15,5],right['start'])
        self.assertEqual([15,-5],right['end'])
        self.assertEqual([15,0],right['center'])
        self.assertEqual([-15,-5],left['start'])
        self.assertEqual([-15,5],left['end'])

    def test_plan_does_not_link_coordinates_by_accumulated_tolerance(self):
        req,plan=self.data();req=copy.deepcopy(req)
        req['facts']['points'].update(a=[0,0],b=[.08,0],c=[.16,0]);req['facts']['datum']='a'
        req['requirements']=[dict(id='c/location_x',feature='c',aspect='location_x',expected=.16)]
        observed=dict(schemaVersion='expressionObservations.v1',sourceDigest=req['sourceDigest'],stage='PLAN',objects=[])
        result=audit_expression_coverage(req,observed)
        self.assertEqual('MISSING',result['coverage'][0]['status'])

    def test_missing_or_wrong_exported_boundary_fails(self):
        s=dict(kind='LINE',start=[0,0],end=[100,0])
        self.assertTrue(boundary_matches(s,[[[0,0],[100,0]]],.1))
        self.assertFalse(boundary_matches(s,[[[0,0],[50,0]]],.1))
        self.assertFalse(boundary_matches(s,[[[0,.2],[100,.2]]],.1))

    def test_duplicate_half_circles_cannot_prove_a_complete_hole(self):
        req=dict(facts=dict(holes=[dict(id='h',pointId='hole:h',kind='ROUND',diameter=10)],points={'hole:h':[0,0]}))
        half=dict(kind='ARC',center=[0,0],radius=5,sweep=math.pi,points=sample_arc([0,0],5,0,math.pi))
        with self.assertRaisesRegex(ValueError,'incomplete'):_hole_shapes(req,dict(paths=[half,half]),.1)
        other=dict(half,points=sample_arc([0,0],5,math.pi,math.pi))
        self.assertEqual(10,_hole_shapes(req,dict(paths=[half,other]),.1)[0]['diameter'])

    def test_full_circle_uses_one_diameter_and_concentric_location_proof(self):
        raw=plate_with_hole();batch=build_native_expression_requirements(raw);req=batch['parts'][0]
        segments=[dict(id='edge:'+str(i),kind='ARC',start=a,end=b,startPoint='vertex:'+str(i),endPoint='vertex:'+str(1-i),center=[0,0],radius=50,clockwise=False) for i,(a,b) in enumerate((([50,0],[-50,0]),([-50,0],[50,0])))]
        req['facts']=dict(segments=segments,points={'vertex:0':[50,0],'vertex:1':[-50,0],'hole:h':[0,0],'edge:0:extreme:1':[0,50],'edge:1:extreme:3':[0,-50]},
                          holes=[dict(id='h',pointId='hole:h',kind='ROUND',diameter=20)],datum='vertex:1',bounds=[-50,-50,50,50],boundaryLoops=[dict(segments=['edge:0','edge:1'])])
        req['requirements'],req['groups']=_requirements(req['source']['partPosition'],10,req['facts'],{})
        p=build_layout(req,batch['geometryAudit']['parts'][0],raw['parts'][0])
        self.assertEqual('READY',p['status'],p['coverage'])
        self.assertEqual([],p['dimensions'])
        self.assertIn('Φ100',[n['text'] for n in p['notes']])
        self.assertFalse(any(n['kind']=='radius' for n in p['notes']))

    def test_export_acquisition_keeps_actual_coordinates_and_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'drawing.dxf';d=ezdxf.new();m=d.modelspace()
            m.add_line([10,20],[60,20]);m.add_circle([35,45],5);m.add_text('2\\U+03A630',dxfattribs={'height':2.5})
            d.saveas(path)
            render=load_render(path,dict(scale=2,origin=[10,20]))
            self.assertEqual([[0,0],[100,0]],render['paths'][0]['points'])
            self.assertEqual([50,50],render['circles'][0]['center'])
            self.assertEqual('2Φ30',render['texts'][0]['text'])

    def test_readback_digest_and_missing_objects_are_rejected(self):
        req,plan=self.data()
        raw=dict(stage='SAVED_REOPENED',id=plan['id'],sourceDigest='old',planDigest=plan['planDigest'],scale=plan['scale'],objects=[])
        with self.assertRaisesRegex(ValueError,'digest'):readback_observations(req,plan,raw,{})
        raw['sourceDigest']=req['sourceDigest']
        raw.update(geometryMode=plan['geometryMode'],linkedTargetCount=1,visiblePartCount=1,sourceBolts=symbol_bolts(plan))
        with self.assertRaisesRegex(ValueError,'Missing'):readback_observations(req,plan,raw,{})

    def test_hidden_solid_bolt_cannot_pass_part_only_readback(self):
        req,plan=self.data()
        raw=dict(stage='SAVED_REOPENED',id=plan['id'],sourceDigest=req['sourceDigest'],planDigest=plan['planDigest'],
                 scale=plan['scale'],geometryMode=plan['geometryMode'],linkedTargetCount=1,visiblePartCount=1,objects=[])
        with self.assertRaisesRegex(ValueError,'visibility evidence'):readback_observations(req,plan,raw,{})
        planned=symbol_bolts(plan)[0]
        raw['sourceBolts']=[dict(planned,hidden=True,representation='Solid')]
        with self.assertRaisesRegex(ValueError,'symbol display was not saved'):readback_observations(req,plan,raw,{})
        raw['sourceBolts']=[planned,dict(modelId=-1,hidden=True,color='Red',representation='Solid',hole=True,axis=True)]
        with self.assertRaisesRegex(ValueError,'hardware graphics'):readback_observations(req,plan,raw,{})
        raw['sourceBolts']=[dict(modelId=-1,hidden=True,color='Invisible',representation='Symbol',hole=False,axis=False)]
        with self.assertRaisesRegex(ValueError,'Planned bolt symbol missing'):readback_observations(req,plan,raw,{})
        raw['sourceBolts']=[planned]
        with self.assertRaisesRegex(ValueError,'Missing/extra native annotations'):readback_observations(req,plan,raw,{})

    def test_three_hole_row_closes_both_edges_without_two_interval_pitch(self):
        # Convention v1: one edge-to-edge chain; n@p needs at least three equal intervals.
        req,geo,raw=self.shoulder_data()
        req['facts']['points']['hole:c']=[113,161]
        req['facts']['holes'].append(dict(id='c',pointId='hole:c',kind='ROUND',diameter=26))
        req['requirements'],req['groups']=_requirements(req['source']['partPosition'],10,req['facts'],{})
        plan=build_layout(req,geo,raw)
        self.assertEqual('READY',plan['status'],plan['coverage'])
        holes=next(d for d in plan['dimensions'] if d['key']=='holes-0')
        self.assertEqual([18,55,40,40,55,18],[round(v,5) for v in holes['values']])
        self.assertFalse(any(d['combine'] for d in plan['dimensions']))
        witnesses=[c for c in plan['curves'] if c['role']=='witness' and c['feature']=='holes-0']
        self.assertEqual([0,18,73,113,153,208,226],[c['start'][0] for c in witnesses])

    def test_observation_budget_cannot_override_model_budget(self):
        req,plan=self.data();obs=copy.deepcopy(plan['observations']);obs['geometryToleranceMm']=10
        self.assertEqual('FAIL',audit_expression_coverage(req,obs)['status'])

    def test_actual_text_collision_and_off_sheet_are_not_pass(self):
        raw=dict(scale=1,origin=[30,50],frameOrigin=[0,0],width=100,height=80,sheetSize=[420,297],objects=[
            dict(kind='text',key='a',text='A',boxMin=[0,0],boxMax=[10,3]),
            dict(kind='text',key='b',text='B',boxMin=[5,0],boxMax=[15,3])])
        render=dict(texts=[],lines=[],source='fixture')
        result=visual_audit({},raw,render)
        self.assertEqual('FAIL',result['status'])
        self.assertEqual('TEXT_OVERLAP',result['errors'][0]['code'])
        raw['origin']=[-20,-20]
        self.assertIn('VIEW_OUTSIDE_SAFE_SHEET',[e['code'] for e in visual_audit({},raw,render)['errors']])


if __name__=='__main__':unittest.main()
