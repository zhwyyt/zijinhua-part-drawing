"""Semantic adaptation failures stay visible before any layout or native write."""

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_native_material import plate_with_hole
from zijinhua_tekla.part_drawing.dimension_scheme import build_scheme
from zijinhua_tekla.part_drawing.expression_requirements import _requirements
from zijinhua_tekla.part_drawing.independent_layout.__main__ import main
from zijinhua_tekla.part_drawing.independent_layout.contracts import ContractError, check_seal, digest, seal
from zijinhua_tekla.part_drawing.independent_layout.expressions import adapt_expressions, adapt_expression_sources
from zijinhua_tekla.part_drawing.independent_layout.semantic_coverage import audit_semantics
from zijinhua_tekla.part_drawing.independent_layout.sources import freeze_native_sources
from zijinhua_tekla.part_drawing.plate_features import build_feature_model


def reseal(value):
    return seal({k: v for k, v in value.items() if k != 'digest'})


def source():
    return freeze_native_sources(plate_with_hole())['entries'][0]


def synthetic_facts(original, facts):
    """Unit-test the semantic boundary, not a claim of native acquisition."""
    result = copy.deepcopy(original)
    plan = result['requirements']
    plan['facts'] = facts
    plan['requirements'], plan['groups'] = _requirements(plan['source']['partPosition'], 10, facts, {})
    result['source']['requirementsDigest'] = digest(plan)
    return reseal(result)


def object_of(expressions, kind):
    return next(o for o in expressions['objects'] if o['kind'] == kind)


class ExpressionTests(unittest.TestCase):
    def setUp(self):
        self.source = source()
        self.expressions = adapt_expressions(self.source)

    def assert_gap(self, expressions):
        result = audit_semantics(self.source, expressions)
        self.assertEqual('UPSTREAM_CONTRACT_GAP', result['status'])
        return result

    def test_deterministic_detached_full_precision_and_no_layout_dependency(self):
        before = copy.deepcopy(self.source)
        with patch('zijinhua_tekla.part_drawing.native_layout.build_layout', side_effect=AssertionError('old solver')):
            result = adapt_expressions(self.source)
        self.assertEqual(self.expressions, result)
        self.assertEqual(before, self.source)
        self.assertEqual('SEMANTICS_COMPLETE', result['status'])
        self.assertEqual('VIEW_MM', result['units'])
        self.assertNotIn('template', result)
        self.assertIn('READBACK_COVERAGE', result['remainingGates'])
        check_seal(result)
        self.source['requirements']['facts']['points']['vertex:0'][0] = 1
        self.assertEqual(before['requirements'], result['expressionRequirements'])

    def test_scheme_groups_are_one_to_one_but_requirements_are_many_to_many(self):
        result = self.expressions
        dimensions = [o for o in result['objects'] if o['kind'] == 'DIMENSION']
        self.assertCountEqual([g['key'] for g in result['scheme']['groups']], [o['schemeKey'] for o in dimensions])
        self.assertCountEqual([r['id'] for r in self.source['requirements']['requirements']],
                              [r['requirementId'] for r in result['coverage']['bindings']])
        self.assertTrue(all(r['status'] == 'BOUND' for r in result['coverage']['bindings']))
        bindings = [r for r in result['coverage']['bindings'] if r['requirementId'].endswith('location_x')]
        self.assertTrue(any(r['sourceEvidence'] for r in bindings))
        self.assertTrue(any(r['objectIds'] for r in bindings))

    def test_removed_dimension_is_not_hidden_by_other_coverage(self):
        target = object_of(self.expressions, 'DIMENSION')
        self.expressions['objects'].remove(target)
        result = self.assert_gap(self.expressions)
        self.assertIn('SCHEME_GROUP_MISSING', [e['code'] for e in result['errors']])

    def test_claimed_success_in_report_cannot_hide_removed_hole(self):
        self.expressions['objects'].remove(object_of(self.expressions, 'HOLE'))
        result = self.assert_gap(self.expressions)
        missing = [r['requirementId'] for r in result['bindings'] if r['status'] == 'MISSING']
        self.assertTrue(any(k.endswith('/shape') for k in missing))
        self.assertTrue(any(k.endswith('/center_cross') for k in missing))

    def test_wrong_coordinates_values_display_side_and_reference_fail(self):
        for field, value in [('coordinates', [[0, 0], [1, 1]]), ('values', [999]),
                             ('displayValues', [999]), ('side', 0), ('reference', {'category': 'FAKE'})]:
            with self.subTest(field=field):
                data = copy.deepcopy(self.expressions)
                object_of(data, 'DIMENSION')[field] = value
                self.assert_gap(data)

    def test_static_shape_and_hole_size_are_compared_to_real_facts(self):
        for kind, change in [('SEGMENT', lambda o: o['geometry']['start'].__setitem__(0, 5)),
                             ('HOLE', lambda o: o['geometry'].__setitem__('diameter', 12))]:
            with self.subTest(kind=kind):
                data = copy.deepcopy(self.expressions)
                change(object_of(data, kind))
                self.assert_gap(data)

    def test_label_thickness_and_hole_callout_cannot_be_substituted(self):
        for kind, field, value in [('LABEL', 'text', 'OTHER-PART'), ('THICKNESS', 'value', 99),
                                   ('HOLE_NOTE', 'text', '99Φ99')]:
            with self.subTest(kind=kind):
                data = copy.deepcopy(self.expressions)
                object_of(data, kind)[field] = value
                self.assert_gap(data)

    def test_unknown_and_duplicate_semantic_objects_fail(self):
        for mode in ('unknown', 'id', 'feature'):
            with self.subTest(mode=mode):
                data = copy.deepcopy(self.expressions)
                duplicate = copy.deepcopy(object_of(data, 'HOLE'))
                if mode != 'id':
                    duplicate['objectId'] = 'extra'
                if mode == 'unknown':
                    duplicate['kind'] = 'FUTURE_KIND'
                data['objects'].append(duplicate)
                self.assert_gap(data)

    def test_changed_source_digest_or_units_and_unknown_version_reject(self):
        for field, value in [('sourceEnvelopeDigest', 'different'), ('units', 'MODEL_MM'),
                             ('schemaVersion', 'LayoutExpressions.v999'), ('adapterVersion', 'future')]:
            with self.subTest(field=field):
                data = copy.deepcopy(self.expressions)
                data[field] = value
                with self.assertRaises(ContractError):
                    audit_semantics(self.source, data)
        self.source['requirements']['requirements'].pop()
        with self.assertRaisesRegex(ContractError, 'DIGEST_MISMATCH'):
            adapt_expressions(self.source)

    def test_bolt_representative_and_ordinary_hole_anchor_policy(self):
        note = object_of(self.expressions, 'HOLE_NOTE')
        self.assertEqual('BOLT_CENTER', note['anchorPolicy'])
        self.assertEqual(note['group']['members'][0], note['representativeFeatureId'])
        facts = copy.deepcopy(self.source['requirements']['facts'])
        del facts['holes'][0]['origin']
        plain = adapt_expressions(synthetic_facts(self.source, facts))
        note = object_of(plain, 'HOLE_NOTE')
        self.assertEqual('NEAR_BOUNDARY', note['anchorPolicy'])
        self.assertIsNone(note['representativeFeatureId'])
        self.assertEqual(note['group']['members'], note['representativeCandidates'])
        object_of(self.expressions, 'HOLE_NOTE')['anchorPolicy'] = 'NEAR_BOUNDARY'
        self.assert_gap(self.expressions)

    def test_explicit_column_membership_is_preserved_and_audited(self):
        plan = self.source['requirements']
        scheme = build_scheme(plan, build_feature_model(plan['facts'], plan['modelToleranceMm']))
        members = [g for g in scheme['groups'] if g['axis'] == 0]
        for group in members:
            group.update(column='confirmed-column', allowedSides=[-1], side=-1)
        with patch('zijinhua_tekla.part_drawing.independent_layout.expressions.build_scheme', return_value=scheme), \
             patch('zijinhua_tekla.part_drawing.independent_layout.semantic_coverage._upstream_scheme', return_value=scheme):
            data = adapt_expressions(self.source)
        self.assertEqual('SEMANTICS_COMPLETE', data['status'])
        self.assertEqual(sorted('dimension/' + g['key'] for g in members), data['columnGroups'][0]['memberObjectIds'])
        data['columnGroups'][0]['memberObjectIds'].pop()
        with patch('zijinhua_tekla.part_drawing.independent_layout.semantic_coverage._upstream_scheme', return_value=scheme):
            self.assert_gap(data)

    def test_conflicting_column_axes_block_instead_of_splitting_group(self):
        plan = self.source['requirements']
        scheme = build_scheme(plan, build_feature_model(plan['facts'], plan['modelToleranceMm']))
        for group in scheme['groups']:
            group['column'] = 'bad-column'
        with patch('zijinhua_tekla.part_drawing.independent_layout.expressions.build_scheme', return_value=scheme):
            with self.assertRaisesRegex(ContractError, 'COLUMN_CONFLICT'):
                adapt_expressions(self.source)

    def test_required_additional_view_is_not_dropped(self):
        data = copy.deepcopy(self.source)
        data['requirements'].update(schemaVersion='expressionRequirements.v2', requiredViews=['main', 'section:0'],
                                    sectionRequirements=[{'id': 'section:0'}])
        data['source']['requirementsDigest'] = digest(data['requirements'])
        with self.assertRaisesRegex(ContractError, 'ADDITIONAL_VIEWS_REQUIRED'):
            adapt_expressions(reseal(data))

    def test_internal_cutout_is_kept_and_missing_location_is_reported(self):
        facts = copy.deepcopy(self.source['requirements']['facts'])
        coords = [[10, 10], [20, 10], [20, 20], [10, 20]]
        segments = []
        for i, point in enumerate(coords):
            a, b = f'cutout:0:vertex:{i}', f'cutout:0:vertex:{(i+1)%4}'
            facts['points'][a] = point
            segments.append(dict(id=f'cutout:0:edge:{i}', kind='LINE', start=point,
                                 end=coords[(i+1)%4], startPoint=a, endPoint=b))
        facts['segments'].extend(segments)
        facts['boundaryLoops'].append(dict(id='cutout:0', segments=[s['id'] for s in segments]))
        data = adapt_expressions(synthetic_facts(self.source, facts))
        self.assertEqual('UPSTREAM_CONTRACT_GAP', data['status'])
        bound = {r['requirementId']: r['status'] for r in data['coverage']['bindings']}
        self.assertTrue(all(bound[s['id']+'/shape'] == 'BOUND' for s in segments))
        self.assertEqual('MISSING', bound['cutout:0:vertex:0/location_x'])

    def test_full_circle_diameter_covers_arc_radii_without_old_layout(self):
        coords = [[-20, 0], [0, -20], [20, 0], [0, 20]]
        facts = dict(points={f'vertex:{i}': p for i, p in enumerate(coords)}, holes=[], datum='vertex:0',
                     bounds=[-20, -20, 20, 20], segments=[], boundaryLoops=[dict(id='outer', segments=[])])
        for i, point in enumerate(coords):
            key = f'edge:{i}'
            facts['segments'].append(dict(id=key, kind='ARC', start=point, end=coords[(i+1)%4],
                                          startPoint=f'vertex:{i}', endPoint=f'vertex:{(i+1)%4}',
                                          center=[0, 0], radius=20, clockwise=False))
            facts['boundaryLoops'][0]['segments'].append(key)
        data_source = synthetic_facts(self.source, facts)
        with patch('zijinhua_tekla.part_drawing.native_layout.outer_circle', side_effect=AssertionError('old layout')):
            data = adapt_expressions(data_source)
        self.assertEqual('SEMANTICS_COMPLETE', data['status'])
        self.assertEqual(1, sum(o['kind'] == 'DIAMETER' for o in data['objects']))
        self.assertFalse(any(o['kind'] == 'RADIUS' for o in data['objects']))
        data['objects'].remove(object_of(data, 'DIAMETER'))
        self.assertEqual('UPSTREAM_CONTRACT_GAP', audit_semantics(data_source, data)['status'])

    def test_shared_coordinates_do_not_accumulate_tolerance_drift(self):
        facts = copy.deepcopy(self.source['requirements']['facts'])
        facts['points'].update({'unbound:a': [0.09, 0], 'unbound:b': [0.18, 0]})
        data = adapt_expressions(synthetic_facts(self.source, facts))
        rows = {r['requirementId']: r for r in data['coverage']['bindings']}
        self.assertEqual('BOUND', rows['unbound:a/location_x']['status'])
        self.assertEqual('MISSING', rows['unbound:b/location_x']['status'])

    def test_pitch_is_explicit_and_bolt_pitch_is_forbidden(self):
        for bolt in (False, True):
            with self.subTest(bolt=bolt):
                facts = copy.deepcopy(self.source['requirements']['facts'])
                facts['holes'] = []
                facts['points'] = {k: v for k, v in facts['points'].items() if not k.startswith('hole:')}
                for i in range(5):
                    key = f'row:{i}'
                    facts['points']['hole:' + key] = [25*i, 30]
                    facts['holes'].append(dict(id=key, pointId='hole:' + key, kind='ROUND', diameter=4,
                                               **({'origin': 'BOLT'} if bolt else {})))
                data_source = synthetic_facts(self.source, facts)
                plan = data_source['requirements']
                scheme = build_scheme(plan, build_feature_model(facts, plan['modelToleranceMm']))
                group = next(g for g in scheme['groups'] if g['key'] == 'holes-0')
                group['combined'] = dict(intervalCount=4, pitch=25, text='4@25')
                with patch('zijinhua_tekla.part_drawing.independent_layout.expressions.build_scheme', return_value=scheme), \
                     patch('zijinhua_tekla.part_drawing.independent_layout.semantic_coverage._upstream_scheme', return_value=scheme):
                    data = adapt_expressions(data_source)
                codes = [e['code'] for e in data['coverage']['errors']]
                if bolt:
                    self.assertIn('BOLT_PITCH_FORBIDDEN', codes)
                    self.assertEqual('UPSTREAM_CONTRACT_GAP', data['status'])
                else:
                    self.assertEqual('SEMANTICS_COMPLETE', data['status'])
                    dimension = next(o for o in data['objects'] if o.get('schemeKey') == 'holes-0')
                    self.assertEqual([[0, 4]], dimension['combineRuns'])
                    dimension['combined']['text'] = '5@25'
                    with patch('zijinhua_tekla.part_drawing.independent_layout.semantic_coverage._upstream_scheme', return_value=scheme):
                        self.assertEqual('UPSTREAM_CONTRACT_GAP', audit_semantics(data_source, data)['status'])

    def test_removing_scheme_group_and_rehashing_cannot_change_source_selection(self):
        group = self.expressions['scheme']['groups'].pop()
        self.expressions['schemeDigest'] = digest(self.expressions['scheme'])
        self.expressions['objects'] = [o for o in self.expressions['objects'] if o.get('schemeKey') != group['key']]
        with self.assertRaisesRegex(ContractError, 'UPSTREAM_SCHEME_MISMATCH'):
            audit_semantics(self.source, self.expressions)

    def test_slot_orientation_is_owned_with_its_shape_and_center_axes(self):
        facts = copy.deepcopy(self.source['requirements']['facts'])
        hole = facts['holes'][0]
        del hole['diameter']
        hole.update(kind='SLOT', width=10, length=20, angle=30)
        data_source = synthetic_facts(self.source, facts)
        data = adapt_expressions(data_source)
        self.assertEqual('SEMANTICS_COMPLETE', data['status'])
        row = next(r for r in data['coverage']['bindings'] if r['requirementId'].endswith('/orientation'))
        self.assertEqual([object_of(data, 'HOLE')['objectId']], row['objectIds'])
        object_of(data, 'HOLE')['geometry']['angle'] = 0
        self.assertEqual('UPSTREAM_CONTRACT_GAP', audit_semantics(data_source, data)['status'])

    def test_partial_arc_radius_is_not_lost_or_replaced_by_diameter(self):
        facts = copy.deepcopy(self.source['requirements']['facts'])
        arc = facts['segments'][0]
        arc.update(kind='ARC', center=[50, 0], radius=50, clockwise=False)
        facts['bounds'] = [0, -50, 100, 60]
        facts['points']['edge:0:extreme:3'] = [50, -50]
        data_source = synthetic_facts(self.source, facts)
        data = adapt_expressions(data_source)
        radius = object_of(data, 'RADIUS')
        self.assertFalse(any(o['kind'] == 'DIAMETER' for o in data['objects']))
        self.assertEqual('REAL_ARC', radius['anchorPolicy'])
        radius['value'] += 1
        report = audit_semantics(data_source, data)
        self.assertEqual('UPSTREAM_CONTRACT_GAP', report['status'])
        self.assertEqual('MISSING', next(r['status'] for r in report['bindings']
                                        if r['requirementId'] == arc['id'] + '/radius'))

    def test_translation_preserves_semantic_values_and_coverage(self):
        facts = copy.deepcopy(self.source['requirements']['facts'])
        shift = lambda p: [p[0] + 17.25, p[1] - 32.5]
        facts['points'] = {key: shift(p) for key, p in facts['points'].items()}
        facts['bounds'] = shift(facts['bounds'][:2]) + shift(facts['bounds'][2:])
        for segment in facts['segments']:
            segment['start'], segment['end'] = shift(segment['start']), shift(segment['end'])
        shifted = adapt_expressions(synthetic_facts(self.source, facts))
        self.assertEqual('SEMANTICS_COMPLETE', shifted['status'])
        values = lambda data: {o['schemeKey']: o['values'] for o in data['objects'] if o['kind'] == 'DIMENSION'}
        self.assertEqual(values(self.expressions), values(shifted))

    def test_specification_pairs_are_json_and_keep_their_source(self):
        data_source = copy.deepcopy(self.source)
        plan = data_source['requirements']
        plan['specifications'] = {'edge_finish': {'value': 'Deburr', 'source': 'synthetic-approved-context'}}
        plan['requirements'], plan['groups'] = _requirements(plan['source']['partPosition'], 10,
                                                            plan['facts'], plan['specifications'])
        data_source['source']['requirementsDigest'] = digest(plan)
        data_source = reseal(data_source)
        data = adapt_expressions(data_source)
        check_seal(data)
        self.assertEqual('SEMANTICS_COMPLETE', data['status'])
        self.assertEqual(['edge_finish', plan['specifications']['edge_finish']], data['scheme']['specifications'][0])
        note = object_of(data, 'SPECIFICATION')
        self.assertEqual('Deburr', note['text'])
        note['source']['source'] = 'different-context'
        self.assertEqual('UPSTREAM_CONTRACT_GAP', audit_semantics(data_source, data)['status'])


class ExpressionBatchTests(unittest.TestCase):
    def test_batch_exclusions_are_explicit_and_duplicates_reject(self):
        batch = freeze_native_sources(plate_with_hole())
        batch['entries'][0].update(status='OUT_OF_SCOPE', reasons=['ADDITIONAL_VIEWS_REQUIRED'],
                                   requiredViews=['main', 'section:0'])
        batch['entries'][0] = reseal(batch['entries'][0])
        result = adapt_expression_sources(reseal(batch))
        self.assertEqual(1, result['counts']['OUT_OF_SCOPE'])
        self.assertEqual(0, result['counts']['SEMANTICS_COMPLETE'])
        batch['entries'].append(copy.deepcopy(batch['entries'][0]))
        with self.assertRaisesRegex(ContractError, 'PART_ID_INVALID'):
            adapt_expression_sources(reseal(batch))

    def test_separate_cli_writes_report_and_preserves_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources = root / 'sources.json'
            sources.write_text(json.dumps(freeze_native_sources(plate_with_hole())), encoding='utf-8')
            args = ['expressions', '--sources', str(sources), '--out', str(root / 'result')]
            self.assertEqual(0, main(args))
            result = json.loads((root / 'result' / 'report.json').read_text(encoding='utf-8'))
            self.assertEqual(1, result['counts']['SEMANTICS_COMPLETE'])
            self.assertEqual(2, main(args))


if __name__ == '__main__':
    unittest.main()
