"""Observed full-page obstacles and conflicts; never a production approval."""
import json
import math

from shapely.geometry import box, shape
from shapely.ops import unary_union
from shapely.strtree import STRtree

from .contracts import ContractError, check_seal, digest, require, seal
from .visible_strokes import read_paint_states, study_visible_strokes, visible_geometry


def geometry_json(value):
    return json.loads(json.dumps(value.__geo_interface__))


class ObstacleIndex:
    def __init__(self, obstacles, field='geometry'):
        self.items = sorted(obstacles, key=lambda o: o['id'])
        self.geometries = [shape(o[field]) for o in self.items]
        self.tree = STRtree(self.geometries)

    def hits(self, geometry, clearance=0):
        query = geometry.buffer(clearance) if clearance else geometry
        return [self.items[int(i)] for i in sorted(self.tree.query(query))
                if geometry.distance(self.geometries[int(i)]) <= clearance and
                not self.geometries[int(i)].is_empty]


def _path_bound(row):
    """Bezier control hull plus conservative transformed stroke/join extent."""
    points = [p for command in row['path']['commands'] for p in command[1:]]
    require(bool(points), 'OBSTACLE_PATH', 'Path has no control points')
    pad = 0
    if row['path']['stroke']:
        require(row['width'] > 0 and row['cap'] in (0, 1, 2) and
                row['join'] in (0, 1, 2) and row['miter'] >= 1,
                'OBSTACLE_UNBOUNDED_STROKE', 'Cannot bound this stroke')
        norm = math.sqrt(sum(v*v for v in row['path']['transform'][:4]))
        pad = row['width'] * norm * max(2, row['miter'])
    extent = [min(p[0] for p in points), min(p[1] for p in points),
              max(p[0] for p in points), max(p[1] for p in points)]
    # Axis expansion also encloses the square cap/miter corners.
    return box(extent[0]-pad, extent[1]-pad, extent[2]+pad, extent[3]+pad)


def _paint_obstacles(rows):
    obstacles = []; gaps = []
    for row in rows:
        identity = 'path:' + str(row['index'])
        if row['mask'] is not None:
            raw = visible = row['mask']; kind = 'MASK'; exact = True
        else:
            kind = 'STROKE'; exact = True
            try:
                raw, visible, _ = visible_geometry(row, rows, row['path']['fill'])
            except ContractError as error:
                exact = False
                try:
                    raw = _path_bound(row); visible = raw.intersection(row['clip'])
                    for later in rows[row['index']+1:]:
                        if later['mask'] is not None:visible = visible.difference(later['mask'])
                except ContractError as bound_error:
                    gaps.append(dict(id=identity, error=str(bound_error)));continue
        commands=row['path']['commands']
        endpoints=[c[1] for c in commands] if [c[0] for c in commands]==['m','l'] else None
        radius=row['width']/2*math.sqrt(sum(v*v for v in row['path']['transform'][:4])) if row['path']['stroke'] else 0
        obstacles.append(dict(id=identity, kind=kind, path=row['index'], owner=None,
                              exact=exact, endpoints=endpoints, strokeRadius=radius,
                              geometry=geometry_json(raw), visible=geometry_json(visible)))
    return obstacles, gaps


def _bind_owners(obstacles, primitives, visible):
    by_path = {o['path']:o for o in obstacles if 'path' in o}
    glyph_owners = {}
    for sample, measured in zip(primitives['samples'], visible['samples']):
        require(sample['key'] == measured['key'], 'OBSTACLE_ORDER', 'Primitive order differs')
        for g in sample['ink']['glyphIndices']:
            require(g not in glyph_owners, 'OBSTACLE_OWNER', 'Shared primitive glyph')
            glyph_owners[g] = sample['key']
        for m in measured['measurements']:
            item = by_path[m['pdfPath']]
            require(item['owner'] in (None, sample['key']), 'OBSTACLE_OWNER', 'Shared primitive path')
            item['owner'] = sample['key']
        masks = [o for o in obstacles if o['kind']=='MASK' and all(
            abs(a-b) <= primitives['comparisonToleranceNative']
            for a,b in zip(shape(o['geometry']).bounds, sample['ink']['maskBox']))]
        require(len(masks)==1 and masks[0]['owner'] is None,
                'OBSTACLE_MASK_OWNER', 'Missing or ambiguous actual text mask')
        masks[0]['owner'] = sample['key']
    return glyph_owners


def build_page_scene(capture, primitives, ink, pdf_bytes):
    for report in (primitives, ink):check_seal(report)
    require(primitives['captureDigests'][-1] == ink['captureDigest'] == digest(capture),
            'OBSTACLE_CAPTURE', 'Scene belongs to another capture')
    visible = study_visible_strokes(primitives, ink, pdf_bytes)
    require(visible['status']=='VISIBLE_STROKES_MEASURED', 'OBSTACLE_PRIMITIVES', 'Unmeasured target paths')
    rows = read_paint_states(pdf_bytes, ink)
    obstacles, gaps = _paint_obstacles(rows)
    owners = _bind_owners(obstacles, primitives, visible)
    for glyph in ink['glyphs']:
        identity = 'glyph:' + str(glyph['index'])
        if 'error' in glyph or glyph['inkBox'] is None and glyph.get('text') != ' ':
            gaps.append(dict(id=identity, error=glyph.get('error','Missing glyph outline')))
        if glyph['inkBox'] is not None:
            geometry = geometry_json(box(*glyph['inkBox']))
            obstacles.append(dict(id=identity, kind='GLYPH', glyph=glyph['index'],
                                  owner=owners.get(glyph['index']), exact=False,
                                  geometry=geometry, visible=geometry))
    size = capture['page']['drawing']['Layout']['SheetSize']
    require(all(type(size[k]) in (int,float) and math.isfinite(size[k]) and size[k]>0
                for k in ('Width','Height')), 'OBSTACLE_PAGE', 'Invalid source sheet')
    return seal(dict(schemaVersion='PageObstacleScene.v1', productionEligible=False,
                     captureDigest=digest(capture), primitiveDigest=primitives['digest'], inkDigest=ink['digest'],
                     pdfSha256=ink['pdfSha256'], page=[0,0,size['Width'],size['Height']],
                     obstacles=obstacles, unresolved=gaps, visibleStudy=visible))


def _accepted_paths(acceptances, primitives, visible):
    accepted = set()
    samples = {s['key']:s for s in primitives['samples']}
    measurements = {(s['key'],m['pdfPath']):m for s in visible['samples'] for m in s['measurements']}
    for item in acceptances:
        key = (item['key'], item['pdfPath']);m = measurements.get(key)
        require(key not in accepted and item['primitiveDigest']==primitives['digest'] and m is not None and
                samples[key[0]]['kind']=='dimension' and m['kind']=='arrow' and
                item['removedAreaFraction']==m['removedAreaFraction'] and
                item.get('basis')=='USER_ACCEPTED_SHORT_DIMENSION_CLIPPING',
                'OBSTACLE_ACCEPTANCE', 'Exception must match one accepted dimension arrow observation')
        accepted.add(key)
    return accepted


def audit_page_obstacles(scene, primitives, acceptances=()):
    check_seal(scene);check_seal(primitives)
    require(scene['primitiveDigest']==primitives['digest'], 'OBSTACLE_BINDING', 'Wrong primitive evidence')
    visible = scene['visibleStudy'];accepted = _accepted_paths(acceptances, primitives, visible)
    glyph_index = ObstacleIndex([o for o in scene['obstacles'] if o['kind']=='GLYPH'])
    stroke_index = ObstacleIndex([o for o in scene['obstacles'] if o['kind']=='STROKE'], 'visible')
    mask_index = ObstacleIndex([o for o in scene['obstacles'] if o['kind']=='MASK'])
    by_id = {o['id']:o for o in scene['obstacles']};page = box(*scene['page']);results = []
    for sample, measured in zip(primitives['samples'], visible['samples']):
        key = sample['key'];conflicts = [];losses = [];hidden = []
        text = unary_union([shape(by_id['glyph:'+str(i)]['geometry']) for i in sample['ink']['glyphIndices']
                            if 'glyph:'+str(i) in by_id])
        for hit in glyph_index.hits(text):
            if hit['owner'] != key:conflicts.append(dict(code='TEXT_TEXT', obstacle=hit['id']))
        for hit in stroke_index.hits(text):
            conflicts.append(dict(code='VISIBLE_STROKE_TEXT', obstacle=hit['id'], owner=hit['owner']))
        for hit in mask_index.hits(text):
            if hit['owner'] != key:conflicts.append(dict(code='TEXT_FOREIGN_MASK', obstacle=hit['id']))
        if not page.covers(box(*sample['ink']['occupancyBox'])):conflicts.append(dict(code='TEXT_OUTSIDE_PAGE'))
        for m in measured['measurements']:
            geom = shape(m['visibleGeometry']);path = m['pdfPath']
            if geom.is_empty:hidden.append(path)
            elif not page.covers(geom):conflicts.append(dict(code='PATH_OUTSIDE_PAGE', path=path))
            for hit in glyph_index.hits(geom):
                if hit['owner'] != key:conflicts.append(dict(code='PATH_OTHER_TEXT', path=path, obstacle=hit['id']))
            if m['kind']=='arrow' and m['removedAreaFraction']>1e-10:
                allowed = (key,path) in accepted
                losses.append(dict(path=path, fraction=m['removedAreaFraction'], accepted=allowed,
                                   whiteMaskPaths=m['whiteMaskPaths']))
                if not allowed:conflicts.append(dict(code='ARROW_OCCLUDED', path=path))
        results.append(dict(key=key, conflicts=conflicts, arrowLosses=losses, fullyHiddenPaths=hidden))
    return seal(dict(schemaVersion='PageObstacleStudy.v1', productionEligible=False,
                     status='GEOMETRY_CONFLICT' if any(s['conflicts'] for s in results) else
                     'INPUT_BLOCKED' if scene['unresolved'] else 'OBSERVED_TARGETS_CLEAR',
                     scope='TARGET_PRIMITIVES_VS_FULL_PAGE_OBSTACLES_NOT_FULL_DRAWING_ACCEPTANCE',
                     sceneDigest=scene['digest'], samples=results, unresolved=scene['unresolved'],
                     acceptanceEvidence=list(acceptances)))
