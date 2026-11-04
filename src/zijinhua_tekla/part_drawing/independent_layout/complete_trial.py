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
from .view_primitives import model_point, source_basis


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


def _dimension_candidates(obj, bounds, scale, height, font, short_witnesses=False, levels=4, local_pair=False, measured_shorts=False):
    axis = 'xy'.index(obj['axis']); cross = 1 - axis
    points = [[v / scale for v in p] for p in obj['coordinates']]
    widths=[_font_width(font,format(v,'g'),height) for v in obj['displayValues']]
    outside_pair=local_pair and len(widths)==2 and all(
        abs(b[axis]-a[axis])<width+height*.8 for a,b,width in zip(points,points[1:],widths))
    result = []
    for side in obj['allowedSides']:
        for level in range(1, levels+1):
            gap = height * (1+level if local_pair else 2+3*level)
            ordinate = bounds[cross + (2 if side > 0 else 0)] + side * gap
            texts = []
            for index,(a, b, width) in enumerate(zip(points, points[1:], widths)):
                mid = (a[axis] + b[axis]) / 2
                # Enclose native above/on-line placement and outside short arrows.
                local = [mid - width / 2, ordinate - 1.8 * height,
                         mid + width / 2, ordinate + 1.8 * height]
                if outside_pair:
                    # Two short intervals: native Outside places the first text
                    # before the chain and the second after it (102 PDF).
                    endpoint=min(p[axis] for p in points) if index==0 else max(p[axis] for p in points)
                    sign=-1 if index==0 else 1
                    ends=[endpoint+sign*height*.25,endpoint+sign*(width+height*.5)]
                    local[0],local[2]=sorted(ends)
                if measured_shorts and abs(b[axis]-a[axis])<width+height*.8:
                    if index==0:local[0]=min(local[0],min(p[axis] for p in points)-width-height)
                    if index==len(widths)-1:local[2]=max(local[2],max(p[axis] for p in points)+width+height)
                texts.append(box(*local) if axis == 0 else box(local[1], local[0], local[3], local[2]))
            low = min(p[axis] for p in points) - height
            high = max(p[axis] for p in points) + height
            if measured_shorts:
                if abs(points[1][axis]-points[0][axis])<widths[0]+height*.8:low-=widths[0]
                if abs(points[-1][axis]-points[-2][axis])<widths[-1]+height*.8:high+=widths[-1]
            def xy(along, perpendicular):
                return [along, perpendicular] if axis == 0 else [perpendicular, along]
            lines = [LineString([xy(low, ordinate), xy(high, ordinate)])]
            lines.extend(LineString([p, xy(p[axis], ordinate + side * height * .4)]) for p in points)
            text = unary_union(texts); strokes = unary_union(lines)
            candidate=dict(key=obj['objectId'], axis=axis, side=side, level=level,
                               overall=obj['role']=='overall', text=text, lines=strokes,
                               extent=text.union(strokes).bounds, ordinate=ordinate,
                               distance=side*(ordinate-points[0][cross]),
                               cost=(side!=obj['side'])*1000+level*10)
            result.append(candidate)
            if short_witnesses:
                short_lines=[lines[0]]+[LineString([xy(p[axis],ordinate-side*height*2),
                                                   xy(p[axis],ordinate+side*height*.4)]) for p in points]
                strokes=unary_union(short_lines)
                result.append(dict(candidate,lines=strokes,extent=text.union(strokes).bounds,
                                   witnessLength=height*2,cost=candidate['cost']+100000))
    return result


def _label_candidates(obj, polygon, height, font, centered=False):
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
                               extent=footprint.bounds, at=[x if centered else x-width/2+height*.3, y],
                               cost=int(abs(x-polygon.centroid.x)+abs(y-polygon.centroid.y))))
    return sorted(result, key=lambda c:(c['cost'], c['at']))[:100]


def _feature_dimension_candidates(obj, bounds, scale, height, font, short, levels, locality, facts, measured_shorts=False):
    """Local obligations restrict the pool before ordinary layout scoring."""
    required=locality is not None
    rows=[] if required else _dimension_candidates(obj,bounds,scale,height,font,short_witnesses=short,levels=levels,measured_shorts=measured_shorts)
    if required or obj['localAllowed'] and obj['band']:
        cross=1-'xy'.index(obj['axis']);local=list(bounds)
        if required:
            local[cross],local[cross+2]=[locality['bounds'][i]/scale for i in (cross,cross+2)]
        else:
            positions=[facts['points'][k][cross]/scale for k in obj['band']]
            local[cross],local[cross+2]=min(positions),max(positions)
        for c in _dimension_candidates(obj,local,scale,height,font,short_witnesses=short,levels=2,local_pair=required,measured_shorts=measured_shorts):
            if c['distance']<=0:continue
            if required and abs(c['ordinate']-obj['coordinates'][0][cross]/scale)>height*3 and 'witnessLength' not in c:
                continue
            c['level']-=10
            if not required:c['cost']-=200000
            rows.append(c)
    return rows


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


def plan_complete_trial(source, font_path, region, height=25, complete_features=False, split_crowded=False, measured_shorts=False):
    require(not split_crowded,'DIMENSION_CHAIN_CONTINUITY','A semantic dimension chain must remain one native set')
    expressions=adapt_expressions(source)
    require(expressions['status']=='SEMANTICS_COMPLETE','TRIAL_COVERAGE','Incomplete expressions')
    objects=expressions['objects'];facts=source['requirements']['facts']
    allowed=('DIMENSION','LABEL','THICKNESS','SEGMENT','HOLE','HOLE_NOTE','RADIUS') if complete_features else (
        'DIMENSION','LABEL','THICKNESS','SEGMENT')
    require(all(o['kind'] in allowed for o in objects) and
            (complete_features or not facts['holes'] and all(s['kind']=='LINE' for s in facts['segments'])),
            'TRIAL_SCOPE','Only complete straight hole-free main views are supported')
    expressions=adapt_expressions(source,boundary_paths=not complete_features,complete_features=complete_features)
    objects=expressions['objects']
    dimensions=[o for o in objects if o['kind']=='DIMENSION']
    labels=[o for o in objects if o['kind']=='LABEL']
    require(len(labels)==1 and dimensions and all(not d['combined'] and d['columnGroup'] is None for d in dimensions),
            'TRIAL_SCOPE','Combined dimensions/columns require another adapter')
    require(len(region)==4 and region[2]>region[0] and region[3]>region[1] and height>0,
            'TRIAL_REGION','Invalid region/style')
    from .feature_geometry import material_polygon, native_curves, source_curves
    from .feature_candidates import note_candidates, outside_labels
    from .feature_locality import internal_feature_localities, check_local_feature_placements
    localities=internal_feature_localities(expressions) if complete_features else {}
    polygon=material_polygon(facts) if complete_features else Polygon([s['start'] for s in facts['segments']])
    require(polygon.is_valid and polygon.area>0,'TRIAL_OUTLINE','Invalid boundary order')
    notes=[o for o in objects if o['kind'] in ('HOLE_NOTE','RADIUS')]
    keys=[d['objectId'] for d in dimensions]+[labels[0]['objectId']]+[o['objectId'] for o in notes]
    attempts=[]
    with TTFont(font_path) as font:
        for scale in (1,2,2.5,5,10,20,50):
            if measured_shorts and ((facts['bounds'][2]-facts['bounds'][0])/scale>region[2]-region[0] or
                                  (facts['bounds'][3]-facts['bounds'][1])/scale>region[3]-region[1]):
                attempts.append(dict(scale=scale,status='CONTOUR_EXCEEDS_REGION'));continue
            paper=material_polygon(facts,scale) if complete_features else Polygon([(x/scale,y/scale) for x,y in polygon.exterior.coords])
            obstacle=paper.boundary
            if complete_features:
                axes=[LineString([[v/scale for v in c[k]] for k in ('start','end')])
                      for c in source_curves(source,scale,height) if c.get('centerLine')]
                obstacle=unary_union([obstacle]+axes)
            for short in (False,True):
                rows=[]
                for d in dimensions:
                    levels=max(4,sum(o['axis']==d['axis'] for o in dimensions)+1) if complete_features else 4
                    if not complete_features:
                        rows.extend(_dimension_candidates(d,paper.bounds,scale,height,font,short_witnesses=short,levels=levels))
                    else:
                        rows.extend(_feature_dimension_candidates(d,paper.bounds,scale,height,font,short,levels,localities.get(d['objectId']),facts,measured_shorts=measured_shorts))
                label_rows=_label_candidates(labels[0],paper,height,font,centered=complete_features)
                if not label_rows and complete_features:
                    label_rows=outside_labels(labels[0],paper.bounds,height,_font_width(font,labels[0]['text'],height))
                rows.extend(label_rows)
                for note in notes:
                    rows.extend(note_candidates(note,facts,paper,scale,height,_font_width(font,note['text'],height)))
                selected,report=_solve(rows,keys,region,height,obstacle)
                attempts.append(dict(scale=scale,**({'shortWitnessCandidates':True} if short else {}),**report))
                if selected is not None:break
            if selected is not None:
                break
    require(selected is not None,'TRIAL_NO_LAYOUT','No complete finite candidate solution')
    primitives=[];placements=[]
    by_key={o['objectId']:o for o in objects}
    for row in selected:
        obj=by_key[row['key']]
        primitive=dict(key=row['key'],font='仿宋',height=height,arrowHeight=height*.4,
                       arrowWidth=height*.4,space='MODEL_GLOBAL',sourceDigest=source['digest'],anchorRefs={})
        if obj['kind']=='DIMENSION':
            cross=1-row['axis'];axis=source_basis(source)[cross]
            primitive.update(kind='dimensionGroup',points=[model_point(source,p) for p in obj['coordinates']],
                             pointRefs=obj['points'],up=[row['side']*v for v in axis],
                             distance=row['distance'],decimals=expressions['profile']['display']['decimals'],
                             shortDimension='Outside',textPlacing='AboveDimensionLine',minimalDistance=2,searchMargin=2)
            if 'witnessLength' in row:
                primitive['witnessLength']=row['witnessLength']
        elif obj['kind']=='LABEL':
            primitive.update(kind='label',text=obj['text'],at=model_point(source,[v*scale for v in row['at']]))
        else:
            primitive.update(kind='leader',text=obj['text'],at=model_point(source,[v*scale for v in row['at']]),
                             tip=model_point(source,row['tip']),elbows=[],anchorRefs={'tip':row['featureId']})
        primitives.append(primitive)
        placements.append({k:v for k,v in row.items() if k not in ('text','lines','extent')})
    from .boundary_planning import check_boundary_placements
    if 'boundaryPlanning' in expressions['scheme']:check_boundary_placements(expressions,placements)
    if complete_features:check_local_feature_placements(expressions,placements,scale,height)
    return seal(dict(schemaVersion='CompleteDrawingTrial.v1',productionEligible=False,
                     expressionAdapterVersion=expressions['adapterVersion'],
                     sourceDigest=source['digest'],expressionDigest=expressions['digest'],
                     scale=scale,region=region,height=height,primitives=primitives,placements=placements,
                     **({'curves':native_curves(source,scale,height)} if complete_features else {}),
                     attempts=attempts,fontSha256=hashlib.sha256(Path(font_path).read_bytes()).hexdigest(),
                     remainingGates=['NATIVE_READBACK','ACTUAL_PDF_VISUAL']))
