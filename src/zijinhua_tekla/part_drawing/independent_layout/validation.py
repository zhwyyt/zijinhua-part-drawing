"""Independent stage-0 observation checks, without any candidate-generator import.

Only explicit line paths and measured filled polygons are supported. This is
not expression coverage, a font estimator, or complete drawing acceptance.
"""

import math

from shapely.geometry import LineString, Point, Polygon

from .contracts import canonical_json, digest, positive, require, seal


def _points(value, minimum, name):
    require(isinstance(value, list) and len(value) >= minimum, 'POINTS_INVALID', name)
    for point in value:
        require(isinstance(point, list) and len(point) == 2 and
                all(type(x) in (int, float) and math.isfinite(x) for x in point),
                'POINT_INVALID', name)
    return value


def _polygon(points, name):
    polygon = Polygon(_points(points, 3, name))
    require(polygon.is_valid and polygon.area > 0, 'POLYGON_INVALID', name)
    return polygon


def _primitives(items):
    primitives = {}
    for item in items:
        key = item.get('id')
        require(isinstance(key, str) and bool(key) and key not in primitives,
                'PRIMITIVE_ID_INVALID', str(key))
        require(isinstance(item.get('objectId'), str) and bool(item['objectId']), 'OBJECT_ID_MISSING', key)
        kind = item.get('kind')
        if kind == 'LINE':
            points = _points(item['points'], 2, key)
            shape = LineString(points)
            require(shape.length > 0 and shape.is_simple, 'LINE_INVALID', key)
            width = item.get('width')
            require(type(width) in (int, float) and math.isfinite(width) and width >= 0,
                    'LINE_WIDTH_INVALID', key)
            occupied = shape.buffer(width / 2, cap_style=2, join_style=2) if width else shape
        elif kind == 'FILLED_POLYGON':
            shape = occupied = _polygon(item['points'], key)
        else:
            require(False, 'PRIMITIVE_UNSUPPORTED', f'{key}: {kind}')
        primitives[key] = (item, shape, occupied)
    return primitives


def _contacts(items, primitives):
    contacts = {}
    for contact in items:
        pair = contact.get('pair')
        require(isinstance(pair, list) and len(pair) == 2 and len(set(pair)) == 2 and
                all(k in primitives for k in pair), 'CONTACT_INVALID', str(pair))
        key = tuple(sorted(pair))
        require(key not in contacts, 'CONTACT_DUPLICATE', str(key))
        point = _points([contact['point']], 1, 'contact')[0]
        for identity in key:
            item = primitives[identity][0]
            require(item['kind'] == 'LINE' and point in (item['points'][0], item['points'][-1]),
                    'CONTACT_INVALID', 'Only exact line endpoints may be exempted')
        contacts[key] = Point(point)
    return contacts


def _clearance_violations(primitives, contacts, minimum_clearance):
    violations = []
    keys = sorted(primitives)
    for index, left in enumerate(keys):
        for right in keys[index + 1:]:
            _, a, area_a = primitives[left]
            _, b, area_b = primitives[right]
            contact = contacts.get((left, right))
            if contact is not None:
                # First vocabulary: endpoint exception only for two straight,
                # zero-width segments. Thick strokes need calibrated join rules.
                require(len(primitives[left][0]['points']) == len(primitives[right][0]['points']) == 2
                        and primitives[left][0]['width'] == primitives[right][0]['width'] == 0,
                        'CONTACT_UNSUPPORTED', 'Thick/polyline joins require calibration')
                if a.intersection(b).equals(contact):
                    continue
            distance = area_a.distance(area_b)
            if distance <= minimum_clearance:
                violations.append({'code': 'CLEARANCE_CONFLICT', 'primitives': [left, right],
                                   'distance': distance})
    return violations


def validate_observation(observation):
    """Return GEOMETRY_CLEAR/GEOMETRY_CONFLICT, never VERIFIED or PLAN_VALID.

    minimumClearance is a measured/configured distance in source drawing units.
    Contacts are allowed only for a declared pair of line endpoints at the same
    exact point. They never exempt a polygon, crossing, or overlapping segment.
    """
    canonical_json(observation)
    require(observation.get('schemaVersion') == 'LayoutObservation.v1',
            'OBSERVATION_SCHEMA', 'Expected LayoutObservation.v1')
    require(observation.get('units') == 'NATIVE_SHEET_UNIT', 'UNIT_UNKNOWN', 'Source drawing units required')
    require(observation.get('stage') in ('PLAN', 'READBACK'), 'STAGE_INVALID', 'PLAN or READBACK required')
    require(isinstance(observation.get('sourceDigest'), str) and bool(observation['sourceDigest']),
            'SOURCE_MISSING', 'An observation must name its evidence digest')
    positive(observation.get('minimumClearance'), 'minimumClearance')
    page = _polygon(observation['usableRegion'], 'usableRegion')
    items = observation.get('primitives')
    require(isinstance(items, list) and bool(items), 'PRIMITIVES_EMPTY', 'Empty observations cannot pass')
    primitives = _primitives(items)
    contacts = _contacts(observation.get('contacts', []), primitives)
    violations = []
    for identity, (_, _, occupied) in sorted(primitives.items()):
        if not page.covers(occupied):
            violations.append({'code': 'OUTSIDE_USABLE_REGION', 'primitives': [identity]})
    violations.extend(_clearance_violations(primitives, contacts, observation['minimumClearance']))
    return seal({'schemaVersion': 'LayoutGeometryCheck.v1',
                 'status': 'GEOMETRY_CONFLICT' if violations else 'GEOMETRY_CLEAR',
                 'observationDigest': digest(observation),
                 'sourceDigest': observation['sourceDigest'], 'stage': observation['stage'],
                 'violations': violations, 'scope': 'LINE_POLYGON_GEOMETRY_ONLY',
                 'remainingGates': ['EXPRESSION_COVERAGE', 'STYLE_CALIBRATION',
                                    'NATIVE_PERSISTENCE', 'VISUAL_QA']})
