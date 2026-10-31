"""Stage-0 failures must remain failures; no native writes or old layout calls."""

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_native_material import plate_with_hole
from zijinhua_tekla.part_drawing.independent_layout.__main__ import main
from zijinhua_tekla.part_drawing.independent_layout.contracts import (
    ContractError, check_seal, digest, read_json, seal,
)
from zijinhua_tekla.part_drawing.independent_layout.sources import freeze_native_sources
from zijinhua_tekla.part_drawing.independent_layout.templates import read_attributes, study_template
from zijinhua_tekla.part_drawing.independent_layout.validation import validate_observation


def template_evidence():
    capture = {'schemaVersion': 'activeLayoutStudy.v1', 'modelPath': 'synthetic-model',
               'partMetadataUnchanged': True, 'workPlaneUnchanged': True,
               'activeIdentityUnchanged': True, 'viewCount': 1,
               'presentation': {'status': 'READ'},
               'drawing': {'Name': '零件图20', 'Mark': 'SYNTHETIC',
                           'Layout': {'SheetSize': {'Width': 5950, 'Height': 4210}}}}
    objects = [{'view': {'Attributes': {'Scale': 1, 'Shortening': {'CutParts': True}},
                         'Width': 1000, 'Height': 500, 'Origin': {'X': 20, 'Y': 30},
                         'FrameOrigin': {'X': 0, 'Y': 0}},
                'objects': [
                    {'index': 1, 'refreshed': True, 'data': {
                        'type': 'Tekla.StraightDimensionSet',
                        'Attributes': {'Text': {'Font': {'Name': 'romsim', 'Height': 50}}}},
                     'children': [{'Value': [{'Font': {'Name': '黑体', 'Height': 3}}]}]},
                    {'index': 2, 'refreshed': True, 'data': {
                        'type': 'Tekla.Mark', 'Attributes': {'Content': [
                            {'Name': 'PART_POS', 'Font': {'Name': 'Arial', 'Height': 50}}]}}}]}]
    presentation = [{'type': 'Tekla.View', 'Primitives': [
        {'type': 'Tekla.TextPrimitive', 'Font': 'romsim', 'Height': 50, 'Text': '100'},
        {'type': 'Tekla.TextPrimitive', 'Font': 'Arial', 'Height': 50, 'Text': 'SYNTHETIC'}]}]
    return capture, objects, presentation


def observation():
    return {'schemaVersion': 'LayoutObservation.v1', 'units': 'NATIVE_SHEET_UNIT',
            'stage': 'READBACK', 'sourceDigest': 'synthetic-evidence', 'minimumClearance': 1,
            'usableRegion': [[0, 0], [100, 0], [100, 100], [0, 100]],
            'primitives': [
                {'id': 'text', 'objectId': 'dimension', 'kind': 'FILLED_POLYGON',
                 'points': [[10, 10], [20, 10], [20, 20], [10, 20]]},
                {'id': 'line', 'objectId': 'other-dimension', 'kind': 'LINE',
                 'points': [[30, 5], [30, 25]], 'width': 2}]}


class ContractTests(unittest.TestCase):
    def test_digest_is_order_independent_but_keeps_precision(self):
        self.assertEqual(digest({'b': 2, 'a': 1}), digest({'a': 1, 'b': 2}))
        self.assertNotEqual(digest([1.0000000000000002]), digest([1.0]))

    def test_seal_detaches_nested_input_and_detects_later_tampering(self):
        source = {'points': [[1, 2]]}
        result = seal(source)
        source['points'][0][0] = 8
        check_seal(result)
        result['points'][0][0] = 4
        with self.assertRaisesRegex(ContractError, 'DIGEST_MISMATCH'):
            check_seal(result)

    def test_nonfinite_and_nonjson_keys_are_rejected(self):
        for value in ([float('nan')], [float('inf')], {1: 'ambiguous'}, (1, 2)):
            with self.subTest(value=value), self.assertRaises(ContractError):
                digest(value)

    def test_duplicate_json_keys_are_not_silently_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'input.json'
            path.write_text('{"status":"FAIL","status":"PASS"}', encoding='utf-8')
            with self.assertRaisesRegex(ContractError, 'DUPLICATE_KEY'):
                read_json(path)


class SourceTests(unittest.TestCase):
    def test_native_gate_recomputes_and_freezes_every_requirement(self):
        raw = plate_with_hole()
        before = copy.deepcopy(raw)
        result = freeze_native_sources(raw)
        entry = result['entries'][0]
        self.assertEqual('SOURCE_READY', entry['status'])
        self.assertEqual(digest(entry['requirements']), entry['source']['requirementsDigest'])
        self.assertNotEqual(entry['source']['nativeInputDigest'], entry['source']['sourceDigest'])
        self.assertEqual(before, raw)
        self.assertEqual(result, freeze_native_sources(raw))
        check_seal(result)
        check_seal(entry)
        raw['parts'][0]['id'] = 999
        self.assertNotEqual(999, entry['rawPart']['id'])

    def test_missing_guid_is_blocked_not_admitted_by_flat_plate_name(self):
        raw = plate_with_hole()
        del raw['parts'][0]['guid']
        result = freeze_native_sources(raw)
        self.assertEqual('INPUT_BLOCKED', result['entries'][0]['status'])

    def test_additional_views_are_preserved_and_excluded_before_layout(self):
        from zijinhua_tekla.part_drawing.native_expressions import build_native_expression_requirements
        raw = plate_with_hole()
        batch = build_native_expression_requirements(raw)
        plan = batch['parts'][0]
        plan.update(schemaVersion='expressionRequirements.v2', requiredViews=['main', 'section:0'],
                    sectionRequirements=[{'id': 'section:0', 'mustKeep': True}])
        with patch('zijinhua_tekla.part_drawing.independent_layout.sources.build_native_expression_requirements',
                   return_value=batch):
            entry = freeze_native_sources(raw)['entries'][0]
        self.assertEqual('OUT_OF_SCOPE', entry['status'])
        self.assertEqual(['ADDITIONAL_VIEWS_REQUIRED'], entry['reasons'])
        self.assertEqual(plan, entry['requirements'])

    def test_duplicate_instances_do_not_disappear_into_dictionary(self):
        raw = plate_with_hole()
        raw['parts'].append(copy.deepcopy(raw['parts'][0]))
        with self.assertRaisesRegex(ContractError, 'PART_ID_INVALID'):
            freeze_native_sources(raw)

    def test_legacy_snapshot_is_not_accepted_as_native_evidence(self):
        with self.assertRaisesRegex(ContractError, 'SOURCE_SCHEMA'):
            freeze_native_sources({'schemaVersion': 'PartDrawingSnapshot.v1'})


class TemplateTests(unittest.TestCase):
    def test_both_observed_attribute_namespaces_preserve_content_formats(self):
        for prefix in ('', 'wdraw_dial.'):
            with self.subTest(prefix=prefix):
                raw = (prefix + 'dl.width 5950\n' + prefix + 'dl.height 4210\n' +
                       prefix + 'dsm.ContentString1 "<?xml version="1.0"?><Mark>' +
                       '<Format font="宋体" height="50"/></Mark>"\n').encode('utf-8')
                result = study_template(*template_evidence(), attributes=raw)
                self.assertEqual([], result['attributeDifferences'])
                formats = result['attributes']['contentFormats'][prefix + 'dsm.ContentString1']
                self.assertEqual('宋体', formats[0]['font'])

    def test_mixed_layout_namespaces_cannot_hide_conflicting_width(self):
        raw = b'dl.width 5950\ndl.height 4210\nwdraw_dial.dl.width 100'
        with self.assertRaisesRegex(ContractError, 'ATTRIBUTE_NAMESPACE_AMBIGUOUS'):
            study_template(*template_evidence(), attributes=raw)

    def test_sdk_effective_style_ignores_value_font_and_keeps_missing_role(self):
        inputs = template_evidence()
        result = study_template(*inputs)
        self.assertEqual([{'font': 'romsim', 'height': 50}], result['observedRoleStyles']['dimension'])
        self.assertEqual([], result['observedRoleStyles']['holeNote'])
        self.assertIn('ROLE_UNOBSERVED:holeNote', result['pending'])
        self.assertFalse(result['productionEligible'])
        self.assertEqual('STUDY_ONLY', result['status'])
        check_seal(result)

    def test_changed_capture_or_absent_display_is_not_a_template(self):
        for key in ('partMetadataUnchanged', 'workPlaneUnchanged', 'activeIdentityUnchanged'):
            inputs = template_evidence()
            inputs[0][key] = False
            with self.subTest(key=key), self.assertRaisesRegex(ContractError, 'CAPTURE_CHANGED'):
                study_template(*inputs)
        inputs = template_evidence()
        inputs[2].clear()
        with self.assertRaisesRegex(ContractError, 'PRESENTATION_MISSING'):
            study_template(*inputs)

    def test_display_font_difference_is_not_silently_resolved(self):
        inputs = template_evidence()
        inputs[2][0]['Primitives'][0]['Height'] = 3
        with self.assertRaisesRegex(ContractError, 'STYLE_DISPLAY_MISMATCH'):
            study_template(*inputs)

    def test_mixed_encoding_content_style_does_not_overwrite_generic_field(self):
        outer = 'wdraw_dial.StyleName "零件图50"\nwdraw_dial.dsm.FontAttr.height 2.500000\n'.encode('gb18030')
        xml = ('wdraw_dial.dsm.ContentString1 "<?xml version="1.0"?><Mark>\x01'
               '<Format font="宋体" height="125.000"/></Mark>"\n').encode('utf-8')
        result = read_attributes(outer + xml)
        self.assertEqual('"零件图50"', result['fields']['wdraw_dial.StyleName'])
        self.assertEqual('2.500000', result['fields']['wdraw_dial.dsm.FontAttr.height'])
        style = result['contentFormats']['wdraw_dial.dsm.ContentString1'][0]
        self.assertEqual(('宋体', 125), (style['font'], style['height']))

    def test_view_placeholder_is_preserved_but_invalid_role_xml_blocks(self):
        raw = b'wdraw_dial.dv.ContentString "<?xml version="1.0"?><Mark><< Mark >></Mark>"'
        result = read_attributes(raw)
        self.assertFalse(result['contentFormats'])
        with self.assertRaisesRegex(ContractError, 'ATTRIBUTE_XML'):
            read_attributes(raw.replace(b'dv.ContentString', b'dpm.ContentString1'))

    def test_no_template_dimension_is_inferred_from_template_name(self):
        inputs = template_evidence()
        result = study_template(*inputs, attributes=b'wdraw_dial.dl.width 5960\nwdraw_dial.dl.height 4220')
        self.assertEqual([5950, 4210], result['sheetSizeNative'])
        self.assertEqual(['wdraw_dial.dl.width', 'wdraw_dial.dl.height'], result['attributeDifferences'])
        self.assertFalse(result['productionEligible'])


class ObservationTests(unittest.TestCase):
    def test_clear_reports_are_bound_to_the_exact_observed_coordinates(self):
        first = observation()
        second = copy.deepcopy(first)
        second['primitives'][1]['points'][0][0] += 1
        a, b = validate_observation(first), validate_observation(second)
        self.assertEqual('GEOMETRY_CLEAR', b['status'])
        self.assertEqual(digest(first), a['observationDigest'])
        self.assertNotEqual(a['digest'], b['digest'])

    def test_geometry_clear_is_never_whole_drawing_verified(self):
        result = validate_observation(observation())
        self.assertEqual('GEOMETRY_CLEAR', result['status'])
        self.assertIn('EXPRESSION_COVERAGE', result['remainingGates'])

    def test_line_through_text_fails_even_with_same_object_id(self):
        data = observation()
        data['primitives'][1].update(points=[[15, 0], [15, 30]], objectId='dimension')
        result = validate_observation(data)
        self.assertEqual('GEOMETRY_CONFLICT', result['status'])
        self.assertTrue(any(v['code'] == 'CLEARANCE_CONFLICT' for v in result['violations']))

    def test_rotated_text_polygon_and_line_width_are_checked(self):
        data = observation()
        data['primitives'][0]['points'] = [[10, 20], [20, 10], [30, 20], [20, 30]]
        data['primitives'][1].update(points=[[32, 10], [32, 30]], width=4)
        self.assertEqual('GEOMETRY_CONFLICT', validate_observation(data)['status'])

    def test_touch_and_exact_minimum_gap_are_conflicts(self):
        for x in (20, 21):
            data = observation()
            data['primitives'][1].update(points=[[x, 0], [x, 30]], width=0)
            with self.subTest(x=x):
                self.assertEqual('GEOMETRY_CONFLICT', validate_observation(data)['status'])

    def test_stroke_outside_page_cannot_pass_by_centerline(self):
        data = observation()
        data['primitives'][1].update(points=[[99.5, 30], [99.5, 50]], width=2)
        self.assertIn('OUTSIDE_USABLE_REGION', [v['code'] for v in validate_observation(data)['violations']])

    def test_explicit_endpoint_join_does_not_exempt_crossing_or_overlap(self):
        data = observation()
        data['primitives'] = [
            {'id': 'a', 'objectId': 'one', 'kind': 'LINE', 'points': [[10, 10], [20, 10]], 'width': 0},
            {'id': 'b', 'objectId': 'two', 'kind': 'LINE', 'points': [[20, 10], [20, 20]], 'width': 0}]
        data['contacts'] = [{'pair': ['b', 'a'], 'point': [20, 10]}]
        self.assertEqual('GEOMETRY_CLEAR', validate_observation(data)['status'])
        data['primitives'][1]['points'] = [[20, 10], [15, 10]]
        self.assertEqual('GEOMETRY_CONFLICT', validate_observation(data)['status'])
        data['primitives'][1]['points'] = [[15, 0], [15, 20]]
        with self.assertRaisesRegex(ContractError, 'CONTACT_INVALID'):
            validate_observation(data)

    def test_empty_unknown_or_invalid_primitive_does_not_pass(self):
        for update in ({'primitives': []}, {'units': 'PAPER_MM'}, {'minimumClearance': -1}):
            data = observation()
            data.update(update)
            with self.subTest(update=update), self.assertRaises(ContractError):
                validate_observation(data)
        data = observation()
        data['primitives'][0]['kind'] = 'UNMEASURED_TEXT'
        with self.assertRaisesRegex(ContractError, 'PRIMITIVE_UNSUPPORTED'):
            validate_observation(data)
        data = observation()
        data['primitives'][0]['points'] = [[10, 10], [20, 20], [20, 10], [10, 20]]
        with self.assertRaisesRegex(ContractError, 'POLYGON_INVALID'):
            validate_observation(data)

    def test_template_obstacle_is_not_exempted_by_owner(self):
        data = observation()
        data['primitives'][1] = {**data['primitives'][0], 'id': 'fixed-template', 'objectId': 'title'}
        self.assertEqual('GEOMETRY_CONFLICT', validate_observation(data)['status'])


class CommandTests(unittest.TestCase):
    def test_new_output_only_and_error_does_not_leave_success_report(self):
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / 'input.json', Path(directory) / 'result'
            source.write_text(json.dumps(observation()), encoding='utf-8')
            args = ['check-observation', '--observation', str(source), '--out', str(output)]
            self.assertEqual(0, main(args))
            before = (output / 'report.json').read_bytes()
            self.assertEqual(2, main(args))
            self.assertEqual(before, (output / 'report.json').read_bytes())
            broken_output = Path(directory) / 'broken'
            source.write_text('{"schemaVersion":"unsupported"}', encoding='utf-8')
            self.assertEqual(2, main(args[:-1] + [str(broken_output)]))
            self.assertFalse(broken_output.exists())


if __name__ == '__main__':
    unittest.main()
