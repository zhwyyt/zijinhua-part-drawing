"""Normalize live paper primitives into conservative, replayable template regions."""
import math

from .expression_requirements import content_digest


SCHEMA = 'nativeTemplateSnapshot.v1'
TOL = .1


def _point(value):
    point = [float(value[k]) for k in ('X', 'Y')]
    if not all(math.isfinite(v) for v in point):
        raise ValueError('TEMPLATE_NONFINITE: invalid paper coordinate')
    return point


def _leaves(node):
    kind = node['type'].rsplit('.', 1)[-1]
    if kind in ('View', 'Segment', 'PrimitiveGroup'):
        for child in node['Primitives']:
            yield from _leaves(child)
    elif kind in ('PolygonPrimitive', 'LoopPrimitive'):
        # Filled polygons could occupy more than their edges; reject until supported.
        raise ValueError('TEMPLATE_UNSUPPORTED_PRIMITIVE: '+kind)
    else:
        yield kind, node


def _line(node):
    return _point(node['StartPoint']), _point(node['EndPoint'])


def _close(a, b):
    return abs(a-b) <= TOL


def _frames(lines, sheet):
    horizontal = []
    vertical = []
    for a, b in lines:
        if _close(a[1], b[1]) and abs(a[0]-b[0]) >= .8*sheet[0]:
            horizontal.append((min(a[0], b[0]), max(a[0], b[0]), a[1]))
        if _close(a[0], b[0]) and abs(a[1]-b[1]) >= .8*sheet[1]:
            vertical.append((a[0], min(a[1], b[1]), max(a[1], b[1])))
    frames = set()
    for left, right, bottom in horizontal:
        for x0, x1, top in horizontal:
            if top-bottom < .8*sheet[1] or not (_close(x0, left) and _close(x1, right)):
                continue
            if all(any(_close(x, vx) and _close(bottom, low) and _close(top, high)
                       for vx, low, high in vertical) for x in (left, right)):
                if left < -TOL or bottom < -TOL or right > sheet[0]+TOL or top > sheet[1]+TOL:
                    raise ValueError('TEMPLATE_FRAME_OUTSIDE_SHEET')
                frames.add((left, bottom, right, top))
    if not frames:
        raise ValueError('TEMPLATE_FRAME_UNAVAILABLE: no closed page frame')
    ordered = sorted(frames, key=lambda b: (b[2]-b[0])*(b[3]-b[1]))
    inner = ordered[0]
    if any(not (b[0] <= inner[0]+TOL and b[1] <= inner[1]+TOL and
                b[2] >= inner[2]-TOL and b[3] >= inner[3]-TOL) for b in ordered):
        raise ValueError('TEMPLATE_FRAME_AMBIGUOUS')
    return ordered


def _frame_edge(line, frames):
    a, b = line
    for x0, y0, x1, y1 in frames:
        edges = [([x0, y0], [x1, y0]), ([x1, y0], [x1, y1]),
                 ([x1, y1], [x0, y1]), ([x0, y1], [x0, y0])]
        for c, d in edges:
            if (math.dist(a, c) <= TOL and math.dist(b, d) <= TOL or
                    math.dist(a, d) <= TOL and math.dist(b, c) <= TOL):
                return True
    return False


def overlaps(a, b):
    return min(a[2], b[2]) > max(a[0], b[0])+1e-6 and min(a[3], b[3]) > max(a[1], b[1])+1e-6


def contains(outer, inner, tolerance=1e-6):
    return all(inner[i] >= outer[i]-tolerance for i in (0, 1)) and all(inner[i] <= outer[i]+tolerance for i in (2, 3))


def build_template_snapshot(raw, model_path, part_id, font_height=25):
    """No Tekla calls. Unknown/ambiguous evidence blocks instead of assuming empty space."""
    if raw['schemaVersion'] != 'nativeTemplateEvidence.v1':
        raise ValueError('TEMPLATE_SCHEMA_INVALID')
    if raw['modelPath'] != model_path or raw['partId'] != part_id:
        raise ValueError('TEMPLATE_SOURCE_MISMATCH')
    if not raw.get('signature') or not raw.get('capturedUtc'):
        raise ValueError('TEMPLATE_PROVENANCE_MISSING')
    sheet = [float(v) for v in raw['sheetSize']]
    if len(sheet) != 2 or not all(math.isfinite(v) and v > 0 for v in sheet):
        raise ValueError('TEMPLATE_SHEET_INVALID')
    if not math.isfinite(font_height) or font_height <= 0:
        raise ValueError('TEMPLATE_STYLE_INVALID')
    if _point(raw['paper']['Position']) != [0, 0]:
        raise ValueError('TEMPLATE_PAPER_ORIGIN_UNSUPPORTED')
    leaves = list(_leaves(raw['paper']))
    lines = [_line(n) for kind, n in leaves if kind == 'LinePrimitive']
    frames = _frames(lines, sheet)
    clearance = font_height/2
    inner = frames[0]
    safe = [inner[0]+clearance, inner[1]+clearance, inner[2]-clearance, inner[3]-clearance]
    if safe[2] <= safe[0] or safe[3] <= safe[1]:
        raise ValueError('TEMPLATE_NO_USABLE_PAGE')
    regions = []
    for index, (kind, node) in enumerate(leaves):
        if kind == 'LinePrimitive':
            a, b = _line(node)
            if _frame_edge((a, b), frames):
                continue
            bounds = [min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])]
            basis = 'ACTUAL_LINE_ENVELOPE'
        elif kind == 'TextPrimitive':
            at = _point(node['Position']); height = float(node['Height']); angle = float(node['Angle'])
            if not math.isfinite(height) or height <= 0 or not math.isfinite(angle):
                raise ValueError('TEMPLATE_TEXT_INVALID')
            axis_angle = math.remainder(angle, math.pi)
            if abs(axis_angle) <= .001:
                drift = sheet[0]*abs(math.tan(axis_angle))
                bounds = [inner[0], at[1]-2*height-drift, inner[2], at[1]+2*height+drift]
                basis = 'CONSERVATIVE_FULL_WIDTH_TEXT_BAND'
            elif abs(abs(axis_angle)-math.pi/2) <= .001:
                drift = sheet[1]*abs(math.cos(axis_angle))
                bounds = [at[0]-2*height-drift, inner[1], at[0]+2*height+drift, inner[3]]
                basis = 'CONSERVATIVE_FULL_HEIGHT_TEXT_BAND'
            else:
                raise ValueError('TEMPLATE_ROTATED_TEXT_UNSUPPORTED')
        elif kind == 'PointPrimitive':
            at = _point(node['Position']); bounds = at+at
            basis = 'ACTUAL_POINT'
        else:
            raise ValueError('TEMPLATE_UNSUPPORTED_PRIMITIVE: '+kind)
        expanded = [bounds[0]-clearance, bounds[1]-clearance, bounds[2]+clearance, bounds[3]+clearance]
        if overlaps(expanded, safe):
            clipped = [max(expanded[0], safe[0]), max(expanded[1], safe[1]), min(expanded[2], safe[2]), min(expanded[3], safe[3])]
            regions.append(dict(primitiveIndex=index, kind=kind, basis=basis, bounds=clipped,
                                text=node.get('Text') if kind == 'TextPrimitive' else None))
    result = dict(schemaVersion=SCHEMA, source={k: raw[k] for k in
                  ('modelPath', 'partId', 'sheetNumber', 'mark', 'capturedUtc', 'signature', 'attributeFile')},
                  templateApproval='UNCONFIRMED', sheetSize=sheet, scale=1,
                  style=dict(fontHeight=font_height, clearance=clearance),
                  frames=[list(f) for f in frames], safePage=safe, occupiedRegions=regions,
                  textBoundsBasis='CONSERVATIVE_BANDS_NOT_GLYPH_BOUNDS')
    result['snapshotDigest'] = content_digest(result)
    return result


def validate_snapshot(snapshot):
    if snapshot.get('schemaVersion') != SCHEMA or snapshot.get('scale') != 1:
        raise ValueError('TEMPLATE_SNAPSHOT_INVALID')
    data = {k: v for k, v in snapshot.items() if k != 'snapshotDigest'}
    if content_digest(data) != snapshot.get('snapshotDigest'):
        raise ValueError('TEMPLATE_SNAPSHOT_DIGEST_MISMATCH')
