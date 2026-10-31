"""Detached loads must not certify production or hide SDK default fallbacks."""

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from test_independent_layout import template_evidence
from test_independent_expressions import source
from zijinhua_tekla.part_drawing.independent_layout.__main__ import main
from zijinhua_tekla.part_drawing.independent_layout.contracts import ContractError, seal
from zijinhua_tekla.part_drawing.independent_layout.expressions import adapt_expressions
from zijinhua_tekla.part_drawing.independent_layout.preparation import prepare_layout_input
from zijinhua_tekla.part_drawing.independent_layout.template_loads import study_template_load
from zijinhua_tekla.part_drawing.independent_layout.templates import study_template


def fixture(directory):
    capture, objects, presentation = template_evidence()
    capture['modelPath'] = r'C:\model'
    capture['drawing'].update(Title1='title',Title2=' ',Title3=' ')
    attributes = b'dl.width 5950\ndl.height 4210\ndl.layout "single20"\n'
    study = study_template(capture,objects,presentation,attributes)
    primary = r'C:\model\attributes\sample.wd'
    files = []
    for i,suffix in enumerate(('', '.more', '.copt', '.filter', '.npfilter', '.lay')):
        data = attributes if i==0 else b'fixture dependency'
        file = 'snapshot'+str(i)
        (directory/file).write_bytes(data)
        checksum = hashlib.sha256(data).hexdigest()
        files.append(dict(source=primary+suffix if i<5 else r'C:\model\attributes\single20.lay',
                          snapshot=file,bytes=len(data),sha256=checksum,afterSha256=checksum))
    layout = dict(size=[5950,4210],mode='SpecifiedSize',autoSize='FixedSizes',listHidden=False)
    loaded = dict(requested='sample',drawing=dict(Name='零件图20',Title1='title',Title2=' ',Title3=' ',layout=layout),
                  drawingError=None,loadReturned=False,layout=dict(size=[287,200]),layoutError=None)
    absolute = copy.deepcopy(loaded)
    absolute['requested'] = primary[:-3]
    negative = dict(requested=r'C:\model\attributes\__independent_missing_fixture',drawing=None,
                    drawingError='not found',layout=None,loadReturned=None,layoutError=None)
    negative_name = dict(negative,requested='__independent_missing_fixture')
    entry = dict(templateId='零件图20',attributePath=primary,files=files,byName=loaded,
                 repeatedName=copy.deepcopy(loaded),absolute=absolute,repeated=copy.deepcopy(absolute),
                 negative=negative,negativeName=negative_name)
    state = dict(modelPath=r'C:\model',part=dict(id=42),active=None,plane=[[0,0,0]])
    probe = dict(schemaVersion='NativeTemplateLoadProbe.v1',scope='DETACHED_ATTRIBUTES_ONLY',
                 modelPath=r'C:\model',partId=42,mutatingApiCalls=False,stateUnchanged=True,
                 before=state,after=copy.deepcopy(state),entries=[entry],
                 assemblies=[dict(name='synthetic-sdk',sha256='fixture')],executableSha256='fixture')
    return study,probe


def reseal(value):
    return seal({k:v for k,v in value.items() if k!='digest'})


class IndependentTemplateLoadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.study,self.probe = fixture(self.directory)
        self.entry = self.probe['entries'][0]

    def audit(self):
        return study_template_load(self.probe,self.study,self.directory)

    def test_name_load_is_distinct_from_standalone_layout_and_production(self):
        result = self.audit()
        self.assertEqual('STUDY_ONLY',result['status'])
        self.assertTrue(result['checks']['nameDrawingMatches'])
        self.assertFalse(result['checks']['standaloneLayoutMatches'])
        self.assertFalse(result['productionEligible'])
        self.assertIn('RESOLVED_ATTRIBUTE_PATH',result['pending'])
        self.assertIn('TRANSITIVE_DEPENDENCY_CLOSURE',result['pending'])
        self.assertEqual(result,self.audit())

    def test_absolute_path_failure_does_not_invent_success(self):
        for key in ('absolute','repeated'):
            self.entry[key]['drawing'] = dict(Name='',layout=dict(size=[0,0]))
        result = self.audit()
        self.assertFalse(result['checks']['absoluteDrawingMatches'])
        self.assertTrue(result['checks']['nameDrawingMatches'])

    def test_missing_name_silent_default_fallback_blocks(self):
        self.entry['negativeName'].update(drawing=dict(Name='standard',layout=dict(size=[287,200])),drawingError=None)
        self.assertEqual('INPUT_BLOCKED',self.audit()['status'])

    def test_name_failure_and_changed_repeat_block(self):
        for key in ('byName','repeatedName'):
            with self.subTest(key=key):
                study,probe = fixture(self.directory)
                probe['entries'][0][key]['drawing']['layout']['size'] = [287,200]
                self.assertEqual('INPUT_BLOCKED',study_template_load(probe,study,self.directory)['status'])

    def test_wrong_name_even_with_same_size_blocks(self):
        self.entry['byName']['drawing']['Name'] = 'another template'
        self.assertEqual('INPUT_BLOCKED',self.audit()['status'])

    def test_file_tampering_or_during_load_change_rejects(self):
        for mode in ('bytes','after'):
            with self.subTest(mode=mode):
                self.study,self.probe = fixture(self.directory)
                if mode=='bytes':
                    (self.directory/'snapshot0').write_bytes(b'replaced')
                else:
                    self.probe['entries'][0]['files'][0]['afterSha256'] = 'changed'
                if mode=='bytes':
                    with self.assertRaisesRegex(ContractError,'LOAD_FILE_CHANGED'):
                        self.audit()
                else:
                    result = self.audit()
                    self.assertEqual('INPUT_BLOCKED',result['status'])
                    self.assertEqual('LOAD_FILE_CHANGED_DURING_PROBE',result['failure']['code'])

    def test_rehashed_different_file_with_same_dimensions_rejects(self):
        file = self.entry['files'][0]
        data = (self.directory/file['snapshot']).read_bytes()+b'other.setting 1\n'
        (self.directory/file['snapshot']).write_bytes(data)
        file.update(bytes=len(data),sha256=hashlib.sha256(data).hexdigest(),afterSha256=hashlib.sha256(data).hexdigest())
        with self.assertRaisesRegex(ContractError,'LOAD_ATTRIBUTE_CHANGED'):
            self.audit()

    def test_new_generated_dependency_blocks_even_if_all_six_files_unchanged(self):
        self.entry['directoryBefore'] = ['sample.wd','single20.lay']
        self.entry['directoryAfter'] = ['new_sample.vi','sample.wd','single20.lay']
        result = self.audit()
        self.assertEqual('INPUT_BLOCKED',result['status'])
        self.assertEqual('LOAD_DIRECTORY_CHANGED_DURING_PROBE',result['failure']['code'])

    def test_missing_file_and_incomplete_inventory_reject(self):
        self.entry['files'].pop()
        with self.assertRaisesRegex(ContractError,'LOAD_FILE_SET'):
            self.audit()
        (self.directory/'snapshot0').unlink()
        with self.assertRaises(FileNotFoundError):
            self.audit()

    def test_duplicate_and_escaping_snapshot_reject(self):
        self.entry['files'].append(copy.deepcopy(self.entry['files'][0]))
        with self.assertRaisesRegex(ContractError,'LOAD_FILE_DUPLICATE'):
            self.audit()
        self.entry['files'].pop()
        self.entry['files'][0]['snapshot'] = '../outside.wd'
        with self.assertRaisesRegex(ContractError,'LOAD_SNAPSHOT_PATH'):
            self.audit()

    def test_wrong_model_state_mutation_or_target_reject(self):
        for field in ('model','state','mutation','part'):
            with self.subTest(field=field):
                study,probe = fixture(self.directory)
                if field=='model':probe['modelPath'] = r'C:\other'
                elif field=='state':probe['after']['active'] = 'changed'
                elif field=='mutation':probe['mutatingApiCalls'] = True
                else:probe['partId'] = 99
                with self.assertRaises(ContractError):
                    study_template_load(probe,study,self.directory)

    def test_mixed_request_negative_control_and_unknown_template_reject(self):
        for mode in ('request','negative','unknown','duplicate'):
            with self.subTest(mode=mode):
                study,probe = fixture(self.directory)
                entry = probe['entries'][0]
                if mode=='request':entry['byName']['requested'] = 'other'
                elif mode=='negative':entry['negativeName']['requested'] = 'sample'
                elif mode=='unknown':entry['templateId'] = '零件图100'
                else:probe['entries'].append(copy.deepcopy(entry))
                with self.assertRaises(ContractError):
                    study_template_load(probe,study,self.directory)

    def test_load_evidence_does_not_close_preparation_gates(self):
        s = source()
        e = adapt_expressions(s)
        before = prepare_layout_input(s,e,[self.study])
        after = prepare_layout_input(s,e,[self.study],[self.audit()])
        self.assertEqual(before['pendingGates'],after['pendingGates'])
        self.assertEqual('INPUT_BLOCKED',after['status'])
        self.assertFalse(after['nativeWriteAllowed'])
        self.assertFalse(after['candidateGenerationAllowed'])

    def test_forged_production_wrong_binding_and_duplicate_load_reject(self):
        s = source()
        e = adapt_expressions(s)
        for mode in ('production','binding','duplicate','unknown'):
            with self.subTest(mode=mode):
                load = self.audit()
                if mode=='production':load['productionEligible'] = True
                elif mode=='binding':load['studyDigest'] = 'another sample'
                elif mode=='unknown':load['templateId'] = '零件图50'
                load = reseal(load)
                with self.assertRaises(ContractError):
                    prepare_layout_input(s,e,[self.study],[load,load] if mode=='duplicate' else [load])

    def test_cli_replays_local_snapshots_and_refuses_existing_output(self):
        (self.directory/'probe.json').write_text(json.dumps(self.probe),encoding='utf-8')
        study = self.directory/'study.json'
        study.write_text(json.dumps(self.study),encoding='utf-8')
        args = ['template-load-study','--probe',str(self.directory),'--template-study',str(study),
                '--out',str(self.directory/'result')]
        self.assertEqual(0,main(args))
        self.assertEqual(2,main(args))


if __name__=='__main__':
    unittest.main()
