"""Recompute whole-trial coverage from persisted native anchors and display IDs."""
import math
import re

from .contracts import check_seal, digest, require, seal
from .expressions import adapt_expressions
from .foundation_studies import _nodes
from .view_primitives import model_point, _translate


def verify_complete_trial(source, plan, captures):
    check_seal(plan)
    version=plan.get('expressionAdapterVersion','independent-expressions.r1')
    require(version in ('independent-expressions.r1','independent-expressions.r3'),
            'TRIAL_VERSION','Unsupported expression adapter')
    expressions=adapt_expressions(source,boundary_paths=version=='independent-expressions.r3')
    if version=='independent-expressions.r3':
        from .boundary_planning import check_boundary_placements
        check_boundary_placements(expressions,plan['placements'])
    require(plan['sourceDigest']==source['digest'] and plan['expressionDigest']==expressions['digest'],
            'TRIAL_SOURCE','Plan/source expression mismatch')
    expected={o['objectId']:o for o in expressions['objects'] if o['mobility']=='DYNAMIC'}
    require(len(captures)==3,'TRIAL_REOPEN','Before and two reopens required')
    require(captures[1]['primitives']==captures[2]['primitives'],'TRIAL_PERSISTENCE','Reopens differ')
    observations=[]
    for capture in captures:
        rows={r['key']:r for r in capture['primitives']}
        require(set(rows)==set(expected) and len(rows)==len(capture['primitives']),
                'TRIAL_READBACK_COVERAGE','Missing/extra native object')
        require(len(capture['page']['views'])==1,'TRIAL_VIEW','One view required')
        actual_view=capture['page']['views'][0]['view']
        results=[]
        for key,obj in expected.items():
            row=rows[key];binding=row['viewBinding'];view=binding['view'];a=view['Attributes']
            require(binding['modelPartId']==source['rawPart']['id'] and a['Scale']==plan['scale'] and
                    not a['Shortening']['CutParts'] and a['FixedViewPlacing'] and
                    all(view[k]==actual_view[k] for k in view if k!='type'),
                    'TRIAL_VIEW','Wrong native view/model/scale')
            cs=view['DisplayCoordinateSystem']
            xyz=lambda p:[p[k] for k in ('X','Y','Z')]
            axes=[xyz(cs['AxisX']),xyz(cs['AxisY'])];origin=xyz(cs['Origin'])
            def project(p):
                return [sum((v-b)*q for v,b,q in zip(p,origin,axis)) for axis in axes]
            def near(actual,wanted):
                return math.dist([actual[k] for k in ('X','Y')],wanted)<=1e-6
            data=row['data'];planned=next(p for p in plan['primitives'] if p['key']==key)
            if obj['kind']=='LABEL':
                require(data['TextString']==obj['text'] and near(data['InsertionPoint'],project(planned['at'])) and
                        not row['children'],'TRIAL_LABEL','Label differs or has leader')
                results.append(dict(key=key,objectId=row['objectId'],text=data['TextString']));continue
            require(obj['kind']=='DIMENSION','TRIAL_SCOPE','Unsupported readback role')
            coords=[project(model_point(source,p)) for p in obj['coordinates']]
            children=list(row['children']);values=[]
            require(len(children)==len(coords)-1,'TRIAL_INTERVALS','Dimension interval missing/extra')
            up=[sum(v*q for v,q in zip(planned['up'],axis)) for axis in axes]
            if version=='independent-expressions.r3':
                cross=1-'xy'.index(obj['axis'])
                frame=source['requirements']['partType']['frame']
                wanted=[obj['side']*v for v in frame['axisX' if cross==0 else 'axisY']]
                require(math.dist(planned['up'],wanted)<=1e-6,'BOUNDARY_SIDE',key)
            require(abs(data['Distance']-planned['distance']*plan['scale'])<=1e-6 and
                    data['Attributes']['Format']['Format']==('NoDecimals' if planned['decimals']==0 else 'OneOptionalDecimal'),
                    'TRIAL_PLACEMENT','Wrong dimension distance/format')
            if 'witnessLength' in planned:
                witnesses=row.get('witnesses',[])
                require(data['Attributes']['ExtensionLine']=='Yes' and len(witnesses)==len(coords),
                        'TRIAL_WITNESS','Missing short witness or native suppression')
                ordinate=sum(x*u for x,u in zip(coords[0],up))+data['Distance']
                for point,witness in zip(coords,witnesses):
                    shift=ordinate-sum(x*u for x,u in zip(point,up))
                    line=[x+shift*u for x,u in zip(point,up)];w=witness['data']
                    start=[x-u*planned['witnessLength']*plan['scale'] for x,u in zip(line,up)]
                    end=[x+u*plan['height']*.4*plan['scale'] for x,u in zip(line,up)]
                    require(near(w['StartPoint'],start) and near(w['EndPoint'],end) and
                            w['Attributes']['Line']['Color']=='Black',
                            'TRIAL_WITNESS','Wrong persisted witness geometry/style')
            for i,(start,end) in enumerate(zip(coords,coords[1:])):
                matches=[c for c in children if near(c['data']['StartPoint'],start) and near(c['data']['EndPoint'],end)]
                require(len(matches)==1,'TRIAL_ANCHORS','Native interval does not match actual source vertices')
                child=matches[0];d=child['data'];text=''.join(v['unformatted'] for v in d['Value'])
                require(near(d['UpDirection'],up) and text==format(obj['displayValues'][i],'g'),
                        'TRIAL_VALUE','Actual value/direction differs from source projection')
                values.append(dict(objectId=child['objectId'],text=text,sourceRefs=obj['points'][i:i+2],
                                   rawProjectedValue=obj['values'][i]))
            results.append(dict(key=key,objectId=row['objectId'],intervals=values))
        observations.append(results)
    last=captures[-1];view=last['page']['views'][0]['view'];sheet_origin=[view['Origin'][k] for k in ('X','Y')]
    displays=[d for d in last['page']['presentation'] if math.dist([d['Position'][k] for k in ('X','Y')],sheet_origin)<1e-6]
    require(len(displays)==1,'TRIAL_DISPLAY','Missing actual view display')
    segments=[_translate(s,sheet_origin) for s in displays[0]['Primitives']]
    bindings=[]
    for row in last['primitives']:
        for witness in row.get('witnesses',[]):
            matches=[s for s in segments if s.get('Id')==witness['objectId']]
            require(len(matches)==1 and matches[0]['Pen']['LineType']==1,
                    'TRIAL_WITNESS_DISPLAY','Missing solid witness display')
            lines=list(_nodes(matches[0],'.LinePrimitive'));w=witness['data']
            start=[w['StartPoint'][k]/plan['scale']+sheet_origin[i] for i,k in enumerate(('X','Y'))]
            end=[w['EndPoint'][k]/plan['scale']+sheet_origin[i] for i,k in enumerate(('X','Y'))]
            require(any(near(l['StartPoint'],start) and near(l['EndPoint'],end) or
                        near(l['StartPoint'],end) and near(l['EndPoint'],start) for l in lines),
                    'TRIAL_WITNESS_DISPLAY','Short witness not actually displayed')
    for result in observations[-1]:
        items=result.get('intervals',[result])
        for item in items:
            matches=[s for s in segments if s.get('Id')==item['objectId']]
            require(len(matches)==1,'TRIAL_DISPLAY','Missing displayed native ID')
            text=list(_nodes(matches[0],'.TextPrimitive'))
            require(len(text)==1 and text[0]['Text']==item['text'] and
                    text[0]['Font']=='仿宋' and text[0]['Height']==plan['height'],
                    'TRIAL_DISPLAY','Displayed text/style differs')
            bindings.append(dict(key=result['key'],objectId=item['objectId'],text=item['text']))
    # Target-part contour is observed independently of dimensions.
    binding=last['primitives'][0]['viewBinding']
    target=[s for s in segments if s.get('Id')==binding['targetObjectId']]
    require(len(target)==1,'TRIAL_CONTOUR','Target display missing')
    actual_lines=[[[l[k][c] for c in ('X','Y')] for k in ('StartPoint','EndPoint')]
                  for l in _nodes(target[0],'.LinePrimitive')]
    residuals=[]
    for edge in source['requirements']['facts']['segments']:
        endpoints=[[q/plan['scale']+sheet_origin[i] for i,q in enumerate(project(model_point(source,edge[k])))]
                   for k in ('start','end')]
        residual=min(min(max(math.dist(a,c),math.dist(b,d)),max(math.dist(a,d),math.dist(b,c)))
                     for a,b in actual_lines for c,d in [endpoints])
        require(residual<=.1,'TRIAL_CONTOUR','Real contour differs from source');residuals.append(residual)
    table_text=[t['Text'] for v in last['page']['presentation'] if v['Position']['X']==v['Position']['Y']==0
                for t in _nodes(v,'.TextPrimitive')]
    profiles=[t for t in table_text if re.match(r'^PL\d+(?:\.\d+)?\*',t)]
    thickness=next(o['value'] for o in expressions['objects'] if o['kind']=='THICKNESS')
    require(len(set(profiles))==1 and abs(float(profiles[0][2:].split('*')[0])-thickness)<=source['requirements']['modelToleranceMm'] and
            '1:'+format(plan['scale'],'g') in table_text,'TRIAL_TABLE','Thickness/scale table differs')
    return seal(dict(schemaVersion='CompleteTrialReadback.v1',status='NATIVE_COMPLETE_PREVIEW',productionEligible=False,
                     sourceDigest=source['digest'],planDigest=plan['digest'],captureDigests=[digest(c) for c in captures],
                     observations=observations,displayBindings=bindings,profile=profiles[0],
                     maximumContourResidual=max(residuals),remainingGates=['ACTUAL_PDF_VISUAL','TEMPLATE_FONT_COMPLETENESS']))
