"""Model, SDK view, DPM and PDF coordinates must agree independently."""
import copy
import unittest

from test_independent_primitive_studies import fixture, leader_fixture, rebind
from zijinhua_tekla.part_drawing.independent_layout.contracts import seal
from zijinhua_tekla.part_drawing.independent_layout.primitive_studies import study_primitives
from zijinhua_tekla.part_drawing.independent_layout.view_primitives import bind_model_anchors, model_point


def model_fixture():
    args=list(fixture());p=args[0][0]
    source=seal(dict(status='SOURCE_READY',units='MODEL_MM',rawPart=dict(id=77),requirements=dict(
        partType=dict(frame=dict(origin=[100,200,300],axisX=[0,1,0],axisY=[-1,0,0])),
        facts=dict(points={'a':[0,0],'b':[25,0]}))))
    p.update(up=[-1,0,0]);args[0][0]=bind_model_anchors(source,p,dict(start='a',end='b'))
    xyz=lambda x,y,z=0:dict(X=x,Y=y,Z=z)
    cs=dict(Origin=xyz(100,200,300),AxisX=xyz(0,1),AxisY=xyz(-1,0))
    view=dict(Origin=xyz(30,40),DisplayCoordinateSystem=cs,ViewCoordinateSystem=cs,
              Attributes=dict(Scale=2.5,FixedViewPlacing=True,Shortening=dict(CutParts=False)))
    for c in args[1]:
        row=c['primitives'][0];row['data']['Distance']=25
        child=row['children'][0]['data'];child['EndPoint']['X']=25;child['Value'][0]['unformatted']='25'
        row['viewBinding']=dict(viewId=90,targetObjectId=91,modelPartId=77,view=copy.deepcopy(view))
        c['page']['views']=[dict(view=dict(ViewType='FrontView',**copy.deepcopy(view)))]
        presentation=c['page']['presentation'][0];presentation['Position']=xyz(30,40)
        presentation['Primitives'][0]['Primitives'][3]['Primitives'][1]['Text']='25'
        presentation['Primitives'].append(dict(Id=91,Primitives=[dict(type='T.LinePrimitive',StartPoint=xyz(0,0),EndPoint=xyz(10,0))]))
    body={k:v for k,v in args[2].items() if k!='digest'}
    for g,char in zip(body['glyphs'],'25'):
        g['text']=char
        for i in range(2):
            g['baseline'][i]+=[30,40][i];g['inkBox'][i]+=[30,40][i];g['inkBox'][i+2]+=[30,40][i];g['glyphToSource'][i+4]+=[30,40][i]
    for path in body['paths']:
        for cmd in path['commands']:
            for q in cmd[1:]:q[0]+=30;q[1]+=40
    args[2]=seal(body);rebind(args)
    return args,source


class ModelViewPrimitiveTests(unittest.TestCase):
    def test_model_leader_projects_elbows_and_saved_text_edge_independently(self):
        args=leader_fixture();base,source=model_fixture();p=args[0][0]
        for key in ('start','end','up','distance'):p.pop(key,None)
        p['at']=model_point(source,[v*2.5 for v in p['at']])
        p['elbows']=[model_point(source,[v*2.5 for v in q]) for q in p['elbows']]
        args[0][0]=bind_model_anchors(source,p,dict(tip='a'))
        for c,template in zip(args[1],base[1]):
            row=c['primitives'][0];row['viewBinding']=copy.deepcopy(template['primitives'][0]['viewBinding'])
            points=[row['data']['InsertionPoint'],row['children'][0]['data']['StartPoint'],
                    row['children'][0]['data']['EndPoint'],*row['children'][0]['data']['ElbowPoints']]
            for q in points:q['X']*=2.5;q['Y']*=2.5
            c['page']['views']=copy.deepcopy(template['page']['views'])
            display=c['page']['presentation'][0];display['Position']=dict(X=30,Y=40)
            display['Primitives'].append(copy.deepcopy(template['page']['presentation'][0]['Primitives'][-1]))
        body={k:v for k,v in args[2].items() if k!='digest'}
        for g in body['glyphs']:
            for i in range(2):
                g['baseline'][i]+=[30,40][i];g['inkBox'][i]+=[30,40][i];g['inkBox'][i+2]+=[30,40][i];g['glyphToSource'][i+4]+=[30,40][i]
        for path in body['paths']:
            for cmd in path['commands']:
                for q in cmd[1:]:q[0]+=30;q[1]+=40
        args[2]=seal(body);rebind(args)
        report=study_primitives(*args,source=source)
        self.assertEqual('PRIMITIVES_OBSERVED',report['status'],report)
        self.assertEqual([[30,40],[30,45],[35,45],[39.4,44.4]],report['samples'][0]['details']['route'])
        args[1][1]['primitives'][0]['children'][0]['data']['ElbowPoints'][0]['Y']+=1;rebind(args)
        self.assertEqual('INPUT_BLOCKED',study_primitives(*args,source=source)['status'])

    def test_rotated_model_and_scaled_view_keep_value_and_sheet_style(self):
        args,source=model_fixture();r=study_primitives(*args,source=source)
        self.assertEqual('PRIMITIVES_OBSERVED',r['status'],r)
        s=r['samples'][0]
        self.assertEqual('25',s['details']['displayText'])
        self.assertEqual([40,50],s['arrows'][1]['tip'])
        self.assertEqual(2.5,s['details']['modelView']['scale'])
        self.assertTrue(all(a['residualNative']==0 for a in s['details']['modelView']['anchors']))
        self.assertFalse(r['productionEligible'])

    def test_wrong_source_or_anchor_is_not_replaced_with_planned_coordinates(self):
        for mode in ('missing','digest','anchor','part'):
            args,source=model_fixture()
            if mode=='missing':source=None
            if mode=='digest':args[0][0]['sourceDigest']='wrong'
            if mode=='anchor':args[0][0]['start'][0]+=1
            if mode=='part':
                for c in args[1]:c['primitives'][0]['viewBinding']['modelPartId']=78
                rebind(args)
            with self.subTest(mode=mode):self.assertEqual('INPUT_BLOCKED',study_primitives(*args,source=source)['status'])

    def test_missing_contour_wrong_view_or_double_scale_blocks(self):
        for mode in ('contour','view','scale','shortening','sdk','reopen'):
            args,source=model_fixture()
            for c in args[1]:
                if mode=='contour':c['page']['presentation'][0]['Primitives'].pop()
                if mode=='view':c['page']['presentation'][0]['Position']['X']+=1
                if mode=='scale':
                    c['primitives'][0]['viewBinding']['view']['Attributes']['Scale']=5
                    c['page']['views'][0]['view']['Attributes']['Scale']=5
                if mode=='shortening':c['primitives'][0]['viewBinding']['view']['Attributes']['Shortening']['CutParts']=True
                if mode=='sdk':c['primitives'][0]['children'][0]['data']['EndPoint']['X']=10
            if mode=='reopen':args[1][1]['primitives'][0]['data']['Distance']=10
            rebind(args)
            with self.subTest(mode=mode):self.assertEqual('INPUT_BLOCKED',study_primitives(*args,source=source)['status'])
