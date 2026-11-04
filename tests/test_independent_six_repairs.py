"""Regression obligations from the six failed historical native pages."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from shapely.geometry import box
from zijinhua_tekla.part_drawing.independent_layout.contracts import ContractError, seal
from zijinhua_tekla.part_drawing.independent_layout.expressions import adapt_expressions
from zijinhua_tekla.part_drawing.independent_layout.complete_trial import _dimension_candidates, plan_complete_trial
from zijinhua_tekla.part_drawing.independent_layout.template_regions import template_regions, verify_template_clearance
from zijinhua_tekla.part_drawing.independent_layout.complete_trial_readback import verify_complete_trial


FIXTURES=Path(__file__).parent/'fixtures/part_drawing'


class SixFailureRepairTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.templates=json.loads((FIXTURES/'independent-template-failures.json').read_text('utf8'))
        sources=json.loads((FIXTURES/'independent-feature-sources.json').read_text('utf8'))
        cls.source=sources['T2-10B-102']

    def test_split_request_is_rejected_before_planning(self):
        with self.assertRaisesRegex(ContractError,'DIMENSION_CHAIN_CONTINUITY'):
            plan_complete_trial({},'unused',[],complete_features=True,split_crowded=True)

    def test_actual_split_page_is_rejected_even_with_complete_values(self):
        f=json.loads((FIXTURES/'independent-fragmented-native.json').read_text('utf8'))
        with self.assertRaisesRegex(ContractError,'DIMENSION_CHAIN_CONTINUITY'):
            verify_complete_trial(f['source'],f['plan'],[f['saved']]*3)

    def test_hiding_split_metadata_does_not_bypass_native_coverage(self):
        f=json.loads((FIXTURES/'independent-fragmented-native.json').read_text('utf8'))
        plan=copy.deepcopy(f['plan']);plan.pop('fragmentedDimensions')
        plan=seal({k:v for k,v in plan.items() if k!='digest'})
        with self.assertRaisesRegex(ContractError,'DIMENSION_CHAIN_CONTINUITY'):
            verify_complete_trial(f['source'],plan,[f['saved']]*3)

    def test_observed_short_end_line_extension_is_in_prediction(self):
        # 73's upper 35 interval extended 52.075 beyond the real endpoint;
        # the previous fixed one-height line envelope stopped at 25.
        obj=dict(objectId='dimension/test',axis='y',coordinates=[[0,0],[0,50],[0,85]],
                 displayValues=[50,35],allowedSides=[-1],side=-1,role='location')
        with patch('zijinhua_tekla.part_drawing.independent_layout.complete_trial._font_width',return_value=50):
            old=_dimension_candidates(obj,[0,0,100,85],1,25,None)
            new=_dimension_candidates(obj,[0,0,100,85],1,25,None,measured_shorts=True)
        self.assertLess(old[0]['lines'].bounds[3],85+52.075)
        self.assertGreaterEqual(new[0]['lines'].bounds[3],85+52.075)

    def test_material_table_height_is_observed_not_a_fixed_region(self):
        for name in ('T2-10B-451','T2-10B-450'):
            report=template_regions(self.templates[name]);self.assertTrue(report['regions'])
            for region in report['regions']:
                for obstacle in report['obstacles']:
                    self.assertGreaterEqual(box(*region).distance(box(*obstacle)),report['margin']-1e-6)
            tallest=max(b[3] for b in report['obstacles'])
            self.assertGreater(tallest,1400)
        self.assertFalse(template_regions(self.templates['T2-10B-451'])['overflow'])
        self.assertTrue(template_regions(self.templates['T2-10B-450'])['overflow'])

    def test_old_pages_are_rejected_for_real_template_intersections(self):
        plan=dict(region=[150,400,2830,1900],digest='fixture')
        for name,capture in self.templates.items():
            report=verify_template_clearance(plan,capture)
            self.assertEqual('TEMPLATE_BLOCKED',report['status'],name)
            self.assertTrue(report['conflicts'],name)

    def test_continuous_native_page_retains_complete_dimension_chains(self):
        f=json.loads((FIXTURES/'independent-feature-102-rejected.json').read_text('utf8'))
        # Use the existing continuous-chain revision with nearby hole dimensions.
        source=self.source;plan=f['correctedPlan'];saved=f['correctedSaved']
        report=verify_complete_trial(source,plan,[saved]*3)
        self.assertEqual(35,len(report['displayBindings']))
        wanted={o['objectId']:o for o in adapt_expressions(source,complete_features=True)['objects']
                if o['kind']=='DIMENSION'}
        actual={p['key']:p for p in plan['primitives'] if p['kind']=='dimensionGroup'}
        self.assertEqual(set(wanted),set(actual))
        for key,obj in wanted.items():
            self.assertEqual(obj['points'],actual[key]['pointRefs'])


if __name__=='__main__':unittest.main()
