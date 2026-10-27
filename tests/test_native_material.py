"""Independent complete-shell fixtures for joint hole/Boolean material verification."""

import base64
import copy
import json
import unittest

from test_native_facts import read
from test_native_geometry import native_capture, add_hole, prism, RECT
from test_cut_final_state import evidence, final_solid, circle_points, operation
from zijinhua_tekla.part_drawing.native_geometry import audit_native_geometry


def plate_with_hole(bolt=True):
    capture = native_capture()
    group = add_hole(capture)
    raw = json.loads(base64.b64decode(capture['before']['bytesBase64']))
    group = raw['parts'][0]['boltGroups']['value'][0]
    group['properties']['Bolt'] = read(bolt)
    raw['parts'][0]['finalSolid'] = read(final_solid(holes=[(circle_points([40, 30], 5), group['id'])]))
    return raw


class NativeMaterialTests(unittest.TestCase):
    def report(self, raw):
        return audit_native_geometry(raw)['parts'][0]

    def test_real_target_walls_or_confirmed_through_rules_keep_distinct_basis(self):
        for bolt in (True, False):
            raw = plate_with_hole(bolt)
            before = copy.deepcopy(raw)
            part = self.report(raw)
            self.assertEqual('MATCHED', part['materialFinalState']['status'], part['materialFinalState'])
            self.assertEqual('SUFFICIENT', part['evidenceSufficiency'], part['remainingProofs'])
            self.assertEqual('BLOCKED', part['manufacturingStatus'])
            self.assertEqual(1, part['finishedHoleCandidates']['candidateCount'])
            self.assertEqual(before, raw)
            raw['parts'][0]['finalSolid'] = read(prism(RECT))
            material = self.report(raw)['materialFinalState']
            self.assertEqual('MATCHED',material['status'])
            self.assertEqual('NOT_EXPOSED_BY_PART_SOLID',material['finalHoleSurfaceObservation'])
            self.assertEqual('RULE_CONFIRMED_THROUGH_HOLE' if bolt else 'HOLE_ONLY_CUTTER_CONFIRMED',
                             material['holeEvidence'][0]['status'])

    def test_void_must_belong_to_selected_instance(self):
        raw = plate_with_hole()
        group = raw['parts'][0]['boltGroups']['value'][0]
        group['targets']['partToBoltTo'] = read({'id': 99})
        group['targets']['partToBeBolted'] = read({'id': 99})
        self.assertEqual('UNRESOLVED', self.report(raw)['materialFinalState']['status'])
        group['targets']['otherParts'] = read([{'id': 10}])
        self.assertEqual('MATCHED', self.report(raw)['materialFinalState']['status'])

    def test_mixed_boolean_swallowing_small_hole_counts_final_material_once(self):
        raw = plate_with_hole()
        op = operation(30, center=[40,30], radius=10)
        part = raw['parts'][0]
        part['operations'] = read([op])
        part['children'] = read([{'id':30, 'runtimeType':'Tekla.Structures.Model.BooleanPart'}])
        part['finalSolid'] = read(final_solid(holes=[(circle_points([40,30],10),30)]))
        report = self.report(raw)
        self.assertEqual('SUFFICIENT',report['evidenceSufficiency'],report['issues'])
        self.assertEqual('EVIDENCE_MATCHED',report['geometryStatus'])
        self.assertEqual(1,report['finishedHoleCandidates']['candidateCount'])
        self.assertEqual(20,report['finishedHoleCandidates']['candidates'][0]['diameter'])
        mappings = report['materialFinalState']['operationMappings']
        self.assertEqual({'BOOLEAN_CUT','HOLE'},{m['sourceKind'] for m in mappings})
        self.assertEqual('NO_EXPOSED_BOUNDARY',next(m for m in mappings if m['sourceKind']=='HOLE')['status'])
        self.assertNotIn('HOLE_CUT_INTERACTIONS_UNPROVEN',report['remainingProofs'])

    def test_hole_and_boolean_notch_preserve_both_boundary_sources(self):
        raw = plate_with_hole()
        part = raw['parts'][0]
        # Independent rectangular notch leaves the hole separated and both walls observable.
        op = operation(30, points=[[80,40],[110,40],[110,70],[80,70]])
        part['operations'] = read([op])
        part['children'] = read([{'id':30,'runtimeType':'Tekla.Structures.Model.BooleanPart'}])
        group = part['boltGroups']['value'][0]
        part['finalSolid'] = read(final_solid([[0,0],[100,0],[100,40],[80,40],[80,60],[0,60]],
            holes=[(circle_points([40,30],5),group['id'])],outer_origins=[10,10,30,30,10,10]))
        report = self.report(raw)
        self.assertEqual('MATCHED',report['materialFinalState']['status'],report['materialFinalState'])
        self.assertEqual(6,len(report['finalOutline']['segments']))
        self.assertEqual('SUFFICIENT',report['evidenceSufficiency'])

    def test_missing_walls_extra_void_wrong_radius_and_reverse_normal_fail(self):
        for change in ('wall','radius','extra','normal'):
            raw = plate_with_hole()
            solid = raw['parts'][0]['finalSolid']['value']
            if change == 'wall': solid['faces']['value'].pop()
            if change == 'radius':
                raw['parts'][0]['finalSolid'] = read(final_solid(holes=[(circle_points([40,30],6),10)]))
            if change == 'extra':
                raw['parts'][0]['finalSolid'] = read(final_solid(holes=[(circle_points([40,30],5),10),(circle_points([70,30],5),10)]))
            if change == 'normal': solid['faces']['value'][-1]['normal']['value'] = [0,0,1]
            with self.subTest(change=change):
                self.assertEqual('UNRESOLVED',self.report(raw)['materialFinalState']['status'])
                self.assertNotEqual('SUFFICIENT',self.report(raw)['evidenceSufficiency'])

    def test_no_hole_plate_needs_complete_outward_shell(self):
        raw = evidence([],final_solid())
        self.assertEqual('SUFFICIENT',self.report(raw)['evidenceSufficiency'])
        raw['parts'][0]['finalSolid']['value']['faces']['value'][0]['normal']['value'] = [0,0,1]
        self.assertEqual('UNRESOLVED',self.report(raw)['materialFinalState']['status'])
        self.assertNotEqual('SUFFICIENT',self.report(raw)['evidenceSufficiency'])

    def test_unknown_children_and_unreadable_targets_stay_blocked(self):
        for change in ('child','target'):
            raw = plate_with_hole()
            part = raw['parts'][0]
            if change == 'child': part['children'] = read([{'id':33,'runtimeType':'unknown'}])
            if change == 'target': part['boltGroups']['value'][0]['targets']['otherParts']['status'] = 'UNAVAILABLE'
            with self.subTest(change=change):
                self.assertNotEqual('SUFFICIENT',self.report(raw)['evidenceSufficiency'])

    def test_stud_is_surface_attachment_and_never_adds_a_hole(self):
        raw = plate_with_hole()
        part = raw['parts'][0]
        part['boltGroups']['value'][0]['properties']['BoltStandard'] = read('STUD')
        part['finalSolid'] = read(prism(RECT))
        report = self.report(raw)
        self.assertEqual('SUFFICIENT',report['evidenceSufficiency'],report['remainingProofs'])
        self.assertEqual(0,report['finishedHoleCandidates']['candidateCount'])
        self.assertEqual('STUD-NO-HOLE-01',next(f for f in report['featureEvidence'] if f['kind']=='ATTACHMENT_STUD')['rule'])
        # A physically observed unexplained hole is still an inconsistency.
        part['finalSolid'] = read(final_solid(holes=[(circle_points([40,30],5),10)]))
        self.assertNotEqual('SUFFICIENT',self.report(raw)['evidenceSufficiency'])

    def test_rule_cannot_guess_multitarget_special_hole_parameters(self):
        raw = plate_with_hole()
        part = raw['parts'][0]
        part['finalSolid'] = read(prism(RECT))
        group = part['boltGroups']['value'][0]
        group['properties']['SlottedHoleX'] = read(20)
        group['targets']['otherParts'] = read([{'id':99}])
        self.assertEqual('UNRESOLVED',self.report(raw)['materialFinalState']['status'])

    def test_rule_cannot_override_missing_source_identity(self):
        raw = plate_with_hole()
        del raw['parts'][0]['guid']
        self.assertEqual('INSUFFICIENT',self.report(raw)['evidenceSufficiency'])

    def test_actual_target_shell_does_not_hide_conflicting_hole_only_cutter(self):
        raw = plate_with_hole(False)
        raw['parts'][0]['boltGroups']['value'][0]['solidWithHighAccuracy'] = read(prism(RECT))
        self.assertNotEqual('SUFFICIENT',self.report(raw)['evidenceSufficiency'])


if __name__ == '__main__':
    unittest.main()
