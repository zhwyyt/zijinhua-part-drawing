"""Finite opaque black/white PDF paint replay; distances are to glyph bounds.

This is observed evidence, not a candidate measurer or a layout approval gate.
"""
import copy
import hashlib
import io
import json
import math

from .contracts import ContractError, check_seal, require, seal
from .glyph_occupancy import affine, compose, point


def _polygon(commands):
    from shapely.geometry import Polygon
    require(commands and commands[0][0]=='m' and
            all(c[0]=='l' for c in commands[1:-1]) and commands[-1][0]=='h',
            'PAINT_POLYGON', 'One closed straight polygon required')
    polygon=Polygon([c[1] for c in commands[:-1]])
    require(polygon.is_valid and polygon.area>0,'PAINT_POLYGON','Invalid polygon')
    return polygon


def _transform(geometry, matrix):
    from shapely.affinity import affine_transform
    a,b,c,d,e,f=affine(matrix)
    return affine_transform(geometry,[a,c,b,d,e,f])


def _inverse(matrix):
    a,b,c,d,e,f=affine(matrix);det=a*d-b*c
    return [d/det,-b/det,-c/det,a/det,(c*f-d*e)/det,(b*e-a*f)/det]


def read_paint_states(pdf_bytes, extracted):
    """pdfminer does not implement clipping/ExtGState; replay those explicitly.

    Paint rows are joined by ordered path index to the same hashed PDF, with
    independently checked geometry and CTM. Unknown operations fail closed.
    """
    from pypdf import PdfReader
    from shapely.geometry import box
    require(hashlib.sha256(pdf_bytes).hexdigest()==extracted['pdfSha256'],
            'PAINT_PDF_HASH','Paint PDF differs from ink evidence')
    pdf=PdfReader(io.BytesIO(pdf_bytes));require(len(pdf.pages)==1,'PAINT_PAGE','One page required')
    page=pdf.pages[0];resources=page['/Resources'];mapping=extracted['pdfToSource']
    group=page.get('/Group')
    require(group is None or dict(group.get_object())==dict(
            {'/CS':'/DeviceRGB','/S':'/Transparency','/I':False,'/K':False}),
            'PAINT_GROUP','Unsupported page compositing group')
    require(not resources.get('/XObject') and not page.get('/Annots') and
            page.get('/Rotate',0)==0 and page.get('/UserUnit',1)==1,
            'PAINT_PAGE','Unsupported page resources/transform/annotations')
    crop=page.cropbox;media=page.mediabox
    clip=box(max(float(crop.left),float(media.left)),max(float(crop.bottom),float(media.bottom)),
             min(float(crop.right),float(media.right)),min(float(crop.top),float(media.top)))
    state=dict(ctm=[1,0,0,1,0,0],clip=_transform(clip,mapping),width=1,cap=0,join=0,
               miter=10,dash=[[],0],stroke=0,fill=0)
    stack=[];path=[];pending_clip=False;rows=[]
    ignored={'BT','ET','Tf','Td','TD','Tm','T*','Tc','Tw','Tz','TL','Ts','Tj','TJ',"'",'"'}
    endings={'S':(True,False,False),'s':(True,False,False),'f':(False,True,False),
             'F':(False,True,False),'f*':(False,True,True),'B':(True,True,False),
             'B*':(True,True,True),'b':(True,True,False),'b*':(True,True,True)}
    for sequence,(args,operator) in enumerate(page.get_contents().operations):
        op=operator.decode('ascii')
        if op=='q':stack.append(copy.copy(state))
        elif op=='Q':
            require(bool(stack),'PAINT_STACK','Unbalanced restore');state=stack.pop()
        elif op=='cm':
            require(not path,'PAINT_CTM','CTM changed during path construction')
            state['ctm']=list(compose(state['ctm'],list(map(float,args))))
        elif op in ('w','J','j','M'):
            value=float(args[0]);require(math.isfinite(value),'PAINT_STATE','Nonfinite stroke state')
            state[{'w':'width','J':'cap','j':'join','M':'miter'}[op]]=value
        elif op=='d':state['dash']=[list(map(float,args[0])),float(args[1])]
        elif op in ('rg','RG','g','G'):
            values=list(map(float,args))
            require(values in ([0],[1],[0,0,0],[1,1,1]),'PAINT_COLOR','Only black/white supported')
            state['stroke' if op.isupper() else 'fill']=values[0]
        elif op=='gs':
            gs=resources['/ExtGState'][args[0]].get_object()
            require(all(k=='/Type' or (k in ('/CA','/ca') and float(v)==1) for k,v in gs.items()),
                    'PAINT_EXT_STATE','Only explicit opaque alpha supported')
        elif op=='Tr':require(int(args[0])==0,'PAINT_TEXT_MODE','Only filled text supported')
        elif op in ignored:
            if op in ('Tj','TJ',"'",'"'):
                require(state['fill']==0,'PAINT_WHITE_TEXT','White text occlusion unsupported')
        elif op in ('m','l','c','v','y'):
            matrix=compose(mapping,state['ctm'])
            path.append([op,*[point(matrix,list(map(float,args[i:i+2]))) for i in range(0,len(args),2)]])
        elif op=='re':
            x,y,w,h=map(float,args);matrix=compose(mapping,state['ctm'])
            path.extend([[kind,point(matrix,p)] for kind,p in
                         zip(('m','l','l','l'),((x,y),(x+w,y),(x+w,y+h),(x,y+h)))]+[['h']])
        elif op=='h':path.append(['h'])
        elif op in ('W','W*'):pending_clip=True
        elif op in endings or op=='n':
            if op in ('s','b','b*'):path.append(['h'])
            if op!='n':
                index=len(rows);require(index<len(extracted['paths']),'PAINT_PATH_BINDING','Extra PDF paint')
                observed=extracted['paths'][index];stroke,fill,evenodd=endings[op]
                require((observed['stroke'],observed['fill'],observed['evenodd'])==(stroke,fill,evenodd) and
                        len(path)==len(observed['commands']) and all(a[0]==b[0] and len(a)==len(b) and
                        all(math.dist(x,y)<1e-6 for x,y in zip(a[1:],b[1:])) for a,b in zip(path,observed['commands'])),
                        'PAINT_PATH_BINDING','Ordered PDF geometry differs')
                matrix=compose(mapping,state['ctm'])
                # Legacy pdfminer lineWidth is CTM-scaled at `w`, not the PDF
                # user width. Read the operator here; do not scale it twice.
                require(all(abs(a-b)<1e-8 for a,b in zip(matrix,observed['transform'])),
                        'PAINT_PATH_BINDING','Path transform differs')
                require(not stroke or state['stroke']==0,'PAINT_WHITE_STROKE','White stroke occlusion unsupported')
                row=dict(state,index=index,sequence=sequence,path=observed,mask=None)
                if fill and state['fill']==1:row['mask']=_polygon(path).intersection(state['clip'])
                rows.append(row)
            if pending_clip:state['clip']=state['clip'].intersection(_polygon(path))
            path=[];pending_clip=False
        else:raise ContractError('PAINT_OPERATOR','Unsupported operator '+op)
    require(not stack and not path and not pending_clip and len(rows)==len(extracted['paths']),
            'PAINT_PATH_BINDING','Incomplete graphics stream')
    return rows


def painted_geometry(row, arrow=False):
    """Construct width in user space, then transform; never scalar-scale width."""
    from shapely.geometry import LineString
    path=row['path'];matrix=path['transform'];commands=path['commands']
    if arrow:
        require(path['fill'] and row['fill']==0,'PAINT_ARROW','Black filled arrow required')
        geometry=_transform(_polygon(commands),_inverse(matrix))
    else:
        require(path['stroke'] and not path['fill'] and len(commands)==2 and
                [c[0] for c in commands]==['m','l'],'PAINT_LINE','One stroked segment required')
        geometry=LineString([point(_inverse(matrix),c[1]) for c in commands])
        require(geometry.length>0,'PAINT_LINE','Zero length stroke')
    if path['stroke']:
        require(row['width']>0,'PAINT_HAIRLINE','Device-dependent zero-width hairline')
        require(row['cap']==0 and row['join']==0 and row['miter']>=1 and not row['dash'][0],
                'PAINT_STROKE_STYLE','Only solid butt/miter strokes supported')
        if arrow:
            vertices=list(geometry.exterior.coords)[:-1]
            for i,v in enumerate(vertices):
                u,w=vertices[i-1],vertices[(i+1)%len(vertices)]
                cosine=sum((u[j]-v[j])*(w[j]-v[j]) for j in (0,1))/(math.dist(u,v)*math.dist(w,v))
                require(1-cosine>0 and math.sqrt(2/(1-cosine))<=row['miter'],
                        'PAINT_MITER_LIMIT','Miter fallback geometry is outside this adapter')
        geometry=geometry.buffer(row['width']/2,cap_style=2,join_style=2,mitre_limit=row['miter'])
    return _transform(geometry,matrix)


def visible_geometry(row, rows, arrow=False):
    raw=painted_geometry(row,arrow);visible=raw.intersection(row['clip']);masks=[]
    clipped_area=visible.area
    for later in rows[row['index']+1:]:
        mask=later['mask']
        if mask is not None and visible.intersection(mask).area>0:
            visible=visible.difference(mask);masks.append(later['index'])
    return raw,visible,dict(clipRemovedArea=max(0,raw.area-clipped_area),
                           maskRemovedArea=max(0,clipped_area-visible.area),whiteMaskPaths=masks)


def study_visible_strokes(primitives, extracted, pdf_bytes):
    from shapely.geometry import box
    from shapely.ops import unary_union
    check_seal(primitives);check_seal(extracted)
    require(primitives.get('status')=='PRIMITIVES_OBSERVED' and extracted.get('status')=='ACTUAL_INK_BOUND' and
            'frameAlignment' in extracted and primitives.get('inkDigest')==extracted['digest'],
            'PAINT_EVIDENCE','Matched frame-aligned ink and primitive evidence required')
    rows=read_paint_states(pdf_bytes,extracted);results=[];tolerance=primitives['comparisonToleranceNative']
    for sample in primitives['samples']:
        try:
            bounds=unary_union([box(*extracted['glyphs'][i]['inkBox']) for i in sample['ink']['glyphIndices']
                                if extracted['glyphs'][i]['inkBox'] is not None])
            occupied=box(*sample['ink']['occupancyBox']);items=[];used=set()
            groups={}
            for index,line in enumerate(sample['lines']):
                key=tuple(sorted(tuple(p) for p in line));groups.setdefault(key,[]).append(index)
            for line,indices in groups.items():
                candidates=[]
                for row in rows:
                    p=row['path'];c=p['commands']
                    if p['stroke'] and not p['fill'] and len(c)==2 and [v[0] for v in c]==['m','l']:
                        endpoints=[c[0][1],c[1][1]]
                        if any(all(math.dist(a,b)<=tolerance for a,b in zip(line,order))
                               for order in (endpoints,endpoints[::-1])):candidates.append(row)
                require(len(candidates)==len(indices),'PAINT_LINE_BINDING','Native/PDF line multiplicity differs')
                reference=sorted(c[1] for c in candidates[0]['path']['commands'])
                require(all(all(math.dist(a,b)<1e-6 for a,b in zip(reference,sorted(c[1] for c in r['path']['commands'])))
                            for r in candidates),'PAINT_LINE_BINDING','Near but distinct PDF strokes are ambiguous')
                for row in candidates:
                    require(row['index'] not in used,'PAINT_LINE_BINDING','PDF stroke reused')
                    used.add(row['index']);items.append(('line',indices,row))
            for index,arrow in enumerate(sample['arrows']):items.append(('arrow',[index],rows[arrow['pdfPath']]))
            measurements=[]
            for kind,indices,row in items:
                raw,visible,occlusion=visible_geometry(row,rows,kind=='arrow')
                measurements.append(dict(kind=kind,nativeIndices=indices,pdfPath=row['index'],
                    pdfUserLineWidth=row['width'] if row['path']['stroke'] else None,
                    strokeTransform=row['path']['transform'],rawArea=raw.area,visibleArea=visible.area,
                    removedAreaFraction=max(0,1-visible.area/raw.area),
                    rawGlyphBoundsDistance=raw.distance(bounds),
                    visibleGlyphBoundsDistance=None if visible.is_empty else visible.distance(bounds),
                    visibleOccupancyDistance=None if visible.is_empty else visible.distance(occupied),
                    visibleGeometry=json.loads(json.dumps(visible.__geo_interface__)),**occlusion))
            results.append(dict(key=sample['key'],status='VISIBLE_STROKES_MEASURED',measurements=measurements,
                                rawNativeIntersections=sample['textLineIntersections']))
        except ContractError as error:results.append(dict(key=sample['key'],status='INPUT_BLOCKED',error=str(error)))
    return seal(dict(schemaVersion='VisibleStrokeStudy.v1',productionEligible=False,
                     status='VISIBLE_STROKES_MEASURED' if all(s['status']=='VISIBLE_STROKES_MEASURED' for s in results) else 'INPUT_BLOCKED',
                     scope='OPAQUE_PDF_STROKES_AND_ARROWS_VS_GLYPH_BOUNDS_NOT_EXACT_GLYPH_COLLISIONS',
                     primitiveDigest=primitives['digest'],inkDigest=extracted['digest'],pdfSha256=extracted['pdfSha256'],
                     paintPathCount=len(rows),whiteMaskCount=sum(r['mask'] is not None for r in rows),samples=results))
