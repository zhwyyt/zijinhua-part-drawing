"""Final-solid admission: a readable flat plate does not need the narrower proofs."""

import math
import unittest

from test_modeling_independent_plate import plate, report
from test_native_facts import fixture, read, unknown
from test_native_geometry import FRAME, prism
from test_cut_final_state import circle_points
from zijinhua_tekla.part_drawing.dimension_scheme import hole_callout_text
from zijinhua_tekla.part_drawing.native_expressions import build_native_expression_requirements
from zijinhua_tekla.part_drawing.native_geometry import _hole_candidates


def cross3(a, b):
    return [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]]


def sub3(a, b):
    return [a[i]-b[i] for i in range(3)]


def face(points, loops=(), outward=1):
    edge = sub3(points[1], points[0])
    normal = [outward*component for component in cross3(edge, sub3(points[-1], points[0]))]
    rings = [points] + list(loops)
    return {'id': 'F', 'normal': read(normal), 'originPartId': read(10),
            'surfaceType': unknown(),
            'loops': read([{'id': 'L'+str(i), 'vertices': read(ring)} for i, ring in enumerate(rings)])}


def ellipse(center, rx, ry, count=16):
    return [[center[0]+rx*math.cos(i*2*math.pi/count),
             center[1]+ry*math.sin(i*2*math.pi/count), center[2]] for i in range(count)]


def sheared_solid():
    """Horizontal caps, 0.4 mm end skew, and a 2.281°-class elliptical opening."""
    bottom = [[0, 0, 0], [100, 0, 0], [100, 60, 0], [0, 60, 0]]
    top = [[0.4, 0, 10], [100.4, 0, 10], [100.4, 60, 10], [0.4, 60, 10]]
    low, high = ellipse([40, 30, 0], 10.15, 10), ellipse([40.3, 30, 10], 10.15, 10)
    faces = [face(bottom, [low], outward=-1), face(top, [high])]
    for i in range(4):
        j = (i+1) % 4
        faces.append(face([bottom[i], bottom[j], top[j], top[i]]))
    for i in range(len(low)):
        j = (i+1) % len(low)
        faces.append(face([low[i], low[j], high[j], high[i]]))
    return {'faces': read(faces), 'edges': read([]),
            'bounds': read({'min': [0, 0, 0], 'max': [100.4, 60, 10]})}


def group(identity, center, diameter, fastener, axis_x, axis_y):
    bolt = fixture()['parts'][0]['boltGroups']['value'][0]
    bolt['id'] = identity
    bolt['positions'] = read([list(center)+[5]])
    bolt['coordinateSystem'] = read({'origin': [0, 0, 0], 'axisX': list(axis_x), 'axisY': list(axis_y)})
    bolt['properties']['Bolt'] = read(fastener)
    bolt['properties']['BoltSize'] = read(diameter-2)
    bolt['properties']['Tolerance'] = read(2)
    bolt['properties']['SlottedHoleX'] = read(0)
    bolt['properties']['SlottedHoleY'] = read(0)
    bolt['targets']['partToBoltTo'] = read({'id': 10})
    bolt['targets']['partToBeBolted'] = read({'id': 10})
    bolt['targets']['otherParts'] = read([])
    if not fastener:
        bolt['solidWithHighAccuracy'] = read(prism(circle_points(center, diameter / 2)))
    return bolt


def opened_plate():
    raw = plate()
    part = raw['parts'][0]
    # The end plane contains both the bottom edge at x=100 and the top edge at x=100.4.
    cut = {'id': 30, 'runtimeType': 'Tekla.Structures.Model.CutPlane',
           'plane': read({'origin': [100, 0, 0], 'axisX': [0, 1, 0], 'axisY': [0.4, 0, 10]})}
    part['operations'] = read([cut])
    part['children'] = read([{'id': 30, 'runtimeType': cut['runtimeType']}])
    part['finalSolid'] = read(sheared_solid())
    part['boltGroups'] = read([
        group(41, (40.15, 30), 20, False, (0, 1, 0), (-0.999, 0, 0.03)),
        group(42, (20, 15), 10, True, (1, 0, 0), (0, 1, 0))])
    return raw


class ObservedFlatSolidTests(unittest.TestCase):
    def test_sloped_round_hole_keeps_an_in_plane_direction(self):
        bolt = group(41, (40, 30), 20, False, (0, 0, 1), (0, 1, 0))
        shapes = _hole_candidates(bolt, FRAME, 'bolt')
        self.assertEqual([1.0, 0.0], shapes[0]['direction'])
        bolt['properties']['SlottedHoleX'] = read(5)
        with self.assertRaises(ValueError) as raised:
            _hole_candidates(bolt, FRAME, 'bolt')
        self.assertIn('plate normal', str(raised.exception))

    def test_skewed_end_and_elliptical_opening_reach_layout(self):
        raw = opened_plate()
        result = report(raw)
        self.assertEqual('SUFFICIENT', result['evidenceSufficiency'], result['issues'])
        material = result['materialFinalState']
        self.assertEqual('OBSERVED_FINAL_SOLID_WITHIN_TOLERANCE', material['basis'])
        self.assertEqual('NOT_APPLICABLE', result['sectionGeometry']['status'])
        pipe = next(hole for hole in material['holes'] if hole['diameter'] == 20)
        self.assertEqual('ROUND', pipe['kind'])
        self.assertFalse(pipe['fastener'])
        self.assertAlmostEqual(40.3, pipe['center'][0])
        self.assertAlmostEqual(30, pipe['center'][1])
        plan = build_native_expression_requirements(raw)['parts'][0]
        self.assertEqual('READY', plan['status'], plan['issues'])
        self.assertNotIn('sectionRequirements', plan)
        self.assertEqual(['1*D10(10x10)', '1Φ20'],
                         sorted(hole_callout_text(item) for item in plan['groups']))
        self.assertFalse(any(item['aspect'] == 'orientation' for item in plan['requirements']))
        located = next(hole for hole in plan['facts']['holes'] if hole.get('diameter') == 20)
        self.assertNotIn('origin', located)
        point = plan['facts']['points'][located['pointId']]
        self.assertAlmostEqual(40.3, point[0])
        self.assertAlmostEqual(30, point[1])

    def test_earlier_parallel_plane_outside_the_solid_does_not_block(self):
        raw = opened_plate()
        part = raw['parts'][0]
        earlier = {'id': 31, 'runtimeType': 'Tekla.Structures.Model.CutPlane',
                   'plane': read({'origin': [110, 0, 0], 'axisX': [0, 1, 0], 'axisY': [0.4, 0, 10]})}
        part['operations']['value'].append(earlier)
        part['children']['value'].append({'id': 31, 'runtimeType': earlier['runtimeType']})
        result = report(raw)
        self.assertEqual('SUFFICIENT', result['evidenceSufficiency'], result['issues'])
        self.assertEqual('OBSERVED_FINAL_SOLID_WITHIN_TOLERANCE', result['materialFinalState']['basis'])

    def test_unread_contradiction_partial_face_and_missing_opening_stay_blocked(self):
        raw = opened_plate()
        part = raw['parts'][0]
        part['finalSolid'] = read(prism([[0, 0], [100, 0], [100, 60], [0, 60]]))
        self.assertNotEqual('SUFFICIENT', report(raw)['evidenceSufficiency'])
        raw = opened_plate()
        raw['parts'][0]['children']['value'].append({'id': 99, 'runtimeType': 'Unknown'})
        self.assertNotEqual('SUFFICIENT', report(raw)['evidenceSufficiency'])
        raw = opened_plate()
        solid = raw['parts'][0]['finalSolid']['value']
        solid['faces']['value'].append(face([[20, 20, 2], [30, 20, 2], [30, 30, 6]]))
        self.assertNotEqual('SUFFICIENT', report(raw)['evidenceSufficiency'])
        raw = opened_plate()
        raw['parts'][0]['boltGroups']['value'][0]['positions'] = read([[10, 10, 5]])
        self.assertNotEqual('SUFFICIENT', report(raw)['evidenceSufficiency'])


if __name__ == '__main__':
    unittest.main()
