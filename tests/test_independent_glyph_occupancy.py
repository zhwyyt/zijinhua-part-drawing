"""Real TrueType curves and embedded PDF streams exercise the readback boundary."""
import copy
import hashlib
import io
import math
import unittest

from zijinhua_tekla.part_drawing.independent_layout.contracts import ContractError
from zijinhua_tekla.part_drawing.independent_layout.glyph_occupancy import (
    affine, align_to_native_frame, bind_ink, extract_pdf_glyphs, outline_bounds, study_glyph_occupancy)


def font_fixture():
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    from fontTools.ttLib import TTFont
    builder=FontBuilder(1000,isTTF=True);order=['.notdef','A','space','B']
    builder.setupGlyphOrder(order);glyphs={}
    for name in order:
        pen=TTGlyphPen(None)
        if name!='space':
            pen.moveTo((0,0));pen.qCurveTo((500,1000),(1000,0));pen.closePath()
        glyphs[name]=pen.glyph()
    builder.setupCharacterMap({65:'A',32:'space',66:'B'});builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics({name:(500 if name=='space' else 1000,0) for name in order})
    builder.setupHorizontalHeader(ascent=1000,descent=0)
    builder.setupNameTable({'familyName':'Fixture','styleName':'Regular','uniqueFontIdentifier':'Fixture','fullName':'Fixture','psName':'Fixture'})
    builder.setupOS2();builder.setupPost();builder.setupMaxp()
    buffer=io.BytesIO();builder.save(buffer)
    return TTFont(io.BytesIO(buffer.getvalue())),buffer.getvalue()


def pdf_fixture(cid=1, mode=0, encoding='/Identity-H', gid_map='/Identity', embedded=True):
    from pypdf import PdfWriter
    from pypdf.generic import ArrayObject, DictionaryObject, NameObject, NumberObject, DecodedStreamObject, ByteStringObject
    _,raw=font_fixture();writer=PdfWriter();page=writer.add_blank_page(width=100,height=100)
    def dictionary(**entries):return DictionaryObject({NameObject('/'+k):v for k,v in entries.items()})
    def stream(data):
        s=DecodedStreamObject();s.set_data(data);return writer._add_object(s)
    desc=dictionary(Type=NameObject('/FontDescriptor'),FontName=NameObject('/Fixture'),Flags=NumberObject(4),
                    FontBBox=ArrayObject([NumberObject(v) for v in [0,0,1000,1000]]),ItalicAngle=NumberObject(0),
                    Ascent=NumberObject(1000),Descent=NumberObject(0),CapHeight=NumberObject(1000),StemV=NumberObject(80))
    if embedded:desc[NameObject('/FontFile2')]=stream(raw)
    child=dictionary(Type=NameObject('/Font'),Subtype=NameObject('/CIDFontType2'),BaseFont=NameObject('/Fixture'),
                     CIDSystemInfo=dictionary(Registry=ByteStringObject(b'Adobe'),Ordering=ByteStringObject(b'Identity'),Supplement=NumberObject(0)),
                     FontDescriptor=writer._add_object(desc),CIDToGIDMap=NameObject(gid_map),DW=NumberObject(1000))
    cmap=b'/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CMapType 2 def 1 begincodespacerange <0000> <FFFF> endcodespacerange 4 beginbfchar <0000> <0041> <0001> <0041> <0002> <0020> <0003> <0042> endbfchar endcmap CMapName currentdict /CMap defineresource pop end end'
    resource=dictionary(Type=NameObject('/Font'),Subtype=NameObject('/Type0'),BaseFont=NameObject('/Fixture'),
                        Encoding=NameObject(encoding),DescendantFonts=ArrayObject([writer._add_object(child)]),ToUnicode=stream(cmap))
    page[NameObject('/Resources')]=dictionary(Font=dictionary(F1=writer._add_object(resource)))
    page[NameObject('/Contents')]=stream(f'BT /F1 10 Tf {mode} Tr 1 0 0 1 10 10 Tm <{cid:04x}> Tj ET'.encode())
    output=io.BytesIO();writer.write(output);data=output.getvalue()
    mm=100*25.4/72
    receipt=dict(exitCode=0,pdfSha256=hashlib.sha256(data).hexdigest(),stdout=f'Source:0x0 (100x100) Target:0x0 ({mm}x{mm}) Offset:Vector2{{X:0 Y:0}}')
    return data,receipt


def capture_fixture():
    coords=[(10,10),(20,10),(20,20),(10,20)]
    edges=[dict(type='Tekla.LinePrimitive',StartPoint=dict(X=x,Y=y),
                EndPoint=dict(X=coords[(i+1)%4][0],Y=coords[(i+1)%4][1])) for i,(x,y) in enumerate(coords)]
    text=dict(type='Tekla.TextPrimitive',Text='A',Font='Fixture',Height=10,Angle=0,Position=dict(X=10,Y=10))
    mask=dict(type='Tekla.PolygonPrimitive',InnerLoops=[],OuterLoop=dict(Segments=edges))
    segment=dict(Id=7,Primitives=[text,mask])
    sample=dict(key='test',objectId=7,text='A',font='Fixture',height=10,angle=0,box=[10,10,20,14])
    return dict(samples=[sample],page=dict(presentation=[dict(Position=dict(X=0,Y=0),Primitives=[segment])]))


class GlyphOutlineTests(unittest.TestCase):
    def test_transform_curves_before_extrema_not_old_bounding_rectangle(self):
        font,_=font_fixture();r=math.sqrt(.5)
        bounds=outline_bounds(font,1,[r,r,-r,r,5,-7])
        for actual,expected in zip(bounds,[5-125*r,-7,5+1000*r,-7+1125*r]):
            self.assertAlmostEqual(expected,actual,places=7)

    def test_space_has_no_ink_and_notdef_or_invalid_gid_rejected(self):
        font,_=font_fixture();self.assertIsNone(outline_bounds(font,2,[1,0,0,1,0,0]))
        for gid in (0,-1,99,True):
            with self.subTest(gid=gid),self.assertRaises(ContractError):outline_bounds(font,gid,[1,0,0,1,0,0])

    def test_nonfinite_and_singular_affines_rejected(self):
        for matrix in ([1,0,0,0,0,0],[1,0,0,1,float('nan'),0],[1,0,0,1,0,float('inf')],[1,0,0,1,0]):
            with self.subTest(matrix=matrix),self.assertRaises(ContractError):affine(matrix)

    def test_real_pdf_curve_ink_and_native_mask_are_both_preserved(self):
        data,receipt=pdf_fixture();result=study_glyph_occupancy(capture_fixture(),data,receipt)
        self.assertEqual('NOMINAL_INK_DIAGNOSTIC',result['status']);self.assertFalse(result['productionEligible'])
        row=result['samples'][0]
        for actual,expected in zip(row['inkBox'],[10,10,20,15]):self.assertAlmostEqual(expected,actual)
        for actual,expected in zip(row['occupancyBox'],[10,10,20,20]):self.assertAlmostEqual(expected,actual)
        self.assertAlmostEqual(1,row['overflowSdk'][3])

    def test_pdf_hash_and_export_transform_mismatch_reject(self):
        data,receipt=pdf_fixture()
        for kind in ('hash','scale','offset'):
            r=copy.deepcopy(receipt)
            if kind=='hash':r['pdfSha256']='wrong'
            if kind=='scale':r['stdout']=r['stdout'].replace('(100x100)','(100x200)')
            if kind=='offset':r['stdout']=r['stdout'].replace('X:0','X:1')
            with self.subTest(kind=kind),self.assertRaises(ContractError):extract_pdf_glyphs(data,r)

    def test_aligned_study_requires_exact_export_page_snapshot(self):
        data,receipt=pdf_fixture();capture=capture_fixture()
        for page in (None,dict(other='page')):
            with self.subTest(page=page),self.assertRaises(ContractError):study_glyph_occupancy(capture,data,receipt,True,page)

    def test_missing_glyph_stroke_and_unembedded_font_block(self):
        for args in (dict(cid=0),dict(mode=1),dict(mode=3),dict(gid_map='/Other'),dict(embedded=False)):
            with self.subTest(args=args):
                data,receipt=pdf_fixture(**args);report=study_glyph_occupancy(capture_fixture(),data,receipt)
                self.assertEqual('INPUT_BLOCKED',report['status'])

    def test_wrong_native_id_text_style_angle_and_mask_reject(self):
        data,receipt=pdf_fixture();glyphs=extract_pdf_glyphs(data,receipt)['glyphs']
        for mode in ('id','text','font','height','angle','mask','position','sdk'):
            c=capture_fixture();sample=c['samples'][0];seg=c['page']['presentation'][0]['Primitives'][0]
            if mode=='id':sample['objectId']=8
            if mode=='text':sample['text']='B'
            if mode=='font':sample['font']='Other'
            if mode=='height':sample['height']=11
            if mode=='angle':sample['angle']=90
            if mode=='mask':seg['Primitives'][1]['OuterLoop']['Segments'][0]['EndPoint']['X']=19
            if mode=='position':seg['Primitives'][0]['Position']['X']=float('nan')
            if mode=='sdk':sample['box'][2]=float('inf')
            with self.subTest(mode=mode),self.assertRaises(ContractError):bind_ink(sample,seg,glyphs)

    def test_font_substitution_duplicate_glyph_or_wrong_direction_reject(self):
        data,receipt=pdf_fixture();observed=extract_pdf_glyphs(data,receipt)['glyphs'];c=capture_fixture()
        for mode in ('font','duplicate','angle','text'):
            glyphs=copy.deepcopy(observed)
            if mode=='font':glyphs[0]['font']='Substitute'
            if mode=='duplicate':glyphs+=copy.deepcopy(glyphs)
            if mode=='angle':glyphs[0]['glyphToSource'][1]=1
            if mode=='text':glyphs[0]['text']='B'
            with self.subTest(mode=mode),self.assertRaises(ContractError):
                bind_ink(c['samples'][0],c['page']['presentation'][0]['Primitives'][0],glyphs)

    def test_nearby_same_text_not_borrowed_and_duplicate_ids_reject(self):
        data,receipt=pdf_fixture();c=capture_fixture();other=copy.deepcopy(c['samples'][0]);other['objectId']=8
        seg=copy.deepcopy(c['page']['presentation'][0]['Primitives'][0]);seg['Id']=8
        c['samples'].append(other);c['page']['presentation'][0]['Primitives'].append(seg)
        result=study_glyph_occupancy(c,data,receipt)
        self.assertEqual('INPUT_BLOCKED',result['status']);self.assertIn('INK_REUSED_GLYPH',result['samples'][1]['error'])
        other['objectId']=7
        with self.assertRaises(ContractError):study_glyph_occupancy(c,data,receipt)

    def test_translation_and_ninety_degree_binding(self):
        data,receipt=pdf_fixture();glyphs=extract_pdf_glyphs(data,receipt)['glyphs'];c=capture_fixture()
        sample=c['samples'][0];seg=c['page']['presentation'][0]['Primitives'][0]
        sample['angle']=90;sample['box']=[80,10,90,20];text=seg['Primitives'][0]
        text['Angle']=math.pi/2;text['Position']=dict(X=90,Y=10)
        for edge in seg['Primitives'][1]['OuterLoop']['Segments']:
            for name in ('StartPoint','EndPoint'):
                p=edge[name];p['X'],p['Y']=100-p['Y'],p['X']
        g=glyphs[0];g['baseline']=[90,10];g['inkBox']=[85,10,90,20];g['glyphToSource']=[0,.01,-.01,0,90,10]
        row=bind_ink(sample,seg,glyphs);self.assertEqual([80,10,90,20],row['occupancyBox'])

    def test_frame_fit_uses_inner_and_outer_edges_and_rejects_distortion(self):
        lines=[]
        for low,high in ((5,995),(25,975)):
            coords=[(low,low),(high,low),(high,high),(low,high)]
            for i,(x,y) in enumerate(coords):
                end=coords[(i+1)%4];lines.append(dict(type='Tekla.LinePrimitive',StartPoint=dict(X=x,Y=y),EndPoint=dict(X=end[0],Y=end[1])))
        capture=dict(page=dict(drawing=dict(Layout=dict(SheetSize=dict(Width=1000,Height=1000))),
                               presentation=[dict(Position=dict(X=0,Y=0),Primitives=[dict(ObjectType=263,Primitives=lines)])]))
        paths=[]
        for i,line in enumerate(lines):
            coords=[[line[k][v]*1.00001+.002 for v in ('X','Y')] for k in ('StartPoint','EndPoint')]
            paths.append(dict(index=i,stroke=True,commands=[['m',coords[0]],['l',coords[1]]],transform=[1,0,0,1,0,0]))
        extracted=dict(paths=paths,glyphs=[],pdfToSource=[1,0,0,1,0,0])
        result=align_to_native_frame(capture,copy.deepcopy(extracted))
        self.assertLess(result['frameAlignment']['maximumCorrectedResidual'],1e-9)
        for mode in ('missing','nonuniform','ambiguous'):
            changed=copy.deepcopy(extracted)
            if mode=='missing':changed['paths'].pop()
            if mode=='nonuniform':changed['paths'][0]['commands'][0][1][0]+=.08
            if mode=='ambiguous':
                extra=copy.deepcopy(changed['paths'][0]);extra['index']=99
                for cmd in extra['commands']:cmd[1][1]+=.01
                changed['paths'].append(extra)
            with self.subTest(mode=mode),self.assertRaises(ContractError):align_to_native_frame(capture,changed)


if __name__=='__main__':unittest.main()
