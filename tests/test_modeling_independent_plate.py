"""Building command is not the manufacturing shape; retain negative geometry gates."""

import base64
import copy
import json
import unittest

from test_cut_final_state import evidence, final_solid, operation
from test_native_geometry import FRAME, RECT, prism, native_capture, add_hole
from test_native_facts import read, unknown
from zijinhua_tekla.part_drawing.native_geometry import audit_native_geometry
from zijinhua_tekla.part_drawing.solid_prisms import _point_set_equal
from zijinhua_tekla.part_drawing.native_expressions import build_native_expression_requirements
from zijinhua_tekla.part_drawing.native_layout import build_layout


def plate(kind='Beam', operations=(), solid=None):
    raw = evidence(list(operations), solid or final_solid())
    part = raw['parts'][0]
    part['runtimeType'] = 'Tekla.Structures.Model.'+kind
    part['rawSolid'] = read(prism(RECT))
    part['profile'] = read('PL10*60')
    if kind != 'ContourPlate':
        # A Beam has no contour; a PolyBeam contour is a path, not its boundary.
        part['modelingContour'] = read(unknown())
    return raw


def report(raw):
    return audit_native_geometry(raw)['parts'][0]


def fitting(identity, origin, axis_x=(0, 1, 0), axis_y=(0, 0, 1)):
    return {'id': identity, 'runtimeType': 'Tekla.Structures.Model.Fitting',
            'plane': read({'origin': list(origin), 'axisX': list(axis_x), 'axisY': list(axis_y)})}


def fit_plate(operations, solid, kind='Beam'):
    raw = plate(kind, operations, solid)
    raw['parts'][0]['children'] = read([
        {'id': op['id'], 'runtimeType': op['runtimeType']} for op in operations])
    return raw


def outline_points(result):
    return [segment['start'] for segment in result['finalOutline']['segments']]


def swept_section(section):
    """Independent valid 3D dogleg/wedge shell, swept across the 60 mm width."""
    solid = prism(section, high=60)
    def swap(point):
        return [point[0], point[2], point[1]]
    for face in solid['faces']['value']:
        face['normal']['value'] = swap(face['normal']['value'])
        for loop in face['loops']['value']:
            loop['vertices']['value'] = [swap(p) for p in loop['vertices']['value']]
    for edge in solid['edges']['value']:
        edge.update(start=swap(edge['start']), end=swap(edge['end']))
    solid['bounds']['value'] = {k: swap(v) for k,v in solid['bounds']['value'].items()}
    return solid


class ModelingIndependentPlateTests(unittest.TestCase):
    def test_same_plate_from_different_commands_has_same_final_geometry_and_expressions(self):
        reports = []
        for kind in ('ContourPlate', 'Beam', 'PolyBeam'):
            with self.subTest(kind=kind):
                raw = plate(kind)
                result = report(raw)
                self.assertEqual('SUFFICIENT', result['evidenceSufficiency'], result['issues'])
                batch = build_native_expression_requirements(raw)
                expressions = batch['parts'][0]
                self.assertEqual('READY', expressions['status'], expressions['issues'])
                plan = build_layout(expressions, batch['geometryAudit']['parts'][0], raw['parts'][0])
                reports.append((result['thickness']['value'], sorted(
                    (tuple(s['start']), tuple(s['end'])) for s in result['finalOutline']['segments']),
                    expressions['groups'], expressions['requirements'], plan['dimensions'], plan['notes']))
        self.assertEqual(3, len(reports))
        self.assertEqual(reports[0], reports[1])
        self.assertEqual(reports[0], reports[2])

    def test_shape_does_not_depend_on_dstv_axis_being_plate_normal(self):
        raw = plate()
        raw['parts'][0]['dstvCoordinateSystem'] = read(dict(FRAME, axisX=[0,0,1], axisY=[0,1,0]))
        result = report(raw)
        self.assertEqual('SUFFICIENT', result['evidenceSufficiency'], result['issues'])
        self.assertAlmostEqual(10, result['thickness']['value'])

    def test_boolean_notch_uses_raw_stock_and_rechecks_final_shell(self):
        op = operation(20, points=[[80,40],[110,40],[110,70],[80,70]])
        outer = [[0,0],[100,0],[100,40],[80,40],[80,60],[0,60]]
        raw = plate(operations=[op], solid=final_solid(outer, outer_origins=[10,10,20,20,10,10]))
        result = report(raw)
        self.assertEqual('SUFFICIENT', result['evidenceSufficiency'], result['materialFinalState'])
        self.assertEqual(6, len(result['finalOutline']['segments']))

    def test_cut_plane_is_a_through_halfplane_not_a_boolean_object(self):
        op = {'id': 30, 'runtimeType': 'Tekla.Structures.Model.CutPlane',
              'plane': read({'origin':[80,0,0], 'axisX':[0,1,0], 'axisY':[0,0,1]})}
        raw = plate(operations=[op], solid=final_solid([[0,0],[80,0],[80,60],[0,60]]))
        raw['parts'][0]['children'] = read([{'id':30, 'runtimeType':op['runtimeType']}])
        result = report(raw)
        self.assertEqual('SUFFICIENT', result['evidenceSufficiency'], result['materialFinalState'])
        self.assertEqual('CUT_PLANE', result['materialFinalState']['operationMappings'][0]['sourceKind'])

    def test_unknown_profile_does_not_replace_unique_geometric_thickness(self):
        raw = plate()
        raw['parts'][0]['profile'] = read('CUSTOM_RECTANGLE')
        raw['parts'][0]['reports']['PROFILE.WIDTH'] = read(60)
        result = report(raw)
        self.assertEqual('SUFFICIENT', result['evidenceSufficiency'], result['issues'])
        self.assertAlmostEqual(10, result['thickness']['value'])

    def test_missing_raw_curved_edges_missing_faces_and_conflicting_thickness_rejected(self):
        for mutation in (
            lambda p: p.pop('rawSolid'),
            lambda p: p['rawSolid']['value']['edges']['value'][0].update(apiType='EDGE_TYPE_CURVED_SURFACE'),
            lambda p: p['rawSolid']['value']['faces']['value'].pop(),
            lambda p: p.update(profile=read('PL12*60')),
            lambda p: p['reports'].update({'PROFILE.WIDTH': read(12)}),
            lambda p: p['finalSolid']['value']['faces']['value'][0].update(originPartId=read(999)),
        ):
            raw = plate()
            mutation(raw['parts'][0])
            self.assertNotEqual('SUFFICIENT', report(raw)['evidenceSufficiency'])

    def test_bent_stock_and_variable_thickness_remain_geometric_failures(self):
        for section in (
            [[0,0],[50,0],[100,5],[150,5],[150,15],[100,15],[50,10],[0,10]],
            [[0,0],[100,0],[100,15],[0,10]],
        ):
            raw = plate()
            changed = swept_section(section)
            raw['parts'][0]['rawSolid'] = read(changed)
            raw['parts'][0]['finalSolid'] = read(changed)
            result = report(raw)
            self.assertNotEqual('SUFFICIENT', result['evidenceSufficiency'])
            self.assertNotIn('Only ContourPlate', str(result['issues']))

    def test_oblique_cut_plane_and_unknown_children_do_not_pass(self):
        raw = plate()
        raw['parts'][0]['operations'] = read([{'id':30, 'runtimeType':'Tekla.Structures.Model.CutPlane',
            'plane':read({'origin':[80,0,0], 'axisX':[0,1,0], 'axisY':[1,0,1]})}])
        self.assertNotEqual('SUFFICIENT', report(raw)['evidenceSufficiency'])
        raw = plate()
        raw['parts'][0]['children'] = read([{'id':99, 'runtimeType':'Unknown'}])
        self.assertNotEqual('SUFFICIENT', report(raw)['evidenceSufficiency'])

    def test_confirmed_bolt_holes_still_join_the_common_material_pipeline(self):
        capture = native_capture()
        add_hole(capture)
        raw = json.loads(base64.b64decode(capture['before']['bytesBase64']))
        part = raw['parts'][0]
        part.update(runtimeType='Tekla.Structures.Model.Beam', rawSolid=read(prism(RECT)),
                    modelingContour=read(unknown()), profile=read('PL10*60'))
        part['boltGroups']['value'][0]['properties']['Bolt'] = read(True)
        result = report(raw)
        self.assertEqual('SUFFICIENT', result['evidenceSufficiency'], result['materialFinalState'])
        self.assertEqual(1, len(result['materialFinalState']['holes']))
        self.assertEqual('READY', build_native_expression_requirements(raw)['parts'][0]['status'])

    def test_beam_operative_part_is_not_forced_to_have_a_contour(self):
        op = operation(20, points=[[80,40],[110,40],[110,70],[80,70]])
        operative = op['operativeGeometry']['value']
        # A 20 mm thick cutter needs wider in-plane dimensions to have a unique thin axis.
        operative.update(id=20, runtimeType='Tekla.Structures.Model.Beam',
                         rawSolid=copy.deepcopy(op['operativeSolid']), modelingContour=read(unknown()))
        outer = [[0,0],[100,0],[100,40],[80,40],[80,60],[0,60]]
        raw = plate(operations=[op], solid=final_solid(outer, outer_origins=[10,10,20,20,10,10]))
        result = report(raw)
        self.assertEqual('SUFFICIENT', result['evidenceSufficiency'], result['planarCuts'])

    def test_cut_plane_keeps_either_side_and_bolt_fallback_does_not_erase_it(self):
        for axis in ([0,0,1], [0,0,-1]):
            op = {'id':30, 'runtimeType':'Tekla.Structures.Model.CutPlane',
                  'plane':read({'origin':[20,0,0], 'axisX':[0,1,0], 'axisY':axis})}
            raw = plate(operations=[op], solid=final_solid([[20,0],[100,0],[100,60],[20,60]]))
            raw['parts'][0]['children'] = read([{'id':30, 'runtimeType':op['runtimeType']}])
            capture = native_capture()
            add_hole(capture)
            bolts = json.loads(base64.b64decode(capture['before']['bytesBase64']))['parts'][0]['boltGroups']
            bolts['value'][0]['properties']['Bolt'] = read(True)
            raw['parts'][0]['boltGroups'] = bolts
            result = report(raw)
            self.assertEqual('SUFFICIENT', result['evidenceSufficiency'], result['materialFinalState'])
            self.assertEqual(1, len(result['materialFinalState']['holes']))

    def test_fitting_shortens_extends_or_replaces_one_oblique_end(self):
        cases = (
            ('shorten', fitting(30, (80, 0, 0)), [[0, 0], [80, 0], [80, 60], [0, 60]]),
            ('extend', fitting(30, (120, 0, 0)), [[0, 0], [120, 0], [120, 60], [0, 60]]),
            ('oblique', fitting(30, (110, 0, 0), (-40, 60, 0)), [[0, 0], [110, 0], [70, 60], [0, 60]]),
            ('reversed-normal', fitting(30, (80, 0, 0), (0, 1, 0), (0, 0, -1)),
             [[0, 0], [80, 0], [80, 60], [0, 60]]),
        )
        for name, op, expected in cases:
            with self.subTest(name=name):
                raw = fit_plate([op], final_solid(expected), 'PolyBeam')
                result = report(raw)
                self.assertEqual('SUFFICIENT', result['evidenceSufficiency'], result['issues'])
                self.assertEqual('FITTING', result['planarCuts'][0]['sourceKind'])
                self.assertTrue(_point_set_equal(outline_points(result), expected, .1))
                self.assertEqual('READY', build_native_expression_requirements(raw)['parts'][0]['status'])

    def test_two_fittings_must_explain_both_ends_in_either_order(self):
        ops = [fitting(30, (20, 0, 0)), fitting(31, (110, 0, 0))]
        expected = [[20, 0], [110, 0], [110, 60], [20, 60]]
        raw = fit_plate(ops, final_solid(expected))
        result = report(raw)
        self.assertEqual('SUFFICIENT', result['evidenceSufficiency'], result['issues'])
        self.assertEqual(['FITTING', 'FITTING'], [cut['sourceKind'] for cut in result['planarCuts']])
        self.assertTrue(_point_set_equal(outline_points(result), expected, .1))
        raw['parts'][0]['operations']['value'].reverse()
        raw['parts'][0]['children']['value'].reverse()
        reversed_result = report(raw)
        self.assertEqual('SUFFICIENT', reversed_result['evidenceSufficiency'], reversed_result['issues'])
        self.assertTrue(_point_set_equal(outline_points(reversed_result), expected, .1))

    def test_fitting_rejects_thickness_tilt_missed_plane_and_unmapped_child(self):
        # A square end the solid actually has is measurable. The fitting plane
        # being oblique through the thickness is not a separate block.
        oblique = fitting(30, (80, 0, 0), (0, 1, 0), (1, 0, 1))
        raw = fit_plate([oblique], final_solid([[0, 0], [80, 0], [80, 60], [0, 60]]))
        result = report(raw)
        self.assertEqual('SUFFICIENT', result['evidenceSufficiency'], result['issues'])
        self.assertTrue(_point_set_equal(outline_points(result), [[0, 0], [80, 0], [80, 60], [0, 60]], .1))
        missed = fitting(30, (80, 0, 0))
        missed_plate = fit_plate([missed], final_solid([[0, 0], [70, 0], [70, 60], [0, 60]]))
        self.assertNotEqual('SUFFICIENT', report(missed_plate)['evidenceSufficiency'])
        raw = fit_plate([fitting(30, (80, 0, 0))], final_solid([[0, 0], [80, 0], [80, 60], [0, 60]]))
        raw['parts'][0]['children']['value'].append({'id': 99, 'runtimeType': 'Tekla.Structures.Model.Fitting'})
        self.assertNotEqual('SUFFICIENT', report(raw)['evidenceSufficiency'])

    def test_nonflat_final_solid_cannot_pass_even_with_a_valid_flat_stock(self):
        for profile in ('PL10*60', 'CUSTOM_RECTANGLE'):
            raw = plate()
            raw['parts'][0]['profile'] = read(profile)
            raw['parts'][0]['finalSolid'] = read(prism(RECT, high=12))
            self.assertNotEqual('SUFFICIENT', report(raw)['evidenceSufficiency'])


if __name__ == '__main__':
    unittest.main()
