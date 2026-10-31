"""Reject plausible but incorrect primitive readback and preserve close-spacing findings."""
import copy
import unittest

from zijinhua_tekla.part_drawing.independent_layout.contracts import ContractError, digest, seal
from zijinhua_tekla.part_drawing.independent_layout.primitive_studies import study_primitives


def polygon(points):
    edges=[dict(type='T.LinePrimitive',StartPoint=dict(X=x,Y=y),
                EndPoint=dict(X=points[(i+1)%len(points)][0],Y=points[(i+1)%len(points)][1])) for i,(x,y) in enumerate(points)]
    return dict(type='T.PolygonPrimitive',InnerLoops=[],OuterLoop=dict(Segments=edges))


def fixture():
    p=dict(key='short',kind='dimension',start=[0,0],end=[10,0],up=[0,1],distance=10,height=2,font='Fixture',shortDimension='Inside')
    style=dict(Arrowhead=dict(Head='FilledArrow',Height=1.6,Width=1.6),Text=dict(Font=dict(Name='Fixture',Height=2)),
               Format=dict(Format='OneOptionalDecimal',Precision='OnePerTen',Unit='Millimeter'),ShortDimension='Inside',Placing=dict(Placing='Fixed'))
    row=dict(key='short',objectId=7,data=dict(Distance=10,Attributes=style),children=[dict(objectId=8,data=dict(
        StartPoint=dict(X=0,Y=0),EndPoint=dict(X=10,Y=0),UpDirection=dict(X=0,Y=1),Value=[dict(unformatted='10',Font=dict(Height=3))]))])
    triangles=[[[0,10],[1.6,9.2],[1.6,10.8]],[[10,10],[8.4,10.8],[8.4,9.2]]]
    mask=polygon([[4,12],[6,12],[6,14],[4,14]])
    text=dict(type='T.TextPrimitive',Text='10',Font='Fixture',Height=2,Angle=0,Position=dict(X=4,Y=12))
    line=dict(type='T.LinePrimitive',StartPoint=dict(X=0,Y=10),EndPoint=dict(X=10,Y=10))
    segment=dict(Id=8,Primitives=[line,*[polygon(q) for q in triangles],dict(Primitives=[mask,text])])
    capture=dict(primitives=[row],page=dict(presentation=[dict(Position=dict(X=0,Y=0),Primitives=[segment])]))
    glyphs=[dict(index=i,baseline=[4+i,12],text=char,font='Fixture',fontSha256='font',inkBox=[4+i,12,5+i,13],glyphToSource=[1,0,0,1,4+i,12]) for i,char in enumerate('10')]
    paths=[dict(index=i,fill=True,commands=[['m',q[0]],['l',q[1]],['l',q[2]],['h']]) for i,q in enumerate(triangles)]
    captures=[copy.deepcopy(capture) for _ in range(3)]
    ink=seal(dict(captureDigest=digest(captures[-1]),glyphs=glyphs,paths=paths,frameAlignment=dict(maximumCorrectedResidual=0)))
    return [p],captures,ink


def rebind(args):
    body={k:v for k,v in args[2].items() if k!='digest'};body['captureDigest']=digest(args[1][-1]);args[2]=seal(body)


def leader_fixture():
    args=list(fixture());p=args[0][0]
    p.update(kind='leader',text='10',at=[10,5],tip=[0,0],elbows=[[0,5],[5,5]])
    route=[p['tip'],*p['elbows'],[9.4,4.4]];triangle=[[0,0],[-.8,1.6],[.8,1.6]]
    for i,capture in enumerate(args[1]):
        row=capture['primitives'][0]
        row['data']=dict(InsertionPoint=dict(X=10,Y=5),Attributes=dict(Font=dict(Name='Fixture',Height=2),
                        ArrowHead=dict(Head='FilledArrow',Height=1.6,Width=1.6),PlacingAttributes=dict(IsFixed=True)))
        end=p['at'] if i==0 else route[-1]
        row['children']=[dict(objectId=9,data=dict(StartPoint=dict(X=0,Y=0),EndPoint=dict(X=end[0],Y=end[1]),
                                                  ElbowPoints=[dict(X=x,Y=y) for x,y in p['elbows']]))]
        lines=[dict(type='T.LinePrimitive',StartPoint=dict(X=a[0],Y=a[1]),EndPoint=dict(X=b[0],Y=b[1])) for a,b in zip(route,route[1:])]
        text=dict(type='T.TextPrimitive',Text='10',Font='Fixture',Height=2,Angle=0,Position=dict(X=10,Y=4))
        symbol=dict(type='T.SymbolPrimitive',SymbolLibrary='arrows.sym',SymbolIndex=17,Position=dict(X=0,Y=0))
        capture['page']['presentation'][0]['Primitives']=[dict(Id=7,Primitives=[*lines,symbol,polygon([[10,4],[12,4],[12,6],[10,6]]),text])]
    body={k:v for k,v in args[2].items() if k!='digest'}
    body['glyphs']=[dict(index=i,baseline=[10+i,4],text=char,font='Fixture',fontSha256='font',inkBox=[10+i,4,11+i,5],glyphToSource=[1,0,0,1,10+i,4]) for i,char in enumerate('10')]
    body['paths']=[dict(index=0,fill=True,commands=[['m',triangle[0]],['l',triangle[1]],['l',triangle[2]],['h']])]
    args[2]=seal(body);rebind(args);return args


class PrimitiveStudyTests(unittest.TestCase):
    def test_matched_primitive_is_not_layout_approval(self):
        r=study_primitives(*fixture());self.assertEqual('PRIMITIVES_OBSERVED',r['status']);self.assertFalse(r['productionEligible'])
        row=r['samples'][0];self.assertEqual(2,len(row['arrows']));self.assertEqual([],row['textLineIntersections'])
        self.assertEqual('DISPLAY_TEXT_MASK',row['ink']['auxiliaryBoxKind'])

    def test_wrong_value_unit_mode_anchor_or_arrow_blocks(self):
        for mode in ('value','unit','mode','anchor','arrow'):
            args=list(fixture())
            for capture in args[1]:
                row=capture['primitives'][0]
                if mode=='value':row['children'][0]['data']['Value'][0]['unformatted']='11'
                if mode=='unit':row['data']['Attributes']['Format']['Unit']='Centimeter'
                if mode=='mode':row['data']['Attributes']['ShortDimension']='Outside'
                if mode=='anchor':row['children'][0]['data']['StartPoint']['X']=.2
                if mode=='arrow':row['data']['Attributes']['Arrowhead']['Head']='NoArrow'
            rebind(args)
            with self.subTest(mode=mode):self.assertEqual('INPUT_BLOCKED',study_primitives(*args)['status'])

    def test_missing_or_ambiguous_pdf_triangle_blocks(self):
        for mode in ('missing','duplicate','moved'):
            args=list(fixture());body={k:v for k,v in args[2].items() if k!='digest'}
            if mode=='missing':body['paths'].pop()
            if mode=='duplicate':body['paths'].append(copy.deepcopy(body['paths'][0]))
            if mode=='moved':body['paths'][0]['commands'][0][1][0]+=.5
            args[2]=seal(body)
            with self.subTest(mode=mode):self.assertEqual('INPUT_BLOCKED',study_primitives(*args)['status'])

    def test_changed_reopen_display_or_sdk_blocks(self):
        for mode in ('sdk','display'):
            args=list(fixture())
            if mode=='sdk':args[1][1]['primitives'][0]['data']['Distance']=11
            if mode=='display':args[1][1]['page']['presentation'][0]['Primitives'][0]['Primitives'][0]['EndPoint']['X']=11
            with self.subTest(mode=mode):self.assertEqual('INPUT_BLOCKED',study_primitives(*args)['status'])

    def test_capture_hash_seal_mapping_and_missing_reopen_reject(self):
        for mode in ('binding','seal','mapping','reopen','budget'):
            args=list(fixture())
            if mode=='binding':args[1][-1]['extra']='wrong page'
            if mode=='seal':args[2]['captureDigest']='wrong'
            if mode=='mapping':args[2]=seal({k:v for k,v in args[2].items() if k not in ('digest','frameAlignment')})
            if mode=='reopen':args[1].pop()
            with self.subTest(mode=mode),self.assertRaises(ContractError):study_primitives(*args,tolerance=.2 if mode=='budget' else .1)

    def test_raw_line_mask_intersection_kept_separate_from_capability(self):
        args=list(fixture())
        for capture in args[1]:
            capture['page']['presentation'][0]['Primitives'][0]['Primitives'].append(
                dict(type='T.LinePrimitive',StartPoint=dict(X=5,Y=10),EndPoint=dict(X=5,Y=15)))
        rebind(args);r=study_primitives(*args)
        self.assertEqual('PRIMITIVES_OBSERVED',r['status']);self.assertTrue(r['samples'][0]['textLineIntersections'])

    def test_leader_end_refreshes_only_before_save_two_reopens_must_agree(self):
        args=leader_fixture();r=study_primitives(*args)
        self.assertEqual('PRIMITIVES_OBSERVED',r['status'])
        self.assertEqual([10,5],r['samples'][0]['details']['unsavedSdkEndPoint'])
        self.assertEqual([9.4,4.4],r['samples'][0]['details']['savedSdkEndPoint'])
        args[1][1]['primitives'][0]['children'][0]['data']['EndPoint']['X']=9.5
        self.assertEqual('INPUT_BLOCKED',study_primitives(*args)['status'])

    def test_leader_elbow_symbol_route_and_actual_arrow_direction_checked(self):
        for mode in ('elbow','symbol','route','arrow'):
            args=leader_fixture()
            if mode=='arrow':
                body={k:v for k,v in args[2].items() if k!='digest'}
                for command in body['paths'][0]['commands']:
                    for p in command[1:]:p[1]=-p[1]
                args[2]=seal(body)
            else:
                for capture in args[1]:
                    if mode=='elbow':capture['primitives'][0]['children'][0]['data']['ElbowPoints'][0]['X']=.5
                    if mode=='symbol':capture['page']['presentation'][0]['Primitives'][0]['Primitives'][3]['SymbolIndex']=1
                    if mode=='route':capture['page']['presentation'][0]['Primitives'][0]['Primitives'][0]['EndPoint']['Y']=4
            rebind(args)
            with self.subTest(mode=mode):self.assertEqual('INPUT_BLOCKED',study_primitives(*args)['status'])


if __name__=='__main__':unittest.main()
