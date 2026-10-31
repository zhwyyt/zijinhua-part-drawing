"""Actual embedded-font ink in source-sheet coordinates, never font-size heuristics.

This readback adapter measures a finite PDF observation. It does not predict a
candidate's unrendered text or certify a production CalibrationProfile.
"""
import hashlib
import io
import math
import re
from importlib.metadata import version

from .contracts import ContractError, digest, require, seal
from .foundation_studies import _nodes


def affine(values):
    require(isinstance(values, (list, tuple)) and len(values)==6 and all(
        type(v) in (int,float) and math.isfinite(v) for v in values), 'INK_MATRIX', 'Finite affine transform required')
    a,b,c,d,_,_=values
    require(abs(a*d-b*c)>1e-15, 'INK_MATRIX', 'Singular transform')
    return tuple(values)


def compose(outer, inner):
    a,b,c,d,e,f=affine(outer);g,h,i,j,k,l=affine(inner)
    return affine((a*g+c*h,b*g+d*h,a*i+c*j,b*i+d*j,a*k+c*l+e,b*k+d*l+f))


def point(matrix, xy):
    a,b,c,d,e,f=matrix;x,y=xy
    return [a*x+c*y+e,b*x+d*y+f]


def union(boxes):
    boxes=[b for b in boxes if b is not None]
    require(bool(boxes), 'INK_EMPTY', 'No measured geometry')
    return [min(b[0] for b in boxes),min(b[1] for b in boxes),max(b[2] for b in boxes),max(b[3] for b in boxes)]


def outline_bounds(font, gid, matrix):
    """Transform the curves first; BoundsPen solves extrema of transformed curves."""
    from fontTools.pens.boundsPen import BoundsPen
    from fontTools.pens.transformPen import TransformPen
    require(type(gid) is int and 0<gid<len(font.getGlyphOrder()), 'INK_MISSING_GLYPH', 'Missing/out-of-range GID')
    glyphs=font.getGlyphSet();pen=BoundsPen(glyphs)
    glyphs[font.getGlyphName(gid)].draw(TransformPen(pen,affine(matrix)))
    return list(pen.bounds) if pen.bounds is not None else None


def export_transform(receipt, media_size):
    """The observed DPM export contract: zero target origin/offset, uniform scale."""
    number=r'([-+0-9.eE]+)'
    pattern=rf'Source:{number}x{number} \({number}x{number}\) Target:0x0 \({number}x{number}\) Offset:Vector2\{{X:0 Y:0\}}'
    matches=re.findall(pattern,receipt['stdout'])
    require(len(matches)==1 and receipt['exitCode']==0, 'INK_EXPORT_MAPPING', 'Unknown/ambiguous DPM transform')
    sx,sy,sw,sh,tw,th=map(float,matches[0])
    require(all(math.isfinite(x) for x in (sx,sy,sw,sh,tw,th)) and min(sw,sh,tw,th)>0,
            'INK_EXPORT_MAPPING', 'Invalid page extents')
    scale=tw/sw*72/25.4
    require(abs(tw/sw-th/sh)<1e-9 and len(media_size)==2 and
            all(abs(v-t*72/25.4)<.02 for v,t in zip(media_size,(tw,th))),
            'INK_EXPORT_MAPPING', 'Nonuniform scale or PDF page differs')
    return affine([1/scale,0,0,1/scale,sx,sy])


def extract_pdf_glyphs(pdf_bytes, receipt):
    """Keep unsupported glyphs as explicit failures, including outside sample scope."""
    from fontTools.ttLib import TTFont
    from pypdf import PdfReader
    from pdfminer.converter import PDFPageAggregator
    from pdfminer.pdfinterp import PDFResourceManager, PDFPageInterpreter
    from pdfminer.pdfpage import PDFPage
    from pdfminer.pdffont import PDFUnicodeNotDefined
    require(hashlib.sha256(pdf_bytes).hexdigest()==receipt['pdfSha256'], 'INK_PDF_HASH', 'PDF differs from export receipt')
    pdf=PdfReader(io.BytesIO(pdf_bytes))
    require(len(pdf.pages)==1, 'INK_PDF_PAGE', 'One source sheet required')
    page=pdf.pages[0]
    require(not page['/Resources'].get('/XObject'), 'INK_PDF_RESOURCES', 'Nested form/image resources are outside this vector-text adapter')
    require(page.get('/Rotate',0)==0 and page.get('/UserUnit',1)==1 and
            float(page.mediabox.left)==float(page.mediabox.bottom)==0, 'INK_PDF_PAGE', 'Unsupported PDF page transform')
    transform=export_transform(receipt,[float(page.mediabox.width),float(page.mediabox.height)])
    fonts={}
    for reference in page['/Resources']['/Font'].values():
        resource=reference.get_object();name=str(resource['/BaseFont'])[1:]
        require(name not in fonts, 'INK_FONT_AMBIGUOUS', name)
        try:
            require(resource['/Subtype']=='/Type0' and resource['/Encoding']=='/Identity-H' and '/ToUnicode' in resource,
                    'INK_FONT_ENCODING', name)
            descendants=resource['/DescendantFonts'];require(len(descendants)==1,'INK_FONT_ENCODING',name)
            child=descendants[0].get_object()
            require(child['/Subtype']=='/CIDFontType2' and child.get('/CIDToGIDMap','/Identity')=='/Identity', 'INK_GID_MAPPING', name)
            descriptor=child['/FontDescriptor'].get_object()
            require('/FontFile2' in descriptor, 'INK_FONT_EMBEDDING', name)
            raw=descriptor['/FontFile2'].get_data();font=TTFont(io.BytesIO(raw))
            require('glyf' in font and font['head'].unitsPerEm>0, 'INK_FONT_OUTLINES', name)
            fonts[name]=dict(font=font,sha256=hashlib.sha256(raw).hexdigest())
        except ContractError as error:
            fonts[name]=dict(error=str(error))

    class Collector(PDFPageAggregator):
        def __init__(self, manager):
            super().__init__(manager);self.glyphs=[];self.paths=[];self.mode=0

        def paint_path(self,graphicstate,stroke,fill,evenodd,path):
            mapping=compose(transform,self.ctm);commands=[]
            for command in path:
                coordinates=[point(mapping,command[i:i+2]) for i in range(1,len(command),2)]
                commands.append([command[0],*coordinates])
            self.paths.append(dict(index=len(self.paths),stroke=stroke,fill=fill,evenodd=evenodd,
                                   lineWidth=graphicstate.linewidth,transform=list(mapping),commands=commands))
            return super().paint_path(graphicstate,stroke,fill,evenodd,path)

        def render_string(self,textstate,seq,ncs,graphicstate):
            self.mode=textstate.render
            return super().render_string(textstate,seq,ncs,graphicstate)

        def render_char(self,matrix,font,fontsize,scaling,rise,cid,ncs,graphicstate):
            advance=super().render_char(matrix,font,fontsize,scaling,rise,cid,ncs,graphicstate)
            name=font.fontname.decode('utf-8') if isinstance(font.fontname,bytes) else font.fontname
            row=dict(index=len(self.glyphs),cid=cid,font=name,baseline=point(transform,[matrix[4],matrix[5]]),
                     matrix=list(matrix),fontSize=fontsize,horizontalScaling=scaling,rise=rise,inkBox=None)
            try:
                require(self.mode==0 and not font.is_vertical(), 'INK_RENDER_MODE', 'Only horizontal fill text supported')
                row['text']=font.to_unichr(cid)
                info=fonts.get(name,{})
                require('font' in info, 'INK_FONT_UNSUPPORTED', info.get('error',name))
                row['fontSha256']=info['sha256'];em=info['font']['head'].unitsPerEm
                glyph_matrix=compose(transform,compose(matrix,[fontsize*scaling/em,0,0,fontsize/em,0,rise]))
                row['inkBox']=outline_bounds(info['font'],cid,glyph_matrix)
                require(row['inkBox'] is not None or row['text']==' ', 'INK_EMPTY_GLYPH', 'Non-space without outline')
                row['glyphToSource']=list(glyph_matrix)
            except (ContractError, KeyError, PDFUnicodeNotDefined) as error:
                row['error']=str(error)
            self.glyphs.append(row)
            return advance

    manager=PDFResourceManager();device=Collector(manager);interpreter=PDFPageInterpreter(manager,device)
    for native_page in PDFPage.get_pages(io.BytesIO(pdf_bytes)):
        interpreter.process_page(native_page)
    return dict(pdfSha256=receipt['pdfSha256'],pdfToSource=list(transform),glyphs=device.glyphs,paths=device.paths,
                dependencies={name:version(name) for name in ('fonttools','pypdf','pdfminer.six')},
                fonts={name:{k:v for k,v in info.items() if k!='font'} for name,info in fonts.items()})


def align_to_native_frame(capture, extracted):
    """Remove DPM matrix serialization drift using independent long frame edges.

    Only positive axis scale/translation is fitted. Both inner/outer frame edges
    must agree; text, arrows and the sample plan never enter the fit.
    """
    page=capture['page'];size=page['drawing']['Layout']['SheetSize'];width,height=size['Width'],size['Height']
    native=[]
    for view in page['presentation']:
        if view.get('Position',{}).get('X')!=0 or view.get('Position',{}).get('Y')!=0:continue
        for segment in view['Primitives']:
            if segment.get('ObjectType')!=263:continue
            for line in _nodes(segment,'.LinePrimitive'):
                a,b=([line[k][v] for v in ('X','Y')] for k in ('StartPoint','EndPoint'))
                if (a[1]==b[1] and abs(a[0]-b[0])>width*.8) or (a[0]==b[0] and abs(a[1]-b[1])>height*.8):
                    key=tuple(sorted((tuple(a),tuple(b))))
                    if key not in native:native.append(key)
    require(len(native)>=8,'INK_FRAME_MAPPING','Inner/outer frame edges required')
    paths={}
    for path in extracted['paths']:
        commands=path['commands']
        if path['stroke'] and len(commands)==2 and commands[0][0]=='m' and commands[1][0]=='l':
            key=tuple(sorted((tuple(commands[0][1]),tuple(commands[1][1]))))
            paths.setdefault(key,[]).append(path['index'])
    pairs=[];matched=[];used=set();search=max(width,height)*1e-4
    for edge in native:
        choices=[(observed,ids) for observed,ids in paths.items() if all(math.dist(a,b)<=search for a,b in zip(edge,observed))]
        require(len(choices)==1,'INK_FRAME_MAPPING','Missing/ambiguous PDF frame edge')
        observed,ids=choices[0]
        require(observed not in used,'INK_FRAME_MAPPING','PDF edge reused');used.add(observed)
        pairs.extend(zip(observed,edge));matched.append(dict(native=[list(p) for p in edge],pdfNominal=[list(p) for p in observed],pdfPaths=ids))
    def fit(axis):
        xs=[a[axis] for a,b in pairs];ys=[b[axis] for a,b in pairs];mx=sum(xs)/len(xs);my=sum(ys)/len(ys)
        variance=sum((x-mx)**2 for x in xs)
        require(variance>0,'INK_FRAME_MAPPING','Degenerate frame')
        scale=sum((x-mx)*(y-my) for x,y in zip(xs,ys))/variance
        require(abs(scale-1)<=1e-4,'INK_FRAME_MAPPING','Unexpected scale change')
        return scale,my-scale*mx
    ax,tx=fit(0);ay,ty=fit(1);correction=affine([ax,0,0,ay,tx,ty])
    residual=max(math.dist(point(correction,a),b) for a,b in pairs)
    require(residual<=.01,'INK_FRAME_MAPPING','Frame residual exceeds independent comparison budget')
    nominal=list(extracted['pdfToSource']);extracted['pdfToSource']=list(compose(correction,nominal))
    for g in extracted['glyphs']:
        g['baseline']=point(correction,g['baseline'])
        if g['inkBox'] is not None:
            b=g['inkBox'];g['inkBox']=[*point(correction,b[:2]),*point(correction,b[2:])]
        if 'glyphToSource' in g:g['glyphToSource']=list(compose(correction,g['glyphToSource']))
    for path in extracted['paths']:
        path['commands']=[[c[0],*[point(correction,p) for p in c[1:]]] for c in path['commands']]
        path['transform']=list(compose(correction,path['transform']))
    extracted['frameAlignment']=dict(nominalPdfToSource=nominal,correction=list(correction),matches=matched,
                                     maximumNominalResidual=max(math.dist(a,b) for a,b in pairs),
                                     maximumCorrectedResidual=residual,comparisonBudgetNative=.01)
    return extracted


def bind_ink(sample, segment, glyphs):
    """ID first, then geometric/full-string binding; ambiguity never resolves by text alone."""
    require(segment.get('Id')==sample['objectId'], 'INK_OBJECT_ID', 'Different native object')
    texts=list(_nodes(segment,'.TextPrimitive'))
    require(len(texts)==1,'INK_NATIVE_TEXT','Expected one native text run')
    text=texts[0];height=sample['height']
    require(type(height) in (int,float) and math.isfinite(height) and height>0 and
            all(text[k]==sample[v] for k,v in (('Text','text'),('Font','font'),('Height','height'))),
            'INK_NATIVE_STYLE','Observed text/font/height differs')
    angle=text['Angle'];require(type(angle) in (int,float) and math.isfinite(angle),'INK_NATIVE_STYLE','Invalid angle')
    require(abs(math.atan2(math.sin(angle-math.radians(sample.get('angle',0))),
                           math.cos(angle-math.radians(sample.get('angle',0)))))<1e-5,
            'INK_NATIVE_STYLE','Native radians differ from SDK degrees')
    def text_containers(node):
        if isinstance(node,dict):
            if any(p.get('type','').endswith('.TextPrimitive') for p in node.get('Primitives',[])):
                yield node
            for value in node.values():
                yield from text_containers(value)
        elif isinstance(node,list):
            for item in node:yield from text_containers(item)
    containers=list(text_containers(segment))
    require(len(containers)==1,'INK_NATIVE_TEXT','Ambiguous text container')
    polygons=[p for p in containers[0]['Primitives'] if p['type'].endswith('.PolygonPrimitive')]
    require(len(polygons)==1 and not polygons[0]['InnerLoops'], 'INK_MASK', 'Single native mask required')
    edges=polygons[0]['OuterLoop']['Segments']
    require(len(edges)==4 and all(e['type'].endswith('.LinePrimitive') and
            all(e['EndPoint'][k]==edges[(i+1)%4]['StartPoint'][k] for k in ('X','Y')) for i,e in enumerate(edges)),
            'INK_MASK','Closed four-line mask required')
    coords=[[e['StartPoint'][k] for k in ('X','Y')] for e in edges]
    require(all(type(v) in (int,float) and math.isfinite(v) for p in coords for v in p),'INK_MASK','Invalid coordinates')
    mask=union([[x,y,x,y] for x,y in coords])
    # Native Position is a baseline origin; its font-dependent PDF baseline shift
    # is bounded here for association only, never used as a collision allowance.
    c,s=math.cos(angle),math.sin(angle);origin=[text['Position'][k] for k in ('X','Y')]
    require(all(type(v) in (int,float) and math.isfinite(v) for v in origin),'INK_NATIVE_STYLE','Invalid position')
    def local(p):
        x,y=p[0]-origin[0],p[1]-origin[1];return [c*x+s*y,-s*x+c*y]
    local_mask=[local(p) for p in coords];lo=min(p[0] for p in local_mask)-height*.1;hi=max(p[0] for p in local_mask)+height*.1
    selected=[g for g in glyphs if lo<=local(g['baseline'])[0]<=hi and abs(local(g['baseline'])[1])<=height*.6]
    require(bool(selected), 'INK_BINDING', 'No PDF glyphs at native position')
    selected.sort(key=lambda g:local(g['baseline'])[0])
    require(all('error' not in g for g in selected),'INK_GLYPH_FAILURE','Unsupported or missing glyph in native text region')
    require(''.join(g.get('text','') for g in selected)==sample['text'], 'INK_BINDING', 'Incomplete/ambiguous PDF string')
    require(all(re.sub(r'^[A-Z]{6}\+','',g['font'])==sample['font'] for g in selected), 'INK_FONT_SUBSTITUTION','Requested font was substituted')
    require(all(abs(math.atan2(g['glyphToSource'][1],g['glyphToSource'][0])-angle)<1e-4 for g in selected),
            'INK_DIRECTION','PDF glyph direction differs')
    ink=union([g['inkBox'] for g in selected]);occupied=union([mask,ink])
    sdk=sample['box'];require(len(sdk)==4 and all(type(v) in (int,float) and math.isfinite(v) for v in sdk) and
                            sdk[0]<sdk[2] and sdk[1]<sdk[3], 'INK_SDK_BOX','Invalid auxiliary SDK box')
    overflow=[max(0,sdk[i]-ink[i]) if i<2 else max(0,ink[i]-sdk[i]) for i in range(4)]
    return dict(key=sample['key'],objectId=sample['objectId'],text=sample['text'],font=sample['font'],height=height,
                angleDegrees=sample.get('angle',0),inkBox=ink,maskBox=mask,occupancyBox=occupied,sdkBox=sdk,
                overflowSdk=overflow,glyphIndices=[g['index'] for g in selected],
                fontHashes=sorted({g['fontSha256'] for g in selected}),status='ACTUAL_INK_BOUND')


def study_glyph_occupancy(capture, pdf_bytes, receipt, align_frame=False, export_page=None):
    if align_frame:
        require(export_page is not None and digest(export_page)==digest(capture['page']),
                'INK_EXPORT_PAGE','Export snapshot must match the native capture exactly')
    extracted=extract_pdf_glyphs(pdf_bytes,receipt);samples=capture['samples']
    if align_frame:align_to_native_frame(capture,extracted)
    require(samples and len({s['objectId'] for s in samples})==len(samples), 'INK_SAMPLE_IDS','Missing/duplicate IDs')
    segments=[s for v in capture['page']['presentation'] if v.get('Position',{}).get('X')==0 and v.get('Position',{}).get('Y')==0
              for s in v.get('Primitives',[])]
    rows=[];claimed=set()
    for sample in samples:
        try:
            matches=[s for s in segments if s.get('Id')==sample['objectId']]
            require(len(matches)==1,'INK_OBJECT_ID','Missing/ambiguous native segment')
            row=bind_ink(sample,matches[0],extracted['glyphs'])
            require(not claimed.intersection(row['glyphIndices']),'INK_REUSED_GLYPH','PDF glyph already bound to another object')
            claimed.update(row['glyphIndices']);rows.append(row)
        except ContractError as error:
            rows.append(dict(key=sample['key'],objectId=sample['objectId'],status='INPUT_BLOCKED',error=str(error)))
    matched=all(r['status']=='ACTUAL_INK_BOUND' for r in rows)
    status=('ACTUAL_INK_BOUND' if align_frame else 'NOMINAL_INK_DIAGNOSTIC') if matched else 'INPUT_BLOCKED'
    return seal(dict(schemaVersion='GlyphOccupancyStudy.v1',status=status,
                     productionEligible=False,scope='OBSERVED_PDF_INK_AND_NATIVE_MASK_ONLY',captureDigest=digest(capture),
                     exportDigest=digest(receipt),exportPageDigest=digest(export_page) if export_page is not None else None,samples=rows,**extracted))
