"""A saved-page study must not turn missing or unstable text into calibration."""
import copy
import unittest

from zijinhua_tekla.part_drawing.independent_layout.contracts import ContractError
from zijinhua_tekla.part_drawing.independent_layout.page_studies import audit_page_samples, study_native_pages


def sample_fixture():
    planned = [dict(key='dimension:0',font='romsim',height=25,text='125',at=[200,300])]
    measured = dict(**planned[0],objectId=42,box=[200,290,250,320],fixedPlacement=True)
    return planned,[dict(samples=[copy.deepcopy(measured)]) for _ in range(3)]


class PageTextStudyTests(unittest.TestCase):
    def test_stable_sdk_boxes_are_not_glyph_proof(self):
        result = audit_page_samples(*sample_fixture())
        self.assertEqual('NATIVE_TEXT_BOXES_STABLE',result['status'])
        self.assertEqual('NATIVE_BOUNDING_BOX_NOT_GLYPH_PROOF',result['scope'])

    def test_missing_reopen_rejected(self):
        planned,captures = sample_fixture()
        with self.assertRaisesRegex(ContractError,'PAGE_REOPEN_COUNT'):
            audit_page_samples(planned,captures[:2])

    def test_lost_or_duplicate_sample_rejected(self):
        for mode in ('lost','duplicate'):
            planned,captures = sample_fixture()
            captures[1]['samples'] = [] if mode=='lost' else captures[1]['samples']*2
            with self.subTest(mode=mode),self.assertRaises(ContractError):audit_page_samples(planned,captures)

    def test_wrong_text_font_height_placement_and_id_block(self):
        for key,value in (('text','126'),('font','Arial'),('height',2.5),('at',[201,300]),('objectId',43),('fixedPlacement',False)):
            planned,captures = sample_fixture(); captures[1]['samples'][0][key] = value
            with self.subTest(key=key):self.assertEqual('INPUT_BLOCKED',audit_page_samples(planned,captures)['status'])

    def test_changed_bbox_blocks(self):
        planned,captures = sample_fixture(); captures[2]['samples'][0]['box'][2] += 1
        self.assertEqual('INPUT_BLOCKED',audit_page_samples(planned,captures)['status'])

    def test_nonfinite_or_degenerate_bounds_reject(self):
        for bounds in ([0,0,0,0],[0,0,float('nan'),2],[0,0,float('inf'),2]):
            planned,captures = sample_fixture(); captures[0]['samples'][0]['box'] = bounds
            with self.subTest(bounds=bounds),self.assertRaises(ContractError):audit_page_samples(planned,captures)


def pages_fixture():
    job = dict(runId='fixture',partId=42,guid='guid',modelPath=r'C:\model',sourceEnvelopeDigest='source',templates=[])
    probe = dict(schemaVersion='NativePageProbe.v1',runId='fixture',partId=42,modelPath=r'C:\model',sourceEnvelopeDigest='source',
                 before=dict(active=None,part=dict(position='P1',profile='PL8*150',material='Q355B')),
                 after=dict(active=None,part=dict(position='P1',profile='PL8*150',material='Q355B')),error=None,stateUnchanged=True,
                 originalFiles={'original.wd':'original'},finalFiles={'original.wd':'original'},originalFilesUnchanged=True,
                 assetsMatch=True,results=[])
    captures = {}
    for index,n in enumerate(('10','20','50')):
        identity='零件图'+n
        planned,pages = sample_fixture()
        asset=dict(name='alias'+n+'.wd',sha256='asset'+n)
        template=dict(templateId=identity,bundleDigest='bundle'+n,alias='alias'+n,size=[1000,800],samples=planned,files=[asset],sheet=index+2)
        job['templates'].append(template); probe['finalFiles'][asset['name']] = asset['sha256']
        record=dict(runId='fixture',templateId=identity,id=42,guid='guid',sheet=index+2,mark='test'+n,objectId=100+index,
                    stage='SAVED_REOPENED_EXPORTED',bundleDigest='bundle'+n,alias='alias'+n,productionEligible=False)
        probe['results'].append(record)
        for p in pages:
            p['page'] = dict(drawing=dict(Name='NATIVE-PLATE-R1',SheetNumber=index+2,PartIdentifier=dict(ID=42),
                Mark='test'+n,IsLocked=False,IsFrozen=False,IsIssued=False,Layout=dict(SheetSize=dict(Width=1000,Height=800))),
                presentation=[dict(type='Tekla.TextPrimitive',Text='125',Font='romsim',Height=25),
                    dict(Position=dict(X=0,Y=0),Primitives=[dict(ObjectType=263,Primitives=[dict(type='Tekla.TextPrimitive',Text=t) for t in ('P1','PL8*150','Q355B','test'+n)])])],
                views=[dict(view=dict(Origin=dict(X=100,Y=100),FrameOrigin=dict(X=0,Y=0),Width=500,Height=200))],sheetObjects=[])
        captures[identity] = pages
    before=dict(owner='NATIVE-PLATE-R1',parts=[dict(id=42,sheet=1,stage='SAVED')])
    after=copy.deepcopy(before);after['independentLayoutTests']=copy.deepcopy(probe['results'])
    return job,probe,captures,before,after


class PageLifecycleTests(unittest.TestCase):
    def test_wrong_requested_sheet_and_duplicate_database_identity_reject(self):
        args=list(pages_fixture());args[0]['templates'][0]['sheet']=99
        with self.assertRaisesRegex(ContractError,'PAGE_OWNERSHIP'):study_native_pages(*args)
        args=list(pages_fixture());args[1]['results'][1]['objectId']=args[1]['results'][0]['objectId']
        with self.assertRaisesRegex(ContractError,'PAGE_RESULT_ID'):study_native_pages(*args)

    def test_missing_tables_cannot_be_satisfied_by_samples_or_view_text(self):
        args=list(pages_fixture())
        for p in args[2]['零件图10']:p['page']['presentation'][-1]['Primitives'][0]['ObjectType']=254
        result=study_native_pages(*args)
        self.assertEqual('INPUT_BLOCKED',result['status'])
        self.assertEqual('TEXT_AND_REOPEN_MATCHED',result['lifecycleStatus'])

    def test_wrong_model_table_text_and_overflow_or_extra_view_block(self):
        for mode in ('material','overflow','extra'):
            args=list(pages_fixture())
            for p in args[2]['零件图10']:
                if mode=='material':p['page']['presentation'][-1]['Primitives'][0]['Primitives'][2]['Text']='Q235B'
                if mode=='overflow':p['page']['views'][0]['view']['Width']=1200
                if mode=='extra':p['page']['views']*=2
            with self.subTest(mode=mode):self.assertEqual('INPUT_BLOCKED',study_native_pages(*args)['status'])

    def test_complete_lifecycle_is_study_only(self):
        result=study_native_pages(*pages_fixture())
        self.assertEqual('STUDY_ONLY',result['status']);self.assertFalse(result['productionEligible'])
        self.assertIn('MULTISCALE_SHORTENING_AND_CLEARANCE',result['pending'])

    def test_missing_template_fails(self):
        args=list(pages_fixture());args[1]['results'].pop()
        with self.assertRaisesRegex(ContractError,'PAGE_RESULT_SET'):study_native_pages(*args)

    def test_old_registry_or_original_attributes_change_blocks(self):
        for target in ('registry','original','asset','state','error'):
            args=list(pages_fixture())
            if target=='registry':args[4]['parts'][0]['sheet']=99
            if target=='original':args[1]['finalFiles']['original.wd']='changed'
            if target=='asset':args[1]['finalFiles']['unexpected']='new'
            if target=='state':args[1]['after']['active']='manual drawing'
            if target=='error':args[1]['error']='save failed'
            with self.subTest(target=target):self.assertEqual('INPUT_BLOCKED',study_native_pages(*args)['status'])

    def test_wrong_drawing_identity_or_unregistered_page_rejects(self):
        for target in ('identity','registration'):
            args=list(pages_fixture())
            if target=='identity':args[2]['零件图10'][1]['page']['drawing']['PartIdentifier']['ID']=43
            else:args[4]['independentLayoutTests'].pop()
            with self.subTest(target=target),self.assertRaises(ContractError):study_native_pages(*args)

    def test_default_size_and_missing_display_text_block(self):
        for target in ('size','display','repeat'):
            args=list(pages_fixture());page=args[2]['零件图20'][2]['page']
            if target=='size':page['drawing']['Layout']['SheetSize']['Width']=287
            if target=='display':page['presentation']=[]
            if target=='repeat':page['views'][0]['view']['Width']=501
            with self.subTest(target=target):self.assertEqual('INPUT_BLOCKED',study_native_pages(*args)['status'])
