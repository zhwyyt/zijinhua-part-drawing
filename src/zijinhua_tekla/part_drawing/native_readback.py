"""Convert saved/reopened SDK values and native DXF into independent observations."""
import math
import re

from shapely.geometry import LineString, Point, box
from shapely.ops import unary_union

from .expression_coverage import audit_expression_coverage
from .native_layout import near_hole_anchor
from .native_render import load_render, boundary_paths, sample_arc


def _numbers(text):
    return [float(s) for s in re.findall(r'[-+]?\d+(?:\.\d+)?',text)]


def _witness_visible(expected, render, tolerance, scale):
    """Tekla clips line exports at actual text backgrounds, beyond glyph bounds.

    Only the measured layout box plus 0.2 of its actual height may explain a gap.
    A witness must still have a visible portion, fully supported by DXF lines.
    """
    nearby = [LineString(line) for line in render['lines']]
    lines = unary_union([line for line in nearby if line.distance(expected) <= tolerance])
    text_box = lambda t: box(*t.get('layoutMin', t['min']), *t.get('layoutMax', t['max']))
    masks = unary_union([text_box(t).buffer(.2*t['height']*scale, join_style=2)
                         for t in render['texts'] if t.get('height', 0) > 0 and
                         text_box(t).distance(expected) <= .2*t['height']*scale])
    visible = expected.difference(masks)
    return visible.length > tolerance and visible.difference(lines.buffer(tolerance)).is_empty


def _faceted_slot(hole, expected, render, tol):
    """Symbol2 slot outlines are exported as line facets, not ARC entities."""
    radius=hole['width']/2
    extra=(hole['length']-hole['width'])/2
    angle=math.radians(hole.get('angle', 0))
    direction=[math.cos(angle), math.sin(angle)]
    spine=LineString([[expected[i]-direction[i]*extra for i in (0,1)],
                      [expected[i]+direction[i]*extra for i in (0,1)]])
    solid=spine.buffer(radius, quad_segs=64)
    boundary=solid.boundary
    inner=solid.buffer(-max(tol, .5))
    lines=[]
    for path in render['paths']:
        if path['kind']!='LINE' or len(path['points'])<2:
            continue
        segment=LineString(path['points'])
        # Keep the outline. An axis that only crosses the outline is not part of it.
        if segment.distance(boundary)<=max(tol, .5) and segment.intersection(inner).length<=tol:
            lines.append(segment)
    if len(lines)<4:
        return None
    points=[point for line in lines for point in line.coords]
    along=[sum((point[i]-expected[i])*direction[i] for i in (0,1)) for point in points]
    normal=[-direction[1], direction[0]]
    across=[sum((point[i]-expected[i])*normal[i] for i in (0,1)) for point in points]
    measured_length, measured_width=max(along)-min(along), max(across)-min(across)
    center=[expected[0]+direction[0]*(max(along)+min(along))/2+normal[0]*(max(across)+min(across))/2,
            expected[1]+direction[1]*(max(along)+min(along))/2+normal[1]*(max(across)+min(across))/2]
    if abs(measured_length-hole['length'])>tol or abs(measured_width-hole['width'])>tol or math.dist(center, expected)>tol:
        return None
    longest=max(lines, key=lambda line: line.length)
    dx,dy=[longest.coords[-1][i]-longest.coords[0][i] for i in (0,1)]
    return dict(center=center, width=measured_width, length=measured_length,
                angle=math.degrees(math.atan2(abs(dy), abs(dx))))


def _hole_shapes(requirements,render,tol):
    """Infer circles/capsules from exported arc centers/radii, not planned values."""
    arcs=[p for p in render['paths'] if p['kind'] in ('ARC','CIRCLE')]
    results=[]
    for h in requirements['facts']['holes']:
        expected=requirements['facts']['points'][h['pointId']]
        if h['kind']=='ROUND':
            candidates=[a for a in arcs if math.dist(a['center'],expected)<=tol and abs(a['radius']-h['diameter']/2)<=tol]
            if not candidates or sum(a['sweep'] for a in candidates)<math.tau-1e-4: raise ValueError('Missing exported round hole '+h['id'])
            c=candidates[0]
            complete=LineString(sample_arc(c['center'],c['radius'],0,math.tau))
            if complete.hausdorff_distance(unary_union([LineString(a['points']) for a in candidates]))>tol:
                raise ValueError('Exported circular hole boundary is incomplete '+h['id'])
            measured=dict(center=c['center'],diameter=2*c['radius'])
        else:
            radius=h['width']/2;extension=(h['length']-h['width'])/2
            candidates=[a for a in arcs if abs(a['radius']-radius)<=tol and abs(math.dist(a['center'],expected)-extension)<=tol and abs(a['sweep']-math.pi)<1e-4]
            if len(candidates)!=2:
                faceted=_faceted_slot(h, expected, render, tol)
                if faceted is None:
                    raise ValueError('Expected two actual slot end arcs '+h['id'])
                results.append(dict(kind='hole_shape',objectId='dxf-hole:'+h['id'],holeId=h['id'],holeKind=h['kind'],visible=True,**faceted))
                continue
            a,b=sorted(candidates,key=lambda p:p['center'])
            dx,dy=[b['center'][i]-a['center'][i] for i in (0,1)]
            measured=dict(center=[(a['center'][i]+b['center'][i])/2 for i in (0,1)],width=a['radius']+b['radius'],
                          length=math.hypot(dx,dy)+a['radius']+b['radius'],angle=math.degrees(math.atan2(dy,dx))%180)
            # Each straight capsule side must actually exist in the export.
            angle=math.atan2(dy,dx);v=[-math.sin(angle),math.cos(angle)]
            actual_paths=[LineString(a['points']),LineString(b['points'])]
            for sign in (-1,1):
                side=LineString([[p['center'][i]+sign*p['radius']*v[i] for i in (0,1)] for p in (a,b)])
                matches=[LineString(p['points']) for p in render['paths'] if p['kind']=='LINE' and side.hausdorff_distance(LineString(p['points']))<=tol]
                if not matches:
                    raise ValueError('Missing exported slot side '+h['id'])
                actual_paths.extend(matches)
            capsule=LineString([a['center'],b['center']]).buffer(a['radius'],quad_segs=180).boundary
            if capsule.hausdorff_distance(unary_union(actual_paths))>tol:
                raise ValueError('Slot end arcs face the wrong side or fail full boundary coverage '+h['id'])
        results.append(dict(kind='hole_shape',objectId='dxf-hole:'+h['id'],holeId=h['id'],holeKind=h['kind'],visible=True,**measured))
    return results


def readback_observations(requirements,plan,raw,render):
    if raw['stage']!='SAVED_REOPENED' or raw['id']!=plan['id'] or raw['sourceDigest']!=requirements['sourceDigest'] or raw['planDigest']!=plan['planDigest']:
        raise ValueError('Readback identity/digest/persistence mismatch')
    if abs(raw['scale']-plan['scale'])>1e-9: raise ValueError('View scale changed')
    if raw.get('geometryMode')!=plan['geometryMode'] or raw.get('linkedTargetCount')!=1:
        raise ValueError('Native model association or boundary mode changed')
    expected_visible=0 if plan['geometryMode']=='ANALYTIC_NATIVE_CURVES' else 1
    if raw.get('visiblePartCount')!=expected_visible:
        raise ValueError('Unexpected visible part count')
    bolts=raw.get('sourceBolts')
    if not isinstance(bolts,list):raise ValueError('Missing source bolt visibility evidence')
    symbol_plan={b['modelId']:b for b in plan['bolts'] if b['display']=='SYMBOL'}
    shown={}
    for b in bolts:
        wanted=symbol_plan.get(b.get('modelId'))
        if wanted:
            if (b.get('hidden') is not False or b.get('representation')!='Symbol2' or b.get('hole') is not True
                    or b.get('axis') is not True):
                raise ValueError('Bolt symbol display was not saved as planned')
            shown[b['modelId']]=b
        elif (b.get('hidden') is not True or b.get('color')!='Invisible' or b.get('representation')!='Symbol'
              or b.get('hole') is not False or b.get('axis') is not False):
            raise ValueError('Source bolt hardware graphics were not suppressed')
    if set(shown)!=set(symbol_plan):raise ValueError('Planned bolt symbol missing from the drawing')
    rows={r['key']:r for r in raw['objects']}
    if len(rows)!=len(raw['objects']): raise ValueError('Duplicate annotation ownership keys')
    expected_keys={c['key'] for domain in ('dimensions','notes','curves','radii') for c in plan[domain]}
    if set(rows)!=expected_keys:raise ValueError('Missing/extra native annotations')
    if len({r['id'] for r in rows.values()})!=len(rows):raise ValueError('Duplicate object identity')
    objects=[];tol=requirements['modelToleranceMm'];facts=requirements['facts']
    for d in plan['dimensions']:
        row=rows[d['key']];axis=0 if d['axis']=='x' else 1
        if row['kind']!='dimension' or row['arrow']!='FilledArrow':raise ValueError('Native dimension style/type changed')
        if d.get('suppressWitnesses') and row.get('extension')!='Yes':raise ValueError('Native witness suppression was not saved')
        if row.get('textColor')!='Black' or row.get('textHeight',0)<2.5:raise ValueError('Dimension text visibility/height changed')
        if 'viewPlan' in plan and abs(row['textHeight']-plan['viewPlan']['fontHeight'])>.01:
            raise ValueError('Dimension font height differs from template style')
        if plan['display'].get('shortDimension') and row.get('shortDimension')!=plan['display']['shortDimension']:
            raise ValueError('Short dimension placement differs from planned style')
        if row.get('combine')!=(plan['display']['combineFormat'] if d['combine'] else 'Off'):raise ValueError('Dimension combine format changed')
        if 'up' not in row or abs(row['up'][axis])>1e-8 or abs(abs(row['up'][1-axis])-1)>1e-8:raise ValueError('Dimension direction changed')
        if abs(row['distance']-d['distance'])>tol or any(abs(row['up'][i]-d['up'][i])>1e-8 for i in (0,1)):
            raise ValueError('Dimension line placement changed')
        segments=[]
        for child in row['children']:
            pts=sorted([child['start'][:2],child['end'][:2]],key=lambda p:p[axis])
            ids=[]
            for pt in pts:
                matches=[k for k in d['points'] if math.dist(facts['points'][k],pt)<1e-6]
                if not matches: raise ValueError('Dimension moved off its real source anchor')
                ids.append(matches[0])
            numbers=_numbers(child['text'])
            if not numbers:raise ValueError('Empty dimension text')
            segments.append((pts,ids,numbers,child['text']))
        segments.sort(key=lambda s:s[0][0][axis])
        if len(segments)!=len(d['points'])-1:raise ValueError('Dimension chain count changed')
        coordinates=[segments[0][0][0]]+[s[0][1] for s in segments]
        ids=[segments[0][1][0]]+[s[1][1] for s in segments]
        for a,b in zip(segments,segments[1:]):
            if math.dist(a[0][1],b[0][0])>1e-6:raise ValueError('Broken native dimension chain')
        obs=dict(kind='dimension',objectId=row['id'],axis=d['axis'],role=d['role'],points=ids,coordinates=coordinates,
                 values=[s[0][1][axis]-s[0][0][axis] for s in segments],displayValues=[s[2][-1] for s in segments])
        # Tekla writes n@p into every child of a combined run; plain values elsewhere.
        runs=d['combineRuns'];inside={i for a,b in runs for i in range(a,b)}
        for i,s in enumerate(segments):
            if ('@' in s[3])!=(i in inside):raise ValueError('Native n@p combination differs from planned runs: '+d['key'])
        for a,b in runs:
            texts={tuple(segments[i][2]) for i in range(a,b)}
            if len(texts)!=1 or next(iter(texts))[0]!=b-a:raise ValueError('Combined pitch count differs from intervals: '+d['key'])
        line_coordinate=d['coordinates'][0][1-axis]+row['up'][1-axis]*row['distance']
        def exported(label,low,high):
            midpoint=(low+high)/2
            target=[midpoint,line_coordinate] if axis==0 else [line_coordinate,midpoint]
            near=sorted((t for t in render['texts'] if math.dist([(t['min'][i]+t['max'][i])/2 for i in (0,1)],target)<4*row['textHeight']*raw['scale']),
                        key=lambda t:t['min'][axis])
            return any(t['text'].replace(' ','')==label for t in near) or ''.join(t['text'] for t in near).replace(' ','')==label
        for i,segment in enumerate(segments):
            if i in inside:continue
            if not exported(f"{segment[2][-1]:g}",segment[0][0][axis],segment[0][1][axis]):
                raise ValueError('Dimension label absent from actual export: '+d['key'])
        for a,b in runs:
            count,pitch=segments[a][2][0],segments[a][2][-1]
            if not exported(f"{int(count)}@{pitch:g}",segments[a][0][0][axis],segments[b-1][0][1][axis]):
                raise ValueError('Combined pitch label absent from actual export: '+d['key'])
        objects.append(obs)
    for curve in plan['curves']:
        if curve.get('role')!='witness':continue
        row=rows[curve['key']]
        if row['kind']!='line' or row['style']!='SolidLine' or any(math.dist(row[k][:2],curve[k])>1e-6 for k in ('start','end')):
            raise ValueError('Custom witness differs from its real-anchor display plan')
        expected=LineString([row['start'][:2],row['end'][:2]])
        if not _witness_visible(expected,render,tol,raw['scale']):
            raise ValueError('Custom witness absent from actual export')
    symbols={h:b for b in plan['bolts'] for h in b['holes']}
    for shape in _hole_shapes(requirements,render,tol):
        bolt=symbols.get(shape['holeId'])
        if bolt:
            # Exported symbol circle measures the hole; axes come from the saved symbol.
            saved=shown[bolt['modelId']]
            symbol=dict(kind='bolt_symbol',objectId='dxf-symbol:'+shape['holeId'],holeId=shape['holeId'],holeKind=shape['holeKind'],
                        center=shape['center'],modelId=bolt['modelId'],representation=saved['representation'],
                        containsHole=saved['hole'],containsAxis=saved['axis'])
            if shape['holeKind']=='ROUND':
                symbol['diameter']=shape['diameter']
            else:
                symbol.update(width=shape['width'],length=shape['length'],angle=shape['angle'])
            objects.append(symbol)
        else:objects.append(shape)
    for h in facts['holes']:
        if h['id'] in symbols:continue
        lines=[rows[f"center:{h['id']}:{j}"] for j in (0,1)]
        if any(l['kind']!='line' or l['style']!='DashDot' for l in lines):raise ValueError('Hole center line style changed')
        objects.append(dict(kind='center_lines',objectId='cross:'+lines[0]['id'],holeId=h['id'],style='DASH_DOT',lines=[[l['start'][:2],l['end'][:2]] for l in lines]))
    for n in plan['notes']:
        row=rows[n['key']]
        if row['kind']!='text':raise ValueError('Native note type changed')
        text=row['text'];kind=n['kind'];obs=dict(kind=kind,objectId=row['id'],text=text)
        exported=any(t['text']==text and math.dist([(t['min'][i]+t['max'][i])/2 for i in (0,1)],row['at'][:2])<4*row['height']*raw['scale'] for t in render['texts'])
        if row['height']<2.5 or not exported:
            raise ValueError('Note absent/unreadable in actual native export: '+n['key'])
        if 'viewPlan' in plan and abs(row['height']-n['height'])>.01:
            raise ValueError('Note height differs from planned template style')
        if kind=='label':obs['leader']=row['tip'] is not None or row['arrow']!='NoArrow'
        elif kind=='thickness':
            if not text.startswith('t='):raise ValueError('Thickness note changed')
            obs.update(value=float(text[2:]),displayValue=float(text[2:]),placement='table',valueBasis='DISPLAY_TEXT')
        elif kind=='specification':obs['aspect']=n['feature']
        elif kind=='diameter':
            if row['tip'] is None or row['arrow']!='FilledArrow' or not text.startswith('Φ'):raise ValueError('Outer diameter leader missing')
            obs.update(value=float(text[1:]),anchor=row['tip'][:2])
        elif kind=='hole_callout':
            g=next(g for g in requirements['groups'] if g['id']==n['key']);values=_numbers(text)
            if row['tip'] is None or row['arrow']!='FilledArrow':raise ValueError('Hole leader missing')
            obs.update(members=n['members'],count=int(values[0]),holeKind=g['kind'],valueBasis='DISPLAY_TEXT')
            if g['kind']=='ROUND':obs['diameter']=values[1]
            else:obs.update(width=values[1],length=values[2],angle=g['angle'])
            representatives=[h for h in facts['holes'] if h['id'] in n['members']]
            if g.get('origin')=='BOLT':
                from .native_layout import bolt_hole_tip
                if math.dist(bolt_hole_tip(representatives, facts), row['tip'][:2])>tol:
                    raise ValueError('Bolt hole leader misses the outermost hole center')
            elif not any(math.dist(near_hole_anchor(h,facts['points'][h['pointId']],row['at'][:2]),row['tip'][:2])<=tol for h in representatives):
                raise ValueError('Leader misses the near side of the called-out hole')
        objects.append(obs)
    for r in plan['radii']:
        row=rows[r['key']];segment=next(s for s in facts['segments'] if s['id']==r['feature'])
        if row['kind']!='radius_dimension' or abs(row['distance']-r['distance'])>tol:raise ValueError('Native radius dimension changed: '+r['key'])
        anchors=[p[:2] for p in row['points'] if p is not None and any(abs(v)>1e-9 for v in p[:2])]
        if not anchors or not all(abs(math.dist(p,segment['center'])-segment['radius'])<=tol for p in anchors):
            raise ValueError('Native radius dimension is not on its arc: '+r['key'])
        labels=[t for t in render['texts'] if re.fullmatch(r'R\s*=?\s*\d+(?:\.\d+)?',t['text'].strip()) and
                math.dist([(t['min'][i]+t['max'][i])/2 for i in (0,1)],r['points'][1])<16*row['textHeight']*raw['scale']]
        if len(labels)!=1:raise ValueError('Native radius label absent or ambiguous in export: '+r['key'])
        value=_numbers(labels[0]['text'])[0];decimals=plan['display']['decimals']
        objects.append(dict(kind='radius',objectId=row['id'],feature=r['feature'],value=value,valueBasis='DISPLAY_TEXT',
                            text=f"R{value:.{decimals}f}",anchor=anchors[0],auxiliaryCircle=False))
    for s in facts['segments']:
        paths,refs=boundary_paths(s,render,tol)
        objects.append(dict(kind='sampled_boundary',objectId='dxf-boundary:'+s['id'],feature=s['id'],visible=True,paths=paths,exportEntities=refs))
    return dict(schemaVersion='expressionObservations.v1',sourceDigest=requirements['sourceDigest'],stage='READBACK',geometryToleranceMm=tol,objects=objects)


def audit_layout_readback(plan, raw, render):
    """Check actual glyphs against M5's declared occupancy and binding layers."""
    if 'layout' not in plan:
        return []
    from .template_snapshot import contains
    errors = []
    planned = plan['layout']['textBoxes']
    if plan['layout']['status'] != 'SOLVED':
        return [dict(code='LAYOUT_NOT_SOLVED')]
    area = box(raw['frameOrigin'][0]*raw['scale'],raw['frameOrigin'][1]*raw['scale'],
               (raw['frameOrigin'][0]+raw['width'])*raw['scale'],
               (raw['frameOrigin'][1]+raw['height'])*raw['scale'])
    for text in render['texts']:
        bounds = text['min']+text['max']
        if not area.intersects(box(*bounds)):
            continue
        matches = [b for b in planned if b['text'] == text['text'] or
                   b.get('placement') == 'NATIVE_RADIUS_ENVELOPE' and re.fullmatch(r'R\s*\d+(?:\.\d+)?',text['text'])]
        if not any(contains(b['bounds'],bounds,.1) for b in matches):
            errors.append(dict(code='TEXT_OUTSIDE_LAYOUT_ENVELOPE',text=text['text'],bounds=bounds))
    rows = {r['key']:r for r in raw['objects']}
    for row in rows.values():
        if row['kind'] == 'text' and 'boxMin' in row and 'boxMax' in row:
            bounds = row['boxMin'][:2]+row['boxMax'][:2]
            if not any(b['key']==row['key'] and contains(b['bounds'],bounds,.1) for b in planned):
                errors.append(dict(code='NATIVE_TEXT_OUTSIDE_LAYOUT_ENVELOPE',key=row['key'],bounds=bounds))
    positions = []
    for d in plan['dimensions']:
        row = rows.get(d['key'],{})
        if 'distance' not in row or 'up' not in row:
            continue  # The independent coverage reader reports missing evidence.
        axis = 'xy'.index(d['axis']); side = row['up'][1-axis]
        positions.append(dict(key=d['key'],axis=axis,side=side,role=d['role'],column=d.get('columnGroup'),
                              line=d['coordinates'][0][1-axis]+side*row['distance']))
    for p in positions:
        for q in positions:
            if p['key'] == q['key'] or p['axis'] != q['axis']:
                continue
            if p['column'] and p['column'] == q['column'] and abs(p['line']-q['line']) > .1:
                errors.append(dict(code='COLUMN_SPLIT_AFTER_SAVE',objects=[p['key'],q['key']]))
            if p['role']=='overall' and q['side']==p['side'] and p['side']*(p['line']-q['line']) <= .1:
                errors.append(dict(code='OVERALL_NOT_OUTERMOST',objects=[p['key'],q['key']]))
    return errors


def visual_audit(plan,raw,render):
    """Check actual sheet bounds and text against other text/linework."""
    scale=raw['scale'];origin=raw['origin'];w,h=raw['sheetSize'];errors=[]
    errors.extend(audit_layout_readback(plan,raw,render))
    lo=[origin[i]+raw['frameOrigin'][i] for i in (0,1)]
    if 'viewPlan' in plan:
        from .view_plan import audit_view_plan
        errors.extend(audit_view_plan(plan,raw))
    elif lo[0]<10 or lo[1]<40 or lo[0]+raw['width']>w-10 or lo[1]+raw['height']>h-10:
        errors.append(dict(code='VIEW_OUTSIDE_SAFE_SHEET',bounds=lo+[raw['width'],raw['height']]))
    # Native text bounding boxes are authoritative for owned notes. Export text
    # supplies dimension labels and template contents that are absent from SDK boxes.
    boxes=[]
    for row in raw['objects']:
        if row['kind']=='text':boxes.append((row['key'],row['text'],box(*row['boxMin'][:2],*row['boxMax'][:2])))
    owned_boxes=[b for _,_,b in boxes]
    view_area=box(raw['frameOrigin'][0]*scale,raw['frameOrigin'][1]*scale,(raw['frameOrigin'][0]+raw['width'])*scale,(raw['frameOrigin'][1]+raw['height'])*scale)
    for t in render['texts']:
        b=box(*t['min'],*t['max'])
        if view_area.intersects(b) and not any(o.intersects(b) for o in owned_boxes): boxes.append(('export:'+t['id'],t['text'],b))
    for i,(key,text,b) in enumerate(boxes):
        for other,other_text,c in boxes[i+1:]:
            if b.intersection(c).area>.05*scale*scale:errors.append(dict(code='TEXT_OVERLAP',objects=[key,other],texts=[text,other_text]))
        # A tiny inset permits dimension baselines beneath the glyph box, but
        # rejects lines through the interior of text. Leaders stop at text edges.
        inset=b.buffer(-.35*scale)
        if inset.is_empty:continue
        hits=[p for p in render['lines'] if LineString(p).intersection(inset).length>.8*scale]
        if hits:errors.append(dict(code='LINE_THROUGH_TEXT',object=key,text=text,count=len(hits)))
    barriers=[]
    for row in raw['objects']:
        if row['kind']=='line' and row['key'].startswith('witness:'):
            barriers.append((row['key'],LineString([row['start'][:2],row['end'][:2]])))
        elif row['kind']=='dimension' and row.get('children') and row.get('up'):
            axis=0 if abs(row['up'][0])<.5 else 1
            points=[p[:2] for c in row['children'] for p in (c['start'],c['end'])]
            ends=[list(min(points,key=lambda p:p[axis])),list(max(points,key=lambda p:p[axis]))]
            coordinate=ends[0][1-axis]+row['up'][1-axis]*row['distance']
            for p in ends:p[1-axis]=coordinate
            barriers.append((row['key'],LineString(ends)))
    for row in raw['objects']:
        if row['kind']!='text' or not row.get('tip'):continue
        leader=LineString([row['tip'][:2],row['at'][:2]])
        crossed=[key for key,line in barriers if leader.crosses(line)]
        if crossed:errors.append(dict(code='LEADER_CROSSES_DIMENSION',object=row['key'],barriers=crossed))
    return dict(status='PASS' if not errors else 'FAIL',errors=errors,checkedTextBoxes=len(boxes),source=render['source'])


def audit_native_drawing(requirements,plan,raw,dxf_path):
    render=load_render(dxf_path,raw)
    section_report = None
    main_raw = raw
    if requirements.get('sectionRequirements'):
        from .section_readback import audit_sections
        section_report = audit_sections(requirements,plan,raw,dxf_path)
        main_raw = dict(raw,objects=[o for o in raw['objects'] if o['kind']!='section_mark'])
    try:
        observations=readback_observations(requirements,plan,main_raw,render)
        coverage=audit_expression_coverage(requirements,observations,display_decimals=plan['display']['decimals'])
    except (ValueError,KeyError,IndexError,TypeError,StopIteration) as error:
        observations=None;coverage=dict(status='FAIL',errors=[dict(code='NATIVE_READBACK_INVALID',message=str(error))])
    visual=visual_audit(plan,raw,render)
    result = dict(schemaVersion='nativeDrawingAudit.v1',partId=plan['id'],status='PASS' if coverage['status']=='PASS' and visual['status']=='PASS' else 'FAIL',
                  persistenceStatus='PASS' if raw['stage']=='SAVED_REOPENED' else 'FAIL',coverage=coverage,visual=visual,observations=observations)
    if section_report is not None:
        result['sections'] = section_report
        if section_report['status'] != 'PASS': result['status'] = 'FAIL'
    return result
