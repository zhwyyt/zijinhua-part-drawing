"""Repeatable CP-SAT smoke check; not a layout solver or benchmark."""

import importlib.metadata
import platform
import sys
from pathlib import Path

from .contracts import require, seal


def check_environment():
    import shapely
    from ortools.sat.python import cp_model

    require(importlib.metadata.version('ortools') == '9.14.6206', 'DEPENDENCY_VERSION', 'OR-Tools 9.14.6206 required')
    results = []
    for _ in range(10):
        model = cp_model.CpModel()
        choices = [model.new_bool_var(f'c{i}') for i in range(4)]
        model.add_exactly_one(choices[:2])
        model.add_exactly_one(choices[2:])
        model.add(choices[0] + choices[2] <= 1)
        model.minimize(sum(weight * choice for weight, choice in zip((0, 2, 0, 3), choices)))
        solver = cp_model.CpSolver()
        solver.parameters.num_search_workers = 1
        solver.parameters.random_seed = 0
        solver.parameters.max_deterministic_time = 1
        require(solver.solve(model) == cp_model.OPTIMAL, 'CP_SAT_SMOKE_FAILED', 'No proven optimal result')
        results.append([solver.value(x) for x in choices])
    require(all(r == [0, 1, 1, 0] for r in results), 'CP_SAT_UNSTABLE', 'Unexpected choice in 10 repeats')
    return seal({'schemaVersion': 'LayoutEnvironmentCheck.v1', 'status': 'SMOKE_PASSED',
                 'python': platform.python_version(), 'executable': sys.executable,
                 'modulePath': str(Path(__file__).resolve()),
                 'platform': platform.platform(),
                 'versions': {name: importlib.metadata.version(name) for name in
                              ('ortools', 'shapely', 'numpy', 'pandas', 'protobuf')},
                 'geos': shapely.geos_version_string, 'workerCount': 1, 'seed': 0,
                 'repeats': results, 'scope': 'ENVIRONMENT_ONLY_NOT_LAYOUT_PERFORMANCE'})
