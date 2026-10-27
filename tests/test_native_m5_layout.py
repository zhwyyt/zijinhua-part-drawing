import base64
import copy
import json
import tempfile
import unittest
from pathlib import Path
from shapely.geometry import box

from test_template_snapshot import evidence
from zijinhua_tekla.part_drawing.native_expressions import build_native_expression_requirements
from zijinhua_tekla.part_drawing.native_layout import build_layout
from zijinhua_tekla.part_drawing.native_readback import audit_layout_readback
from zijinhua_tekla.part_drawing.native_render import load_render
from zijinhua_tekla.part_drawing.template_snapshot import build_template_snapshot
from zijinhua_tekla.part_drawing.layout_solver import LayoutOverflow


FIXTURES = Path(__file__).parent/'fixtures/part_drawing'


class NativeM5Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raw=json.loads((FIXTURES/'template-source-live.json').read_text(encoding='utf-8-sig'))
        batch=build_native_expression_requirements(raw)
        cls.req,cls.geo,cls.part=batch['parts'][0],batch['geometryAudit']['parts'][0],raw['parts'][0]
        paper=evidence();paper.update(partId=cls.part['id'],modelPath=raw['model']['value']['path'])
        cls.template=build_template_snapshot(paper,paper['modelPath'],cls.part['id'])

    def plan(self, requirements=None):
        return build_layout(requirements or self.req,self.geo,self.part,template=self.template)

    def test_repeatable_search_preserves_source_and_records_bounded_occupancy(self):
        before=copy.deepcopy((self.req,self.geo,self.part,self.template))
        a,b=self.plan(),self.plan()
        self.assertEqual(a,b)
        self.assertEqual(before,(self.req,self.geo,self.part,self.template))
        self.assertEqual('SOLVED',a['layout']['status'])
        self.assertEqual(18,len(a['layout']['textBoxes']))
        self.assertLessEqual(a['layout']['visited'],a['layout']['budget'])
        self.assertTrue(a['schemeDigest'])
        self.assertEqual('READY',a['status'])

    def test_saved_glyphs_fit_but_displaced_text_and_inner_overall_do_not(self):
        saved=json.loads((FIXTURES/'layout-side-saved-live/evidence.json').read_text(encoding='utf-8'))
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'drawing.dxf';path.write_bytes(base64.b64decode(saved['dxfBase64']))
            render=load_render(path,saved['readback'])
        plan=self.plan()
        self.assertEqual([],audit_layout_readback(plan,saved['readback'],render))
        changed=copy.deepcopy(render)
        expected=next(b for b in plan['layout']['textBoxes'] if b['key']=='overall-0:text:0')
        label=next(t for t in changed['texts'] if t['text']=='567' and box(*expected['bounds']).covers(box(*t['min'],*t['max'])))
        for key in ('min','max'):
            label[key]=[label[key][0]+100,label[key][1]+200]
        errors=audit_layout_readback(plan,saved['readback'],changed)
        self.assertIn('TEXT_OUTSIDE_LAYOUT_ENVELOPE',{e['code'] for e in errors})
        raw=copy.deepcopy(saved['readback'])
        next(r for r in raw['objects'] if r['key']=='overall-0')['distance']=50
        self.assertIn('OVERALL_NOT_OUTERMOST',{e['code'] for e in audit_layout_readback(plan,raw,render)})

    def test_required_label_that_cannot_fit_reports_the_object_without_dropping_it(self):
        req=copy.deepcopy(self.req);req['source']['partPosition']='W'*1000
        with self.assertRaises(LayoutOverflow) as caught:
            self.plan(req)
        self.assertEqual('label',caught.exception.report['blockedObject'])
        self.assertEqual('NO_GEOMETRIC_NOTE_CANDIDATE',caught.exception.report['reason'])
        self.assertEqual('W'*1000,req['source']['partPosition'])
