"""Reject wrong source, view controls and text masks despite plausible text."""
import copy
import math
import unittest

from zijinhua_tekla.part_drawing.independent_layout.contracts import ContractError
from zijinhua_tekla.part_drawing.independent_layout.foundation_studies import audit_view_control, audit_sample_masks


def control_fixture():
    template=dict(size=[1000,800],viewControl=dict(region=[100,100,900,700],sourceDigest='source'))
    receipt=dict(region=[100,100,900,700],sourceDigest='source',scale=2,origin=[200,200],box=[200,200,700,600],
                 attempts=[dict(scale=1,Width=1000,Height=800,fits=False),dict(scale=2,Width=500,Height=400,fits=True)])
    view=dict(ViewType='FrontView',Origin=dict(X=200,Y=200),FrameOrigin=dict(X=0,Y=0),Width=500,Height=400,
              Attributes=dict(Scale=2,FixedViewPlacing=True,Shortening=dict(CutParts=False)))
    page=dict(views=[dict(view=view,objects=[dict(data=dict(type='Tekla.Structures.Drawing.Part',ModelIdentifier=dict(ID=42)))])],
              presentation=[dict(Position=dict(X=0,Y=0),Primitives=[dict(ObjectType=263,Primitives=[dict(type='Tekla.TextPrimitive',Text='1:2')])])])
    return template,receipt,[dict(page=copy.deepcopy(page)) for _ in range(3)],42,'source'


def mask_fixture():
    points=[(0,0),(10,0),(10,5),(0,5)]
    edges=[dict(type='Tekla.LinePrimitive',StartPoint=dict(X=x,Y=y),EndPoint=dict(X=points[(i+1)%4][0],Y=points[(i+1)%4][1]))
           for i,(x,y) in enumerate(points)]
    segment=dict(type='Tekla.Segment',ObjectType=254,Id=7,Primitives=[
        dict(type='Tekla.TextPrimitive',Text='125',Font='romsim',Height=5,Angle=0),
        dict(type='Tekla.PolygonPrimitive',InnerLoops=[],OuterLoop=dict(Segments=edges))])
    return dict(samples=[dict(key='dimension',objectId=7,text='125',font='romsim',height=5,box=[-1,-1,11,6])],
                page=dict(presentation=[dict(Position=dict(X=0,Y=0),Primitives=[segment])]))


class RotatedMaskTests(unittest.TestCase):
    def test_rotated_mask_uses_sdk_degrees_and_presentation_radians(self):
        capture=mask_fixture();sample=capture['samples'][0];sample['angle']=30
        segment=capture['page']['presentation'][0]['Primitives'][0];segment['Primitives'][0]['Angle']=.5236
        points=[];c=math.cos(math.pi/6);s=math.sin(math.pi/6)
        for edge in segment['Primitives'][1]['OuterLoop']['Segments']:
            for key in ('StartPoint','EndPoint'):
                p=edge[key];x,y=p['X'],p['Y'];p['X'],p['Y']=c*x-s*y,s*x+c*y;points.append([p['X'],p['Y']])
        sample['box']=[min(p[0] for p in points)-1,min(p[1] for p in points)-1,
                       max(p[0] for p in points)+1,max(p[1] for p in points)+1]
        self.assertEqual('ID_BOUND_MASKS_MATCHED',audit_sample_masks(capture)['status'])
        sample['angle']=90
        self.assertEqual('INPUT_BLOCKED',audit_sample_masks(capture)['status'])


class ViewControlTests(unittest.TestCase):
    def test_saved_control_matches_only_finite_calibration(self):
        result=audit_view_control(*control_fixture())
        self.assertEqual('VIEW_CONTROL_MATCHED',result['status'])
        self.assertFalse(result['productionEligible'])

    def test_title_may_refresh_on_save_but_must_match_both_reopens(self):
        args=control_fixture()
        args[2][0]['page']['presentation'][0]['Primitives'][0]['Primitives'][0]['Text']='1:1'
        self.assertEqual('VIEW_CONTROL_MATCHED',audit_view_control(*args)['status'])
        args[2][1]['page']['presentation'][0]['Primitives'][0]['Primitives'][0]['Text']='1:1'
        self.assertEqual('INPUT_BLOCKED',audit_view_control(*args)['status'])

    def test_wrong_target_shortening_scale_placement_and_extent_block(self):
        for mode in ('target','shortened','scale','fixed','origin','extent','extra','type'):
            args=control_fixture();item=args[2][1]['page']['views'][0];v=item['view']
            if mode=='target':item['objects'][0]['data']['ModelIdentifier']['ID']=43
            if mode=='shortened':v['Attributes']['Shortening']['CutParts']=True
            if mode=='scale':v['Attributes']['Scale']=5
            if mode=='fixed':v['Attributes']['FixedViewPlacing']=False
            if mode=='origin':v['Origin']['X']=500
            if mode=='extent':v['Width']=501
            if mode=='extra':args[2][1]['page']['views']*=2
            if mode=='type':v['ViewType']='SectionView'
            with self.subTest(mode=mode):self.assertEqual('INPUT_BLOCKED',audit_view_control(*args)['status'])

    def test_missing_reopen_wrong_source_and_invalid_region_reject(self):
        for mode in ('source','region','missing','nan'):
            args=control_fixture()
            if mode=='source':args[1]['sourceDigest']='other'
            if mode=='region':args[0]['viewControl']['region']=[0,0,1001,800]
            if mode=='missing':args[2].pop()
            if mode=='nan':args[1]['box'][0]=float('nan')
            with self.subTest(mode=mode),self.assertRaises(ContractError):audit_view_control(*args)

    def test_scale_sequence_false_fit_and_skipped_first_fit_reject(self):
        for mode in ('sequence','fit','first','nonfinite'):
            args=control_fixture()
            if mode=='sequence':args[1]['attempts'][0]['scale']=2.5
            if mode=='fit':args[1]['attempts'][0]['fits']=True
            if mode=='first':args[1]['attempts'][0].update(Width=400,Height=300,fits=True)
            if mode=='nonfinite':args[1]['attempts'][0]['Width']=float('inf')
            with self.subTest(mode=mode),self.assertRaises(ContractError):audit_view_control(*args)


class MaskTests(unittest.TestCase):
    def test_id_bound_mask_is_not_glyph_proof(self):
        result=audit_sample_masks(mask_fixture())
        self.assertEqual('ID_BOUND_MASKS_MATCHED',result['status'])
        self.assertIn('NOT_GLYPH_PROOF',result['scope'])

    def test_same_text_on_wrong_object_or_view_cannot_satisfy_sample(self):
        for mode in ('id','offset','duplicate','type'):
            capture=mask_fixture();v=capture['page']['presentation'][0];s=v['Primitives'][0]
            if mode=='id':s['Id']=8
            if mode=='offset':v['Position']['X']=20
            if mode=='duplicate':v['Primitives']*=2
            if mode=='type':s['ObjectType']=263
            with self.subTest(mode=mode):self.assertEqual('INPUT_BLOCKED',audit_sample_masks(capture)['status'])

    def test_missing_wrong_rotated_or_changed_text_and_mask_block(self):
        for mode in ('missing','font','text','angle','box','open','diagonal','hole','nan'):
            capture=mask_fixture();s=capture['page']['presentation'][0]['Primitives'][0]
            text=s['Primitives'][0];polygon=s['Primitives'][1]
            if mode=='missing':s['Primitives'].pop()
            if mode=='font':text['Font']='Arial'
            if mode=='text':text['Text']='126'
            if mode=='angle':text['Angle']=1
            if mode=='box':capture['samples'][0]['box']=[0,0,9,5]
            if mode=='open':polygon['OuterLoop']['Segments'][0]['EndPoint']['X']=9
            if mode=='diagonal':polygon['OuterLoop']['Segments'][0]['StartPoint']['Y']=1
            if mode=='hole':polygon['InnerLoops']=[{}]
            if mode=='nan':polygon['OuterLoop']['Segments'][0]['StartPoint']['Y']=float('nan')
            with self.subTest(mode=mode):
                if mode=='nan':
                    with self.assertRaises(ContractError):audit_sample_masks(capture)
                else:self.assertEqual('INPUT_BLOCKED',audit_sample_masks(capture)['status'])
