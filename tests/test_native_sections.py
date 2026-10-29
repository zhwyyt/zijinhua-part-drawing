import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from zijinhua_tekla.part_drawing.native_expressions import build_native_expression_requirements
from zijinhua_tekla.part_drawing.native_layout import build_layout
from zijinhua_tekla.part_drawing.section_plan import audit_section_plan, section_scheme
from zijinhua_tekla.part_drawing.template_snapshot import build_template_snapshot, overlaps
from test_template_snapshot import evidence
from zijinhua_tekla.part_drawing.section_readback import audit_sections


FIXTURES = Path(__file__).parent/'fixtures'/'native_sections'


def capture(name='plate_with_shared_slots'):
    return json.loads((FIXTURES/(name+'.json')).read_text(encoding='utf-8'))


def evaluate(raw):
    batch = build_native_expression_requirements(raw)
    return batch['parts'][0], batch['geometryAudit']['parts'][0]


class SectionGeometryTests(unittest.TestCase):
    def test_shared_bolt_slots_use_group_parameters_and_shop_callout(self):
        raw = capture()
        req, geo = evaluate(raw)
        proof = geo['sectionGeometry']
        self.assertEqual('MATCHED', proof['status'], proof)
        bevel, = proof['bevels']
        for key, expected in [('angle',45), ('depth',12), ('land',2), ('thickness',14)]:
            self.assertAlmostEqual(expected, bevel[key], places=5)
        self.assertEqual('MATCHED', geo['materialFinalState']['status'], geo['materialFinalState'])
        self.assertEqual('BOLT_GROUP_PARAMETERS', geo['materialFinalState']['holeEvidence'][0]['shapeBasis'])
        self.assertEqual('READY', req['status'], req['issues'])
        group, = req['groups']
        self.assertEqual('BOLT', group['origin'])
        self.assertEqual('SLOT', group['kind'])
        self.assertEqual(6, group['count'])
        self.assertAlmostEqual(26, group['width'])
        self.assertAlmostEqual(56, group['length'])
        from zijinhua_tekla.part_drawing.native_layout import bolt_display, bolt_hole_tip, build_layout
        from test_template_snapshot import evidence
        part = raw['parts'][0]
        self.assertEqual(['SYMBOL'], [b['display'] for b in bolt_display(req['facts'], part)])
        template = evidence()
        template['partId'] = part['id']
        snapshot = build_template_snapshot(template, 'fixture-model', part['id'], 2.5)
        plan = build_layout(req, geo, part, template=snapshot)
        self.assertEqual('READY', plan['status'], plan.get('coverage'))
        note = next(n for n in plan['notes'] if n['kind']=='hole_callout')
        self.assertEqual('6*D26(56x26)', note['text'])
        self.assertEqual(bolt_hole_tip(
            [h for h in req['facts']['holes'] if h['id'] in note['members']], req['facts']), note['tip'])
        self.assertFalse(any(d['combine'] for d in plan['dimensions'] if d['key'].startswith('holes')))
        self.assertEqual('MATCHED', geo['operationCheck']['status'])
        self.assertEqual(['main','section:0'], req['requiredViews'])
        self.assertEqual('expressionRequirements.v2', req['schemaVersion'])

    def test_real_beam_two_bevels_becomes_ready_with_independent_cut_proof(self):
        req, geo = evaluate(capture('beam_two_bevels'))
        self.assertEqual('READY', req['status'], geo['issues'])
        self.assertEqual('MATCHED', geo['materialFinalState']['status'])
        bevels = geo['sectionGeometry']['bevels']
        self.assertEqual(2, len(bevels))
        for b in bevels:
            self.assertAlmostEqual(45, b['angle'], places=5)
        self.assertAlmostEqual(2.217624492, bevels[0]['land'], places=6)
        self.assertAlmostEqual(2, bevels[1]['land'], places=5)

    def test_shell_tampering_cannot_pass(self):
        for change in ('missing_face', 'extra_face', 'normal', 'owner', 'vertex', 'missing_edge', 'curved_edge'):
            raw = capture('beam_two_bevels'); part = raw['parts'][0]
            solid = part['finalSolid']['value']; faces = solid['faces']['value']
            if change == 'missing_face': faces.pop()
            if change == 'extra_face': faces.append(copy.deepcopy(faces[0]))
            if change == 'normal': faces[0]['normal']['value'] = [-x for x in faces[0]['normal']['value']]
            if change == 'owner': faces[0]['originPartId']['value'] = 999
            if change == 'vertex': faces[0]['loops']['value'][0]['vertices']['value'][0][0] += 1
            if change == 'missing_edge': solid['edges']['value'].pop()
            if change == 'curved_edge': solid['edges']['value'][0]['apiType'] = 'EDGE_TYPE_CURVED_SURFACE'
            with self.subTest(change=change):
                req, geo = evaluate(raw)
                self.assertEqual('BLOCKED', req['status'])
                self.assertNotEqual('MATCHED', geo['sectionGeometry']['status'])

    def test_unexplained_operation_and_changed_cutter_stay_blocked(self):
        for change in ('child', 'cutter', 'raw', 'thickness'):
            raw = capture('beam_two_bevels'); p = raw['parts'][0]
            if change == 'child': p['children']['value'].append({'id':987, 'runtimeType':'Unknown'})
            if change == 'cutter': p['operations']['value'][0]['operativeSolid']['value']['faces']['value'].pop()
            if change == 'raw': p['rawSolid']['value']['faces']['value'].pop()
            if change == 'thickness': p['profile']['value'] = 'PL20*1050'
            with self.subTest(change=change):
                self.assertEqual('BLOCKED', evaluate(raw)[0]['status'])


def planned_fixture():
    # Synthetic no-hole variation of the measured shell; NEVER a live target.
    raw=capture();raw['parts'][0]['boltGroups']['value']=[]
    req,geo=evaluate(raw); part=raw['parts'][0]
    t=evidence();t['partId']=part['id']
    snapshot=build_template_snapshot(t,'fixture-model',part['id'],2.5)
    plan=build_layout(req,geo,part,template=snapshot)
    return req,geo,part,snapshot,plan


class SectionPlanningTests(unittest.TestCase):
    def test_required_cut_and_native_section_are_separate_from_main_plane(self):
        req,geo,part,snapshot,plan=planned_fixture()
        self.assertEqual('READY',plan['status'])
        self.assertEqual('nativeDrawingPlan.v2',plan['schemaVersion'])
        self.assertEqual('PLAN_COMPLETE',plan['sectionCoverage']['status'])
        section,=plan['sections']; requirement,=req['sectionRequirements']
        self.assertEqual(1,section['scale'])
        self.assertEqual(27,requirement['station'])
        self.assertEqual('45°  d=12  c=2',section_scheme(requirement)['notes'][0]['text'])
        self.assertFalse(overlaps(plan['viewPlan']['availableArea'],section['viewPlan']['availableArea']))
        self.assertEqual(5,len(section['curves']))
        for view in (plan,section):
            self.assertFalse(any(overlaps(view['viewPlan']['availableArea'],r['bounds']) for r in snapshot['occupiedRegions']))

    def test_missing_section_changed_numbers_and_wrong_plane_do_not_cover(self):
        req,_,_,_,plan=planned_fixture()
        for change in ('missing','angle','boundary','frame','station','paper'):
            p=copy.deepcopy(plan)
            if change=='missing':p['sections']=[]
            if change=='angle':p['sections'][0]['notes'][0]['text']='35°'
            if change=='boundary':p['sections'][0]['curves'].pop()
            if change=='frame':p['sections'][0]['frame']['origin'][0]+=2
            if change=='station':p['sections'][0]['cut']['start'][0]+=1
            if change=='paper':p['sections'][0]['viewPlan']['availableArea']=p['viewPlan']['availableArea']
            with self.subTest(change=change):self.assertEqual('FAIL',audit_section_plan(req,p)['status'])

    def test_fixed_scale_overflow_and_no_template_cannot_drop_section(self):
        req,geo,part,_,_=planned_fixture()
        with self.assertRaisesRegex(ValueError,'SECTION_LIVE_TEMPLATE_REQUIRED'):
            build_layout(req,geo,part)
        raw=capture('beam_two_bevels');req,geo=evaluate(raw);part=raw['parts'][0]
        t=evidence();t['partId']=part['id'];snapshot=build_template_snapshot(t,'fixture-model',part['id'],2.5)
        with self.assertRaisesRegex(ValueError,'LAYOUT_OVERFLOW'):
            build_layout(req,geo,part,scale=1,template=snapshot)

    def test_long_bevel_plate_shares_one_reduced_scale(self):
        raw=capture('beam_two_bevels'); req,geo=evaluate(raw); part=raw['parts'][0]
        t=evidence(); t['partId']=part['id']
        snapshot=build_template_snapshot(t,'fixture-model',part['id'],2.5)
        plan=build_layout(req,geo,part,template=snapshot)
        self.assertEqual('READY', plan['status'], plan.get('readability'))
        self.assertGreater(plan['scale'], 1)
        self.assertEqual(plan['scale'], plan['viewPlan']['scale'])
        self.assertTrue(plan['sections'])
        for section in plan['sections']:
            self.assertEqual(plan['scale'], section['scale'])
            self.assertEqual(plan['scale'], section['viewPlan']['scale'])
            self.assertFalse(overlaps(plan['viewPlan']['availableArea'], section['viewPlan']['availableArea']))

    def test_saved_section_and_dxf_are_both_required_and_must_agree(self):
        req,_,_,_,plan=planned_fixture(); p=plan['sections'][0]; r=req['sectionRequirements'][0]
        # Simulated SDK/DXF adapter boundary, not claimed as a live Tekla result.
        rows=[]; lines=[]; texts=[]
        for c in p['curves']:
            rows.append(dict(key=c['key'],id=c['key'],kind='line',style='SolidLine',start=c['start'],end=c['end']))
            lines.append([c['start'],c['end']])
        for n in p['notes']:
            rows.append(dict(key=n['key'],id=n['key'],kind='text',text=n['text'],height=n['height'],at=n['at'],
                             tip=n['tip'],arrow='FilledArrow' if n['tip'] else 'NoArrow'))
            texts.append(dict(text=n['text'],min=n['at'],max=n['at']))
        section=dict(viewId=r['id'],viewType='SectionView',id=plan['id'],stage='SAVED_REOPENED',
                     sourceDigest=req['sourceDigest'],planDigest=plan['planDigest'],scale=1,
                     linkedTargetCount=1,visiblePartCount=0,geometryMode='ANALYTIC_NATIVE_CURVES',
                     displayFrame=r['frame'],viewFrame=r['frame'],sourceBolts=[],objects=rows,
                     origin=[1000,1000],frameOrigin=[0,0],width=200,height=400)
        raw=dict(schemaVersion='nativeDrawingReadback.v2',sections=[section],displayFrame=plan['frame'],
                 origin=[100,1000],frameOrigin=[0,0],width=400,height=400,
                 objects=[dict(kind='section_mark',key='section-mark:'+r['id'],label='A',**p['cut'])])
        for c in plan['curves']:
            if c['role']=='bevel':
                raw['objects'].append(dict(key=c['key'],kind='line',style='SolidLine',start=c['start'],end=c['end']))
                lines.append([c['start'],c['end']])
        texts += [dict(text=r['label'],min=p['cut'][end],max=p['cut'][end]) for end in ('start','end')]
        render=dict(lines=lines,texts=texts)
        for change in ('none','missing','plane','cut','angle','dxf_line','dxf_text','part','overlap','bolt','rim','end_label'):
            actual=copy.deepcopy(raw); drawing=copy.deepcopy(render); child=actual['sections'][0]
            if change=='missing':actual['sections']=[]
            if change=='plane':child['viewFrame']['origin'][2]+=1
            if change=='cut':actual['objects'][0]['start'][0]+=1
            if change=='angle':next(o for o in child['objects'] if o['kind']=='text')['text']='35°'
            if change=='dxf_line':drawing['lines']=[]
            if change=='dxf_text':drawing['texts']=[]
            if change=='part':child['linkedTargetCount']=0
            if change=='overlap':child['origin']=actual['origin']
            if change=='bolt':child['sourceBolts']=[dict(hidden=False)]
            if change=='rim':actual['objects'][-1]['end'][0]+=1
            if change=='end_label':drawing['texts'].pop()
            with self.subTest(change=change), patch('zijinhua_tekla.part_drawing.section_readback.load_render',return_value=drawing), \
                    patch('zijinhua_tekla.part_drawing.native_readback.visual_audit',return_value=dict(status='PASS')):
                report=audit_sections(req,plan,actual,'simulated.dxf')
                self.assertEqual('PASS' if change=='none' else 'FAIL',report['status'],report)


if __name__ == '__main__':
    unittest.main()
