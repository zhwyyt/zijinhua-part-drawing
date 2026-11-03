"""Finite measured-style leader candidates and a separate geometry checker."""
import math

from shapely.affinity import translate
from shapely.geometry import LineString, Point, box, shape
from shapely.ops import unary_union

from .contracts import ContractError, check_seal, digest, positive, require, seal
from .page_obstacles import ObstacleIndex, geometry_json
from .primitive_studies import _lines


def _point(value):
    return isinstance(value,list) and len(value)==2 and all(
        type(v) in (int,float) and math.isfinite(v) for v in value)


def _leader(scene, primitives, key):
    check_seal(scene);check_seal(primitives)
    require(scene['primitiveDigest']==primitives['digest'], 'CANDIDATE_BINDING', 'Wrong scene')
    matches=[s for s in primitives['samples'] if s['key']==key and s['kind']=='leader']
    require(len(matches)==1, 'CANDIDATE_ROLE', 'One measured leader required')
    sample=matches[0]
    require('modelView' in sample['details'] and sample['ink']['angleDegrees']==0,
            'CANDIDATE_ROLE', 'Only source-bound horizontal text is supported')
    measured=next(s for s in scene['visibleStudy']['samples'] if s['key']==key)['measurements']
    radii=[m['pdfUserLineWidth']/2*math.sqrt(sum(v*v for v in m['strokeTransform'][:4]))
           for m in measured if m['kind']=='line']
    require(bool(radii) and all(v>0 for v in radii), 'CANDIDATE_WIDTH', 'Measured line width required')
    return sample,max(radii)


def _anchor_paths(scene, sample, capture):
    require(digest(capture)==scene['captureDigest'], 'CANDIDATE_CAPTURE', 'Wrong contour capture')
    binding=sample['details']['modelView'];origin=binding['origin'];tip=sample['details']['route'][0]
    parts=[s for v in capture['page']['presentation']
           if math.dist([v['Position']['X'],v['Position']['Y']],origin)<1e-6
           for s in v['Primitives'] if s.get('Id')==binding['targetObjectId']]
    require(len(parts)==1, 'CANDIDATE_CONTOUR', 'Missing actual target contour')
    lines=[[[p[i]+origin[i] for i in range(2)] for p in line] for line in _lines(parts[0])]
    lines=[line for line in lines if LineString(line).distance(Point(tip))<=.1]
    ids=set()
    # Only an actual PDF straight segment matched to that contour may connect.
    for o in scene['obstacles']:
        if o['kind']!='STROKE' or o['owner'] is not None:continue
        endpoints=o.get('endpoints')
        if endpoints and any(all(math.dist(a,b)<=.1 for a,b in zip(endpoints,order))
                             for line in lines for order in (line,line[::-1])):ids.add(o['id'])
    require(bool(ids), 'CANDIDATE_CONTOUR', 'Anchor has no bound PDF contour')
    return ids


def candidate_geometry(scene, sample, candidate, radius):
    delta=candidate['translation'];route=candidate['route'];original=sample['details']['route']
    require(_point(delta) and isinstance(route,list) and 2<=len(route)<=8 and all(_point(p) for p in route),
            'CANDIDATE_POINTS', 'Finite translation and route required')
    require(route[0]==original[0] and math.dist(route[-1],[original[-1][i]+delta[i] for i in range(2)])<1e-8,
            'CANDIDATE_ANCHOR', 'Anchor or measured text endpoint changed')
    direction=[original[1][i]-original[0][i] for i in range(2)];length=math.hypot(*direction)
    unit=[v/length for v in direction];first=[route[1][i]-route[0][i] for i in range(2)]
    require(abs(first[0]*unit[1]-first[1]*unit[0])<1e-8 and sum(a*b for a,b in zip(first,unit))>0,
            'CANDIDATE_ARROW', 'Measured arrow direction must be preserved')
    require(all(math.dist(a,b)>1e-8 for a,b in zip(route,route[1:])) and LineString(route).is_simple,
            'CANDIDATE_ROUTE', 'Degenerate or self-crossing route')
    arrow=unary_union([shape(o['geometry']) for o in scene['obstacles']
                      if o['id'] in {'path:'+str(a['pdfPath']) for a in sample['arrows']}])
    extent=max((x-route[0][0])*unit[0]+(y-route[0][1])*unit[1] for x,y in arrow.exterior.coords)
    require(math.hypot(*first)>=extent, 'CANDIDATE_ARROW', 'First segment is shorter than measured arrow')
    lines=unary_union([LineString([a,b]).buffer(radius,cap_style=2) for a,b in zip(route,route[1:])])
    text=translate(box(*sample['ink']['occupancyBox']),xoff=delta[0],yoff=delta[1])
    return text,lines,arrow


def check_leader_candidate(scene, primitives, capture, candidate, movable_keys, clearance):
    positive(clearance,'candidate clearance')
    sample,radius=_leader(scene,primitives,candidate['key'])
    leaders={s['key'] for s in primitives['samples'] if s['kind']=='leader'}
    require(set(movable_keys)<=leaders and candidate['key'] in movable_keys,
            'CANDIDATE_MOVABLE', 'Only explicitly selected measured leaders may move')
    text,lines,arrow=candidate_geometry(scene,sample,candidate,radius)
    contacts=_anchor_paths(scene,sample,capture)
    static=[o for o in scene['obstacles'] if o['owner'] not in movable_keys]
    index=ObstacleIndex(static);conflicts=[];tip=Point(candidate['route'][0])
    for role,geometry in (('TEXT',text),('LINE',lines),('ARROW',arrow)):
        if not box(*scene['page']).covers(geometry):conflicts.append(dict(role=role,code='OUTSIDE_PAGE'))
        for hit in index.hits(geometry,clearance):
            obstacle=shape(hit['geometry'])
            if role!='TEXT' and hit['id'] in contacts:
                # The attached first segment has no positive gap at its anchor.
                # Its physical overlap must remain local; later legs keep clearance.
                contact=tip.buffer(.1+2*(radius+hit.get('strokeRadius',0)))
                tail=unary_union([LineString([a,b]).buffer(radius,cap_style=2)
                                  for a,b in zip(candidate['route'][1:],candidate['route'][2:])])
                if (geometry.intersection(obstacle).difference(contact).is_empty and
                        (role=='ARROW' or tail.distance(obstacle)>clearance)):continue
            conflicts.append(dict(role=role,code='STATIC_OBSTACLE',obstacle=hit['id']))
    glyphs=[translate(shape(o['geometry']),xoff=candidate['translation'][0],yoff=candidate['translation'][1])
            for o in scene['obstacles'] if o['kind']=='GLYPH' and o['owner']==candidate['key']]
    ink=unary_union(glyphs) if glyphs else text
    before_terminal=unary_union([LineString([a,b]).buffer(radius,cap_style=2)
                                for a,b in zip(candidate['route'][:-2],candidate['route'][1:-1])])
    # The measured final endpoint is a native connection to its own mask.
    # Only this terminal gap may be below the requested clearance; ink stays protected.
    if (text.intersects(lines) or text.intersects(arrow) or ink.distance(lines)<=clearance or
            ink.distance(arrow)<=clearance or before_terminal.distance(text)<=clearance):
        conflicts.append(dict(code='OWN_TEXT_ROUTE'))
    return dict(status='CANDIDATE_CONFLICT' if conflicts else 'KNOWN_GEOMETRY_CLEAR', conflicts=conflicts,
                occupied=geometry_json(unary_union([text,lines,arrow])), requiresNativeReadback=True)


def _routes(sample, radius, delta, clearance, channels):
    original=sample['details']['route'];tip=original[0]
    d=[original[1][i]-tip[i] for i in range(2)];length=math.hypot(*d);unit=[v/length for v in d]
    arrow=sample['arrows'][0]['points']
    span=max(sum((p[i]-tip[i])*unit[i] for i in range(2)) for p in arrow)
    stub=[tip[i]+unit[i]*(span+2*clearance+2*radius) for i in range(2)]
    end=[original[-1][i]+delta[i] for i in range(2)]
    left=min(sample['ink']['occupancyBox'][0]+delta[0],end[0])-clearance*2-radius
    entry=[left,end[1]]
    yield [tip,stub,entry,end]
    for y in channels:
        yield [tip,stub,[stub[0],y],[left,y],entry,end]


def generate_leader_candidates(scene, primitives, capture, options):
    """Enumerate the declared finite pool, not a complete/global feasibility proof."""
    clearance=positive(options['clearance'],'clearance');keys=options['keys'];offsets=options['offsets']
    require(keys and len(keys)==len(set(keys)) and 0<len(offsets)<=81 and all(_point(p) for p in offsets),
            'CANDIDATE_POOL', 'Explicit finite unique keys and at most 81 offsets required')
    channels=options['channels']
    require(isinstance(channels,list) and len(channels)<=32 and all(type(v) in (int,float) and math.isfinite(v) for v in channels),
            'CANDIDATE_POOL', 'Finite route channels required')
    results=[]
    for key in sorted(keys):
        sample,radius=_leader(scene,primitives,key);seen=set()
        for delta in sorted(offsets):
            for route in _routes(sample,radius,delta,clearance,sorted(channels)):
                route=[p for i,p in enumerate(route) if i==0 or math.dist(p,route[i-1])>1e-8]
                candidate=dict(key=key,translation=delta,route=route)
                identity=digest(candidate)
                if identity in seen:continue
                seen.add(identity)
                try:checked=check_leader_candidate(scene,primitives,capture,candidate,keys,clearance)
                except ContractError as error:
                    if error.code not in ('CANDIDATE_ROUTE','CANDIDATE_ARROW'):raise
                    checked=dict(status='CANDIDATE_CONFLICT',conflicts=[dict(code=error.code)],requiresNativeReadback=True)
                cost=[math.hypot(*delta),LineString(route).length,len(route)-2]
                results.append(dict(id=identity,request=candidate,cost=cost,**checked))
    results.sort(key=lambda r:(r['request']['key'],r['cost'],r['id']))
    clear=[r for r in results if r['status']=='KNOWN_GEOMETRY_CLEAR'];pairs=[]
    for i,a in enumerate(clear):
        for b in clear[i+1:]:
            if a['request']['key']!=b['request']['key'] and shape(a['occupied']).distance(shape(b['occupied']))<=clearance:
                pairs.append([a['id'],b['id']])
    counts={k:sum(r['request']['key']==k for r in clear) for k in sorted(keys)}
    return seal(dict(schemaVersion='LeaderCandidateStudy.v1',productionEligible=False,
                     status='FINITE_POOL_BLOCKED' if not all(counts.values()) else 'CANDIDATES_REQUIRE_JOINT_SELECTION',
                     scope='MEASURED_STYLE_TRANSLATIONS_FIXED_ANCHORS_NOT_NATIVE_REALIZATION',
                     sceneDigest=scene['digest'],options=options,candidates=results,clearCounts=counts,
                     pairConflicts=pairs,unresolved=scene['unresolved'],remainingGates=['JOINT_SELECTION','NATIVE_READBACK','FULL_PAGE_FONT_COVERAGE']))
