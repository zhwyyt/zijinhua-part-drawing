"""Regressions for optional NC and per-feature native evidence requirements."""

import base64
import json
from pathlib import Path
import tempfile
import unittest

from test_native_geometry import native_capture, change_part, add_hole
from test_nc_witness import file_record, GUID
from test_native_facts import read
from zijinhua_tekla.cli import main
from zijinhua_tekla.part_drawing.native_geometry import audit_native_geometry
from zijinhua_tekla.part_drawing.nc_witness import parse_nc
from zijinhua_tekla.part_drawing.nc_geometry import nc_hole_inventory, match_nc_geometry


class GeometryEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.capture = native_capture()

    def report(self):
        return audit_native_geometry(self.capture, '0.00001', '0.01', '0.01')['parts'][0]

    def replace_nc(self, change):
        text = base64.b64decode(self.capture['files'][0]['bytesBase64']).decode()
        self.capture['files'] = [file_record('TEST-1.nc1', change(text))]

    def mismatch_instance(self):
        log = base64.b64decode(self.capture['exportLog']['value']['bytesBase64']).decode()
        self.capture['exportLog'] = read(file_record('dstv_nc.log', log.replace(GUID,
            '11234567-1234-5678-9abc-0123456789ab')))

    def test_native_only_has_no_nc_requirement(self):
        raw = json.loads(base64.b64decode(self.capture['before']['bytesBase64']))
        part = audit_native_geometry(raw, '0.00001')['parts'][0]
        self.assertEqual('EVIDENCE_MATCHED', part['geometryStatus'])
        self.assertEqual('NOT_REQUESTED', part['ncSource']['status'])
        self.assertEqual('SUFFICIENT', part['evidenceSufficiency'])
        self.assertEqual('BLOCKED', part['manufacturingStatus'])
        self.assertNotIn('EFFECTIVE_NC_SETTINGS_BINDING', part['remainingProofs'])
        self.assertTrue(part['featureEvidence'])

    def test_selection_restoration_ignores_enumeration_order_only(self):
        self.capture['selectionBefore'] = [10, 20, 30]
        self.capture['selectionAfter'] = [30, 20, 10]
        part = self.report()
        self.assertEqual('READ_CONSISTENT', part['nativeSource']['status'])
        self.capture['selectionAfter'] = [30, 20]
        part = self.report()
        self.assertIn('SELECTION_NOT_RESTORED',
                      [i['code'] for i in part['nativeSource']['issues']])

    def test_same_number_other_instance_is_compared_and_conflict_is_preserved(self):
        self.mismatch_instance()
        self.replace_nc(lambda s: s.replace('100.00 0.00', '999.00 0.00'))
        part = self.report()
        self.assertEqual('UNRESOLVED', part['geometryStatus'])
        self.assertEqual('READ_CONSISTENT', part['nativeSource']['status'])
        self.assertEqual('CANDIDATE', part['ncSource']['status'])
        self.assertEqual('CONFLICT', part['ncCheck']['status'])
        self.assertEqual('NUMBERED_GROUP_REPRESENTATIVE', part['ncSource']['instanceRelation'])
        self.assertEqual('CONFLICT', part['evidenceSufficiency'])
        self.assertFalse(any(f['ncEvidence'] for f in part['featureEvidence']))

    def test_unfolded_nc_is_not_used_for_original_geometry(self):
        self.replace_nc(lambda s: s.replace('EN\n', 'KA\n0.00 0.00 90.00 5.00 0.00\nEN\n'))
        part = self.report()
        self.assertEqual('EVIDENCE_MATCHED', part['geometryStatus'])
        self.assertEqual('CANDIDATE', part['ncSource']['status'])
        self.assertEqual('SKIPPED', part['ncCheck']['status'])
        self.assertEqual('UNFOLDED_COORDINATE_MAPPING_REQUIRED', part['ncCheck']['reason'])

    def test_shared_number_nc_matches_without_instance_identity_requirement(self):
        self.mismatch_instance()
        part = self.report()
        self.assertEqual('MATCHED', part['ncCheck']['status'])
        self.assertEqual('CANDIDATE', part['ncSource']['status'])
        self.assertEqual('EXACT_PART_POSITION', part['ncSource']['associationBasis'])
        self.assertEqual('MATCHED', part['ncHoleInventory']['modelPositionMapping'])

    def test_unfolded_holes_remain_readable_without_model_coordinate_mapping(self):
        add_hole(self.capture)
        self.replace_nc(lambda s: s.replace('EN\n', 'KA\n1.00 2.00 3.00 4.00 -12.29\nEN\n'))
        part = self.report()
        inventory = part['ncHoleInventory']
        self.assertEqual('READ', inventory['status'])
        self.assertEqual('UNFOLDED', inventory['coordinateScope'])
        self.assertEqual(1, inventory['holeCount'])
        self.assertEqual(10, inventory['holes'][0]['diameter'])
        self.assertEqual('NOT_ESTABLISHED', inventory['modelPositionMapping'])
        self.assertEqual('SKIPPED', part['ncCheck']['status'])
        self.assertEqual('BLOCKED', part['manufacturingStatus'])

    def test_actual_unfolded_nc_contains_two_diameter_30_inner_contours(self):
        path = Path(__file__).parent/'fixtures/native_nc/numbered-unfolded-plate.nc1'
        parsed = parse_nc(path.read_bytes().decode('utf-8-sig'))
        inventory = nc_hole_inventory(parsed)
        self.assertEqual('T3-P-3429', parsed['partPosition'])
        self.assertEqual([], parsed['issues'])
        self.assertEqual('UNFOLDED', inventory['coordinateScope'])
        self.assertEqual(2, inventory['holeCount'])
        self.assertEqual([30, 30], [h['diameter'] for h in inventory['holes']])
        self.assertEqual([[1665.88, 58.5], [530.21, 100]], [h['center'] for h in inventory['holes']])
        self.assertTrue(all(len(h['lineNumbers']) == 3 for h in inventory['holes']))
        with self.assertRaisesRegex(ValueError, 'explicit mapping'):
            match_nc_geometry({}, [], None, parsed, .01)

    def test_unusable_source_does_not_expose_nc_holes_as_usable_inventory(self):
        add_hole(self.capture)
        self.capture['numbering'][0]['upToDate'] = False
        part = self.report()
        self.assertEqual('UNAVAILABLE', part['ncSource']['status'])
        self.assertEqual('SKIPPED', part['ncHoleInventory']['status'])

    def test_unsupported_hole_semantics_not_reported_as_empty_nc(self):
        add_hole(self.capture)
        self.replace_nc(lambda s: s.replace('40.00s', '40.00o'))
        part = self.report()
        self.assertEqual('UNRESOLVED', part['ncHoleInventory']['status'])
        self.assertNotIn('holeCount', part['ncHoleInventory'])

    def test_unfolded_nc_acceptance_does_not_enable_unsupported_polybeam(self):
        change_part(self.capture, lambda p: p.update(runtimeType='Tekla.Structures.Model.PolyBeam'))
        self.replace_nc(lambda s: s.replace('EN\n', 'KA\n0.00 0.00 90.00 5.00 0.00\nEN\n'))
        part = self.report()
        self.assertEqual('UNRESOLVED', part['geometryStatus'])
        self.assertEqual('CANDIDATE', part['ncSource']['status'])
        self.assertIn('PART_TYPE_UNRESOLVED', [i['code'] for i in part['issues']])
        self.assertEqual('NOT_RUN',part['specializedGeometryStatus'])

    def test_nc_export_failures_do_not_fail_native_geometry(self):
        changes = [lambda c: c.update(apiReturn=False, captureStatus='FAILED', captureError='NC export failed'),
                   lambda c: c.update(files=[]),
                   lambda c: c.pop('exportLog'),
                   lambda c: c['settingsBefore'].update(sha256='bad'),
                   lambda c: c['numbering'][0].update(upToDate=False)]
        for change in changes:
            self.capture = native_capture()
            change(self.capture)
            part = self.report()
            self.assertEqual('EVIDENCE_MATCHED', part['geometryStatus'], part)
            self.assertEqual('UNAVAILABLE', part['ncSource']['status'])
            self.assertEqual('SKIPPED', part['ncCheck']['status'])

    def test_applicable_nc_conflict_remains_blocking(self):
        self.replace_nc(lambda s: s.replace('100.00 0.00', '99.00 0.00'))
        part = self.report()
        self.assertEqual('UNRESOLVED', part['geometryStatus'])
        self.assertEqual('CONFLICT', part['evidenceSufficiency'])
        self.assertIn('NC_GEOMETRY_CONFLICT', [i['code'] for i in part['issues']])
        self.assertTrue(any(f['proofStatus'] == 'CONFLICT' for f in part['featureEvidence']))

    def test_unsupported_nc_semantics_do_not_create_geometry_conflict(self):
        self.replace_nc(lambda s: s.replace('0.00u', '0.00o'))
        part = self.report()
        self.assertEqual('EVIDENCE_MATCHED', part['geometryStatus'])
        self.assertEqual('UNRESOLVED', part['ncCheck']['status'])
        self.assertNotEqual('CONFLICT', part['evidenceSufficiency'])

    def test_nc_collinear_subdivision_is_not_a_geometry_conflict(self):
        self.replace_nc(lambda s: s.replace('100.00 0.00 0.00',
            '50.00 0.00 0.00 0.00 0.00 0.00 0.00\n100.00 0.00 0.00'))
        part = self.report()
        self.assertEqual('EVIDENCE_MATCHED', part['geometryStatus'])
        self.assertEqual('UNRESOLVED', part['ncCheck']['status'])
        self.assertNotEqual('CONFLICT', part['evidenceSufficiency'])

    def test_fastener_hole_uses_user_rule_and_nc_is_only_corroboration(self):
        add_hole(self.capture)
        change_part(self.capture, lambda p: p['boltGroups']['value'][0]['properties'].update(Bolt=read(True)))
        part = self.report()
        holes = [f for f in part['featureEvidence'] if f['id'].startswith('materialHole:')]
        self.assertEqual(1, len(holes))
        self.assertTrue(holes[0]['ncEvidence'])
        self.assertEqual([], holes[0]['missingProofs'])
        self.assertEqual('SUFFICIENT', holes[0]['proofStatus'])
        self.assertEqual('BOLT-THROUGH-01',part['materialFinalState']['holeEvidence'][0]['rule'])
        self.assertEqual('EVIDENCE_MATCHED', part['geometryStatus'])

    def test_shared_number_nc_cannot_hide_native_operations_or_missing_hole(self):
        add_hole(self.capture)
        self.mismatch_instance()
        change_part(self.capture, lambda p: p.update(operations=read([{'id': 123, 'runtimeType': 'Cut'}])))
        part = self.report()
        self.assertEqual('UNRESOLVED', part['geometryStatus'])
        operations = next(f for f in part['featureEvidence'] if f['id'] == 'operations')
        self.assertEqual('INSUFFICIENT', operations['proofStatus'])
        holes = [f for f in part['featureEvidence'] if f['kind'] == 'ROUND']
        self.assertIn('HOLE_EFFECT_UNPROVEN', holes[0]['missingProofs'])

    def test_source_change_and_corruption_remain_blocking_without_nc(self):
        self.capture['files'] = []
        raw = json.loads(base64.b64decode(self.capture['after']['bytesBase64']))
        raw['parts'][0]['profile'] = read('PL12')
        self.capture['after'] = file_record('after.json', json.dumps(raw))
        part = self.report()
        self.assertEqual('BLOCKED', part['nativeSource']['status'])
        self.assertEqual('UNRESOLVED', part['geometryStatus'])
        self.assertTrue(all(f['proofStatus'] != 'SUFFICIENT' for f in part['featureEvidence']))
        self.capture['before']['sha256'] = 'bad'
        with self.assertRaises(ValueError):
            self.report()

    def test_per_feature_thickness_and_empty_operations_do_not_require_nc(self):
        self.mismatch_instance()
        part = self.report()
        features = {f['id']: f for f in part['featureEvidence']}
        for key in ('thickness', 'operations', 'holeInventory'):
            self.assertEqual('SUFFICIENT', features[key]['proofStatus'])
            self.assertEqual('NOT_NEEDED', features[key]['ncRole'])
        self.assertEqual(4, len([f for f in features.values() if f['kind'] == 'LINE']))

    def test_incomplete_native_hole_inventory_is_not_reported_as_nc_conflict(self):
        add_hole(self.capture)
        change_part(self.capture, lambda p: p['boltGroups']['value'][0]['properties'].update(BoltStandard=read('')))
        part = self.report()
        self.assertEqual('UNRESOLVED', part['geometryStatus'])
        self.assertEqual('SKIPPED', part['ncCheck']['status'])
        self.assertEqual('NATIVE_HOLE_INVENTORY_UNRESOLVED', part['ncCheck']['reason'])
        self.assertNotEqual('CONFLICT', part['evidenceSufficiency'])

    def test_native_only_missing_identity_never_becomes_matched(self):
        raw = json.loads(base64.b64decode(self.capture['before']['bytesBase64']))
        del raw['parts'][0]['guid']
        part = audit_native_geometry(raw, '0.00001')['parts'][0]
        self.assertEqual('BLOCKED', part['nativeSource']['status'])
        self.assertEqual('UNRESOLVED', part['geometryStatus'])

    def test_resolution_rejection_and_extra_hole_conflict_are_distinct(self):
        part = audit_native_geometry(self.capture, '0.00001', '0.01', '0.001')['parts'][0]
        self.assertEqual('UNAVAILABLE', part['ncSource']['status'])
        self.assertEqual('EVIDENCE_MATCHED', part['geometryStatus'])
        self.replace_nc(lambda s: s.replace('EN\n', 'BO\nv 20.00s 20.00 10.00 0.00 0.00 0.00 0.00\nEN\n'))
        part = self.report()
        self.assertEqual('CONFLICT', part['evidenceSufficiency'])
        self.assertEqual('HOLES', part['ncCheck']['featureScope'])

    def test_shared_number_nc_does_not_hide_native_thickness_conflict(self):
        self.mismatch_instance()
        change_part(self.capture, lambda p: p.update(profile=read('PL12')))
        part = self.report()
        self.assertEqual('UNRESOLVED', part['geometryStatus'])
        thickness = next(f for f in part['featureEvidence'] if f['id'] == 'thickness')
        self.assertEqual('INSUFFICIENT', thickness['proofStatus'])

    def test_cli_native_only_and_capture_budgets(self):
        with tempfile.TemporaryDirectory() as directory:
            source, out = Path(directory)/'raw.json', Path(directory)/'audit.json'
            source.write_bytes(base64.b64decode(self.capture['before']['bytesBase64']))
            args = ['audit-native-geometry', '--evidence', str(source), '--model-tolerance-mm', '0.00001',
                    '--out', str(out)]
            self.assertEqual(0, main(args))
            self.assertEqual('NOT_REQUESTED', json.loads(out.read_text('utf8'))['parts'][0]['ncSource']['status'])
            self.assertEqual(2, main(args))
            default_args = ['audit-native-geometry', '--evidence', str(source),
                            '--out', str(Path(directory)/'default-budget.json')]
            self.assertEqual(0, main(default_args))
            self.assertEqual(.1, json.loads(Path(default_args[-1]).read_text('utf8'))['modelToleranceMm'])
            source.write_text(json.dumps(self.capture), encoding='utf8')
            args[1] = '--capture'
            args[-1] = str(Path(directory)/'missing-budget.json')
            self.assertEqual(2, main(args))


if __name__ == '__main__':
    unittest.main()
