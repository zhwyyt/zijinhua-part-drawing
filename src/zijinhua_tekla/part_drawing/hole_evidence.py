"""Target-specific hole evidence; observations must come from an independent adapter.

This contract is not a Tekla SDK type. Labels, bolt symbols and generated plans
are not geometry observations. A digest binds data, not its authenticity.
"""

import hashlib
import json
import math

from .analytic_geometry import dot, frame_axes, norm, project, sub


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("Finite numeric observation required")
    return value


def _vector(value):
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError("Global three-dimensional observation required")
    return [_number(v) for v in value]


def validate_observations(bundle, raw):
    """Reject stale/foreign/malformed inputs before evaluating any target."""
    if bundle is None:
        return None
    if (not isinstance(bundle, dict) or bundle.get('schemaVersion') != 'nativeHoleObservations.v1'
            or bundle.get('origin') != 'TEKLA_NATIVE_DRAWING_GEOMETRY'):
        raise ValueError("Independent native hole geometry origin required")
    canonical = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    if bundle.get('nativeEvidenceDigest') != hashlib.sha256(canonical.encode('utf-8')).hexdigest():
        raise ValueError("Hole observations refer to a different native snapshot")
    rows = bundle.get('observations')
    if not isinstance(rows, list):
        raise ValueError("Hole observation list required")
    groups = {p['id']: {b['id'] for b in p['boltGroups']['value']} for p in raw['parts']}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Hole observation must be an object")
        if (type(row.get('targetPartId')) is not int or type(row.get('modelGroupId')) is not int
                or row['modelGroupId'] not in groups.get(row['targetPartId'], set())):
            raise ValueError("Observation target/group is absent from native snapshot")
        for key in ('drawingId', 'viewId', 'objectId', 'sectionViewId', 'sectionObjectId'):
            if not isinstance(row.get(key), str) or not row[key].strip():
                raise ValueError("Native boundary and section object identities required")
        if row['sectionViewId'] == row['viewId'] or row['sectionObjectId'] == row['objectId']:
            raise ValueError("Independent section view/object required for penetration evidence")
        if row['kind'] not in ('ROUND', 'SLOT'):
            raise ValueError("Unsupported observed hole kind")
        diameter, extension = _number(row['diameter']), _number(row['extension'])
        if diameter <= 0 or extension < 0 or (row['kind'] == 'ROUND') != (extension == 0):
            raise ValueError("Invalid observed hole dimensions")
        for key in ('center', 'direction', 'entry', 'exit'):
            _vector(row[key])
        if abs(norm(row['direction'])-1) > 1e-8:
            raise ValueError("Observed direction must be a unit vector")
    return rows


def _match(hole, row, frame, levels, tolerance):
    if row['kind'] != hole['kind']:
        raise ValueError("Hole shape disagrees")
    center = project(row['center'], frame)
    if norm(sub(center[:2], hole['center'])) > tolerance:
        raise ValueError("Hole center disagrees")
    if not levels[0]-tolerance <= center[2] <= levels[1]+tolerance:
        raise ValueError("Observed center is outside target thickness")
    if any(abs(row[k]-hole[k]) > tolerance for k in ('diameter', 'extension')):
        raise ValueError("Hole diameter/extension disagrees")
    if hole['kind'] == 'SLOT':
        x, y, z = frame_axes(frame)
        direction = row['direction']
        if (abs(dot(direction, z)) > 1e-8
                or abs(abs(dot([dot(direction, x), dot(direction, y)], hole['direction']))-1) > 1e-8):
            raise ValueError("Slot direction disagrees")
    ends = [project(row[k], frame) for k in ('entry', 'exit')]
    if any(norm(sub(p[:2], hole['center'])) > tolerance for p in ends):
        raise ValueError("Penetration is not on the hole axis")
    if any(abs(a-b) > tolerance for a, b in zip(sorted(p[2] for p in ends), levels)):
        raise ValueError("Hole does not penetrate both target faces")


def composite_hole_effect(bolt, part_id, candidates, frame, spatial, rows, tolerance, source):
    """Require all four evidence categories; never infer absence from missing data."""
    result = {'status': 'INSUFFICIENT', 'missingProofs': [], 'mapping': []}
    if rows is None:
        result['missingProofs'].append('INDEPENDENT_NATIVE_HOLE_GEOMETRY')
        return result
    observed = [(i, r) for i, r in enumerate(rows)
                if r['targetPartId'] == part_id and r['modelGroupId'] == bolt['id']]
    if not observed:
        result['missingProofs'].append('INDEPENDENT_NATIVE_HOLE_GEOMETRY')
        return result
    if source['status'] != 'READ_CONSISTENT':
        result['missingProofs'].append('NATIVE_SOURCE_UNCONFIRMED')
    if spatial['status'] != 'MATCHED':
        result['missingProofs'].append('TARGET_SPATIAL_DOMAIN_UNPROVEN')
    targets = bolt['targets']
    try:
        values = [targets[k] for k in ('partToBoltTo', 'partToBeBolted', 'otherParts')]
        if any(v.get('status') != 'READ' for v in values):
            raise ValueError('Target associations unreadable')
        ids = [v['value']['id'] for v in values[:2]] + [v['id'] for v in values[2]['value']]
        if part_id not in ids:
            return dict(result, status='CONFLICT', missingProofs=['HOLE_TARGET_CONFLICT'])
    except (KeyError, TypeError, ValueError):
        result['missingProofs'].append('HOLE_TARGET_UNPROVEN')
    if result['missingProofs']:
        return result
    if len(observed) != len(candidates):
        # Partial observations are a gap; extra observations are a contradiction.
        return dict(result, status='CONFLICT' if len(observed) > len(candidates) else 'INSUFFICIENT',
                    missingProofs=['HOLE_OBSERVATION_COUNT_MISMATCH'])
    remaining = list(observed)
    for hole in candidates:
        matches = []
        for i, row in remaining:
            try:
                _match(hole, row, frame, spatial['levels'], tolerance)
                matches.append((i, row))
            except ValueError:
                pass
        if len(matches) != 1:
            return dict(result, status='CONFLICT', missingProofs=['HOLE_GEOMETRY_CONFLICT'], mapping=[])
        i, row = matches[0]
        remaining.remove(matches[0])
        result['mapping'].append({'holeId': hole['id'], 'observationRef': f'#/observations/{i}',
                                  'drawingId': row['drawingId'], 'viewId': row['viewId'],
                                  'objectId': row['objectId'], 'sectionViewId': row['sectionViewId'],
                                  'sectionObjectId': row['sectionObjectId']})
    result['status'] = 'COMPOSITE_CONFIRMED'
    return result
