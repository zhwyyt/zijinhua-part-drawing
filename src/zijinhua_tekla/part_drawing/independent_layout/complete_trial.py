"""Finite whole-page trial for admitted straight, hole-free single-view plates.

Font advance metrics predict conservative candidate boxes. Actual native readback and
PDF inspection remain mandatory; this is not the production release gate.
"""
import hashlib
import itertools
from pathlib import Path

from fontTools.ttLib import TTFont
from shapely.geometry import LineString, Polygon, box
from shapely.ops import unary_union

from .contracts import require, seal
from .expressions import adapt_expressions
from .view_primitives import model_point


def _font_width(font, text, height):
    cmap = font.getBestCmap()
    require(all(ord(c) in cmap for c in text), 'TRIAL_FONT', 'Missing required glyph')
    units = font['head'].unitsPerEm
    advances = sum(font['hmtx'][cmap[ord(c)]][0] for c in text)
    # Deliberate prediction margin, independently checked against native output.
    return advances / units * height * 1.4 + height * .6


def _conflict(a, b, clearance):
    if a['text'].distance(b['text']) <= clearance:
        return True
    if a['text'].distance(b['lines']) <= clearance or b['text'].distance(a['lines']) <= clearance:
        return True
    if a.get('axis') == b.get('axis') and a.get('side') == b.get('side'):
        if a.get('overall') != b.get('overall'):
            outer, inner = (a, b) if a.get('overall') else (b, a)
            return outer['level'] <= inner['level']
    return False


def _dimension_candidates(obj, bounds, scale, height, font):
    axis = 'xy'.index(obj['axis']); cross = 1 - axis
    points = [[v / scale for v in p] for p in obj['coordinates']]
    result = []
    for side in obj['allowedSides']:
        for level in range(1, 5):
            gap = height * (2 + 3 * level)
            ordinate = bounds[cross + (2 if side > 0 else 0)] + side * gap
            texts = []
            for a, b, value in zip(points, points[1:], obj['displayValues']):
                width = _font_width(font, format(value, 'g'), height)
                mid = (a[axis] + b[axis]) / 2
                # Enclose native above/on-line placement and outside short arrows.
                local = [mid - width / 2, ordinate - 1.8 * height,
                         mid + width / 2, ordinate + 1.8 * height]
                texts.append(box(*local) if axis == 0 else box(local[1], local[0], local[3], local[2]))
            low = min(p[axis] for p in points) - height
            high = max(p[axis] for p in points) + height
            def xy(along, perpendicular):
                return [along, perpendicular] if axis == 0 else [perpendicular, along]
            lines = [LineString([xy(low, ordinate), xy(high, ordinate)])]
            lines.extend(LineString([p, xy(p[axis], ordinate + side * height * .4)]) for p in points)
            text = unary_union(texts); strokes = unary_union(lines)
            result.append(dict(key=obj['objectId'], axis=axis, side=side, level=level,
                               overall=obj['role']=='overall', text=text, lines=strokes,
                               extent=text.union(strokes).bounds, ordinate=ordinate,
                               distance=side*(ordinate-points[0][cross]),
                               cost=(side!=obj['side'])*1000+level*10))
    return result


def _label_candidates(obj, polygon, height, font):
    width = _font_width(font, obj['text'], height)
    x0, y0, x1, y1 = polygon.bounds
    result = []
    # Grid spacing follows typography; add each edge-midpoint/centroid candidate.
    centers = [(x0 + i * height, y0 + j * height)
               for i in range(1, int((x1-x0)/height))
               for j in range(1, int((y1-y0)/height))]
    centers.append((polygon.centroid.x, polygon.centroid.y))
    for x, y in centers:
        footprint = box(x-width/2, y-height, x+width/2, y+height)
        if polygon.buffer(-height*.3).covers(footprint):
            result.append(dict(key=obj['objectId'], text=footprint, lines=LineString(),
                               extent=footprint.bounds, at=[x-width/2+height*.3, y],
                               cost=int(abs(x-polygon.centroid.x)+abs(y-polygon.centroid.y))))
    return sorted(result, key=lambda c:(c['cost'], c['at']))[:100]


def _solve(rows, keys, region, height, contour):
    from ortools.sat.python import cp_model
    width, depth = region[2]-region[0], region[3]-region[1]
    rows = [c for c in rows if c['text'].distance(contour) > height*.2]
    model = cp_model.CpModel()
    variables = [model.new_bool_var(str(i)) for i in range(len(rows))]
    for key in keys:
        model.add_exactly_one([v for v,c in zip(variables,rows) if c['key']==key])
    conflicts = []
    for i, a in enumerate(rows):
        if a['extent'][2]-a['extent'][0]>width or a['extent'][3]-a['extent'][1]>depth:
            model.add(variables[i]==0)
        for j in range(i):
            b=rows[j]
            if a['key']==b['key']:
                continue
            extents=[a['extent'],b['extent']]
            fits=(max(e[2] for e in extents)-min(e[0] for e in extents)<=width and
                  max(e[3] for e in extents)-min(e[1] for e in extents)<=depth)
            if not fits or _conflict(a,b,height*.2):
                model.add(variables[i]+variables[j]<=1);conflicts.append([j,i])
    # Cost prioritizes preferred sides and compact dimension layers; tie is stable.
    objective=sum((c['cost']*10000+i)*v for i,(c,v) in enumerate(zip(rows,variables)))
    model.minimize(objective)
    solver=cp_model.CpSolver();solver.parameters.num_search_workers=1
    solver.parameters.random_seed=0;solver.parameters.max_time_in_seconds=20
    status=solver.solve(model)
    if status not in (cp_model.OPTIMAL,cp_model.FEASIBLE):
        return None, dict(status=solver.status_name(status),candidates=len(rows),conflicts=len(conflicts))
    selected=[c for c,v in zip(rows,variables) if solver.value(v)]
    require(sorted(c['key'] for c in selected)==sorted(keys),'TRIAL_COVERAGE','Missing selected role')
    require(not any(_conflict(a,b,height*.2) for a,b in itertools.combinations(selected,2)),
            'TRIAL_COLLISION','Independent selected-pair check failed')
    extent=unary_union([c['text'].union(c['lines']) for c in selected]+[contour]).bounds
    require(extent[2]-extent[0]<=width and extent[3]-extent[1]<=depth,'TRIAL_FIT','Selected page exceeds region')
    return selected,dict(status=solver.status_name(status),candidates=len(rows),conflicts=len(conflicts),extent=list(extent))


def plan_complete_trial(source, font_path, region, height=25):
    expressions=adapt_expressions(source)
    require(expressions['status']=='SEMANTICS_COMPLETE','TRIAL_COVERAGE','Incomplete expressions')
    objects=expressions['objects'];facts=source['requirements']['facts']
    require(all(o['kind'] in ('DIMENSION','LABEL','THICKNESS','SEGMENT') for o in objects) and
            not facts['holes'] and all(s['kind']=='LINE' for s in facts['segments']),
            'TRIAL_SCOPE','Only complete straight hole-free main views are supported')
    dimensions=[o for o in objects if o['kind']=='DIMENSION']
    labels=[o for o in objects if o['kind']=='LABEL']
    require(len(labels)==1 and dimensions and all(not d['combined'] and d['columnGroup'] is None for d in dimensions),
            'TRIAL_SCOPE','Combined dimensions/columns require another adapter')
    require(len(region)==4 and region[2]>region[0] and region[3]>region[1] and height>0,
            'TRIAL_REGION','Invalid region/style')
    polygon=Polygon([s['start'] for s in facts['segments']])
    require(polygon.is_valid and polygon.area>0,'TRIAL_OUTLINE','Invalid boundary order')
    keys=[d['objectId'] for d in dimensions]+[labels[0]['objectId']]
    attempts=[]
    with TTFont(font_path) as font:
        for scale in (1,2,2.5,5,10,20,50):
            paper=Polygon([(x/scale,y/scale) for x,y in polygon.exterior.coords])
            rows=[]
            for d in dimensions:
                rows.extend(_dimension_candidates(d,paper.bounds,scale,height,font))
            rows.extend(_label_candidates(labels[0],paper,height,font))
            selected,report=_solve(rows,keys,region,height,paper.boundary)
            attempts.append(dict(scale=scale,**report))
            if selected is not None:
                break
    require(selected is not None,'TRIAL_NO_LAYOUT','No complete finite candidate solution')
    frame=source['requirements']['partType']['frame'];primitives=[];placements=[]
    by_key={o['objectId']:o for o in objects}
    for row in selected:
        obj=by_key[row['key']]
        primitive=dict(key=row['key'],font='仿宋',height=height,arrowHeight=height*.4,
                       arrowWidth=height*.4,space='MODEL_GLOBAL',sourceDigest=source['digest'],anchorRefs={})
        if obj['kind']=='DIMENSION':
            cross=1-row['axis'];axis=frame['axisX' if cross==0 else 'axisY']
            primitive.update(kind='dimensionGroup',points=[model_point(source,p) for p in obj['coordinates']],
                             pointRefs=obj['points'],up=[row['side']*v for v in axis],
                             distance=row['distance'],decimals=expressions['profile']['display']['decimals'],
                             shortDimension='Outside',textPlacing='AboveDimensionLine',minimalDistance=2,searchMargin=2)
        else:
            primitive.update(kind='label',text=obj['text'],at=model_point(source,[v*scale for v in row['at']]))
        primitives.append(primitive)
        placements.append({k:v for k,v in row.items() if k not in ('text','lines','extent')})
    return seal(dict(schemaVersion='CompleteDrawingTrial.v1',productionEligible=False,
                     sourceDigest=source['digest'],expressionDigest=expressions['digest'],
                     scale=scale,region=region,height=height,primitives=primitives,placements=placements,
                     attempts=attempts,fontSha256=hashlib.sha256(Path(font_path).read_bytes()).hexdigest(),
                     remainingGates=['NATIVE_READBACK','ACTUAL_PDF_VISUAL']))
