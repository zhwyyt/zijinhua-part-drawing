"""Finite convex, straight stock/Boolean bevel proof. No fitted BREP or hole inference."""
import copy
import math

from shapely.geometry import Polygon

from .analytic_geometry import dot, sub, norm, unit, project, frame_axes


def _cross(a, b):
    return [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]]


def _unique(points, tol):
    result = []
    for p in points:
        if not any(math.dist(p, q) <= tol for q in result):
            result.append(p)
    return result


def _order(points, normal, tol):
    points = _unique(points, tol)
    if len(points) < 3:
        return []
    center = [sum(p[i] for p in points)/len(points) for i in range(3)]
    x = unit(sub(points[0], center)); y = _cross(normal, x)
    return sorted(points, key=lambda p: math.atan2(dot(sub(p, center), y), dot(sub(p, center), x)))


def _clip(faces, normal, offset, owner, tol):
    """Clip convex faces to n.p <= d, retaining source and outward normals."""
    result, intersections = [], []
    for face in faces:
        output = []; points = face['points']
        for a, b in zip(points, points[1:]+points[:1]):
            da, db = dot(normal, a)-offset, dot(normal, b)-offset
            if da <= 1e-8:
                output.append(a)
            if (da < -1e-8 and db > 1e-8) or (db < -1e-8 and da > 1e-8):
                p = [a[i]+da/(da-db)*(b[i]-a[i]) for i in range(3)]
                output.append(p); intersections.append(p)
            if abs(da) <= 1e-8:
                intersections.append(a)
        output = _unique(output, tol/100)
        if len(output) >= 3:
            result.append(dict(face, points=output))
    cap = _order(intersections, normal, tol/100)
    if cap:
        result.append(dict(points=cap, normal=normal, owner=owner))
    return result


def _shell(solid, frame, tol):
    from .native_geometry import value, _point_set_equal
    result = []
    axes = frame_axes(frame)
    for f in value(solid['faces']):
        loops = value(f['loops'])
        if len(loops) != 1:
            raise ValueError('Section shell needs single-loop planar faces; observed hole walls are not supported here')
        points = [project(p, frame) for p in value(loops[0]['vertices'])]
        n = unit([dot(value(f['normal']), a) for a in axes])
        if len(points) < 3 or any(abs(dot(n, sub(p, points[0]))) > tol for p in points):
            raise ValueError('Section face is degenerate/nonplanar')
        turns = [dot(_cross(sub(b,a),sub(c,b)),n) for a,b,c in
                 zip(points,points[1:]+points[:1],points[2:]+points[:2])]
        if not (all(t >= -1e-8 for t in turns) or all(t <= 1e-8 for t in turns)) or max(map(abs,turns)) <= 1e-8:
            raise ValueError('Section face loop is not a convex boundary')
        result.append(dict(points=points, normal=n, owner=value(f['originPartId'])))
    vertices = [p for f in result for p in f['points']]
    if not result or any(dot(f['normal'], sub(p, f['points'][0])) > tol for f in result for p in vertices):
        raise ValueError('Section solid is not a complete outward convex shell')
    chords = [(a, b) for f in result for a, b in zip(f['points'], f['points'][1:]+f['points'][:1])]
    edges = []
    for e in value(solid['edges']):
        if e['apiType'] != 'EDGE_TYPE_NORMAL':
            raise ValueError('Section shell contains non-analytic curved edges')
        edges.append([project(e[k], frame) for k in ('start', 'end')])
    if (any(not any(_point_set_equal(e, c, tol) for c in chords) for e in edges)
            or any(not any(_point_set_equal(c, e, tol) for e in edges) for c in chords)
            or any(sum(_point_set_equal(c, other, tol) for other in chords) != 2 for c in chords)):
        raise ValueError('Section shell edge/face closure is incomplete')
    return result


def prove_sections(part, outline, frame, path, tol):
    """Derive effective cutter planes, then independently check every final face."""
    from .native_geometry import value, match_prisms, _outline, _point_set_equal, _known_cut_children
    operations = value(part['operations'])
    if not operations or not _known_cut_children(operations, value(part['children'])):
        raise ValueError('Section proof requires known cutting operations')
    if len({op['id'] for op in operations}) != len(operations):
        raise ValueError('Duplicate section operation')
    if any(s['kind'] != 'LINE' for s in outline['segments']):
        raise ValueError('Section stock requires analytic straight boundaries')
    stock_check = match_prisms(value(part['rawSolid']), [outline], frame, tol,
                              path+'/rawSolid/value', origin_id=part['id'])
    faces = _shell(value(part['rawSolid']), frame, tol)
    stock = copy.deepcopy(faces); bevels = []
    for oi, op in enumerate(operations):
        op_path = path+f'/operations/value/{oi}'
        if op['runtimeType'] != 'Tekla.Structures.Model.BooleanPart' or value(op['type'])['name'] != 'BOOLEAN_CUT':
            raise ValueError('Section proof supports subtractive Boolean bevels only')
        operative = value(op['operativeGeometry'])
        own_frame = value(operative['coordinateSystem'])
        contour = _outline(operative, own_frame, op_path+'/operativeGeometry/value', tol)
        if any(s['kind'] != 'LINE' for s in contour['segments']):
            raise ValueError('Curved bevel cutter is unsupported')
        match_prisms(value(op['operativeSolid']), [contour], own_frame, tol,
                     op_path+'/operativeSolid/value', origin_id=value(op['operativePart'])['id'])
        cutter = _shell(value(op['operativeSolid']), frame, tol)
        vertices = [p for f in faces for p in f['points']]
        candidates = []
        for face in cutter:
            n = face['normal']; d = dot(n, face['points'][0])
            distances = [dot(n, p)-d for p in vertices]
            if min(distances) >= -tol or max(distances) <= tol:
                continue
            removed = _clip(faces, n, d, op['id'], tol)
            removed_points = [p for f in removed for p in f['points']]
            # The other cutter planes must contain the entire removed region.
            if removed_points and all(dot(c['normal'], sub(p, c['points'][0])) <= tol
                                      for c in cutter for p in removed_points):
                candidates.append((n, d))
        if len(candidates) != 1:
            raise ValueError('Cutter does not prove one effective planar bevel')
        n, d = candidates[0]
        axes = [i for i in (0, 1) if abs(n[i]) < 1e-8]
        if len(axes) != 1 or abs(n[2]) < 1e-8 or abs(n[1-axes[0]]) < 1e-8:
            raise ValueError('Section requires a thickness bevel constant along one plate axis')
        faces = _clip(faces, [-v for v in n], -d, op['id'], tol)
        bevels.append(dict(id='bevel:'+str(op['id']), operationId=op['id'], alongAxis=axes[0],
                           normal=[-v for v in n], offset=-d, sourceRef=op_path,
                           sideOriginIds=[op['id'], value(op['operativePart'])['id']]))
    observed = _shell(value(part['finalSolid']), frame, tol)
    remaining = list(observed); mappings = []
    for face in faces:
        allowed = next((b['sideOriginIds'] for b in bevels if b['operationId'] == face['owner']), [part['id']])
        matches = [f for f in remaining if _point_set_equal(f['points'], face['points'], tol)
                   and dot(f['normal'], face['normal']) > 1-1e-8 and f['owner'] in allowed]
        if len(matches) != 1:
            raise ValueError('Final section face differs from Boolean result/source/normal')
        match = matches[0]; remaining.remove(match)
        mappings.append(dict(faceIndex=observed.index(match), originPartId=match['owner']))
    if remaining:
        raise ValueError('Unexplained final section faces')
    # Restrict to full-edge bevels leaving the main-plane projection unchanged.
    stock_xy = Polygon([s['start'] for s in outline['segments']])
    final_xy = Polygon(_order_xy([p[:2] for f in faces for p in f['points']]))
    if stock_xy.symmetric_difference(final_xy).area > tol*tol:
        raise ValueError('Bevel changes the main-plane envelope; additional planar cuts required')
    levels = stock_check['levels']; thickness = levels[1]-levels[0]
    for bevel in bevels:
        face = next(f for f in faces if f['owner'] == bevel['operationId'])
        cross_axis = 1-bevel['alongAxis']
        profile = _unique([[p[cross_axis], p[2]] for p in face['points']], tol)
        if len(profile) != 2:
            raise ValueError('Bevel is not constant along its complete edge')
        profile.sort(key=lambda p: p[1])
        a, b = profile; depth = b[1]-a[1]
        if depth <= tol or abs(a[0]-b[0]) <= tol:
            raise ValueError('Degenerate bevel')
        if abs(a[1]-levels[0]) <= tol:
            side, land = 'LOW', levels[1]-b[1]
        elif abs(b[1]-levels[1]) <= tol:
            side, land = 'HIGH', a[1]-levels[0]
        else:
            raise ValueError('Bevel does not reach a thickness face')
        if land <= tol:
            raise ValueError('Knife-edge or compound bevel requires a separate expression')
        bevel.update(profile=profile, depth=depth, land=land, thickness=thickness, side=side,
                     angle=math.degrees(math.atan2(abs(b[0]-a[0]), depth)),
                     extent=[min(p[bevel['alongAxis']] for p in face['points']),
                             max(p[bevel['alongAxis']] for p in face['points'])])
    return dict(status='MATCHED', basis='ANALYTIC_CONVEX_CLIP_PLUS_COMPLETE_NATIVE_SHELL',
                bevels=bevels, faces=faces, stockFaces=stock, levels=levels, thickness=thickness,
                faceEvidence=mappings, sourceRef=path+'/finalSolid')


def _order_xy(points):
    # Convex planar projection is exact for this explicitly convex finite scope.
    from shapely.geometry import MultiPoint
    return list(MultiPoint(points).convex_hull.exterior.coords)[:-1]


def section_material(part, outline, groups, proof, frame, path, tol):
    """Separate holes from bevels only when their complete domains are disjoint."""
    from .native_geometry import _material_final_state
    base = copy.deepcopy(part)
    base['operations']['value'] = []; base['children']['value'] = []
    base['finalSolid'] = copy.deepcopy(part['rawSolid'])
    # Reuse target-specific shape/penetration proof independently of bevels.
    # The independent proof above owns ALL actual cuts/faces.
    result = _material_final_state(base, outline, [], groups, frame, path, tol)
    for hole in result['holes']:
        for bevel in proof['bevels']:
            axis = 1-bevel['alongAxis']
            if hole['kind'] not in ('ROUND', 'SLOT'):
                raise ValueError('Section/hole interaction is unsupported')
            center = hole['center']; radius = hole['diameter']/2
            extent = radius+abs(hole.get('direction', [0, 0])[axis])*hole.get('extension', 0)/2
            for c in (center[axis]-extent, center[axis]+extent):
                for z in proof['levels']:
                    point = list(center)+[z]; point[axis] = c
                    if dot(bevel['normal'], point)-bevel['offset'] > -tol:
                        raise ValueError('Hole intersects a thickness bevel; section interaction required')
    result.update(sectionGeometry=proof, scope='DISJOINT_THROUGH_HOLES_AND_PLANAR_BEVELS',
                  solidCheck=dict(status='MATCHED', levels=proof['levels'], thickness=proof['thickness'],
                                  sourceRef=proof['sourceRef'], faceEvidence=proof['faceEvidence']),
                  basis=proof['basis']+'; '+result['basis'])
    result['operationMappings'] += [dict(id=b['id'], sourceOperationId=b['operationId'],
                                       sourceKind='BEVEL', sourceRef=b['sourceRef']) for b in proof['bevels']]
    return result
