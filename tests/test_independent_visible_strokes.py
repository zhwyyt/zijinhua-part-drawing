"""Real PDF operator streams exercise paint order, widths and clipping."""
import copy
import hashlib
import io
import unittest

from test_independent_glyph_occupancy import pdf_fixture
from zijinhua_tekla.part_drawing.independent_layout.contracts import ContractError, seal
from zijinhua_tekla.part_drawing.independent_layout.glyph_occupancy import extract_pdf_glyphs
from zijinhua_tekla.part_drawing.independent_layout.visible_strokes import (
    painted_geometry, read_paint_states, study_visible_strokes, visible_geometry)


def paint_fixture(content, ext_state=None):
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, FloatObject
    data,receipt=pdf_fixture();writer=PdfWriter();writer.clone_document_from_reader(PdfReader(io.BytesIO(data)))
    page=writer.pages[0];stream=DecodedStreamObject();stream.set_data(content.encode('ascii'))
    page[NameObject('/Contents')]=writer._add_object(stream)
    if ext_state:
        page['/Resources'][NameObject('/ExtGState')]=DictionaryObject({NameObject('/GS'):DictionaryObject(
            {NameObject(k):FloatObject(v) for k,v in ext_state.items()})})
    output=io.BytesIO();writer.write(output);data=output.getvalue();receipt['pdfSha256']=hashlib.sha256(data).hexdigest()
    return data,extract_pdf_glyphs(data,receipt)


def fixture_reports(content, lines):
    data,ink=paint_fixture(content+' BT /F1 10 Tf 1 0 0 1 10 10 Tm <0001> Tj ET')
    ink=seal(dict(ink,status='ACTUAL_INK_BOUND',frameAlignment={'fixture':True}))
    primitives=seal(dict(status='PRIMITIVES_OBSERVED',inkDigest=ink['digest'],comparisonToleranceNative=.1,
        samples=[dict(key='fixture',lines=lines,arrows=[],textLineIntersections=[],
                      ink=dict(glyphIndices=[0],occupancyBox=[10,10,20,20]))]))
    return data,ink,primitives


class VisibleStrokeTests(unittest.TestCase):
    def test_later_white_mask_removes_stroke_but_earlier_mask_does_not(self):
        line='2 w 0 0 m 10 0 l S '
        mask='q 1 g 4 -2 2 4 re f Q '
        for content,index,area in ((line+mask,0,16),(mask+line,1,20)):
            data,ink=paint_fixture(content);rows=read_paint_states(data,ink)
            raw,visible,details=visible_geometry(rows[index],rows)
            # The page clips the lower half at y=0.
            self.assertAlmostEqual(20,raw.area);self.assertAlmostEqual(area/2,visible.area)
            self.assertAlmostEqual(10,details['clipRemovedArea'])
            self.assertAlmostEqual(2 if index==0 else 0,details['maskRemovedArea'])

    def test_stroke_width_detects_collision_missed_by_centerline(self):
        from shapely.geometry import LineString, box
        data,ink=paint_fixture('4 w 0 10 m 10 10 l S');rows=read_paint_states(data,ink)
        obstacle=box(3,11,7,13)
        self.assertEqual(1,LineString([(0,10),(10,10)]).distance(obstacle))
        self.assertEqual(0,painted_geometry(rows[0]).distance(obstacle))

    def test_nonuniform_transform_scales_width_normal_to_segment(self):
        data,ink=paint_fixture('2 0 0 3 0 0 cm 2 w 0 10 m 10 10 l S')
        row=read_paint_states(data,ink)[0];geometry=painted_geometry(row)
        self.assertAlmostEqual(2,row['width'])
        for a,b in zip(geometry.bounds,(0,27,20,33)):self.assertAlmostEqual(a,b)
        self.assertAlmostEqual(120,geometry.area)

    def test_width_is_transformed_at_paint_not_at_width_operator(self):
        data,ink=paint_fixture('2 w 2 0 0 3 0 0 cm 0 10 m 10 10 l S')
        self.assertAlmostEqual(120,painted_geometry(read_paint_states(data,ink)[0]).area)

    def test_clip_restored_by_q_and_Q(self):
        data,ink=paint_fixture('2 w q 0 0 5 20 re W n 0 10 m 10 10 l S Q 0 15 m 10 15 l S')
        rows=read_paint_states(data,ink)
        self.assertAlmostEqual(10,visible_geometry(rows[0],rows)[1].area)
        self.assertAlmostEqual(20,visible_geometry(rows[1],rows)[1].area)

    def test_pending_clip_applies_after_current_path_is_painted(self):
        data,ink=paint_fixture('2 w 10 10 10 10 re W S 0 15 m 30 15 l S')
        rows=read_paint_states(data,ink)
        self.assertAlmostEqual(10000,rows[0]['clip'].area)
        self.assertAlmostEqual(100,rows[1]['clip'].area)
        self.assertAlmostEqual(20,visible_geometry(rows[1],rows)[1].area)

    def test_white_mask_itself_is_clipped(self):
        data,ink=paint_fixture('2 w 0 10 m 10 10 l S q 0 0 5 20 re W* n 1 g 0 0 10 20 re f Q')
        rows=read_paint_states(data,ink)
        self.assertAlmostEqual(10,visible_geometry(rows[0],rows)[1].area)

    def test_filled_arrow_and_outline_width_are_both_counted(self):
        data,ink=paint_fixture('2 w 10 10 m 20 10 l 15 20 l h B')
        row=read_paint_states(data,ink)[0];geometry=painted_geometry(row,True)
        self.assertGreater(geometry.area,50);self.assertLess(geometry.bounds[0],10)

    def test_hairline_dash_and_round_cap_are_explicitly_blocked(self):
        for style in ('0 w','1 w [2 1] 0 d','1 w 1 J','1 w 2 j'):
            with self.subTest(style=style):
                data,ink=paint_fixture(style+' 0 10 m 10 10 l S')
                with self.assertRaises(ContractError):painted_geometry(read_paint_states(data,ink)[0])

    def test_arrow_mask_loss_is_preserved_and_miter_fallback_is_blocked(self):
        data,ink=paint_fixture('10 10 m 20 10 l 15 20 l h f q 1 g 0 15 30 15 re f Q')
        rows=read_paint_states(data,ink);raw,visible,details=visible_geometry(rows[0],rows,True)
        self.assertAlmostEqual(50,raw.area);self.assertAlmostEqual(37.5,visible.area)
        self.assertAlmostEqual(12.5,details['maskRemovedArea'])
        data,ink=paint_fixture('2 w 1 M 10 10 m 20 10 l 15 20 l h B')
        with self.assertRaisesRegex(ContractError,'PAINT_MITER_LIMIT'):
            painted_geometry(read_paint_states(data,ink)[0],True)

    def test_alpha_unknown_operator_white_text_and_color_are_blocked(self):
        for content,ext in (('/GS gs 1 w 0 10 m 10 10 l S',{'/ca':.5}),
                            ('/GS gs 1 w 0 10 m 10 10 l S',{'/OP':1}),
                            ('0 1 0 rg',None),('BX EX',None),
                            ('1 g BT /F1 10 Tf <0001> Tj ET',None)):
            with self.subTest(content=content):
                data,ink=paint_fixture(content,ext)
                with self.assertRaises(ContractError):read_paint_states(data,ink)

    def test_hash_geometry_and_transform_tampering_rejected(self):
        data,ink=paint_fixture('1 w 0 10 m 10 10 l S')
        for kind in ('hash','geometry','matrix'):
            changed=copy.deepcopy(ink)
            if kind=='hash':changed['pdfSha256']='bad'
            elif kind=='geometry':changed['paths'][0]['commands'][0][1][0]+=1
            else:changed['paths'][0]['transform'][4]+=1
            with self.subTest(kind=kind),self.assertRaises(ContractError):read_paint_states(data,changed)

    def test_duplicate_native_lines_require_equal_pdf_multiplicity(self):
        line=[[0,10],[30,10]];data,ink,primitives=fixture_reports('1 w 0 10 m 30 10 l S 0 10 m 30 10 l S',[line,line])
        result=study_visible_strokes(primitives,ink,data)
        self.assertEqual('VISIBLE_STROKES_MEASURED',result['status'])
        self.assertEqual([[0,1],[0,1]],[m['nativeIndices'] for m in result['samples'][0]['measurements']])
        primitives['samples'][0]['lines'].pop();primitives.pop('digest');primitives=seal(primitives)
        self.assertEqual('INPUT_BLOCKED',study_visible_strokes(primitives,ink,data)['status'])

    def test_missing_pdf_line_is_blocked(self):
        data,ink,primitives=fixture_reports('1 w 0 10 m 30 10 l S',[[[0,20],[30,20]]])
        self.assertEqual('INPUT_BLOCKED',study_visible_strokes(primitives,ink,data)['status'])

    def test_near_distinct_strokes_cannot_impersonate_duplicate(self):
        line=[[0,10],[30,10]]
        data,ink,primitives=fixture_reports('1 w 0 10 m 30 10 l S 0 10.01 m 30 10.01 l S',[line,line])
        self.assertEqual('INPUT_BLOCKED',study_visible_strokes(primitives,ink,data)['status'])

    def test_measurement_does_not_claim_layout_or_exact_glyph_collision(self):
        data,ink,primitives=fixture_reports('4 w 0 9 m 30 9 l S',[[[0,9],[30,9]]])
        result=study_visible_strokes(primitives,ink,data)
        self.assertEqual('VISIBLE_STROKES_MEASURED',result['status']);self.assertFalse(result['productionEligible'])
        self.assertIn('NOT_EXACT_GLYPH_COLLISIONS',result['scope'])
        self.assertEqual(0,result['samples'][0]['measurements'][0]['visibleGlyphBoundsDistance'])
        primitives['inkDigest']='wrong';primitives.pop('digest')
        with self.assertRaisesRegex(ContractError,'PAINT_EVIDENCE'):study_visible_strokes(seal(primitives),ink,data)

    def test_fully_masked_stroke_has_no_distance(self):
        data,ink,primitives=fixture_reports('1 w 0 10 m 30 10 l S q 1 g 0 0 30 30 re f Q',[[[0,10],[30,10]]])
        result=study_visible_strokes(primitives,ink,data)
        measurement=result['samples'][0]['measurements'][0]
        self.assertEqual(0,measurement['visibleArea']);self.assertIsNone(measurement['visibleGlyphBoundsDistance'])


if __name__=='__main__':unittest.main()
