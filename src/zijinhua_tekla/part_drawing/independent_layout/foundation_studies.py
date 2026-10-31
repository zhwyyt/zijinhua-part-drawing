"""Independent checks of native view controls and ID-bound text masks.

These finite observations never certify font glyphs or production layout.
"""
import math

from .contracts import digest, require, seal

SCALES = (1, 2, 2.5, 5, 10, 20, 50)


def _numbers(values, length):
    return isinstance(values, list) and len(values)==length and all(
        type(v) in (int,float) and math.isfinite(v) for v in values)


def _box(values):
    return _numbers(values,4) and values[0]<values[2] and values[1]<values[3]


def _inside(inner, outer, tolerance=1e-6):
    return _box(inner) and _box(outer) and all(inner[i]>=outer[i]-tolerance for i in (0,1)) and all(
        inner[i]<=outer[i]+tolerance for i in (2,3))


def _nodes(node, suffix):
    if isinstance(node,dict):
        if node.get('type','').endswith(suffix):
            yield node
        for value in node.values():
            yield from _nodes(value,suffix)
    elif isinstance(node,list):
        for value in node:
            yield from _nodes(value,suffix)


def audit_view_control(template, receipt, captures, part_id, source_digest):
    """Check each saved observation, actual scale attempts and target association."""
    require(len(captures)==3, 'CONTROL_REOPENS', 'Before and two reopens required')
    control=template['viewControl'];region=control['region'];size=template['size']
    require(_numbers(size,2) and _inside(region,[0,0,*size]), 'CONTROL_REGION', 'Invalid calibration region')
    require(control['sourceDigest']==receipt['sourceDigest']==source_digest and receipt['region']==region,
            'CONTROL_SOURCE', 'Control receipt belongs to another admitted source/region')
    attempts=receipt['attempts']
    require(0<len(attempts)<=len(SCALES) and [a['scale'] for a in attempts]==list(SCALES[:len(attempts)]),
            'CONTROL_ATTEMPTS', 'Missing/reordered scale observations')
    fit=[]
    for a in attempts:
        require(_numbers([a['Width'],a['Height']],2) and a['Width']>0 and a['Height']>0,
                'CONTROL_EXTENT', 'Invalid measured extent')
        fits=a['Width']<=region[2]-region[0] and a['Height']<=region[3]-region[1]
        require(type(a['fits']) is bool and a['fits']==fits, 'CONTROL_FIT', 'Fit flag contradicts measured extent')
        fit.append(fits)
    require(fit==[False]*(len(fit)-1)+[True] and receipt['scale']==attempts[-1]['scale'],
            'CONTROL_FIRST_FIT', 'Not the first measured fitting scale')
    require(_box(receipt['box']) and _numbers(receipt['origin'],2), 'CONTROL_BOX', 'Invalid receipt geometry')
    observations=[]
    for capture in captures:
        page=capture['page'];views=page['views']
        checks=dict(singleFrontView=len(views)==1 and views[0]['view']['ViewType']=='FrontView')
        if checks['singleFrontView']:
            item=views[0];v=item['view'];a=v['Attributes']
            x=v['Origin']['X']+v['FrameOrigin']['X'];y=v['Origin']['Y']+v['FrameOrigin']['Y']
            box=[x,y,x+v['Width'],y+v['Height']]
            targets=[o['data'] for o in item['objects'] if o['data']['type']=='Tekla.Structures.Drawing.Part']
            tables=[s for p in page['presentation'] if p.get('Position',{}).get('X')==0 and p.get('Position',{}).get('Y')==0
                    for s in p.get('Primitives',[]) if s.get('ObjectType')==263]
            scale_text='1:'+format(receipt['scale'],'g')
            checks.update(targetPart=len(targets)==1 and targets[0]['ModelIdentifier']['ID']==part_id,
                          fixed=a['FixedViewPlacing'] is True,unshortened=a['Shortening']['CutParts'] is False,
                          scale=a['Scale']==receipt['scale'],withinRegion=_inside(box,region),
                          receiptGeometry=_numbers(box,4) and all(abs(a-b)<=1e-6 for a,b in zip(box,receipt['box'])) and
                          all(abs(v['Origin'][k]-receipt['origin'][i])<=1e-6 for i,k in enumerate(('X','Y'))) and
                          all(abs(v[k]-attempts[-1][k])<=1e-6 for k in ('Width','Height')),
                          titleScale=any(t['Text']==scale_text for t in _nodes(tables,'.TextPrimitive')))
        observations.append(checks)
    # Tekla refreshes the title table on save/open. Only the unsaved title may lag;
    # both actual reopened observations must show the new scale.
    matched=all(all(value for key,value in c.items() if index>0 or key!='titleScale') for index,c in enumerate(observations))
    return seal(dict(status='VIEW_CONTROL_MATCHED' if matched else 'INPUT_BLOCKED',
                     observations=observations,receiptDigest=digest(receipt),captureDigests=[digest(c) for c in captures],
                     productionEligible=False,scope='SINGLE_FRONT_VIEW_FIXED_UNSHORTENED_CALIBRATION_ONLY'))


def audit_sample_masks(capture):
    """Match the sheet text segment by database ID, then check its native mask."""
    page=capture['page'];samples=capture['samples'];rows=[]
    require(samples and len({s['objectId'] for s in samples})==len(samples), 'MASK_SAMPLE_IDS', 'Missing/duplicate samples')
    segments=[s for v in page['presentation'] if v.get('Position',{}).get('X')==0 and v.get('Position',{}).get('Y')==0
              for s in v.get('Primitives',[]) if s.get('ObjectType')==254]
    for sample in samples:
        matched=[s for s in segments if s.get('Id')==sample['objectId']]
        checks=dict(uniqueSegment=len(matched)==1);bounds=None
        if len(matched)==1:
            text=list(_nodes(matched[0],'.TextPrimitive'));polygons=list(_nodes(matched[0],'.PolygonPrimitive'))
            checks['textStyle']=len(text)==1 and all(text[0][k]==sample[v] for k,v in (
                ('Text','text'),('Font','font'),('Height','height'))) and abs(text[0]['Angle']-math.radians(sample.get('angle',0)))<=1e-5
            checks['singleMask']=len(polygons)==1 and not polygons[0]['InnerLoops']
            if checks['singleMask']:
                edges=polygons[0]['OuterLoop']['Segments']
                coords=[[e[k]['X'],e[k]['Y']] for e in edges for k in ('StartPoint','EndPoint')]
                finite=all(_numbers(p,2) for p in coords)
                checks['rectangle']=finite and len(edges)==4 and all(e['type'].endswith('.LinePrimitive') and
                    e['EndPoint']['X']==edges[(i+1)%4]['StartPoint']['X'] and
                    e['EndPoint']['Y']==edges[(i+1)%4]['StartPoint']['Y'] for i,e in enumerate(edges))
                if finite and coords:
                    bounds=[min(p[0] for p in coords),min(p[1] for p in coords),max(p[0] for p in coords),max(p[1] for p in coords)]
                    vectors=[[e['EndPoint'][k]-e['StartPoint'][k] for k in ('X','Y')] for e in edges]
                    lengths=[math.hypot(*v) for v in vectors]
                    checks['rectangle']=checks['rectangle'] and len(vectors)==4 and all(v>0 for v in lengths) and all(
                        abs(sum(vectors[i][k]*vectors[(i+1)%4][k] for k in range(2)))<=1e-5*lengths[i]*lengths[(i+1)%4]
                        and all(abs(vectors[i][k]+vectors[(i+2)%4][k])<=1e-6 for k in range(2)) for i in range(4))
                    checks['maskInsideSdkBox']=_inside(bounds,sample['box'])
        rows.append(dict(key=sample['key'],objectId=sample['objectId'],checks=checks,maskBox=bounds))
    return seal(dict(status='ID_BOUND_MASKS_MATCHED' if all(all(r['checks'].values()) for r in rows) else 'INPUT_BLOCKED',
                     samples=rows,captureDigest=digest(capture),productionEligible=False,scope='NATIVE_RECTANGULAR_MASK_NOT_GLYPH_PROOF'))
