"""Synthetic independent observations test the rule, not live Tekla acquisition."""

import base64
import copy
import json
from pathlib import Path
import tempfile
import unittest

from test_native_geometry import native_capture, add_hole, change_part, prism, RECT
from test_native_facts import read
from zijinhua_tekla.cli import main
from zijinhua_tekla.part_drawing.native_facts import build_native_facts
from zijinhua_tekla.part_drawing.native_geometry import audit_native_geometry


def observation_bundle(capture, extension=0, vertical=False):
    raw = json.loads(base64.b64decode(capture['before']['bytesBase64']))
    part = raw['parts'][0]
    return {'schemaVersion': 'nativeHoleObservations.v1',
            'origin': 'TEKLA_NATIVE_DRAWING_GEOMETRY',
            'nativeEvidenceDigest': build_native_facts(raw)['rawDigest'],
            'observations': [{'targetPartId': part['id'], 'modelGroupId': part['boltGroups']['value'][0]['id'],
                              'drawingId': 'D1', 'viewId': 'V1', 'objectId': 'H1',
                              'sectionViewId': 'S1', 'sectionObjectId': 'H2',
                              'kind': 'SLOT' if extension else 'ROUND', 'center': [40, 30, 10],
                              'diameter': 10, 'extension': extension,
                              'direction': [0, 1, 0] if vertical else [1, 0, 0],
                              'entry': [40, 30, 0], 'exit': [40, 30, 10]}]}


class CompositeHoleTests(unittest.TestCase):
    def setUp(self):
        self.capture = native_capture()
        add_hole(self.capture)
        self.bundle = observation_bundle(self.capture)

    def report(self):
        return audit_native_geometry(self.capture, '0.00001', '0.01', '0.01', self.bundle)['parts'][0]

    def effect(self):
        return self.report()['holeGroups'][0]['effectEvidence']

    def rebind(self):
        self.bundle['nativeEvidenceDigest'] = observation_bundle(self.capture)['nativeEvidenceDigest']

    def test_round_and_both_slot_axes_confirm_without_releasing_whole_part(self):
        for extension, vertical in ((0, False), (20, False), (20, True)):
            self.capture = native_capture()
            add_hole(self.capture, extension, vertical)
            self.bundle = observation_bundle(self.capture, extension, vertical)
            part = self.report()
            self.assertEqual('COMPOSITE_CONFIRMED', part['holeGroups'][0]['effectEvidence']['status'])
            hole = next(f for f in part['featureEvidence'] if f['id'].startswith('hole:'))
            self.assertEqual('SUFFICIENT', hole['proofStatus'])
            self.assertEqual([], hole['missingProofs'])
            self.assertEqual('SUFFICIENT', part['evidenceSufficiency'])
            self.assertEqual('BLOCKED', part['manufacturingStatus'])
            self.assertNotIn('FINISHED_HOLE_INVENTORY_UNPROVEN', part['remainingProofs'])

    def test_fastener_and_multiple_targets_use_target_specific_observation(self):
        def change(p):
            b = p['boltGroups']['value'][0]
            b['properties']['Bolt'] = read(True)
            b['targets']['partToBeBolted'] = read({'id': 999})
        change_part(self.capture, change)
        self.rebind()
        part = self.report()
        self.assertEqual('COMPOSITE_CONFIRMED', part['holeGroups'][0]['effectEvidence']['status'])
        self.assertEqual('UNRESOLVED', part['holeGroups'][0]['solidCheck']['status'])
        self.assertTrue(part['holeGroups'][0]['solidDiagnostics'])
        self.assertNotIn('HOLE_SOLID_UNRESOLVED', [i['code'] for i in part['issues']])

    def test_missing_and_empty_observations_are_gaps_not_absence_of_holes(self):
        for bundle in (None, dict(self.bundle, observations=[])):
            self.bundle = bundle
            part = self.report()
            self.assertEqual('INSUFFICIENT', part['holeGroups'][0]['effectEvidence']['status'])
            self.assertEqual(1, len(part['holeGroups'][0]['candidates']))
            self.assertNotEqual('CONFLICT', part['evidenceSufficiency'])

    def test_bad_origin_stale_digest_and_foreign_targets_rejected(self):
        original = copy.deepcopy(self.bundle)
        changes = [lambda b: b.update(origin='ANNOTATION_PLAN'),
                   lambda b: b.update(origin='TEKLA_BOLT_SYMBOL'),
                   lambda b: b.update(nativeEvidenceDigest='old'),
                   lambda b: b['observations'][0].update(targetPartId=999),
                   lambda b: b['observations'][0].update(modelGroupId=999),
                   lambda b: b['observations'][0].update(sectionViewId='V1'),
                   lambda b: b['observations'][0].update(sectionObjectId='H1'),
                   lambda b: b['observations'][0].pop('sectionObjectId')]
        for change in changes:
            self.bundle = copy.deepcopy(original)
            change(self.bundle)
            with self.assertRaises((ValueError, KeyError)):
                self.report()

    def test_geometry_conflicts_cannot_be_overridden_by_matched_cutter(self):
        original = copy.deepcopy(self.bundle)
        for fields in ({'diameter': 12}, {'center': [42, 30, 10]}, {'exit': [40, 30, 9]},
                       {'entry': [41, 30, 0]}, {'center': [40, 30, 100]},
                       {'kind': 'SLOT', 'extension': 10}):
            self.bundle = copy.deepcopy(original)
            self.bundle['observations'][0].update(fields)
            part = self.report()
            self.assertEqual('MATCHED', part['holeGroups'][0]['solidCheck']['status'])
            self.assertEqual('CONFLICT', part['holeGroups'][0]['effectEvidence']['status'])
            self.assertEqual('CONFLICT', part['evidenceSufficiency'])
            self.assertEqual('UNRESOLVED', part['geometryStatus'])

    def test_slot_direction_reversed_is_equivalent_but_perpendicular_is_conflict(self):
        self.capture = native_capture()
        add_hole(self.capture, 20)
        self.bundle = observation_bundle(self.capture, 20)
        self.bundle['observations'][0]['direction'] = [-1, 0, 0]
        self.assertEqual('COMPOSITE_CONFIRMED', self.effect()['status'])
        self.bundle['observations'][0]['direction'] = [0, 1, 0]
        self.assertEqual('CONFLICT', self.effect()['status'])

    def test_duplicate_observations_not_counted_as_two_holes(self):
        self.bundle['observations'] *= 2
        self.assertEqual('CONFLICT', self.effect()['status'])

    def test_multiple_holes_require_complete_unique_mapping(self):
        def change(p):
            b = p['boltGroups']['value'][0]
            b['properties']['Bolt'] = read(True)
            b['positions'] = read([[40, 30, 10], [60, 30, 10]])
        change_part(self.capture, change)
        self.rebind()
        self.assertEqual('INSUFFICIENT', self.effect()['status'])
        second = dict(self.bundle['observations'][0], objectId='H3', sectionObjectId='H4',
                      center=[60, 30, 10], entry=[60, 30, 0], exit=[60, 30, 10])
        self.bundle['observations'].insert(0, second)
        effect = self.effect()
        self.assertEqual('COMPOSITE_CONFIRMED', effect['status'])
        self.assertEqual(['#/observations/1', '#/observations/0'], [m['observationRef'] for m in effect['mapping']])
        self.bundle['observations'][0] = copy.deepcopy(self.bundle['observations'][1])
        self.assertEqual('CONFLICT', self.effect()['status'])

    def test_overlapping_confirmed_holes_form_one_final_cutout(self):
        def change(p):
            b = p['boltGroups']['value'][0]
            b['properties']['Bolt'] = read(True)
            b['positions'] = read([[40, 30, 10], [44, 30, 10]])
        change_part(self.capture, change)
        self.rebind()
        self.bundle['observations'].append(dict(self.bundle['observations'][0],
            objectId='H3', sectionObjectId='H4', center=[44, 30, 10], entry=[44, 30, 0], exit=[44, 30, 10]))
        self.assertEqual('COMPOSITE_CONFIRMED', self.effect()['status'])
        material = self.report()['materialFinalState']
        self.assertEqual('MATCHED',material['status'])
        self.assertEqual(['CUTOUT'],[h['kind'] for h in material['holes']])

    def test_other_parts_association_can_identify_the_target(self):
        def change(p):
            b = p['boltGroups']['value'][0]
            b['properties']['Bolt'] = read(True)
            b['targets'].update(partToBoltTo=read({'id': 998}), partToBeBolted=read({'id': 999}),
                                otherParts=read([{'id': p['id']}]))
        change_part(self.capture, change)
        self.rebind()
        self.assertEqual('COMPOSITE_CONFIRMED', self.effect()['status'])

    def test_nonfinite_boolean_and_short_coordinates_rejected(self):
        original = copy.deepcopy(self.bundle)
        for fields in ({'diameter': float('nan')}, {'extension': True}, {'entry': [40, 30]},
                       {'direction': [0, 0, 0]}, {'center': [True, 30, 10]}):
            self.bundle = copy.deepcopy(original)
            self.bundle['observations'][0].update(fields)
            with self.assertRaises(ValueError):
                self.report()

    def test_target_must_be_explicitly_associated(self):
        def change(p):
            targets = p['boltGroups']['value'][0]['targets']
            targets.update(partToBoltTo=read({'id': 998}), partToBeBolted=read({'id': 999}))
        change_part(self.capture, change)
        self.rebind()
        self.assertEqual('CONFLICT', self.effect()['status'])

    def test_missing_polybeam_base_geometry_stops_before_specialized_hole_proof(self):
        change_part(self.capture, lambda p: p.update(runtimeType='Tekla.Structures.Model.PolyBeam',
                                                   profile=read('PL10*100')))
        self.rebind()
        part = self.report()
        self.assertEqual('UNKNOWN',part['partType']['type'])
        self.assertEqual('TYPE_ROUTING',part['blockedStage'])
        self.assertEqual('NOT_RUN',part['specializedGeometryStatus'])
        self.assertNotIn('holeGroups',part)
        self.assertEqual('UNRESOLVED', part['geometryStatus'])
        self.assertIn('COMPLETE_BASE_SHAPE_OR_ANALYTIC_SURFACE_EVIDENCE',part['remainingProofs'])

    def test_outside_material_missing_face_or_wrong_thickness_not_confirmed(self):
        changes = [lambda p: p.update(finalSolid=read(prism([[0, 0], [35, 0], [35, 60], [0, 60]]))),
                   lambda p: p['finalSolid']['value']['faces']['value'].pop(),
                   lambda p: p.update(profile=read('PL12'))]
        for change in changes:
            self.setUp()
            change_part(self.capture, change)
            self.rebind()
            self.assertEqual('INSUFFICIENT', self.effect()['status'])

    def test_applicable_cutter_mismatch_is_not_hidden(self):
        change_part(self.capture, lambda p: p['boltGroups']['value'][0].update(
            solidWithHighAccuracy=read(prism(RECT))))
        self.rebind()
        self.assertIn('CANDIDATE_SOLID_UNRESOLVED', self.effect()['missingProofs'])

    def test_native_source_change_and_nc_conflict_remain_blocking(self):
        self.capture['selectionRestored'] = False
        self.assertEqual('INSUFFICIENT', self.effect()['status'])
        self.capture['selectionRestored'] = True
        from test_nc_witness import file_record
        nc = base64.b64decode(self.capture['files'][0]['bytesBase64']).decode()
        self.capture['files'] = [file_record('TEST-1.nc1', nc.replace('100.00 0.00', '99.00 0.00'))]
        self.assertEqual('CONFLICT', self.report()['evidenceSufficiency'])

    def test_stud_is_attachment_before_hole_parameter_formulas(self):
        change_part(self.capture, lambda p: p['boltGroups']['value'][0]['properties'].update(BoltStandard=read('STUD')))
        self.bundle = None
        part = self.report()
        codes = [i['code'] for i in part['issues']]
        self.assertNotIn('ATTACHMENT_EFFECT_UNPROVEN', codes)
        self.assertIn('NC_GEOMETRY_CONFLICT', codes)  # NC's drill hole contradicts the now-complete no-hole inventory.
        self.assertNotIn('HOLE_PARAMETERS_UNRESOLVED', codes)
        self.assertEqual('ATTACHMENT_STUD', part['holeGroups'][0]['classification'])

    def test_cli_observations_and_source_preservation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = json.loads(base64.b64decode(self.capture['before']['bytesBase64']))
            source, obs, out = root/'raw.json', root/'observations.json', root/'report.json'
            source.write_text(json.dumps(raw), encoding='utf8')
            obs.write_text(json.dumps(self.bundle), encoding='utf8')
            args = ['audit-native-geometry', '--evidence', str(source), '--hole-observations', str(obs),
                    '--model-tolerance-mm', '0.00001', '--out', str(out)]
            self.assertEqual(0, main(args))
            report = json.loads(out.read_text('utf8'))
            self.assertEqual(self.bundle, report['holeObservations'])
            self.assertEqual('SUFFICIENT', next(f for f in report['parts'][0]['featureEvidence']
                                              if f['id'].startswith('hole:'))['proofStatus'])
            args[-1] = str(obs)
            self.assertEqual(2, main(args))
            self.assertEqual(self.bundle, json.loads(obs.read_text('utf8')))


if __name__ == '__main__':
    unittest.main()
