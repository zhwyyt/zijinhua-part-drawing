"""Independent cutter/shell and machining contract regressions; no live Tekla."""

import copy
import math
import unittest

from test_native_facts import read
from test_native_geometry import FRAME, prism
from test_cut_final_state import evidence, final_solid, operation, circle_points
from test_independent_expressions import source as simple_source, synthetic_facts
from zijinhua_tekla.part_drawing.native_expressions import _facts
from zijinhua_tekla.part_drawing.analytic_geometry import contour_segments, _at
from zijinhua_tekla.part_drawing.independent_layout.contracts import ContractError, digest, seal
from zijinhua_tekla.part_drawing.independent_layout.cut_geometry import tangent_contour
from zijinhua_tekla.part_drawing.independent_layout.expressions import adapt_expressions
from zijinhua_tekla.part_drawing.independent_layout.semantic_coverage import audit_semantics
from zijinhua_tekla.part_drawing.independent_layout.sources import freeze_native_sources
from zijinhua_tekla.part_drawing.independent_layout.preparation import prepare_layout_input


def cylinder(identity=31):
    op=operation(identity,center=[80,30],radius=6)
    g=op['operativeGeometry']['value']
    g.update(runtimeType='Tekla.Structures.Model.Beam',profile=read('D12'),
             coordinateSystem=read(dict(origin=[80,30,0],axisX=[0,0,1],axisY=[1,0,0])))
    return op


def sample(rounded=True):
    points=[[20,15],[50,15],[50,45],[20,45]]
    op=operation(30,points=points)
    if rounded:
        ring=contour_segments(points,[('CHAMFER_ROUNDING',4,4)]*4,['fixture']*4)
        points=[_at(s,i/(8 if s['kind']=='ARC' else 1)) for s in ring for i in range(8 if s['kind']=='ARC' else 1)]
        for c in op['operativeGeometry']['value']['modelingContour']['value']['value']:
            c['chamfer'].update(Type=read(dict(name='CHAMFER_ROUNDING')),X=read(4),Y=read(4))
        solid=prism(points,-5,15)
        for f in solid['faces']['value']:f['originPartId']=read(30)
        op['operativeSolid']=read(solid)
    return evidence([op,cylinder()],final_solid(holes=[(points,30),(circle_points([80,30],6),31)]))


def reseal(value):
    return seal({k:v for k,v in value.items() if k!='digest'})


class IndependentCutoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw=sample()
        cls.source=freeze_native_sources(cls.raw,extend_cutouts=True)['entries'][0]
        cls.expressions=adapt_expressions(cls.source)

    def test_complete_proof_keeps_legacy_failure_and_all_requirements(self):
        s=self.source
        self.assertEqual('SOURCE_READY',s['status'],s.get('extensionFailure'))
        self.assertEqual('INPUT_BLOCKED',s['legacySource']['status'])
        self.assertEqual('MATCHED',s['geometryProof']['solidCheck']['status'])
        self.assertEqual('SEMANTICS_COMPLETE',self.expressions['status'])
        self.assertTrue(all(b['status']=='BOUND' for b in self.expressions['coverage']['bindings']))
        self.assertEqual(2,len(s['geometryProof']['holes']))
        self.assertEqual(self.raw,s['nativeEvidence'])

    def test_sharp_rectangle_has_complete_location(self):
        source=freeze_native_sources(sample(False),extend_cutouts=True)['entries'][0]
        self.assertEqual('SEMANTICS_COMPLETE',adapt_expressions(source)['status'])

    def test_missing_faces_edges_wrong_sources_and_blind_cut_fail(self):
        for change in ('side','edge','cap','owner','normal','thickness','blind','radius','unknown_child','source'):
            raw=copy.deepcopy(self.raw);p=raw['parts'][0];solid=p['finalSolid']['value']
            if change=='side':solid['faces']['value'].pop()
            elif change=='edge':solid['edges']['value'].pop()
            elif change=='cap':solid['faces']['value'].pop(0)
            elif change=='owner':solid['faces']['value'][-1]['originPartId']=read(999)
            elif change=='normal':solid['faces']['value'][-1]['normal']=read([0,0,1])
            elif change=='thickness':p['reports']['PROFILE.WIDTH']=read(11)
            elif change=='radius':p['operations']['value'][1]['operativeGeometry']['value']['profile']=read('D14')
            elif change=='unknown_child':p['children']['value'].append(dict(id=99,runtimeType='unknown'))
            elif change=='source':del p['guid']
            elif change=='blind':
                cutter=prism(circle_points([80,30],6),0,5)
                for f in cutter['faces']['value']:f['originPartId']=read(31)
                p['operations']['value'][1]['operativeSolid']=read(cutter)
            with self.subTest(change=change):
                result=freeze_native_sources(raw,extend_cutouts=True)
                self.assertEqual(0,result['counts']['SOURCE_READY'])

    def test_opposite_cap_drift_is_not_hidden_by_shared_projection(self):
        raw=copy.deepcopy(self.raw)
        raw['parts'][0]['finalSolid']['value']['faces']['value'][1]['loops']['value'][1]['vertices']['value'][0][0]+=.5
        self.assertEqual('INPUT_BLOCKED',freeze_native_sources(raw,extend_cutouts=True)['entries'][0]['status'])

    def test_deleted_location_group_and_object_cannot_be_resealed(self):
        for axis in (0,1):
            e=copy.deepcopy(self.expressions)
            group=next(g for g in e['scheme']['groups'] if g['role']=='cutout_location' and g['axis']==axis)
            e['scheme']['groups'].remove(group);e['schemeDigest']=digest(e['scheme'])
            e['objects']=[o for o in e['objects'] if o.get('schemeKey')!=group['key']]
            result=audit_semantics(self.source,reseal(e))
            self.assertEqual('UPSTREAM_CONTRACT_GAP',result['status'])
            self.assertIn('CUTOUT_LOCATION_MISSING',[x.get('code') for x in result['errors']])

    def test_deleted_size_and_radius_are_missing_even_with_other_paths(self):
        for kind in ('size','radius'):
            e=copy.deepcopy(self.expressions)
            if kind=='size':
                group=next(g for g in e['scheme']['groups'] if g['role']=='cutout_size')
                e['scheme']['groups'].remove(group);e['schemeDigest']=digest(e['scheme'])
                e['objects']=[o for o in e['objects'] if o.get('schemeKey')!=group['key']]
            else:e['objects'].remove(next(o for o in e['objects'] if o['kind']=='RADIUS'))
            self.assertEqual('UPSTREAM_CONTRACT_GAP',audit_semantics(self.source,reseal(e))['status'])

    def test_proof_tamper_requires_recomputation_not_only_digest(self):
        s=copy.deepcopy(self.source);s['geometryProof']['solidCheck']['thickness']=11
        with self.assertRaisesRegex(ContractError,'EXTENDED_SOURCE_MISMATCH'):
            adapt_expressions(reseal(s))

    def test_tangent_consumed_edge_is_supported_but_overlap_is_not(self):
        pts=[[0,0],[20,0],[20,10],[0,10]]
        chamfers=[('CHAMFER_NONE',0,0)]*4;chamfers[2]=('CHAMFER_ROUNDING',10,10)
        ring=tangent_contour(pts,chamfers,['fixture']*4)
        self.assertEqual(1,sum(s['kind']=='ARC' for s in ring))
        self.assertTrue(all(math.dist(s['start'],s['end'])>1e-6 for s in ring))
        chamfers[2]=('CHAMFER_ROUNDING',10.1,10.1)
        with self.assertRaisesRegex(ContractError,'CUT_OVERLAP'):tangent_contour(pts,chamfers,['fixture']*4)

    def test_translation_and_mirror_keep_complete_semantics(self):
        original=self.source['requirements']['facts']
        for sign,dx,dy in ((1,1200,-300),(-1,0,0)):
            facts=copy.deepcopy(original)
            transform=lambda p:[sign*p[0]+dx,p[1]+dy]
            facts['points']={k:transform(p) for k,p in facts['points'].items()}
            for s in facts['segments']:
                for k in ('start','end','center'):
                    if k in s:s[k]=transform(s[k])
                if s['kind']=='ARC' and sign<0:s['clockwise']=not s['clockwise']
            xmin,ymin,xmax,ymax=facts['bounds']
            xs=sorted([sign*xmin+dx,sign*xmax+dx]);facts['bounds']=[xs[0],ymin+dy,xs[1],ymax+dy]
            result=adapt_expressions(synthetic_facts(simple_source(),facts),complete_cutouts=True)
            self.assertEqual('SEMANTICS_COMPLETE',result['status'],result['coverage'])

    def test_same_size_openings_use_their_own_real_step_edge(self):
        from test_cut_final_state import polygon
        material=dict(outline=dict(segments=polygon([[0,0],[100,0],[100,70],[50,70],[50,60],[0,60]])),
                      holes=[dict(kind='CUTOUT',segments=polygon([[x,20],[x+10,20],[x+10,40],[x,40]])) for x in (20,70)])
        facts=_facts(material)
        result=adapt_expressions(synthetic_facts(simple_source(),facts),complete_cutouts=True)
        self.assertEqual('SEMANTICS_COMPLETE',result['status'])
        dims=[o for o in result['objects'] if o['kind']=='DIMENSION' and o['role']=='cutout_location' and o['axis']=='y']
        self.assertEqual(2,len(dims))
        # The first opening chooses the high-side edge on the equal-distance tie;
        # the second chooses the closer low-side edge. Neither extends the step.
        self.assertEqual([20,20],sorted(o['values'][0] for o in dims))
        for g in result['scheme']['groups']:
            if g['role']=='cutout_location':
                self.assertIn(g['cutoutEvidence']['datumSegmentId'],facts['boundaryLoops'][0]['segments'])

    def test_rotated_cutout_is_explicit_contract_gap(self):
        from test_cut_final_state import polygon
        facts=_facts(dict(outline=dict(segments=polygon([[0,0],[100,0],[100,60],[0,60]])),
                          holes=[dict(kind='CUTOUT',segments=polygon([[30,20],[40,30],[30,40],[20,30]]))]))
        result=adapt_expressions(synthetic_facts(simple_source(),facts),complete_cutouts=True)
        self.assertEqual('UPSTREAM_CONTRACT_GAP',result['status'])
        self.assertTrue(result['scheme']['cutoutDiagnostics'])

    def test_nearby_wrong_datum_does_not_pass_by_numeric_value(self):
        e=copy.deepcopy(self.expressions)
        g=next(g for g in e['scheme']['groups'] if g['role']=='cutout_location')
        g['cutoutEvidence']['datumSegmentId']=next(s['id'] for s in self.source['requirements']['facts']['segments'] if s['id'].startswith('cutout:'))
        e['schemeDigest']=digest(e['scheme'])
        with self.assertRaisesRegex(ContractError,'CUTOUT_DATUM'):audit_semantics(self.source,e)

    def test_template_sample_cannot_become_layout_or_write_permission(self):
        s=simple_source();e=adapt_expressions(s)
        study=seal(dict(schemaVersion='TemplateStudy.v1',status='STUDY_ONLY',productionEligible=False,
                        templateId='fixture10',sheetSizeNative=[2980,2110],pending=['CALIBRATION']))
        r=prepare_layout_input(s,e,[study])
        self.assertEqual('INPUT_BLOCKED',r['status'])
        self.assertFalse(r['candidateGenerationAllowed']);self.assertFalse(r['nativeWriteAllowed'])
        self.assertEqual({o['objectId'] for o in e['objects']},set(r['requiredObjectIds']))
        with self.assertRaisesRegex(ContractError,'TEMPLATE_DUPLICATE'):prepare_layout_input(s,e,[study,study])
        bad=copy.deepcopy(study);bad['productionEligible']=True
        with self.assertRaisesRegex(ContractError,'TEMPLATE_STUDY_CONTRACT'):prepare_layout_input(s,e,[reseal(bad)])

    def test_preparation_rechecks_missing_semantics(self):
        s=simple_source();e=adapt_expressions(s)
        e['objects']=[o for o in e['objects'] if o['kind']!='HOLE_NOTE']
        with self.assertRaisesRegex(ContractError,'SEMANTIC_GAP'):prepare_layout_input(s,reseal(e),[])


if __name__=='__main__':unittest.main()
