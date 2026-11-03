"""Bounded deterministic CP-SAT selection for measured research pools only."""
import importlib.metadata
import math
import time
from decimal import Decimal, ROUND_HALF_UP

from shapely.geometry import shape

from .contracts import check_seal, positive, require, seal


def integer_cost(value):
    require(type(value) in (int,float) and math.isfinite(value) and 0<=value<=1e9,
            'SELECTION_COST', 'Finite nonnegative study cost required')
    return int((Decimal(str(value))*1000).quantize(Decimal(1),rounding=ROUND_HALF_UP))


def verify_selection(pool, selected):
    """Recompute selected pair distances, not the generator's conflict edges."""
    check_seal(pool);by_id={c['id']:c for c in pool['candidates']}
    require(len(selected)==len(set(selected)) and all(i in by_id for i in selected),
            'SELECTION_IDS', 'Unknown/duplicate selected candidate')
    rows=[by_id[i] for i in selected]
    require(sorted(c['key'] for c in rows)==sorted(pool['keys']) and all(
        c['status']=='KNOWN_GEOMETRY_CLEAR' for c in rows), 'SELECTION_COVERAGE', 'One eligible candidate per role required')
    conflicts=[]
    for i,a in enumerate(rows):
        for b in rows[i+1:]:
            if shape(a['occupied']).distance(shape(b['occupied']))<=pool['options']['clearance']:
                conflicts.append([a['id'],b['id']])
    return dict(status='SELECTED_PAIRS_CLEAR' if not conflicts else 'SELECTED_PAIR_CONFLICT',
                conflicts=conflicts,scope='INDEPENDENT_PAIR_CHECK_NOT_STATIC_OR_NATIVE_VERIFICATION')


def select_joint_candidates(pool, deterministic_budget=5, wall_budget=60):
    from ortools.sat.python import cp_model
    check_seal(pool)
    require(pool['schemaVersion']=='JointMeasuredCandidates.v1', 'SELECTION_SCOPE', 'Measured joint pool required')
    positive(deterministic_budget,'deterministic budget');positive(wall_budget,'wall budget')
    version=importlib.metadata.version('ortools')
    require(version=='9.14.6206', 'DEPENDENCY_VERSION', 'OR-Tools 9.14.6206 required')
    keys=sorted(pool['keys']);rows=pool['candidates'];ids=[c['id'] for c in rows]
    require(keys and len(keys)==len(set(keys)) and len(ids)==len(set(ids)) and
            len(rows)<=2000 and len(pool['pairConflicts'])<=500000 and
            all(c['key'] in keys for c in rows), 'SELECTION_POOL', 'Invalid or oversized pool')
    clear=sorted([c for c in rows if c['status']=='KNOWN_GEOMETRY_CLEAR'],key=lambda c:(c['key'],c['id']))
    groups={key:[c for c in clear if c['key']==key] for key in keys}
    costs={c['id']:[integer_cost(v) for v in c['cost']] for c in clear}
    require(all(len(v)==5 for v in costs.values()), 'SELECTION_COST', 'Five study cost components required')
    model=cp_model.CpModel();variables={c['id']:model.new_bool_var(c['id']) for c in clear}
    for group in groups.values():model.add_exactly_one([variables[c['id']] for c in group])
    for pair in pool['pairConflicts']:
        require(len(pair)==2 and pair[0]!=pair[1] and all(i in variables for i in pair),
                'SELECTION_CONFLICT', 'Conflict must reference eligible candidates')
        model.add(sum(variables[i] for i in pair)<=1)
    objectives=[sum(costs[i][j]*v for i,v in variables.items()) for j in range(5)]
    # Canonical tuple tie break: one proven rank optimum per stable role, not a sum.
    objectives.extend(sum(rank*variables[c['id']] for rank,c in enumerate(groups[key])) for key in keys)
    elapsed=0;deadline=time.monotonic()+wall_budget;selected=[];values=[];status='UNKNOWN';complete=False
    for objective in objectives:
        remaining=deterministic_budget-elapsed;seconds=deadline-time.monotonic()
        if remaining<=0 or seconds<=0:status='UNKNOWN';break
        model.minimize(objective);solver=cp_model.CpSolver()
        solver.parameters.num_search_workers=1;solver.parameters.random_seed=0
        solver.parameters.max_deterministic_time=remaining;solver.parameters.max_time_in_seconds=seconds
        code=solver.solve(model);status=solver.status_name(code);elapsed+=solver.response_proto.deterministic_time
        require(code!=cp_model.MODEL_INVALID,'SELECTION_MODEL',solver.solution_info())
        if code in (cp_model.OPTIMAL,cp_model.FEASIBLE):
            selected=[c['id'] for c in clear if solver.value(variables[c['id']])]
        if code!=cp_model.OPTIMAL:break
        value=solver.value(objective);values.append(value);model.add(objective==value)
    else:complete=True
    verification=verify_selection(pool,selected) if selected else None
    if verification:require(verification['status']=='SELECTED_PAIRS_CLEAR','SELECTION_GEOMETRY','Conflict graph missed a selected pair')
    result_status=('JOINT_KNOWN_GEOMETRY_SELECTED' if complete else
                   'INFEASIBLE_CANDIDATES' if status=='INFEASIBLE' else 'SEARCH_LIMIT')
    reason=('NO_CANDIDATE' if any(not any(c['key']==k for c in rows) for k in keys) else
            'STATIC_BLOCKED' if any(not g for g in groups.values()) else 'MUTUAL_CONFLICT') if status=='INFEASIBLE' else None
    return seal(dict(schemaVersion='JointMeasuredSelection.v1',productionEligible=False,poolDigest=pool['digest'],
        status=result_status,solverStatus=status,optimizationComplete=complete,selected=selected,
        provenObjectiveValues=values,reason=reason,blockedKeys=[k for k,g in groups.items() if not g],
        verification=verification,unresolved=pool['unresolved'],remainingGates=pool['remainingGates'],
        solver=dict(version=version,workers=1,seed=0,deterministicBudget=deterministic_budget,
                    deterministicUsed=elapsed,wallBudget=wall_budget,costUnit=.001)))
