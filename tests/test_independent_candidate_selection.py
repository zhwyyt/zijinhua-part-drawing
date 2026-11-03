import copy
import itertools
import io
import random
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from shapely.geometry import box, shape

from zijinhua_tekla.part_drawing.independent_layout.candidate_selection import (
    integer_cost, select_joint_candidates, verify_selection)
from zijinhua_tekla.part_drawing.independent_layout.contracts import ContractError, seal
from zijinhua_tekla.part_drawing.independent_layout.page_obstacles import geometry_json


def pool(rows):
    pairs=[[a['id'],b['id']] for a,b in itertools.combinations(rows,2) if a['key']!=b['key'] and
           a['status']==b['status']=='KNOWN_GEOMETRY_CLEAR' and shape(a['occupied']).distance(shape(b['occupied']))<=.1]
    return seal(dict(schemaVersion='JointMeasuredCandidates.v1',keys=sorted({c['key'] for c in rows}),candidates=rows,
                     pairConflicts=pairs,unresolved=[dict(id='missing-glyph')],options=dict(clearance=.1),remainingGates=['NATIVE_READBACK']))


def candidate(key,identity,x,cost=0):
    return dict(key=key,id=identity,status='KNOWN_GEOMETRY_CLEAR',occupied=geometry_json(box(x,0,x+1,1)),cost=[cost,0,0,0,0])


def oracle(p):
    groups=[[c for c in p['candidates'] if c['key']==k and c['status']=='KNOWN_GEOMETRY_CLEAR'] for k in p['keys']]
    choices=[]
    for combo in itertools.product(*groups):
        if any(shape(a['occupied']).distance(shape(b['occupied']))<=.1 for a,b in itertools.combinations(combo,2)):continue
        score=tuple(sum(integer_cost(c['cost'][i]) for c in combo) for i in range(5))
        choices.append((score+tuple(c['id'] for c in combo),[c['id'] for c in combo]))
    return min(choices)[1] if choices else None


class CandidateSelectionTests(unittest.TestCase):
    def test_joint_choice_can_move_first_object_to_fit_second(self):
        p=pool([candidate('dimension','d0',0),candidate('dimension','d1',3,1),candidate('number','n0',0)])
        r=select_joint_candidates(p)
        self.assertEqual(['d1','n0'],r['selected']);self.assertTrue(r['optimizationComplete'])
        self.assertEqual('JOINT_KNOWN_GEOMETRY_SELECTED',r['status']);self.assertFalse(r['productionEligible'])
        self.assertEqual(p['unresolved'],r['unresolved'])

    def test_small_random_pools_match_exhaustive_geometry_oracle(self):
        rng=random.Random(42)
        for _ in range(12):
            p=pool([candidate(k,k+str(i),rng.randrange(5)*2,rng.randrange(3)/1000)
                    for k in ('dimension','leader','number') for i in range(3)])
            expected=oracle(p);result=select_joint_candidates(p)
            self.assertEqual(expected or [],result['selected'])
            self.assertEqual(expected is not None,result['optimizationComplete'])

    def test_tie_break_is_order_independent_candidate_tuple(self):
        rows=[candidate('a','a0',0),candidate('a','a1',3),candidate('b','b0',0),candidate('b','b1',3)]
        first=select_joint_candidates(pool(rows))
        self.assertEqual(['a0','b1'],first['selected'])
        self.assertEqual(first['selected'],select_joint_candidates(pool(list(reversed(rows))))['selected'])

    def test_static_and_mutual_failure_are_distinguished(self):
        rows=[candidate('a','a0',0),candidate('b','b0',0)]
        self.assertEqual('MUTUAL_CONFLICT',select_joint_candidates(pool(rows))['reason'])
        rows[0]['status']='CANDIDATE_CONFLICT';result=select_joint_candidates(pool(rows))
        self.assertEqual('STATIC_BLOCKED',result['reason']);self.assertEqual(['a'],result['blockedKeys'])

    def test_budget_exhaustion_is_not_infeasibility(self):
        p=pool([candidate('a','a0',0)])
        with patch('zijinhua_tekla.part_drawing.independent_layout.candidate_selection.time.monotonic',side_effect=[0,2]):
            result=select_joint_candidates(p,wall_budget=1)
        self.assertEqual('SEARCH_LIMIT',result['status']);self.assertFalse(result['optimizationComplete'])
        self.assertIsNone(result['reason'])

    def test_independent_pair_verifier_catches_missing_conflict_graph_edge(self):
        p=pool([candidate('a','a0',0),candidate('b','b0',0)]);body={k:v for k,v in p.items() if k!='digest'}
        body['pairConflicts']=[];p=seal(body)
        self.assertEqual('SELECTED_PAIR_CONFLICT',verify_selection(p,['a0','b0'])['status'])
        with self.assertRaisesRegex(ContractError,'SELECTION_GEOMETRY'):select_joint_candidates(p)

    def test_wrong_coverage_and_unknown_conflict_are_rejected(self):
        p=pool([candidate('a','a0',0),candidate('b','b0',3)])
        with self.assertRaisesRegex(ContractError,'SELECTION_COVERAGE'):verify_selection(p,['a0'])
        body={k:copy.deepcopy(v) for k,v in p.items() if k!='digest'};body['pairConflicts']=[['a0','missing']]
        with self.assertRaisesRegex(ContractError,'SELECTION_CONFLICT'):select_joint_candidates(seal(body))

    def test_cost_quantization_has_explicit_half_up_boundary(self):
        self.assertEqual(1,integer_cost(.0005));self.assertEqual(0,integer_cost(.000499))
        for value in (-1,float('inf'),True):
            with self.assertRaises(ContractError):integer_cost(value)

    def test_cli_reports_infeasible_limited_and_missing_glyph_results_as_blocked(self):
        from zijinhua_tekla.part_drawing.independent_layout.__main__ import main
        for status,gaps,expected in (('INFEASIBLE_CANDIDATES',[],1),('SEARCH_LIMIT',[],1),
                ('JOINT_KNOWN_GEOMETRY_SELECTED',[{'id':'glyph'}],1),('JOINT_KNOWN_GEOMETRY_SELECTED',[],0)):
            with tempfile.TemporaryDirectory() as directory, self.subTest(status=status,gaps=gaps):
                args=['joint-candidate-study','--out',str(Path(directory)/'out')]
                for key in ('scene','capture','primitives','ink','source','options'):args.extend(['--'+key,'input.json'])
                result=dict(status=status,unresolved=gaps)
                with patch('zijinhua_tekla.part_drawing.independent_layout.__main__.read_json',return_value={}), \
                     patch('zijinhua_tekla.part_drawing.independent_layout.joint_candidates.generate_joint_candidates',return_value={}), \
                     patch('zijinhua_tekla.part_drawing.independent_layout.candidate_selection.select_joint_candidates',return_value=result), \
                     redirect_stdout(io.StringIO()):
                    self.assertEqual(expected,main(args))
                self.assertTrue((Path(directory)/'out/report.json').exists())
