"""Separate physical bolt penetration from target-specific special-hole shape."""

import math

from .analytic_geometry import frame_axes, norm, project, sub
from .nc_geometry import _outline_match, _hole_witnesses, _hole_residual


def nc_target_shapes(part, outline, groups, frame, witness, tolerance):
    """Use an audited, numbered NC only for complete one-to-one hole attribution."""
    if witness is None or not witness.get('ncFile'):
        raise ValueError('No target-specific NC hole evidence')
    # Known AK bevel fields are not hole semantics. Every other source/syntax
    # issue still rejects this scoped proof; 3D bevels have their own solid gate.
    allowed = {'NC_MACHINING_FIELDS_UNSUPPORTED'}
    if any(i['code'] not in allowed for i in witness['issues']):
        raise ValueError('NC source or syntax is not valid for target hole attribution')
    parsed = witness['ncFile']['parsed']
    if parsed['geometryRepresentation'] != 'NON_UNFOLDED':
        raise ValueError('Unfolded NC needs an independently established mapping')
    if any(b['kind'] not in ('ST', 'AK', 'BO', 'EN') for b in parsed['blocks']):
        raise ValueError('Target-hole proof requires BO holes and one outer contour')
    source_frame = part['dstvCoordinateSystem']['value']
    if (norm(sub(frame['origin'], source_frame['origin'])) > tolerance or
            any(norm(sub(a, b)) > 1e-8 for a, b in zip(frame_axes(frame), frame_axes(source_frame)))):
        raise ValueError('Target frame and NC frame do not correspond')
    if witness['id'] != part['id'] or witness['partPosition'] != part['reports']['PART_POS']['value']:
        raise ValueError('NC target identity differs')
    outer, = [b for b in parsed['blocks'] if b['kind'] == 'AK']
    boundary = _outline_match(outline, outer, tolerance)
    thickness = float(parsed['blocks'][0]['lines'][10]['text'])
    if abs(thickness-part['reports']['PROFILE.WIDTH']['value']) > tolerance:
        raise ValueError('NC target thickness differs')
    holes = [h for g in groups if isinstance(g['candidates'], list) for h in g['candidates']]
    observed = _hole_witnesses(parsed['blocks'])
    if len(observed) != len(holes):
        raise ValueError('NC target hole count differs')
    matches, used = [], set()
    for row in observed:
        candidates = [(h, _hole_residual(h, row)) for h in holes]
        candidates = [(h, r) for h, r in candidates if r <= tolerance]
        if len(candidates) != 1 or candidates[0][0]['id'] in used:
            raise ValueError('NC target hole shape/position mapping is missing or ambiguous')
        hole, residual = candidates[0]; used.add(hole['id'])
        matches.append(dict(holeId=hole['id'], lineNumbers=row['lineNumbers'], residualMm=residual))
    return dict(status='MATCHED', basis='EXACT_NUMBERED_NC_HOLES_WITH_MODEL_COORDINATE_MAPPING',
                targetPartId=part['id'], fileSha256=witness['ncFile']['sha256'], mappings=matches,
                outlineMapping=boundary, settingsBinding='UNCONFIRMED',
                excludedScope='NC_BEVEL_FIELDS_AND_PRODUCTION_RELEASE')


def physical_penetration(bolt, part_id, holes, frame, levels, tolerance):
    """SDK line/solid intersections, not association-list length or slot symbols."""
    node = bolt.get('axisIntersections', {})
    if node.get('status') != 'READ' or len(node['value']) != len(holes):
        raise ValueError('Actual target/bolt axis intersections are missing')
    evidence = []
    for index, (row, hole) in enumerate(zip(node['value'], holes)):
        if row['index'] != index or row['targetPartId'] != part_id:
            raise ValueError('Axis intersection target/index differs')
        target = [project(p, frame) for p in row['targetPoints']]
        hardware = [project(p, frame) for p in row['hardwarePoints']]
        query = [project(row[k], frame) for k in ('queryStart', 'queryEnd')]
        if any(not math.isfinite(v) for p in target+hardware+query for v in p):
            raise ValueError('Nonfinite axis intersection')
        if len(target) != 2 or len(hardware) < 2 or len(hardware) % 2:
            raise ValueError('Axis intersections do not define complete material intervals')
        if any(norm(sub(p[:2], hole['center'])) > tolerance for p in target+hardware+query):
            raise ValueError('Intersection is not on the actual hole axis')
        target_z = sorted(p[2] for p in target)
        if any(abs(a-b) > tolerance for a,b in zip(target_z, levels)):
            raise ValueError('Actual axis does not cross both proven target thickness faces')
        z = sorted(p[2] for p in hardware)
        q = sorted(p[2] for p in query)
        if z[0] < q[0]-tolerance or z[-1] > q[1]+tolerance:
            raise ValueError('Hardware intersection lies outside query segment')
        intervals = []
        for lo, hi in zip(z[::2], z[1::2]):
            if intervals and lo <= intervals[-1][1]+tolerance:
                intervals[-1][1] = max(hi, intervals[-1][1])
            else:
                intervals.append([lo,hi])
        if not any(lo <= levels[0]+tolerance and hi >= levels[1]-tolerance for lo,hi in intervals):
            raise ValueError('Finite bolt material does not span target thickness')
        evidence.append(dict(holeId=hole['id'], targetLevels=target_z, hardwareIntervals=intervals))
    return dict(status='MATCHED', targetPartId=part_id, sourceRef=node['source'], axes=evidence,
                basis='SDK_LINE_SOLID_INTERSECTIONS')
