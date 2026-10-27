"""P5 deterministic paper layout of a P2 dimension scheme."""
import math
import re
from functools import lru_cache

from shapely.geometry import LineString, Point, Polygon, box

from .expression_requirements import content_digest, arc_contains
from .expression_coverage import audit_expression_coverage, display_round
from .plate_features import build_feature_model, outer_circle
from .dimension_scheme import build_scheme
from .readability import audit_readability, load_profile
from .template_snapshot import validate_snapshot
from .view_plan import build_view_plan
from .layout_solver import LayoutOverflow, solve_native_layout, witness_curves, scene_envelope, note_bounds


VERSION = 'native-layout.m5'
# Smallest view denominator first. Model offsets grow with it, so paper gaps stay font-sized.
VIEW_SCALES = (1, 2, 2.5, 5, 10, 20, 50)


def number(x):
    return f'{x:.1f}'.rstrip('0').rstrip('.')


def arc_points(segment, step=math.pi/90):
    if segment['kind'] == 'LINE':
        return [segment['start'], segment['end']]
    c, r = segment['center'], segment['radius']
    a = math.atan2(segment['start'][1]-c[1], segment['start'][0]-c[0])
    b = math.atan2(segment['end'][1]-c[1], segment['end'][0]-c[0])
    sign = -1 if segment['clockwise'] else 1
    sweep = (sign*(b-a)) % math.tau
    count = max(2, math.ceil(sweep/step))
    return [[c[0]+r*math.cos(a+sign*sweep*i/count), c[1]+r*math.sin(a+sign*sweep*i/count)]
            for i in range(count+1)]


def plate_polygon(facts):
    rings = []
    by_id = {s['id']: s for s in facts['segments']}
    for loop in facts['boundaryLoops']:
        rings.append([p for identity in loop['segments'] for p in arc_points(by_id[identity])[:-1]])
    polygon = Polygon(rings[0], rings[1:])
    for h in facts['holes']:
        c = facts['points'][h['pointId']]
        if h['kind'] == 'ROUND':
            cut = Point(c).buffer(h['diameter']/2, resolution=90)
        else:
            a, ext = math.radians(h['angle']), (h['length']-h['width'])/2
            ends = [[c[0]+sign*ext*math.cos(a), c[1]+sign*ext*math.sin(a)] for sign in (-1, 1)]
            cut = LineString(ends).buffer(h['width']/2, resolution=90)
        polygon = polygon.difference(cut)
    return polygon


def hole_boundary(hole, center):
    """Native curve commands; no bolt symbol can resurrect a swallowed source."""
    if hole['kind'] == 'ROUND':
        return [{'kind': 'circle', 'center': center, 'radius': hole['diameter']/2}]
    angle = math.radians(hole['angle'])
    u, v = [math.cos(angle), math.sin(angle)], [-math.sin(angle), math.cos(angle)]
    ext, r = (hole['length']-hole['width'])/2, hole['width']/2
    ends = [[center[i]+sign*ext*u[i] for i in (0, 1)] for sign in (-1, 1)]
    at = lambda c, x, y: [c[i]+x*u[i]+y*v[i] for i in (0, 1)]
    return [{'kind': 'line', 'start': at(ends[0], 0, r), 'end': at(ends[1], 0, r)},
            {'kind': 'arc', 'start': at(ends[1], 0, r), 'center': ends[1], 'end': at(ends[1], 0, -r)},
            {'kind': 'line', 'start': at(ends[1], 0, -r), 'end': at(ends[0], 0, -r)},
            {'kind': 'arc', 'start': at(ends[0], 0, -r), 'center': ends[0], 'end': at(ends[0], 0, r)}]


def near_hole_anchor(hole, center, at):
    if hole['kind'] == 'ROUND':
        d = math.dist(center, at)
        return [center[i]+(at[i]-center[i])*hole['diameter']/2/d for i in (0, 1)]
    angle, ext, r = math.radians(hole['angle']), (hole['length']-hole['width'])/2, hole['width']/2
    u = [math.cos(angle), math.sin(angle)]
    capsule = LineString([[center[i]+sign*ext*u[i] for i in (0, 1)] for sign in (-1, 1)]).buffer(r, resolution=360)
    ray = LineString([center, at])
    hit = ray.intersection(capsule.boundary)
    if hit.geom_type != 'Point':
        raise ValueError('Hole leader must approach a unique near boundary')
    # Refine polygonal circle approximation to exact capsule boundary.
    p = [hit.x, hit.y]
    along = sum((p[i]-center[i])*u[i] for i in (0, 1))
    if abs(along) <= ext:
        return p
    cap = [center[i]+math.copysign(ext, along)*u[i] for i in (0, 1)]
    d = [at[i]-center[i] for i in (0, 1)]
    q = [center[i]-cap[i] for i in (0, 1)]
    aa, bb, cc = sum(x*x for x in d), 2*sum(q[i]*d[i] for i in (0, 1)), sum(x*x for x in q)-r*r
    t = (-bb+math.sqrt(bb*bb-4*aa*cc))/(2*aa)
    return [center[i]+t*d[i] for i in (0, 1)]


def combine_runs(values, minimum, tol):
    """Interval index ranges [i, j) that Tekla combines as n@p (equal, consecutive)."""
    runs, start = [], 0
    for i in range(1, len(values)+1):
        if i == len(values) or abs(values[i]-values[start]) > tol:
            if i-start >= minimum:
                runs.append([start, i])
            start = i
    return runs


BOLT_POSITION = re.compile(r'/boltGroups/value/(\d+)/positions/value/(\d+)$')
BOLT_SHARED = re.compile(r'/boltGroups/value/(\d+)/(properties|targets)$')


def bolt_display(facts, raw_part):
    """A bolt group is drawn as its Tekla symbol only when each of its positions
    is exactly one final round hole made by that position alone; any other
    group stays hidden and its final holes are drawn from the material result."""
    groups = (raw_part.get('boltGroups') or {}).get('value') or []
    refs = facts.get('sourceFeatureRefs') or {}
    owner = {}
    for h in facts['holes']:
        sources = refs.get(h['pointId']) or []
        positions = [BOLT_POSITION.search(r) for r in sources]
        shared = [BOLT_SHARED.search(r) for r in sources]
        found = [(int(m.group(1)), int(m.group(2))) for m in positions if m]
        if (h['kind'] == 'ROUND' and len(found) == 1 and
                all(m or s and int(s.group(1)) == found[0][0] for m, s in zip(positions, shared))):
            owner[h['id']] = found[0]
    result = []
    for index, group in enumerate(groups):
        count = len((group.get('positions') or {}).get('value') or [])
        holes = sorted((h for h, (g, _) in owner.items() if g == index), key=lambda h: owner[h][1])
        symbol = count > 0 and sorted(owner[h][1] for h in holes) == list(range(count))
        result.append(dict(modelId=group['id'], display='SYMBOL' if symbol else 'HIDDEN', holes=holes if symbol else []))
    return result


def _cross_span(points, axis, station, tolerance):
    """Cross-axis range of a monotone polyline at one station, or None outside its span."""
    cross = 1-axis
    values = []
    for index in range(len(points)-1):
        start, end = points[index], points[index+1]
        low, high = (start[axis], end[axis]) if start[axis] <= end[axis] else (end[axis], start[axis])
        if station < low-tolerance or station > high+tolerance:
            continue
        if abs(end[axis]-start[axis]) <= tolerance:
            values.extend((start[cross], end[cross]))
            continue
        ratio = (station-start[axis])/(end[axis]-start[axis])
        if ratio < -1e-9 or ratio > 1+1e-9:
            continue
        ratio = min(1.0, max(0.0, ratio))
        values.append(start[cross]+ratio*(end[cross]-start[cross]))
    if not values:
        return None
    return min(values), max(values)


def boundary_edge_side(points, mates, axis, tolerance):
    """+1 when every comparable anchor lies above the other contour chains, -1 when below.

    Shared corners and stations the other chains do not cover abstain. A split
    vote returns None so the caller keeps the envelope-distance rule.
    """
    votes = []
    cross = 1-axis
    for point in points:
        spans = []
        for mate in mates:
            span = _cross_span(mate, axis, point[axis], tolerance)
            if span is not None:
                spans.append(span)
        if not spans:
            continue
        low, high = min(span[0] for span in spans), max(span[1] for span in spans)
        if point[cross] > high+tolerance:
            votes.append(1)
        elif point[cross] < low-tolerance:
            votes.append(-1)
    if votes and all(vote == votes[0] for vote in votes):
        return votes[0]
    return None


def lock_boundary_edge_sides(groups, facts, tolerance):
    """Keep a contour chain on the edge its anchors belong to.

    Envelope distance puts a sloping plate's lower edge above, because those
    anchors can still be nearer the global top. The extension lines then read
    as the upper edge. A unanimous edge vote locks the side; overall, column
    and hole chains stay free.
    """
    boundaries = [group for group in groups if group['key'].startswith('boundary')
                  and group.get('role') != 'overall' and 'column' not in group]
    for group in boundaries:
        mates = [[facts['points'][key] for key in other['points']]
                 for other in boundaries if other is not group and other['axis'] == group['axis']]
        edge = boundary_edge_side([facts['points'][key] for key in group['points']],
                                  mates, group['axis'], tolerance)
        if edge is not None:
            group['side'] = edge
            group['allowedSides'] = [edge]


def place_dimension_side(group, points, bounds, tol):
    """Choose among declared sides by anchor-to-envelope distance in model units."""
    cross = 1-group['axis']
    preferred = group['side']
    costs = [dict(side=side, anchorDistance=sum(abs(p[cross]-bounds[cross+(2 if side > 0 else 0)])
                                             for p in points))
             for side in group.get('allowedSides', [preferred])]
    best = min(c['anchorDistance'] for c in costs)
    tied = [c['side'] for c in costs if c['anchorDistance'] <= best+len(points)*tol]
    selected = preferred if preferred in tied else tied[0]
    return dict(key=group['key'], preferred=preferred, selected=selected, candidates=costs)


def build_layout(requirements, geometry, raw_part, scale=None, spacing=1.0, template=None, _view_scale=None):
    if template is not None and scale is None and _view_scale is None:
        overflow = None
        for candidate in VIEW_SCALES:
            try:
                return build_layout(requirements, geometry, raw_part, spacing=spacing,
                                    template=template, _view_scale=candidate)
            except ValueError as error:
                if 'LAYOUT_OVERFLOW' not in str(error):
                    raise
                overflow = error
        raise overflow
    if requirements['status'] != 'READY':
        raise ValueError('Native requirements are not READY')
    facts = requirements['facts']
    bounds = facts['bounds']; x0,y0,x1,y1 = bounds
    if template is not None:
        validate_snapshot(template)
        if template['source']['partId'] != raw_part['id'] or scale not in (None, 1) or _view_scale not in (None,)+VIEW_SCALES:
            raise ValueError('TEMPLATE_PART_OR_SCALE_MISMATCH')
        view_scale = 1 if _view_scale is None else _view_scale
        style_factor = template['style']['fontHeight']/2.5
        scale = view_scale*style_factor  # Existing P5 nominal paper offsets, in template units.
    else:
        scale = scale or next((s for s in (1, 2, 2.5, 5, 10, 20) if max(x1-x0,y1-y0)/s <= 220), 50)
        view_scale, style_factor = scale, 1
    tol = requirements['modelToleranceMm']
    objects, dimensions, notes, curves = [], [], [], []
    # Main-plane rims of proven bevels are visible geometry, not planar cutouts.
    for bevel in geometry.get('sectionGeometry', {}).get('bevels', []):
        axis = bevel['alongAxis']; cross = 1-axis
        for index, profile_point in enumerate(bevel['profile']):
            ends = []
            for station in bevel['extent']:
                point = [0.,0.]; point[axis]=station; point[cross]=profile_point[0]; ends.append(point)
            curves.append(dict(key=bevel['id']+':rim:'+str(index),kind='line',role='bevel',start=ends[0],end=ends[1]))
    polygon = plate_polygon(facts)
    label_height = 3
    def note(key, kind, text, at, tip=None, candidates=None, **meta):
        height = label_height if kind == 'label' else 2.5
        if candidates is None:
            candidates = [dict(at=at, tip=tip)]
            if kind in ('thickness', 'specification'):
                candidates += [dict(at=[at[0]+dx*scale,at[1]+dy*scale],tip=None)
                               for dx,dy in ((0,-10),(0,-20),(-20,0),(20,0))]
                candidates += [dict(at=p,tip=None) for p in
                               ([(x0+x1)/2,y1+55*scale],[x0-55*scale,(y0+y1)/2],[x1+55*scale,(y0+y1)/2])]
        notes.append(dict(key=key,kind=kind,text=text,at=at,tip=tip,height=height*style_factor,
                          candidates=candidates,**meta))

    features = build_feature_model(facts, tol)
    profile = load_profile()
    scheme = build_scheme(requirements, features, profile)
    lock_boundary_edge_sides(scheme['groups'], facts, tol)
    circular = features['circular']
    decimals = profile['display']['decimals']
    minimum = profile['pitch']['minIntervals']

    # Scheme groups become native dimension commands; lanes are paper offsets.
    side_placements = []
    for group in scheme['groups']:
        axis, ids = group['axis'], group['points']
        pts = [facts['points'][i] for i in ids]
        placement = place_dimension_side(group, pts, bounds, tol)
        side_placements.append(placement)
        side = placement['selected']
        up = [0,side] if axis==0 else [side,0]
        boundary = (y1 if side>0 else y0) if axis==0 else (x1 if side>0 else x0)
        coordinate = boundary+side*group['lane']*scale*spacing
        # Tekla 2017 measures set Distance from the first supplied anchor.
        base = pts[0][1-axis]
        distance = side*(coordinate-base)
        values = [abs(b[axis]-a[axis]) for a,b in zip(pts,pts[1:])]
        # Tekla combines equal consecutive intervals itself (n@p); the plan only
        # predicts those runs so witnesses inside them are not drawn.
        runs = combine_runs(values, minimum, tol)
        command = dict(key=group['key'],axis='xy'[axis],points=ids,coordinates=pts,up=up,distance=distance,
                       role=group['role'],combine=bool(runs),combineRuns=runs,values=values,
                       displayValues=[display_round(v,decimals) for v in values])
        observation = {k:v for k,v in command.items() if k not in ('key','up','distance','combine','combineRuns')}
        # A single contour interval too short for the outer lane stays beside its
        # own anchors; this moves the line only, never the scheme selection.
        # A scheme column is binding, so its members keep full witnesses instead.
        short=(group['key'].startswith('boundary') and 'column' not in group
               and len(values)==1 and values[0]/scale<12)
        if short:
            local=(max if side>0 else min)(p[1-axis] for p in pts)+side*12*scale*spacing
            command['distance']=side*(local-pts[0][1-axis])
        if group.get('local') or short:command['local']=True
        if 'column' in group:command['columnGroup']=group['column']
        dimensions.append(command)
        objects.append(dict(observation,kind='dimension',objectId=group['key']))

    # Allocate layers only for overlapping spans; keep local shoulders near their feature.
    placed=[];allocated=set()
    # Hole positions / direct straight lengths are inside containing body spans.
    def layer_order(d):
        return (0 if d['key'].startswith(('holes','straight')) else 1,
                abs(d['values'][0]) if len(d['values'])==1 else sum(d['values']))
    for d in sorted(dimensions,key=layer_order):
        if d['role']=='overall' or d['key'] in allocated:continue
        members=([item for item in dimensions if item.get('columnGroup')==d['columnGroup']]
                 if d.get('columnGroup') else [d])
        axis=0 if d['axis']=='x' else 1;side=d['up'][1-axis]
        spans=[[item['coordinates'][0][axis],item['coordinates'][-1][axis]] for item in members]
        coordinate=(max if side>0 else min)(item['coordinates'][0][1-axis]+side*item['distance'] for item in members)
        for _ in range(len(dimensions)+1):
            conflicts=[old for old in placed if old['axis']==axis and old['side']==side and
                       any(min(span[1],old['span'][1])-max(span[0],old['span'][0])>tol for span in spans) and
                       abs(coordinate-old['coordinate'])<8*scale]
            if not conflicts:break
            coordinate+=side*10*scale*spacing
        for item,span in zip(members,spans):
            item['distance']=side*(coordinate-item['coordinates'][0][1-axis])
            allocated.add(item['key'])
            placed.append(dict(axis=axis,side=side,span=span,coordinate=coordinate))

    for d in dimensions:
        axis=0 if d['axis']=='x' else 1;side=d['up'][1-axis]
        coordinate=lambda item: item['coordinates'][0][1-axis]+side*item['distance']
        if d['role']=='overall':
            same=[item for item in dimensions if item is not d and item['axis']==d['axis'] and item['up']==d['up']]
            if same:
                target=(max if side>0 else min)(coordinate(item) for item in same)+side*12*scale
                d['distance']=max(d['distance'],side*(target-d['coordinates'][0][1-axis]))

    for s in facts['segments']:
        objects.append(dict(objectId='shape-'+s['id'],kind='segment',feature=s['id'],visible=True,geometry=s))
    bolts=bolt_display(facts,raw_part)
    symbols={h:b for b in bolts for h in b['holes']}
    for h in facts['holes']:
        c=facts['points'][h['pointId']]
        if h['id'] in symbols:
            # The Tekla bolt symbol draws this hole and its axes.
            objects.append(dict(objectId='symbol-'+h['id'],kind='bolt_symbol',holeId=h['id'],holeKind=h['kind'],center=c,
                                diameter=h['diameter'],modelId=symbols[h['id']]['modelId'],**profile['holes']['symbol']))
            continue
        objects.append(dict(objectId='shape-'+h['id'],kind='hole_shape',holeId=h['id'],holeKind=h['kind'],visible=True,center=c,
                            **{k:v for k,v in h.items() if k in ('diameter','width','length','angle')}))
        for j,cmd in enumerate(hole_boundary(h,c)):
            curves.append(dict(cmd,key=f"shape:{h['id']}:{j}",role='hole',feature=h['id']))
        angle=math.radians(h.get('angle',0)); lines=[]
        for j in (0,1):
            a=angle+j*math.pi/2; length=(h.get('length',h.get('diameter')) if j==0 else h.get('width',h.get('diameter')))/2+2*view_scale
            pair=[[c[0]+sign*length*math.cos(a),c[1]+sign*length*math.sin(a)] for sign in (-1,1)]
            curves.append(dict(key=f"center:{h['id']}:{j}",kind='line',role='center',feature=h['id'],start=pair[0],end=pair[1]))
            lines.append(pair)
        objects.append(dict(objectId='center-'+h['id'],kind='center_lines',holeId=h['id'],style='DASH_DOT',lines=lines))

    # Leaders occupy an outside band, with separate lanes for dense callouts.
    for index,callout in enumerate(scheme['holeCallouts']):
        g,text=callout['group'],callout['text']
        members=[h for h in facts['holes'] if h['id'] in g['members']]
        placements=[]
        for member in members:
            center=facts['points'][member['pointId']]
            for dx,dy in ((12,18),(-12,18),(12,-18),(-12,-18)):
                at=[center[0]+dx*scale,center[1]+dy*scale]
                area=box(*note_bounds(text,at,2.5*scale,leader=True))
                tip=near_hole_anchor(member,center,at)
                if polygon.contains(area) and polygon.buffer(tol).covers(LineString([tip,at])):
                    placements.append((at,tip))
        for member in members:
            center=facts['points'][member['pointId']]
            gap=(35+index*10)*scale*spacing
            for at in ([center[0]+12*scale,y1+gap],[x1+gap,center[1]],[x0-gap,center[1]],[center[0],y0-gap]):
                placements.append((at,near_hole_anchor(member,center,at)))
        note(g['id'],'hole_callout',text,None,candidates=[dict(at=at,tip=tip) for at,tip in placements],members=g['members'])
        objects.append(dict(objectId=g['id'],kind='hole_callout',holeKind=g['kind'],text=text,**{k:v for k,v in g.items() if k not in ('id','kind')}))

    arcs=[s for s in facts['segments'] if s['kind']=='ARC']
    if scheme['diameter']:
        diameter=scheme['diameter']
        at=[x1+30*scale,(y0+y1)/2+15*scale]
        c=diameter['center'];r=diameter['radius'];length=math.dist(c,at)
        tip=[c[i]+r*(at[i]-c[i])/length for i in (0,1)]
        diameter_positions=[at,[x0-30*scale,(y0+y1)/2+15*scale],
                            [(x0+x1)/2,y1+30*scale],[(x0+x1)/2,y0-30*scale]]
        placements=[dict(at=p,tip=[c[i]+r*(p[i]-c[i])/math.dist(c,p) for i in (0,1)])
                    for p in diameter_positions]
        note('outer-diameter','diameter',diameter['text'],at,tip,candidates=placements)
        objects.append(dict(objectId='outer-diameter',kind='diameter',text=diameter['text'],value=round(2*r,1),anchor=tip))
    radii=[]
    for radius in scheme['radii']:
        s=radius['segment']
        samples=arc_points(s); tip=samples[len(samples)//2]
        # Native radius dimension through three real arc points.
        radii.append(dict(key='radius:'+s['id'],feature=s['id'],points=[s['start'],tip,s['end']],distance=10*scale))
        text='R'+f"{display_round(s['radius'],decimals):.{decimals}f}"
        objects.append(dict(objectId='radius:'+s['id'],kind='radius',feature=s['id'],value=s['radius'],text=text,anchor=tip,auxiliaryCircle=False))

    label=scheme['label']
    # Keep the established emphasized label where it fits. A dense small part
    # may use the template's normal text size, never smaller than that size.
    candidates=[]
    for label_height in ([3,2.5] if template is not None else [3]):
        for ix in range(1,20):
            for iy in range(1,20):
                p=[x0+(x1-x0)*ix/20,y0+(y1-y0)*iy/20]
                rect=box(*note_bounds(label,p,label_height*scale))
                if polygon.contains(rect) and not any(rect.intersects(LineString([c['start'],c['end']]))
                        for c in curves if c['kind']=='line'):
                    candidates.append((polygon.boundary.distance(Point(p)),p))
        if candidates:break
    label_candidates=[dict(at=p,tip=None) for _,p in sorted(candidates,key=lambda x:-x[0])]
    note('label','label',label,None,candidates=label_candidates)
    objects.append(dict(objectId='label',kind='label',text=label,leader=False))
    thickness=scheme['thickness']
    note('thickness','thickness','t='+number(thickness),[(x0+x1)/2,y0-55*scale*spacing])
    objects.append(dict(objectId='thickness',kind='thickness',value=thickness,displayValue=round(thickness,1),placement='table'))
    for index,(aspect,spec) in enumerate(scheme['specifications']):
        note('spec:'+aspect,'specification',spec['value'],[(x0+x1)/2,y0-(65+10*index)*scale*spacing],feature=aspect)
        objects.append(dict(objectId='spec:'+aspect,kind='specification',aspect=aspect,text=spec['value']))
    padding = 5*scale

    @lru_cache(maxsize=4096)
    def fits_extent(extent):
        if template is not None:
            try:
                build_view_plan(template, extent, view_scale)
                return True
            except ValueError as error:
                if not str(error).startswith('LAYOUT_OVERFLOW'):
                    raise
                return False
        width=(extent[2]-extent[0])/view_scale+100
        height=(extent[3]-extent[1])/view_scale+85
        return width<=1189 and height<=841

    def fits(chosen):
        return fits_extent(tuple(scene_envelope(chosen,bounds,padding)))

    if template is not None and not fits_extent((x0-padding, y0-padding, x1+padding, y1+padding)):
        raise LayoutOverflow(dict(status='LAYOUT_OVERFLOW', reason='TEMPLATE_ENVELOPE_OVERFLOW',
                                  blockedObject='part', conflicts=[], budget=0, visited=0, backtracks=0))

    dimensions,notes,radii,layout_report=solve_native_layout(dimensions,notes,radii,scheme,polygon,
        dict(bounds=bounds,unit=scale,viewScale=view_scale,decimals=decimals,padding=padding,fits=fits,
             staticLines=[dict(key=c['key'],role='geometry',points=[c['start'],c['end']])
                          for c in curves if c['kind']=='line']))
    curves.extend(witness_curves(dimensions,bounds,scale,polygon))
    for dim in dimensions:
        dim['suppressWitnesses']=True
    selected={d['key']:d['up'][1-'xy'.index(d['axis'])] for d in dimensions}
    for placement in side_placements:
        placement['selected']=selected[placement['key']]
    for obj in objects:
        if obj.get('kind')=='diameter':
            obj['anchor']=next(n['tip'] for n in notes if n['key']=='outer-diameter')
    obs=dict(schemaVersion='expressionObservations.v1',sourceDigest=requirements['sourceDigest'],stage='PLAN',objects=objects)
    coverage=audit_expression_coverage(requirements,obs,display_decimals=decimals)
    readability=audit_readability(requirements,scheme,profile,plan=dict(dimensions=dimensions))
    readable=readability['status']=='READABLE' or readability['mode']!='BLOCKING'
    extent=layout_report['envelope']
    view_plan = None
    if template is not None:
        view_plan=build_view_plan(template,extent,view_scale)
        sheet=template['sheetSize']
    else:
        width=(extent[2]-extent[0])/view_scale+100
        height=(extent[3]-extent[1])/view_scale+85
        sheet=next([w,h] for w,h in ((420,297),(594,420),(841,594),(1189,841)) if width<=w and height<=h)
    result=dict(schemaVersion='nativeDrawingPlan.v1',version=VERSION,source=requirements['source'],sourceDigest=requirements['sourceDigest'],
                id=raw_part['id'],position=requirements['source']['partPosition'],scale=view_scale,sheet=sheet,frame=geometry['frame'],
                guid=raw_part['guid']['value'],modification=raw_part['modificationTime']['value']['value'],
                sourceFrame=raw_part['dstvCoordinateSystem']['value'],
                dimensions=dimensions,notes=notes,curves=curves,radii=radii,bolts=bolts,
                sidePlacements=side_placements,layout=layout_report,schemeDigest=content_digest(scheme),
                display=dict(decimals=decimals,combineFormat=profile['combine']['format'],combineMinimum=minimum),
                observations=obs,coverage=coverage,readability=readability,
                status='READY' if coverage['status']=='PLAN_COMPLETE' and readable else 'BLOCKED')
    result['geometryMode']='ANALYTIC_NATIVE_CURVES' if arcs or geometry.get('planarCuts') else 'MODEL_PART_WITH_FINAL_HOLES'
    if view_plan is not None:
        result['viewPlan'] = view_plan
        result['display']['shortDimension'] = 'Outside'
    if scheme.get('decisions'):
        result['schemeDecisions'] = scheme['decisions']
    if result['geometryMode']=='ANALYTIC_NATIVE_CURVES':
        for s in facts['segments']:
            start,end=s['start'],s['end']
            # Tekla 2017 drawing Arc chooses the clockwise side for its radius.
            if s['kind']=='ARC' and not s['clockwise']:start,end=end,start
            curve=dict(key='boundary:'+s['id'],kind=s['kind'].lower(),role='boundary',feature=s['id'],start=start,end=end)
            if s['kind']=='ARC':curve['center']=s['center']
            result['curves'].append(curve)
    elif geometry['materialFinalState']['finalHoleSurfaceObservation']=='MATCHED':
        result['curves']=[c for c in curves if c['role']!='hole']
    result['planDigest']=content_digest(result)
    from .section_plan import attach_sections
    return attach_sections(result, requirements, template)
