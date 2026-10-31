"""Finite synthetic short-dimension/leader checks against SDK, display and PDF."""
import math
import copy
from decimal import Decimal, ROUND_HALF_UP

from .contracts import ContractError, check_seal, digest, require, seal
from .foundation_studies import _nodes
from .glyph_occupancy import bind_ink, union


def _xy(p):
    return [p['X'],p['Y']]


def _near(a,b,tolerance):
    return len(a)==len(b) and all(type(x) in (int,float) and math.isfinite(x) for x in [*a,*b]) and math.dist(a,b)<=tolerance


def _polygon(poly):
    edges=poly['OuterLoop']['Segments']
    require(not poly['InnerLoops'] and edges and all(e['type'].endswith('.LinePrimitive') and
        _near(_xy(e['EndPoint']),_xy(edges[(i+1)%len(edges)]['StartPoint']),1e-6) for i,e in enumerate(edges)),
        'PRIMITIVE_POLYGON','Open/unsupported polygon')
    return [_xy(e['StartPoint']) for e in edges]


def _lines(node):
    if isinstance(node,dict):
        kind=node.get('type','')
        if kind.endswith('.PolygonPrimitive'):return
        if kind.endswith('.LinePrimitive'):yield [_xy(node['StartPoint']),_xy(node['EndPoint'])]
        else:
            for value in node.values():yield from _lines(value)
    elif isinstance(node,list):
        for value in node:yield from _lines(value)


def _pdf_triangles(paths):
    result=[]
    for path in paths:
        commands=path['commands']
        if not path['fill'] or not commands or commands[-1][0]!='h' or any(c[0] not in ('m','l','h') for c in commands):continue
        points=[]
        for c in commands:
            for p in c[1:]:
                if not any(_near(p,q,1e-6) for q in points):points.append(p)
        if len(points)==3:result.append(dict(index=path['index'],points=points))
    return result


def _match_triangle(points, candidates, tolerance):
    matches=[p for p in candidates if all(any(_near(v,q,tolerance) for q in p['points']) for v in points)]
    require(len(matches)==1,'PRIMITIVE_PDF_ARROW','Filled arrow missing/ambiguous in actual PDF')
    return matches[0]


def _text_observation(key, object_id, segment):
    text=list(_nodes(segment,'.TextPrimitive'))
    require(len(text)==1,'PRIMITIVE_TEXT','One actual display string required')
    t=text[0]
    # Auxiliary envelope for ink reporting; not an SDK box for child dimensions.
    polygons=[p for p in _nodes(segment,'.PolygonPrimitive') if len(p['OuterLoop']['Segments'])==4]
    require(len(polygons)==1,'PRIMITIVE_TEXT','One four-edge text mask required')
    box=union([[x,y,x,y] for x,y in _polygon(polygons[0])])
    return dict(key=key,objectId=object_id,text=t['Text'],font=t['Font'],height=t['Height'],angle=math.degrees(t['Angle']),box=box)


def study_primitives(planned, captures, extracted, tolerance=.1):
    """Tolerance associates readback paths in source units, not a layout clearance."""
    from shapely.geometry import LineString, box
    check_seal(extracted)
    require('frameAlignment' in extracted,'PRIMITIVE_PDF_BINDING','Independent source-frame mapping required')
    require(type(tolerance) in (int,float) and math.isfinite(tolerance) and 0<tolerance<=.1,
            'PRIMITIVE_TOLERANCE','Invalid comparison budget')
    require(len(captures)==3 and planned and len({p['key'] for p in planned})==len(planned),
            'PRIMITIVE_CAPTURES','Plan and before/two reopens required')
    observed=[]
    for capture in captures:
        rows=capture['primitives'];by_key={r['key']:r for r in rows}
        require(len(by_key)==len(rows)==len(planned) and set(by_key)=={p['key'] for p in planned} and
                len({r['objectId'] for r in rows})==len(rows),'PRIMITIVE_IDS','Missing/duplicate primitive IDs')
        observed.append(by_key)
    require(extracted.get('captureDigest')==digest(captures[-1]),'PRIMITIVE_PDF_BINDING','Ink report belongs to another capture')
    page=captures[-1]['page']
    segments=[s for v in page['presentation'] if v.get('Position',{}).get('X')==0 and v.get('Position',{}).get('Y')==0
              for s in v.get('Primitives',[])]
    triangles=_pdf_triangles(extracted['paths']);results=[]
    for p in planned:
        try:
            rows=[m[p['key']] for m in observed];r=rows[-1];data=r['data'];a=data['Attributes']
            unsaved=copy.deepcopy(rows[0]);initial_endpoint=None
            if p['kind']=='leader' and len(unsaved['children'])==len(r['children'])==1:
                initial_endpoint=_xy(unsaved['children'][0]['data']['EndPoint'])
                if _near(initial_endpoint,p['at'],1e-6):
                    unsaved['children'][0]['data']['EndPoint']=copy.deepcopy(r['children'][0]['data']['EndPoint'])
            checks=dict(sdkStable=digest(unsaved)==digest(rows[-1]) and digest(rows[1])==digest(rows[2]))
            require(len(r['children'])==1,'PRIMITIVE_CHILD','Exactly one child required')
            child=r['children'][0];d=child['data'];identity=child['objectId'] if p['kind']=='dimension' else r['objectId']
            matches=[s for s in segments if s.get('Id')==identity]
            require(len(matches)==1,'PRIMITIVE_SEGMENT','Missing/ambiguous native segment')
            segment=matches[0];text=_text_observation(p['key'],identity,segment)
            display=[]
            for capture in captures:
                display.append([s for v in capture['page']['presentation'] if v.get('Position',{}).get('X')==0 and v.get('Position',{}).get('Y')==0
                                for s in v.get('Primitives',[]) if s.get('Id')==identity])
            checks['displayStable']=all(len(s)==1 for s in display) and digest(display[1])==digest(display[2])
            checks['actualFont']=text['font']==p['font'] and text['height']==p['height']
            ink=bind_ink(text,segment,extracted['glyphs'])
            # This adapter's auxiliary box is a display mask, not SDK child metrics.
            ink['auxiliaryBoxKind']='DISPLAY_TEXT_MASK';ink.pop('sdkBox');ink.pop('overflowSdk')
            arrows=[];lines=list(_lines(segment));details={}
            if p['kind']=='dimension':
                length=math.dist(p['start'],p['end']);value=Decimal(str(length)).quantize(Decimal('.1'),rounding=ROUND_HALF_UP)
                expected=format(value,'f').rstrip('0').rstrip('.') if '.' in format(value,'f') else format(value,'f')
                sdk_value=''.join(v['unformatted'] for v in d['Value'])
                checks.update(anchors=_near(_xy(d['StartPoint']),p['start'],1e-6) and _near(_xy(d['EndPoint']),p['end'],1e-6),
                              direction=_near(_xy(d['UpDirection']),p['up'],1e-6),distance=abs(data['Distance']-p['distance'])<=1e-6,
                              displayValue=text['text']==sdk_value==expected,
                              format=a['Format']['Format']=='OneOptionalDecimal' and a['Format']['Precision']=='OnePerTen' and a['Format']['Unit']=='Millimeter',
                              shortMode=a['ShortDimension']==p['shortDimension'],fixed=a['Placing']['Placing']=='Fixed')
                arrow=a['Arrowhead'];font=a['Text']['Font']
                native_triangles=[_polygon(q) for q in _nodes(segment,'.PolygonPrimitive') if len(q['OuterLoop']['Segments'])==3]
                require(len(native_triangles)==2,'PRIMITIVE_ARROWS','Expected two actual dimension arrow triangles')
                tips=[[p[k][i]+p['up'][i]*p['distance'] for i in range(2)] for k in ('start','end')]
                axis=[(p['end'][i]-p['start'][i])/length for i in range(2)]
                for tip_index,tip in enumerate(tips):
                    matches=[q for q in native_triangles if any(_near(tip,v,1e-6) for v in q)]
                    require(len(matches)==1,'PRIMITIVE_ARROW_TIP','Arrow does not meet actual dimension endpoint')
                    q=matches[0];pdf=_match_triangle(q,triangles,tolerance)
                    tail=[sum(v[i] for v in q)/3-tip[i] for i in range(2)]
                    inward=sum(tail[i]*axis[i] for i in range(2))*(1 if tip_index==0 else -1)>0
                    axial=[sum((v[i]-tip[i])*axis[i] for i in range(2)) for v in q]
                    normal=[-(v[0]-tip[0])*axis[1]+(v[1]-tip[1])*axis[0] for v in q]
                    checks['arrowSize'+str(tip_index)]=abs(max(abs(v) for v in axial)-p['height']*.8)<=1e-6 and abs(max(normal)-min(normal)-p['height']*.8)<=1e-6
                    arrows.append(dict(tip=tip,points=q,pdfPath=pdf['index'],bodyInsideSpan=inward))
                details=dict(length=length,displayText=text['text'],sdkValueText=sdk_value,expectedText=expected,
                             childValueFonts=[v.get('Font') for v in d['Value']])
            else:
                checks.update(text=text['text']==p['text'],tip=_near(_xy(d['StartPoint']),p['tip'],1e-6),
                              position=_near(_xy(data['InsertionPoint']),p['at'],1e-6),
                              elbows=len(d['ElbowPoints'])==len(p['elbows']) and all(_near(_xy(x),y,1e-6) for x,y in zip(d['ElbowPoints'],p['elbows'])),
                              fixed=a['PlacingAttributes']['IsFixed'] is True)
                arrow=a['ArrowHead'];font=a['Font'];symbols=list(_nodes(segment,'.SymbolPrimitive'))
                require(len(symbols)==1,'PRIMITIVE_LEADER_SYMBOL','One actual leader arrow symbol required')
                symbol=symbols[0]
                checks['symbol']=symbol['SymbolLibrary']=='arrows.sym' and symbol['SymbolIndex']==17 and _near(_xy(symbol['Position']),p['tip'],1e-6)
                route=[p['tip'],*p['elbows'],_xy(d['EndPoint'])]
                checks['routeLines']=len(lines)==len(route)-1 and all(any(
                    (_near(a,x,1e-6) and _near(b,y,1e-6)) or (_near(a,y,1e-6) and _near(b,x,1e-6)) for x,y in lines) for a,b in zip(route,route[1:]))
                candidates=[q for q in triangles if any(_near(p['tip'],v,tolerance) for v in q['points'])]
                require(len(candidates)==1,'PRIMITIVE_PDF_ARROW','Leader tip has no unique actual filled triangle')
                q=candidates[0];direction=[route[1][i]-route[0][i] for i in range(2)];distance=math.hypot(*direction)
                require(distance>0,'PRIMITIVE_ROUTE','Zero leader segment')
                unit=[v/distance for v in direction]
                projected=[[(v[0]-p['tip'][0])*unit[0]+(v[1]-p['tip'][1])*unit[1],
                            -(v[0]-p['tip'][0])*unit[1]+(v[1]-p['tip'][1])*unit[0]] for v in q['points']]
                checks['pdfArrowSizeDirection']=abs(max(v[0] for v in projected)-p['height']*.8)<=tolerance and min(v[0] for v in projected)>=-tolerance and abs(max(v[1] for v in projected)-min(v[1] for v in projected)-p['height']*.8)<=tolerance
                arrows=[dict(tip=p['tip'],points=q['points'],pdfPath=q['index'])];details=dict(route=route,symbol=symbol,unsavedSdkEndPoint=initial_endpoint,
                                                                                           savedSdkEndPoint=_xy(d['EndPoint']))
            checks['sdkStyle']=font['Name']==p['font'] and font['Height']==p['height'] and arrow['Head']=='FilledArrow' and arrow['Height']==arrow['Width']==p['height']*.8
            obstacle=box(*ink['occupancyBox']);clearances=[obstacle.distance(LineString(line)) for line in lines]
            results.append(dict(key=p['key'],kind=p['kind'],objectId=r['objectId'],displayObjectId=identity,
                                status='PRIMITIVE_MATCHED' if all(checks.values()) else 'INPUT_BLOCKED',checks=checks,
                                ink=ink,arrows=arrows,lines=lines,details=details,
                                textLineIntersections=[i for i,value in enumerate(clearances) if value==0],
                                clearanceScope='RAW_NATIVE_LINE_CENTERLINES_VS_MASK_INK_UNION_NOT_VISIBLE_CLIPPED_STROKES',
                                minimumTextToLineCenterDistance=min(clearances) if clearances else None))
        except ContractError as error:
            results.append(dict(key=p['key'],status='INPUT_BLOCKED',error=str(error)))
    return seal(dict(schemaVersion='PrimitiveStudy.v1',status='PRIMITIVES_OBSERVED' if all(r['status']=='PRIMITIVE_MATCHED' for r in results) else 'INPUT_BLOCKED',
                     productionEligible=False,comparisonToleranceNative=tolerance,samples=results,captureDigests=[digest(c) for c in captures],
                     scope='SYNTHETIC_SHEET_PRIMITIVES_NOT_MODEL_ANCHORS_OR_COMPLETE_CLEARANCE',
                     inkDigest=extracted['digest'],planDigest=digest(planned)))
