"""Compare a dimension scheme with the dimension sets of an accepted drawing case."""
import json
from pathlib import Path

from .dimension_scheme import build_scheme
from .plate_features import build_feature_model
from .readability import audit_readability, load_profile

MATCH_MM = 0.6


def load_cases(directory):
    return [json.loads(p.read_text(encoding='utf-8')) for p in sorted(Path(directory).glob('*.json'))]


def scheme_sets(requirements, scheme):
    facts = requirements['facts']; bounds = facts['bounds']
    return [dict(key=g['key'], axis='xy'[g['axis']],
                 positions=[round(facts['points'][k][g['axis']]-bounds[g['axis']], 1) for k in g['points']])
            for g in scheme['groups']]


def _same(a, b):
    return (a['axis'] == b['axis'] and len(a['positions']) == len(b['positions'])
            and all(abs(x-y) <= MATCH_MM for x, y in zip(a['positions'], b['positions'])))


def compare_case(case, profile=None):
    """Matched shop sets, shop sets the scheme lacks, and scheme sets the shop drawing lacks."""
    if case['status'] != 'READY':
        return dict(id=case['id'], status='BLOCKED', blockedBy=case.get('blockedBy', []))
    requirements = case['requirements']
    scheme = build_scheme(requirements, build_feature_model(requirements['facts'], requirements['modelToleranceMm']))
    produced = scheme_sets(requirements, scheme)
    unused = list(produced); matched, missing = [], []
    for shop in case['shopSets']:
        hit = next((p for p in unused if _same(shop, p)), None)
        if hit:
            unused.remove(hit); matched.append(dict(shop=shop, scheme=hit['key']))
        else:
            missing.append(shop)
    readability = audit_readability(requirements, scheme, profile or load_profile())
    return dict(id=case['id'], status='COMPARED', shopSets=len(case['shopSets']), matched=matched,
                missing=missing, extra=unused, readability=readability['status'],
                readabilityIssues=[i['code']+':'+str(i.get('group')) for i in readability['issues']])
