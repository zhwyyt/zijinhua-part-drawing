"""Faceted Boolean-cut contours become arcs; other facets stay lines.

The 2026-10-29 beam fillet was stored as chords on the final solid. The cutter
chamfer parameter could not be rebuilt, and the observed outline kept every
chord as a line. Only a chain that lies on a BooleanPart cutter and on one
circle within the model tolerance is fitted.
"""

import math
import unittest

from test_cut_final_state import final_solid
from test_modeling_independent_plate import fit_plate, report
from test_native_facts import read, unknown
from test_native_geometry import prism
from zijinhua_tekla.part_drawing.analytic_geometry import line
from zijinhua_tekla.part_drawing.native_expressions import build_native_expression_requirements
from zijinhua_tekla.part_drawing.native_geometry import fit_cut_produced_contours


TOLERANCE = 0.1


def arc_points(center, radius, start, sweep, count):
    return [[center[0] + radius * math.cos(start + sweep * i / count),
             center[1] + radius * math.sin(start + sweep * i / count)]
            for i in range(count + 1)]


def segments_of(points, refs=('final',)):
    return [line(a, b, list(refs)) for a, b in zip(points, points[1:] + points[:1])]


def loop(points, operation_id, source='cut'):
    return {'operationId': operation_id, 'sourceRef': source, 'points': points}


class CutContourFitTests(unittest.TestCase):
    def test_fillet_on_the_cutter_becomes_one_arc_and_records_the_fit(self):
        # Quarter circle R35, 12 chords: the same tessellation as the beam fillet.
        fillet = arc_points((0, 0), 35, 0, math.pi / 2, 12)
        outline = fillet + [[0, 100], [80, 100], [80, 0]]
        stock = segments_of([[0, 0], [100, 0], [100, 100], [0, 100]], ('stock',))
        cutter = fillet + [[-5, 35], [-5, -5], [35, -5]]
        fitted = fit_cut_produced_contours(
            segments_of(outline), stock, [loop(cutter, 20, 'ops/0')], TOLERANCE)
        arcs = [segment for segment in fitted if segment['kind'] == 'ARC']
        self.assertEqual(1, len(arcs))
        arc = arcs[0]
        self.assertEqual('CUT_CONTOUR_FITTED_WITHIN_TOLERANCE', arc['basis'])
        self.assertAlmostEqual(35, arc['radius'])
        self.assertAlmostEqual(0, arc['center'][0])
        self.assertAlmostEqual(0, arc['center'][1])
        self.assertEqual([20], arc['fit']['operationIds'])
        self.assertEqual(12, arc['fit']['chordCount'])
        self.assertEqual('CUT_CONTOUR_CIRCLE', arc['fit']['method'])
        self.assertLessEqual(arc['fit']['maxRadialResidualMm'], TOLERANCE)
        self.assertLessEqual(arc['fit']['maxChordDeviationMm'], TOLERANCE)
        self.assertIn('ops/0', arc['sourceRefs'])
        self.assertTrue(all(segment['kind'] == 'LINE' for segment in fitted if segment is not arc))
        ends = {(round(segment['start'][0], 3), round(segment['start'][1], 3),
                 round(segment['end'][0], 3), round(segment['end'][1], 3))
                for segment in fitted if segment['kind'] == 'LINE'}
        self.assertIn((0, 35, 0, 100), ends)
        self.assertIn((80, 100, 80, 0), ends)

    def test_stock_straight_cut_notch_and_uncuttered_facets_stay_lines(self):
        fillet = arc_points((0, 0), 35, 0, math.pi / 2, 12)
        notch = [[40, 0], [40, 15], [55, 15], [55, 0]]
        # The notch lies on its own cutter but is not a circle. The right edge
        # is a straight cut on a third cutter. Neither becomes an arc.
        stock = segments_of([[0, 0], [120, 0], [120, 100], [0, 100]])
        loops = [
            loop(fillet + [[-5, 35], [-5, -5], [35, -5]], 20),
            loop(notch, 21),
            loop([[100, 0], [100, 100], [110, 100], [110, 0]], 22),
        ]
        outline = fillet + [[0, 100], [100, 100], [100, 0], [55, 0], [55, 15], [40, 15], [40, 0]]
        fitted = fit_cut_produced_contours(segments_of(outline), stock, loops, TOLERANCE)
        arcs = [segment for segment in fitted if segment['kind'] == 'ARC']
        self.assertEqual(1, len(arcs))
        self.assertEqual([20], arcs[0]['fit']['operationIds'])
        self.assertTrue(any(segment['kind'] == 'LINE' and segment['start'] == [100, 100]
                            and segment['end'] == [100, 0] for segment in fitted))
        self.assertTrue(any(segment['kind'] == 'LINE' and segment['start'] == [55, 0]
                            and segment['end'] == [55, 15] for segment in fitted))

    def test_no_cutter_and_coarse_chords_are_not_fitted(self):
        fillet = arc_points((0, 0), 35, 0, math.pi / 2, 12)
        stock = segments_of([[0, 0], [100, 0], [100, 100], [0, 100]])
        outline = fillet[:-1] + [[0, 100], [80, 100], [80, 0]]
        untouched = fit_cut_produced_contours(segments_of(outline), stock, [], TOLERANCE)
        self.assertTrue(all(segment['kind'] == 'LINE' for segment in untouched))
        coarse = arc_points((0, 0), 35, 0, math.pi / 2, 2)
        coarse_outline = coarse[:-1] + [[0, 100], [80, 100], [80, 0]]
        refused = fit_cut_produced_contours(
            segments_of(coarse_outline), stock, [loop(coarse + [[-5, 35], [-5, -5], [35, -5]], 20)],
            TOLERANCE)
        self.assertTrue(all(segment['kind'] == 'LINE' for segment in refused))

    def test_observed_outline_fits_only_the_boolean_cut_fillet(self):
        fillet = arc_points((0, 0), 10, 0, math.pi / 2, 8)
        outer = fillet + [[0, 60], [90, 60], [90, 0]]
        cutter = fillet + [[-4, 10], [-4, -4], [10, -4]]
        operation = {'id': 20, 'runtimeType': 'Tekla.Structures.Model.BooleanPart',
                     'type': read({'name': 'BOOLEAN_CUT'}), 'operativePart': read({'id': 20}),
                     'operativeGeometry': unknown(),
                     'operativeSolid': read(prism(cutter))}
        raw = fit_plate([operation], final_solid(outer))
        result = report(raw)
        self.assertEqual('SUFFICIENT', result['evidenceSufficiency'], result['issues'])
        self.assertEqual('OBSERVED_FINAL_SOLID_WITHIN_TOLERANCE', result['materialFinalState']['basis'])
        segments = result['finalOutline']['segments']
        arcs = [segment for segment in segments if segment['kind'] == 'ARC']
        self.assertEqual(1, len(arcs), [(segment['kind'], segment['start'], segment['end']) for segment in segments])
        self.assertAlmostEqual(10, arcs[0]['radius'])
        self.assertEqual([20], arcs[0]['fit']['operationIds'])
        self.assertEqual('CUT_CONTOUR_FITTED_WITHIN_TOLERANCE', arcs[0]['basis'])
        self.assertTrue(any(segment['kind'] == 'LINE' and abs(segment['start'][0] - 90) < 1e-6
                            and abs(segment['end'][0] - 90) < 1e-6 for segment in segments))
        plan = build_native_expression_requirements(raw)['parts'][0]
        self.assertEqual('READY', plan['status'], plan['issues'])
        fact_arcs = [segment for segment in plan['facts']['segments'] if segment['kind'] == 'ARC']
        self.assertEqual(1, len(fact_arcs))
        self.assertAlmostEqual(10, fact_arcs[0]['radius'])


if __name__ == '__main__':
    unittest.main()
