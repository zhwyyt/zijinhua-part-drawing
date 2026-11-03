"""Finite measured dimensions/part mark/leaders; not a production layout input."""
import copy
import math

from shapely.affinity import translate
from shapely.geometry import LineString, Point, box, shape
from shapely.ops import unary_union

from .contracts import ContractError, check_seal, digest, positive, require, seal
from .foundation_studies import _nodes
from .glyph_occupancy import bind_ink
from .leader_candidates import _anchor_paths, _point, generate_leader_candidates
from .page_obstacles import ObstacleIndex, _accepted_paths, geometry_json
from .primitive_studies import _lines, _text_observation
from .view_primitives import _translate


def bind_part_number(scene, primitives, capture, ink, source):
    """Bind one plain PART_POS mark in the already source-bound single view."""
    for value in (scene, primitives, ink, source):check_seal(value)
    require(scene['captureDigest']==digest(capture)==ink['captureDigest'] and
            scene['primitiveDigest']==primitives['digest'] and scene['inkDigest']==ink['digest'],
            'JOINT_BINDING', 'Different native observations')
    bindings=[s['details']['modelView'] for s in primitives['samples']]
    require(bindings and source['status']=='SOURCE_READY' and all(
        b['sourceDigest']==source['digest'] and str(b['modelPartId'])==source['source']['partId'] and
        all(b[k]==bindings[0][k] for k in ('viewId','targetObjectId','origin','scale')) for b in bindings),
        'JOINT_SOURCE', 'One admitted source and view required')
    require(len(capture['page']['views'])==1, 'NUMBER_VIEW', 'Single main view required')
    view=capture['page']['views'][0];origin=bindings[0]['origin'];scale=bindings[0]['scale']
    parts=[o for o in view['objects'] if o['data']['type'].endswith('.Part')]
    require(len(parts)==1 and str(parts[0]['data']['ModelIdentifier']['ID'])==source['source']['partId'],
            'NUMBER_PART', 'One visible-view model part must match the source')
    marks=[o for o in view['objects'] if o['data']['type'].endswith('.Mark')]
    require(len(marks)==1, 'NUMBER_MARK', 'Exactly one unambiguous part mark required')
    mark=marks[0];attributes=mark['data']['Attributes'];content=attributes['Content']
    require(len(content)==1 and content[0].get('Name')=='PART_POS' and
            content[0]['Value']==content[0]['unformatted']==source['source']['partPosition'] and
            attributes['Angle']==0 and attributes['ArrowHead']['Head']=='NoArrow' and
            attributes['Frame']['Type']=='None' and mark['data']['Hideable']['IsHidden'] is False,
            'NUMBER_CONTENT', 'Plain visible exact part position required')
    sdk=mark['boundingBox'];bounds=[sdk[k][axis]/scale+origin[i] for k in ('MinPoint','MaxPoint')
                                   for i,axis in enumerate(('X','Y'))]
    font=content[0]['Font'];matches=[]
    for display in capture['page']['presentation']:
        if math.dist([display['Position'][k] for k in ('X','Y')],origin)>1e-6:continue
        for raw in display['Primitives']:
            texts=list(_nodes(raw,'.TextPrimitive'))
            if len(texts)!=1 or texts[0]['Text']!=content[0]['Value']:continue
            segment=_translate(raw,origin)
            polygons=[p for p in segment['Primitives'] if p['type'].endswith('.PolygonPrimitive')]
            require(polygons and all(p==polygons[0] for p in polygons), 'NUMBER_MASK', 'Only identical repeated mark masks supported')
            mask_count=len(polygons)
            segment['Primitives']=[p for p in segment['Primitives'] if not p['type'].endswith('.PolygonPrimitive')]+[polygons[0]]
            observed=_text_observation('part-number',raw['Id'],segment)
            mask=observed['box']
            if all(abs((mask[i]+mask[i+2])-(bounds[i]+bounds[i+2]))<1e-5 for i in range(2)):
                matches.append((segment,observed,mask_count))
    require(len(matches)==1, 'NUMBER_ID', 'Mark SDK box must bind one display ID')
    segment,observed,mask_count=matches[0]
    require(observed['font']==font['Name'] and observed['height']==font['Height'] and observed['angle']==0 and
            box(*bounds).buffer(1e-5).covers(box(*observed['box'])) and
            all(abs(a-b)<=font['Height']*.1 for a,b in zip(bounds,observed['box'])) and
            not list(_nodes(segment,'.SymbolPrimitive')) and not list(_lines(segment)),
            'NUMBER_STYLE', 'Unexpected mark frame, angle, font or size')
    observed['box']=bounds
    bound=bind_ink(observed,segment,ink['glyphs'])
    masks=[o for o in scene['obstacles'] if o['kind']=='MASK' and all(
        abs(a-b)<=primitives['comparisonToleranceNative'] for a,b in zip(shape(o['geometry']).bounds,bound['maskBox']))]
    require(len(masks)==mask_count, 'NUMBER_MASK', 'PDF/native repeated mask counts differ')
    ids={*(o['id'] for o in masks),*('glyph:'+str(i) for i in bound['glyphIndices'])}
    owned=[o for o in scene['obstacles'] if o['id'] in ids]
    require(len(owned)==len(ids) and all(o['owner'] is None for o in owned),
            'NUMBER_OWNER', 'Mark geometry missing or already owned')
    return dict(key='part-number',ink=bound,obstacleIds=sorted(ids),sourceDigest=source['digest'],
                displayObjectId=observed['objectId'],sdkMarkIndex=mark['index'])


def bind_dimension_points(scene, primitives, capture, ink):
    """Bind native PointPrimitives missing from the earlier line/arrow study."""
    body={k:copy.deepcopy(v) for k,v in scene.items() if k!='digest'};bindings=[]
    by_id={o['id']:o for o in body['obstacles']};tolerance=primitives['comparisonToleranceNative']
    for sample in primitives['samples']:
        if sample['kind']!='dimension':continue
        origin=sample['details']['modelView']['origin']
        segments=[_translate(s,origin) for v in capture['page']['presentation']
                  if math.dist([v['Position'][k] for k in ('X','Y')],origin)<1e-6
                  for s in v['Primitives'] if s.get('Id')==sample['displayObjectId']]
        require(len(segments)==1,'DIMENSION_POINTS','Missing measured dimension display')
        for point in _nodes(segments[0],'.PointPrimitive'):
            center=[point['Position'][k] for k in ('X','Y')]
            paths=[p for p in ink['paths'] if p['stroke'] and p['fill'] and p['commands'][-1][0]=='h' and
                   any(c[0]=='c' for c in p['commands']) and
                   all(math.dist(q,center)<=tolerance for c in p['commands'] for q in c[1:])]
            require(len(paths)==1, 'DIMENSION_POINTS', 'Native point must match a unique small PDF curve')
            identity='path:'+str(paths[0]['index']);item=by_id.get(identity)
            require(item is not None and item['owner'] is None,'DIMENSION_POINTS','Missing/shared point obstacle')
            item['owner']=sample['key'];bindings.append(dict(key=sample['key'],path=identity,nativePoint=center))
    return seal(body),bindings


def _dimension_frame(sample):
    require(sample['kind']=='dimension' and len(sample['arrows'])==2,
            'DIMENSION_SCOPE', 'Measured two-anchor dimension set required')
    anchors={a['role']:a['sheetPoint'] for a in sample['details']['modelView']['anchors']}
    require(set(anchors)=={'start','end'}, 'DIMENSION_ANCHORS', 'Two source anchors required')
    points=[anchors[k] for k in ('start','end')];tips=[a['tip'] for a in sample['arrows']]
    delta=[tips[0][i]-points[0][i] for i in range(2)];distance=math.hypot(*delta)
    positive(distance,'measured dimension distance');normal=[v/distance for v in delta]
    require(math.dist(delta,[tips[1][i]-points[1][i] for i in range(2)])<.001 and
            abs(sum((points[1][i]-points[0][i])*normal[i] for i in range(2)))<.001,
            'DIMENSION_BASELINE', 'Only common orthogonal baseline supported')
    return points,normal,distance


def dimension_geometry(scene, sample, offset, accepted):
    """Keep extension near endpoints fixed; translate the baseline and text."""
    require(type(offset) in (int,float) and math.isfinite(offset), 'DIMENSION_OFFSET', 'Finite offset required')
    anchors,normal,distance=_dimension_frame(sample)
    require(distance+offset>0, 'DIMENSION_SIDE', 'Original side must be preserved')
    delta=[offset*v for v in normal];owned=[o for o in scene['obstacles'] if o['owner']==sample['key']]
    extensions={};parts=[];tolerance=scene['associationTolerance']
    for item in owned:
        geometry=shape(item['geometry']);ends=item.get('endpoints');anchor_index=None
        if item['kind']=='STROKE' and ends:
            length=math.dist(*ends)
            if length>0 and abs((ends[1][0]-ends[0][0])*normal[1]-(ends[1][1]-ends[0][1])*normal[0])<=tolerance:
                matches=[]
                for j,a in enumerate(anchors):
                    along=[sum((p[i]-a[i])*normal[i] for i in range(2)) for p in ends]
                    across=abs((ends[0][0]-a[0])*normal[1]-(ends[0][1]-a[1])*normal[0])
                    if across<=tolerance and -tolerance<=min(along)<distance-tolerance and max(along)>=distance:
                        matches.append(j)
                if len(matches)==1:
                    anchor_index=matches[0];a=anchors[anchor_index]
                    near,far=sorted(ends,key=lambda p:math.dist(p,a))
                    require(anchor_index not in extensions and math.dist(near,a)<distance and
                            sum((far[i]-near[i])*normal[i] for i in range(2))>0,
                            'DIMENSION_EXTENSION', 'Ambiguous extension direction')
                    end=[far[i]+delta[i] for i in range(2)]
                    require(sum((end[i]-near[i])*normal[i] for i in range(2))>0,
                            'DIMENSION_EXTENSION', 'Offset reverses an extension')
                    geometry=LineString([near,end]).buffer(item['strokeRadius'],cap_style=2)
                    extensions[anchor_index]=dict(path=item['id'],near=near,far=end,anchor=a)
        if anchor_index is None:geometry=translate(geometry,xoff=delta[0],yoff=delta[1])
        parts.append(dict(id=item['id'],kind=item['kind'],geometry=geometry,anchorIndex=anchor_index))
    require(set(extensions)=={0,1}, 'DIMENSION_EXTENSION', 'Two unique measured extensions required')
    mask=unary_union([p['geometry'] for p in parts if p['kind']=='MASK'])
    glyphs=unary_union([p['geometry'] for p in parts if p['kind']=='GLYPH'])
    strokes=[p for p in parts if p['kind']=='STROKE'];conflicts=[]
    measurements={m['pdfPath']:m for s in scene['visibleStudy']['samples'] if s['key']==sample['key'] for m in s['measurements']}
    for arrow in sample['arrows']:
        raw=next(p['geometry'] for p in strokes if p['id']=='path:'+str(arrow['pdfPath']))
        loss=raw.intersection(mask).area/raw.area
        if loss>1e-10 and not ((sample['key'],arrow['pdfPath']) in accepted and
                abs(loss-measurements[arrow['pdfPath']]['removedAreaFraction'])<1e-8):
            conflicts.append(dict(code='OWN_ARROW_MASK',path=arrow['pdfPath'],removedAreaFraction=loss))
    for part in strokes:
        if part['anchorIndex'] is not None and part['geometry'].intersects(mask):
            conflicts.append(dict(code='OWN_EXTENSION_MASK',path=part['id']))
        visible=part['geometry'].difference(mask)
        if visible.intersects(glyphs):conflicts.append(dict(code='OWN_VISIBLE_STROKE_TEXT',path=part['id']))
        part['geometry']=visible
    occupied=unary_union([p['geometry'] for p in parts])
    return dict(parts=parts,occupied=occupied,conflicts=conflicts,extensions=extensions,
                translation=delta,baselineDistance=distance+offset,anchors=anchors)


def _static_conflicts(scene, geometry, parts, contacts=None):
    index=ObstacleIndex(scene['obstacles']);clearance=scene['clearance'];conflicts=[]
    if not box(*scene['page']).covers(geometry):conflicts.append(dict(code='OUTSIDE_PAGE'))
    for part in parts:
        for hit in index.hits(part['geometry'],clearance):
            contact=(contacts or {}).get(part.get('anchorIndex'))
            if contact and hit['id'] in contact['paths']:
                overlap=part['geometry'].intersection(shape(hit['geometry']))
                if overlap.difference(Point(contact['point']).buffer(contact['radius'])).is_empty:continue
            conflicts.append(dict(code='STATIC_OBSTACLE',path=part['id'],obstacle=hit['id']))
    return conflicts


def _row(key, request, occupied, conflicts, cost):
    return dict(id=digest(dict(key=key,request=request)),key=key,request=request,cost=cost,
                status='CANDIDATE_CONFLICT' if conflicts else 'KNOWN_GEOMETRY_CLEAR',conflicts=conflicts,
                occupied=geometry_json(occupied),requiresNativeReadback=True)


def generate_joint_candidates(scene, primitives, capture, ink, source, options, acceptances=()):
    number=bind_part_number(scene,primitives,capture,ink,source)
    original_digest=scene['digest'];scene,point_bindings=bind_dimension_points(scene,primitives,capture,ink)
    clearance=positive(options['clearance'],'clearance');dimensions=options['dimensions'];leaders=options['leaders']
    require(isinstance(dimensions,dict) and dimensions and isinstance(leaders,dict), 'JOINT_POOL', 'Explicit roles required')
    samples={s['key']:s for s in primitives['samples']};keys=sorted([*dimensions,*leaders['keys'],'part-number'])
    require(len(keys)==len(set(keys)) and set(keys)-{'part-number'}==set(samples),
            'JOINT_COVERAGE', 'Every observed primitive must participate exactly once')
    offsets=options['numberOffsets']
    require(isinstance(offsets,list) and 0<len(offsets)<=81 and all(_point(p) for p in offsets),
            'JOINT_POOL', 'Finite number translations required')
    for values in dimensions.values():
        require(isinstance(values,list) and 0<len(values)<=81 and all(type(v) in (int,float) and math.isfinite(v) for v in values),
                'JOINT_POOL', 'Finite dimension offsets required')
    estimate=sum(len(v) for v in dimensions.values())+len(offsets)+len(leaders['keys'])*len(leaders['offsets'])*(1+len(leaders['channels']))
    require(estimate<=2000, 'JOINT_BUDGET', 'Measured study candidate limit exceeded')
    accepted=_accepted_paths(acceptances,primitives,scene['visibleStudy'])
    static={k:copy.deepcopy(v) for k,v in scene.items() if k!='digest'}
    static['obstacles']=[o for o in static['obstacles'] if o['owner'] not in samples and o['id'] not in number['obstacleIds']]
    static['clearance']=clearance
    measured={**scene,'associationTolerance':primitives['comparisonToleranceNative']}
    rows=[]
    for key in sorted(dimensions):
        sample=samples[key];anchors,_,_=_dimension_frame(sample);contacts={}
        radius=max(o.get('strokeRadius',0) for o in scene['obstacles'] if o['owner']==key)
        for j,anchor in enumerate(anchors):
            pseudo=dict(details=dict(modelView=sample['details']['modelView'],route=[anchor]))
            paths=_anchor_paths(scene,pseudo,capture)
            contour_radius=max(o.get('strokeRadius',0) for o in scene['obstacles'] if o['id'] in paths)
            contacts[j]=dict(point=anchor,paths=paths,radius=primitives['comparisonToleranceNative']+2*(radius+contour_radius))
        for offset in sorted(set(dimensions[key])):
            try:geometry=dimension_geometry(measured,sample,offset,accepted)
            except ContractError as error:
                if error.code!='DIMENSION_SIDE':raise
                request=dict(kind='dimension',offset=offset)
                rows.append(dict(id=digest(dict(key=key,request=request)),key=key,request=request,cost=[0]*5,
                    status='CANDIDATE_CONFLICT',conflicts=[dict(code=error.code)],requiresNativeReadback=True))
                continue
            conflicts=geometry['conflicts']+_static_conflicts(static,geometry['occupied'],geometry['parts'],contacts)
            request=dict(kind='dimension',offset=offset,translation=geometry['translation'],anchors=anchors,
                         extensions={str(j):v for j,v in geometry['extensions'].items()},baselineDistance=geometry['baselineDistance'])
            rows.append(_row(key,request,geometry['occupied'],conflicts,[geometry['baselineDistance'],0,0,0,0]))
    for delta in sorted({tuple(p) for p in offsets}):
        geom=translate(box(*number['ink']['occupancyBox']),xoff=delta[0],yoff=delta[1])
        conflicts=_static_conflicts(static,geom,[dict(id='part-number',geometry=geom)])
        rows.append(_row('part-number',dict(kind='number',translation=list(delta)),geom,conflicts,[0,0,0,math.hypot(*delta),0]))
    # Keep leader-owned paths in this derived scene for measured arrows and glyphs.
    leader_scene={k:copy.deepcopy(v) for k,v in scene.items() if k!='digest'}
    leader_scene['obstacles']=[o for o in leader_scene['obstacles'] if o['owner'] not in dimensions and o['id'] not in number['obstacleIds']]
    leader_report=generate_leader_candidates(seal(leader_scene),primitives,capture,{**leaders,'clearance':clearance})
    for c in leader_report['candidates']:
        rows.append(dict(id=c['id'],key=c['request']['key'],request=dict(kind='leader',**c['request']),
                         cost=[0,c['cost'][1],c['cost'][2],0,c['cost'][0]],
                         **{k:v for k,v in c.items() if k in ('occupied','status','conflicts','requiresNativeReadback')}))
    rows.sort(key=lambda c:(c['key'],c['id']));clear=[c for c in rows if c['status']=='KNOWN_GEOMETRY_CLEAR']
    require(len(rows)<=2000, 'JOINT_BUDGET', 'Measured study candidate limit exceeded')
    pairs=[];geometries={c['id']:shape(c['occupied']) for c in clear}
    for i,a in enumerate(clear):
        for b in clear[i+1:]:
            if a['key']!=b['key'] and geometries[a['id']].distance(geometries[b['id']])<=clearance:
                pairs.append([a['id'],b['id']])
                require(len(pairs)<=500000,'JOINT_BUDGET','Measured study conflict limit exceeded')
    counts={k:sum(c['key']==k for c in clear) for k in keys}
    return seal(dict(schemaVersion='JointMeasuredCandidates.v1',productionEligible=False,
        status='FINITE_POOL_BLOCKED' if not all(counts.values()) else 'CANDIDATES_REQUIRE_JOINT_SELECTION',
        scope='MEASURED_SAME_SIDE_DIMENSIONS_PLAIN_MARK_AND_LEADERS_NOT_COMPLETE_MACHINING_LAYOUT',
        sceneDigest=original_digest,sourceDigest=source['digest'],numberBinding=number,dimensionPointBindings=point_bindings,options=options,
        acceptances=list(acceptances),keys=keys,candidates=rows,clearCounts=counts,pairConflicts=pairs,
        unresolved=scene['unresolved'],remainingGates=['FULL_PAGE_FONT_COVERAGE','NATIVE_READBACK',
        'MARK_PLACEMENT_POLICY','COMPLETE_MACHINING_COVERAGE','PRODUCTION_ROLE_CLEARANCE']))
