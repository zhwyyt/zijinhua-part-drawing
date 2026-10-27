import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_template_snapshot import evidence
from zijinhua_tekla.part_drawing.native_drawing import run_native_drawing_batch
from zijinhua_tekla.part_drawing.layout_solver import LayoutOverflow


class NativeTemplatePipelineTests(unittest.TestCase):
    def run_batch(self, root, *, ready=True, wrong_template=False, active=False, layout_error=False, prepare_failure=False):
        raw=dict(model=dict(value=dict(path='fixture-model')),parts=[dict(id=1,guid=dict(value='g'),
                 modificationTime=dict(value=dict(value='2026-10-23T00:00:00Z')))])
        req=dict(status='READY' if ready else 'BLOCKED',source=dict(partId=1,partPosition='P1'),sourceDigest='source',
                 issues=[] if ready else [dict(code='PART_TYPE_ROUTE_UNSUPPORTED',message='BENT_PLATE route unavailable')],
                 partType=dict(type='FLAT_PLATE' if ready else 'BENT_PLATE'),blockedStage=None if ready else 'TYPE_ROUTING')
        batch=dict(parts=[req],geometryAudit=dict(parts=[dict(frame=dict(origin=[0,0,0],axisX=[1,0,0],axisY=[0,1,0]))]))
        exe=root/'fake.exe';exe.touch()
        calls=[]
        def save(path, value):
            Path(path).write_text(json.dumps(value),encoding='utf-8')
        def execute(args, log):
            args=list(map(str,args));calls.append(args)
            if '--out' in args:
                save(args[args.index('--out')+1],raw)
            elif '--prepare' in args:
                if active:return 2
                out=Path(args[-1]);out.mkdir()
                if prepare_failure:
                    save(out/'1.created.json',dict(id=1,guid='g',sheet=1,mark='P1-1',objectId=100,drawingId=0))
                    save(out/'batch.json',dict(results=[dict(id=1,status='FAILED')]))
                    return 1
                save(out/'batch.json',dict(results=[dict(id=1,status='TEMPLATE_PREPARED',sheet=1,mark='P1-1',created=True,objectId=100,drawingId=100)]))
                template=evidence()
                if wrong_template:template['partId']=2
                save(out/'1.template.json',template)
            elif '--write' in args:
                job=json.loads(Path(args[-2]).read_text(encoding='utf-8'))
                self.assertIn('viewPlan',job['parts'][0])
                self.assertEqual('fixture-attributes',job['attributeFile'])
                self.assertTrue((root/'registry.json').is_file())
                out=Path(args[-1]);out.mkdir()
                self.assertEqual(100,job['parts'][0]['ownedDrawingId'])
                save(out/'batch.json',dict(results=[dict(id=1,status='SAVED_REOPENED_EXPORTED',sheet=1,mark='P1-1',objectId=100,drawingId=100)]))
                save(out/'1.json',{})
            return 0
        def layout(*args, **kwargs):
            self.assertIn('template',kwargs)
            self.assertEqual(1,kwargs['template']['source']['partId'])
            if layout_error:
                raise LayoutOverflow(dict(status='LAYOUT_OVERFLOW',reason='SEARCH_BUDGET_EXHAUSTED',
                                          blockedObject='holes-0',visited=4096,budget=4096))
            return dict(id=1,guid='g',status='READY',planDigest='initial',viewPlan=dict(snapshotDigest=kwargs['template']['snapshotDigest']))
        audit=dict(partId=1,status='PASS',observations={})
        with patch('zijinhua_tekla.part_drawing.native_drawing._run',side_effect=execute), \
             patch('zijinhua_tekla.part_drawing.native_drawing.build_native_expression_requirements',return_value=batch), \
             patch('zijinhua_tekla.part_drawing.native_drawing.build_layout',side_effect=layout), \
             patch('zijinhua_tekla.part_drawing.native_drawing.audit_native_drawing',return_value=copy.deepcopy(audit)), \
             patch('zijinhua_tekla.part_drawing.native_drawing.render_png'):
            result=run_native_drawing_batch(root/'batch',ids=[1],reader=exe,writer=exe,ownership=root/'registry.json',drawing_attributes='fixture-attributes')
        return result,calls

    def test_prepare_and_snapshot_precede_planning_and_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); result,calls=self.run_batch(root)
            self.assertEqual(1,result['passed'])
            self.assertEqual(['--out','--prepare','--write','--ids'],[c[1] for c in calls])
            self.assertTrue((root/'batch/template-1.json').is_file())

    def test_bad_template_never_reaches_write_and_preserves_prepared_ownership(self):
        for option in ('wrong_template','layout_error'):
            with self.subTest(option=option),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);result,calls=self.run_batch(root,**{option:True})
                self.assertEqual(1,result['failed'])
                self.assertFalse(any('--write' in c for c in calls))
                registry=json.loads((root/'registry.json').read_text(encoding='utf-8'))
                self.assertEqual('PREPARED',registry['parts'][0]['stage'])
                if option=='layout_error':
                    report=json.loads((root/'batch/layout-1.json').read_text(encoding='utf-8'))
                    self.assertEqual('holes-0',report['blockedObject'])
                    self.assertEqual(report,result['parts'][0]['layout'])

    def test_blocked_source_cannot_create_trial_drawing(self):
        with tempfile.TemporaryDirectory() as tmp:
            result,calls=self.run_batch(Path(tmp),ready=False)
            self.assertEqual(1,result['failed'])
            self.assertEqual(1,len(calls))
            self.assertEqual('BENT_PLATE',result['parts'][0]['partType']['type'])
            self.assertEqual('TYPE_ROUTING',result['parts'][0]['blockedStage'])
            self.assertIn('BENT_PLATE',result['parts'][0]['reason'])

    def test_active_drawing_refusal_does_not_continue_or_claim_a_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with self.assertRaisesRegex(RuntimeError,'never saves/closes'):
                self.run_batch(root,active=True)
            self.assertFalse((root/'registry.json').exists())
            self.assertFalse((root/'batch/result.json').exists())

    def test_insert_receipt_preserves_identity_even_if_preparation_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);result,calls=self.run_batch(root,prepare_failure=True)
            self.assertEqual(1,result['failed'])
            self.assertFalse(any('--write' in c for c in calls))
            owned=json.loads((root/'registry.json').read_text(encoding='utf-8'))['parts'][0]
            self.assertEqual(('INCOMPLETE',100,0),(owned['stage'],owned['objectId'],owned['drawingId']))

    def test_old_registry_without_database_identity_blocks_before_preparation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'registry.json').write_text(json.dumps(dict(owner='NATIVE-PLATE-R1',modelPath='fixture-model',
                parts=[dict(id=1,guid='g',sheet=1,mark='P1-1')])),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'no catalog scan'):
                self.run_batch(root)
            self.assertFalse((root/'batch/prepare-job.json').exists())
