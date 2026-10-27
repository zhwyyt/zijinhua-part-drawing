import copy
import base64
import json
import tempfile
import unittest
from pathlib import Path

from zijinhua_tekla.part_drawing.native_readback import readback_observations, audit_native_drawing, visual_audit
from zijinhua_tekla.part_drawing.native_render import load_render


class SavedM4ReadbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).parent/'fixtures/part_drawing/m4-saved-live'
        cls.evidence = json.loads((root/'evidence.json').read_text(encoding='utf-8'))
        directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(directory.cleanup)
        cls.dxf = Path(directory.name)/'drawing.dxf'
        cls.dxf.write_bytes(base64.b64decode(cls.evidence['dxfBase64']))
        cls.render = load_render(cls.dxf, cls.evidence['readback'])

    def test_actual_saved_drawing_template_dimensions_and_export_pass(self):
        e = self.evidence
        result = audit_native_drawing(e['requirements'], e['plan'], e['readback'], self.dxf)
        self.assertEqual('PASS', result['status'], result)
        self.assertEqual(18, result['visual']['checkedTextBoxes'])

    def test_removing_actual_label_cannot_be_replaced_by_sdk_value(self):
        e = self.evidence
        render = dict(self.render, texts=[t for t in self.render['texts'] if t['text'] != '567'])
        with self.assertRaisesRegex(ValueError, 'Dimension label absent'):
            readback_observations(e['requirements'], e['plan'], e['readback'], render)

    def test_short_dimension_setting_must_survive_save(self):
        e = self.evidence; raw = copy.deepcopy(e['readback'])
        next(o for o in raw['objects'] if o['kind'] == 'dimension')['shortDimension'] = 'Inside'
        with self.assertRaisesRegex(ValueError, 'Short dimension placement'):
            readback_observations(e['requirements'], e['plan'], raw, self.render)

    def test_actual_view_or_template_drift_still_fails(self):
        e = self.evidence
        raw = dict(e['readback'], templateSignature='changed', origin=[100, 100, 0])
        codes = {r['code'] for r in visual_audit(e['plan'], raw, self.render)['errors']}
        self.assertIn('TEMPLATE_CHANGED_AFTER_SAVE', codes)
        self.assertIn('VIEW_OUTSIDE_TEMPLATE_FREE_AREA', codes)
