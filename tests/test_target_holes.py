import base64
import json
from pathlib import Path
import unittest

from test_nc_witness import file_record
from zijinhua_tekla.part_drawing.native_expressions import build_native_expression_requirements
from zijinhua_tekla.part_drawing.native_layout import build_layout
from zijinhua_tekla.part_drawing.template_snapshot import build_template_snapshot, overlaps


def capture():
    return json.loads((Path(__file__).parent/'fixtures/native_sections/target_slot_nc.json').read_text('utf-8'))


def change_raw(bundle, change, both=True):
    for key in ('before', 'after') if both else ('after',):
        row = bundle[key]
        raw = json.loads(base64.b64decode(row['bytesBase64']))
        change(raw['parts'][0])
        bundle[key] = dict(row, **file_record(row['name'], json.dumps(raw)))


def evaluate(bundle):
    b = build_native_expression_requirements(bundle, nc_tolerance_mm=.01, max_resolution_mm=.01)
    return b['parts'][0], b['geometryAudit']['parts'][0]


class TargetHoleTests(unittest.TestCase):
    def test_current_three_part_group_with_real_axis_and_nc_passes(self):
        r,g = evaluate(capture())
        self.assertEqual('READY',r['status'],g['issues'])
        h, = g['materialFinalState']['holeEvidence']
        self.assertEqual('MATCHED', h['penetration']['status'])
        self.assertEqual(6,len(h['penetration']['axes']))
        self.assertEqual(6,len(h['targetShapeEvidence']['mappings']))
        self.assertEqual(45,round(g['sectionGeometry']['bevels'][0]['angle']))

    def test_real_template_fits_main_and_section_with_bolt_symbol(self):
        c=capture();r,g=evaluate(c)
        raw=json.loads(base64.b64decode(c['before']['bytesBase64']))
        evidence=json.loads((Path(__file__).parent/'fixtures/native_sections/active_template.json').read_text('utf-8'))
        t=build_template_snapshot(evidence,'fixture-model',raw['parts'][0]['id'],25)
        p=build_layout(r,g,raw['parts'][0],template=t)
        self.assertEqual('READY',p['status'])
        self.assertEqual('PLAN_COMPLETE',p['sectionCoverage']['status'])
        self.assertEqual(25,next(n['height'] for n in p['notes'] if n['kind']=='label'))
        # The bolt symbol draws the hole and its axes. Owned centerlines are not planned.
        self.assertEqual(['SYMBOL'], [b['display'] for b in p['bolts']])
        self.assertEqual([], [v for v in p['curves'] if v['role'] in ('center', 'hole')])
        self.assertEqual('6*D26(56x26)', next(n['text'] for n in p['notes'] if n['kind']=='hole_callout'))
        for mark in [b for b in p['layout']['textBoxes'] if b['key'].startswith('section-mark:')]:
            self.assertFalse(any(overlaps(mark['bounds'],b['bounds']) for b in p['layout']['textBoxes'] if b is not mark))
        self.assertFalse(overlaps(p['viewPlan']['availableArea'],p['sections'][0]['viewPlan']['availableArea']))

    def test_association_count_has_no_cap_and_order_does_not_assign_shape(self):
        for count in (2,3,5,8):
            c=capture()
            def change(p):
                b=p['boltGroups']['value'][0]
                b['targets']['otherParts']['value']=[{'id':p['id'],'runtimeType':p['runtimeType']}]+[
                    {'id':900000+i,'runtimeType':p['runtimeType']} for i in range(count-3)]
                if count==2:b['targets']['partToBeBolted']['value']['id']=p['id'];b['targets']['otherParts']['value']=[]
            change_raw(c,change)
            with self.subTest(count=count): self.assertEqual('READY',evaluate(c)[0]['status'])

    def test_intersection_cannot_be_replaced_by_association_or_infinite_axis(self):
        for mode in ('missing','target','short','off_axis','gap','odd'):
            c=capture()
            def change(p):
                b=p['boltGroups']['value'][0];rows=b['axisIntersections']['value']
                if mode=='missing':rows.clear();return
                r=rows[0]
                if mode=='target':r['targetPartId']+=1
                if mode=='short':r['hardwarePoints']=r['hardwarePoints'][:2]
                if mode=='off_axis':r['targetPoints'][0][2]+=5
                if mode=='gap':
                    a,z=r['targetPoints'];mid=[(x+y)/2 for x,y in zip(a,z)]
                    r['hardwarePoints']=[a,mid,[m+(v-m)*.5 for m,v in zip(mid,z)],z]
                if mode=='odd':r['hardwarePoints'].pop()
            change_raw(c,change)
            with self.subTest(mode=mode):self.assertEqual('BLOCKED',evaluate(c)[0]['status'])

    def test_nc_hole_mapping_and_source_fail_closed(self):
        for mode in ('wrong_number','missing','duplicate','direction','dimension','unknown_block','changed_source'):
            c=capture();f=c['files'][0];text=base64.b64decode(f['bytesBase64']).decode()
            if mode=='wrong_number':text=text.replace('T2-10B-304','T2-10B-999')
            if mode=='missing':text='\n'.join(line for line in text.splitlines() if '55.00s     68.98' not in line)
            if mode=='duplicate':text=text.replace('55.00s    148.98','55.00s     68.98')
            if mode=='direction':text=text.replace('0.00l      0.00     30.00','0.00l     30.00      0.00')
            if mode=='dimension':text=text.replace('26.00','28.00')
            if mode=='unknown_block':text=text.replace('EN','XX\nEN')
            if mode=='changed_source':change_raw(c,lambda p:p.update(id=p['id']+1),False)
            c['files'][0]=dict(f,**file_record(f['name'],text))
            with self.subTest(mode=mode):self.assertEqual('BLOCKED',evaluate(c)[0]['status'])


if __name__=='__main__':unittest.main()
