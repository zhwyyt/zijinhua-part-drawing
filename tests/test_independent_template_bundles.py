"""Migration must preserve values; copied templates cannot certify drawing output."""
import base64
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from test_independent_layout import template_evidence
from test_independent_expressions import source
from zijinhua_tekla.part_drawing.independent_layout.expressions import adapt_expressions
from zijinhua_tekla.part_drawing.independent_layout.preparation import prepare_layout_input
from zijinhua_tekla.part_drawing.independent_layout.__main__ import main
from zijinhua_tekla.part_drawing.independent_layout.contracts import ContractError, seal
from zijinhua_tekla.part_drawing.independent_layout.templates import study_template, read_attributes
from zijinhua_tekla.part_drawing.independent_layout.template_bundles import (
    build_template_bundle, verify_template_bundle, stage_bundle_files, study_bundle_load, bind_existing_layout, bind_single_main_view,
)


def fixture(n='20'):
    capture,objects,presentation = template_evidence()
    capture['modelPath'] = r'C:\model'
    capture['drawing']['Name'] = '零件图'+n
    original = (f'in_file "sample{n}"\nout_file "sample{n}"\ndl.width 5950\ndl.height 4210\n'
                'dl.layout "layout"\ndl.sheet 43\ntext "尺寸"\n').encode('gb18030')
    original = b''.join(b'wdraw_dial.'+line for line in original.splitlines(keepends=True))
    converted = original.replace(b'wdraw_dial.',b'')+b'dv.aDrawingViews "1,1,,view,16,0,,system_view,"\n'
    study = study_template(capture,objects,presentation,original)
    deps = {'layout.lay':b'opaque layout','view.vi':b'ViewDimensioning.aDimensioningPlugins ",albl_General,dimension"\n',
            'dimension.wdcd':b'aStraightDimensionsAttributeName "style"\naAngleDimensionsAttributeName "style"\naCheckDimensionsAttributeName "style"\n',
            'style.dim':b'font "Arial"\nheight 50\n'}
    deps.update({f'sample{n}.wd'+s:b'opaque companion' for s in ('.more','.copt','.filter','.npfilter')})
    deps.update({'view.vi'+s:b'opaque companion' for s in ('.copt','.filter','.npfilter')})
    return study,f'sample{n}.wd',original,converted,deps


def reseal(value):
    return seal({k:v for k,v in value.items() if k!='digest'})


def probe_fixture(root):
    studies,bundles,entries,originals = [],[],[],{}
    (root/'originals').mkdir()
    for n in ('10','20','50'):
        args = fixture(n)
        study,bundle = args[0],build_template_bundle(*args)
        studies.append(study); bundles.append(bundle)
        originals[args[1]] = hashlib.sha256(args[2]).hexdigest()
        (root/'originals'/args[1]).write_bytes(args[2])
        missing = dict(requested=bundle['alias'],drawing=dict(Name='',layout=dict(size=[0,0])),drawingError=None)
        loaded = dict(requested=bundle['alias'],drawing=dict(Name=study['templateId'],Title1=None,Title2=None,Title3=None,
                      layout=dict(size=study['sheetSizeNative'])),drawingError=None)
        entries.append(dict(templateId=study['templateId'],alias=bundle['alias'],bundleDigest=bundle['digest'],
                            absentBefore=copy.deepcopy(missing),absentAfter=copy.deepcopy(missing),
                            first=copy.deepcopy(loaded),second=copy.deepcopy(loaded)))
    installed = dict(originals)
    for b in bundles:
        installed.update({f['name']:f['sha256'] for f in b['files']})
    state = dict(modelPath=r'C:\model',part=dict(id=42),active=None,plane=[[0,0,0]])
    phases = [('locked',originals)]+[('absentBefore:'+e['alias'],originals) for e in entries]+[('installed',installed)]
    phases += [(k+':'+e['alias'],installed) for e in entries for k in ('first','second')]
    phases += [('cleaned',originals)]+[('absentAfter:'+e['alias'],originals) for e in entries]
    probe = dict(schemaVersion='NativeTemplateBundleProbe.v1',scope='DETACHED_ISOLATED_ATTRIBUTES_ONLY',
                 modelPath=r'C:\model',partId=42,before=state,after=copy.deepcopy(state),mutatingDrawingApiCalls=False,
                 originalFiles=originals,finalFiles=copy.deepcopy(originals),entries=entries,
                 events=[dict(phase=p,files=copy.deepcopy(f),filesMatch=True,stateMatch=True,state=copy.deepcopy(state)) for p,f in phases],
                 cleanupErrors=[],error=None,filesRestored=True,stateUnchanged=True,assemblies=['synthetic-sdk'],executableSha256='fixture')
    return probe,bundles,studies


class TemplateBundleTests(unittest.TestCase):
    def test_single_view_changes_only_declaration_and_alias(self):
        args=fixture();bundle=build_template_bundle(*args)
        parent=bind_existing_layout(bundle,args[0]);result=bind_single_main_view(bundle,args[0])
        self.assertEqual(result,bind_single_main_view(bundle,args[0]))
        self.assertEqual(parent['existingDependencies'],result['existingDependencies'])
        self.assertEqual(43,result['tableLayoutId']);self.assertFalse(result['productionEligible'])
        wd=lambda b:read_attributes(base64.b64decode(next(f for f in b['files'] if f['name'].endswith('.wd'))['contentBase64']))['fields']
        expected=wd(parent);expected.update(in_file='"'+result['alias']+'"',out_file='"'+result['alias']+'"')
        expected['dv.aDrawingViews']='"'+result['viewDeclarationAfter']+'"'
        self.assertEqual(expected,wd(result))
        parts=result['viewDeclarationAfter'].strip(',').split(',')
        self.assertEqual(['1'],[parts[i] for i in range(0,len(parts),4) if parts[i+1]!='0'])

    def test_existing_layout_binding_preserves_all_other_fields_and_dependencies(self):
        args=fixture();bundle=build_template_bundle(*args);original=copy.deepcopy(bundle)
        binding=bind_existing_layout(bundle,args[0])
        self.assertEqual(bundle,original)
        self.assertEqual(binding,bind_existing_layout(bundle,args[0]))
        self.assertEqual(43,binding['tableLayoutId']);self.assertFalse(binding['productionEligible'])
        self.assertEqual(5,len(binding['files']))
        old=next(f for f in bundle['files'] if f['name'].endswith('.wd'))
        new=next(f for f in binding['files'] if f['name'].endswith('.wd'))
        expected=read_attributes(base64.b64decode(old['contentBase64']))['fields']
        expected.update(in_file='"'+binding['alias']+'"',out_file='"'+binding['alias']+'"')
        expected['dl.layout']='"layout"'
        self.assertEqual(expected,read_attributes(base64.b64decode(new['contentBase64']))['fields'])
        deps={f['name']:f['sha256'] for f in binding['existingDependencies']}
        self.assertEqual(hashlib.sha256(args[4]['layout.lay']).hexdigest(),deps.pop('layout.lay'))
        self.assertEqual({f['name']:f['sha256'] for f in bundle['files'] if '.wd.' not in f['name'] and not f['name'].endswith(('.wd','.lay'))},deps)
        for f in binding['files']:
            self.assertEqual(f['sha256'],hashlib.sha256(base64.b64decode(f['contentBase64'])).hexdigest())

    def test_layout_binding_rejects_tampered_parent(self):
        args=fixture();bundle=build_template_bundle(*args);bundle['files'][0]['sha256']='changed'
        with self.assertRaises(ContractError):bind_existing_layout(reseal(bundle),args[0])

    def test_preserves_fields_and_only_rewrites_bound_references(self):
        args = fixture(); bundle = build_template_bundle(*args)
        verify_template_bundle(bundle,args[0])
        self.assertEqual(bundle,build_template_bundle(*args))
        self.assertEqual(['system_view.vi'],bundle['unresolvedDisabledViews'])
        wd = next(f for f in bundle['files'] if f['name'].endswith('.wd'))
        fields = read_attributes(base64.b64decode(wd['contentBase64']))['fields']
        self.assertEqual('"尺寸"',fields['text'])
        self.assertEqual('5950',fields['dl.width'])
        self.assertTrue(all(f['name'].startswith(bundle['alias']) for f in bundle['files']))
        self.assertFalse(bundle['productionEligible'])

    def test_changed_removed_or_extra_converted_field_rejected(self):
        for change in (lambda b:b.replace(b'5950',b'5951'),lambda b:b.replace(b'text "'+ '尺寸'.encode('gb18030')+b'"\n',b''),
                       lambda b:b+b'extra 1\n'):
            args = list(fixture()); args[3] = change(args[3])
            with self.subTest(change=change),self.assertRaisesRegex(ContractError,'BUNDLE_MIGRATION'):
                build_template_bundle(*args)

    def test_missing_enabled_dependency_and_companion_rejected(self):
        for name in ('view.vi','view.vi.filter','style.dim','layout.lay'):
            args = list(fixture()); del args[4][name]
            with self.subTest(name=name),self.assertRaisesRegex(ContractError,'BUNDLE_DEPENDENCY_MISSING'):
                build_template_bundle(*args)

    def test_unknown_plugin_rejected(self):
        args = list(fixture()); args[4]['view.vi'] = b'ViewDimensioning.aDimensioningPlugins ",unknown,dimension"\n'
        with self.assertRaisesRegex(ContractError,'BUNDLE_PLUGIN'):build_template_bundle(*args)

    def test_disabled_external_view_cannot_be_enabled_without_evidence(self):
        args = list(fixture()); args[3] = args[3].replace(b'16,0',b'16,1')
        with self.assertRaisesRegex(ContractError,'BUNDLE_DEPENDENCY_MISSING'):build_template_bundle(*args)

    def test_current_attributes_must_remain_disabled(self):
        args = list(fixture()); args[3] = args[3].replace(b'16,0,,system_view',b'16,1,,current attributes')
        with self.assertRaisesRegex(ContractError,'BUNDLE_CURRENT_VIEW'):build_template_bundle(*args)

    def test_escaping_and_unused_dependencies_rejected(self):
        for name in ('../bad.dim','C:\\bad.dim','extra.dim'):
            args = list(fixture()); args[4][name] = b'x 1\n'
            with self.subTest(name=name),self.assertRaises(ContractError):build_template_bundle(*args)

    def test_resealed_tampering_and_wrong_study_rejected(self):
        args = fixture(); bundle = build_template_bundle(*args)
        changed = copy.deepcopy(bundle); changed['files'][0]['name'] = '../escaped'
        with self.assertRaisesRegex(ContractError,'BUNDLE_REPLAY'):verify_template_bundle(reseal(changed),args[0])
        with self.assertRaises(ContractError):verify_template_bundle(bundle,fixture('10')[0])

    def test_local_staging_never_overwrites_existing_directory(self):
        args = fixture(); bundle = build_template_bundle(*args)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)/'files'
            entry = stage_bundle_files(bundle,args[0],target)
            self.assertEqual(len(bundle['files']),len(entry['files']))
            with self.assertRaises(FileExistsError):stage_bundle_files(bundle,args[0],target)


class BundleLoadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.probe,self.bundles,self.studies = probe_fixture(self.root)

    def audit(self):return study_bundle_load(self.probe,self.bundles,self.studies,self.root)

    def test_all_three_match_but_production_stays_blocked(self):
        result = self.audit()
        self.assertEqual('STUDY_ONLY',result['status']); self.assertFalse(result['productionEligible'])
        self.assertEqual(['ISOLATED_LOAD_MATCHED']*3,[e['status'] for e in result['templates']])
        self.assertIn('SAVE_REOPEN',result['pending'])

    def test_missing_template_cannot_degrade_to_ten_only(self):
        self.probe['entries'].pop()
        with self.assertRaisesRegex(ContractError,'BUNDLE_ALL_TEMPLATES'):self.audit()

    def test_false_success_flags_cannot_hide_file_or_state_changes(self):
        for target in ('final','event','state','cleanup'):
            original = copy.deepcopy(self.probe)
            if target=='final':self.probe['finalFiles']['extra'] = 'new'
            if target=='event':self.probe['events'][6]['files']['extra'] = 'new'
            if target=='state':self.probe['after']['active'] = 'changed'
            if target=='cleanup':self.probe['cleanupErrors'] = ['alias retained']
            with self.subTest(target=target):self.assertEqual('INPUT_BLOCKED',self.audit()['status'])
            self.probe = original

    def test_default_fallback_repeat_error_and_cache_after_removal_block(self):
        for key in ('first','second','absentBefore','absentAfter'):
            original = copy.deepcopy(self.probe)
            self.probe['entries'][1][key]['drawing'] = dict(Name='standard',layout=dict(size=[287,200]))
            with self.subTest(key=key):self.assertEqual('INPUT_BLOCKED',self.audit()['status'])
            self.probe = original

    def test_missing_call_evidence_blocks(self):
        self.probe['events'].pop()
        self.assertEqual('INPUT_BLOCKED',self.audit()['status'])

    def test_snapshot_and_bundle_binding_tampering_rejects(self):
        name = next(iter(self.probe['originalFiles']))
        (self.root/'originals'/name).write_bytes(b'tampered')
        with self.assertRaisesRegex(ContractError,'BUNDLE_SNAPSHOT'):self.audit()

    def test_wrong_probe_bundle_rejected(self):
        self.probe['entries'][0]['bundleDigest'] = 'wrong'
        with self.assertRaisesRegex(ContractError,'BUNDLE_LOAD_BINDING'):self.audit()

    def test_negative_control_transport_error_is_not_resolution_proof(self):
        self.probe['entries'][0]['absentAfter'].update(drawing=None,drawingError='disconnected')
        self.assertEqual('INPUT_BLOCKED',self.audit()['status'])

    def test_preparation_keeps_all_gates_and_rejects_wrong_sample(self):
        s = source(); expressions = adapt_expressions(s)
        baseline = prepare_layout_input(s,expressions,self.studies)
        report = self.audit()
        prepared = prepare_layout_input(s,expressions,self.studies,bundle_load_study=report)
        self.assertEqual(baseline['pendingGates'],prepared['pendingGates'])
        self.assertEqual('INPUT_BLOCKED',prepared['status'])
        self.assertFalse(prepared['nativeWriteAllowed'])
        report['templates'][0]['studyDigest'] = 'wrong'
        with self.assertRaisesRegex(ContractError,'BUNDLE_LOAD_REPORT_BINDING'):
            prepare_layout_input(s,expressions,self.studies,bundle_load_study=reseal(report))

    def test_cli_load_study_replays_saved_evidence(self):
        (self.root/'probe.json').write_text(json.dumps(self.probe),'utf-8')
        args = ['template-bundle-study','--probe',str(self.root),'--out',str(self.root/'result')]
        for i,(bundle,study) in enumerate(zip(self.bundles,self.studies)):
            for flag,data in (('--bundle',bundle),('--template-study',study)):
                path = self.root/f'{flag[2:]}{i}.json'
                path.write_text(json.dumps(data),'utf-8'); args.extend([flag,str(path)])
        self.assertEqual(0,main(args))
        self.assertEqual('STUDY_ONLY',json.loads((self.root/'result'/'report.json').read_text('utf-8'))['status'])
